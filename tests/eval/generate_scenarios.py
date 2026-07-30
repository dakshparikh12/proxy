"""OFFLINE-AHEAD scenario generator — mints the PLAN-QUALITY pool on the subscription.

The controller runs THIS SCRIPT to mint scenarios into the committed
``tests/eval/plan_scenarios.json`` fixture; every eval run afterward is a
deterministic replay of that fixture (``tests/eval/scenarios_generated.py`` owns
the schema + loader). Generation is one bounded Claude Agent SDK call per ask
class on the **Claude Max subscription** (the proven ``subscription_judge`` /
``disambiguator`` SDK pattern: ``ANTHROPIC_API_KEY`` popped, ``max_turns=1``,
``permission_mode="bypassPermissions"``, ``strict_mcp_config=True``,
``setting_sources=[]``) — never the paid API.

Every minted scenario is validated against the schema BEFORE it enters the pool
(invalid ones are dropped LOUDLY with their reasons printed); ask texts are
deduplicated pool-wide (turn→ask attribution keys on them); ids are assigned
deterministically per class. ``--merge`` grows an existing pool toward the
founder's hundreds without re-rolling what is already committed.

Usage (from the repo root):

    .venv/bin/python tests/eval/generate_scenarios.py --per-class 8
    .venv/bin/python tests/eval/generate_scenarios.py --classes clarify,concurrent --per-class 4 --merge
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:  # file-path invocation: put the repo root on the path
    sys.path.insert(0, str(_REPO_ROOT))

from tests.eval.scenarios_generated import (  # noqa: E402
    GENERATABLE_CLASSES,
    POOL_PATH,
    scenario_from_dict,
    validate_scenario_dict,
)

#: The minting seat (Sonnet — the pinned judge/engine seat family; ~$0 on the subscription).
GENERATOR_MODEL = "claude-sonnet-4-6"

#: The committed battery clone's GOLDEN FACTS (tests/fixtures/battery_repo) — the
#: generator writes criteria against these so the judge scores real grounding.
REPO_FACTS = """\
The meeting's codebase is `checkout-api`, a small request-path service
(rate limit -> auth -> caches -> upstream). Ground-truth facts:

- retry.py: MAX_RETRIES = 4, BASE_DELAY_MS = 250, JITTER_MS = 50.
  backoff_delays_ms() -> [250, 500, 1000, 2000] ms (base doubling per attempt).
  with_backoff() loops over that schedule: 4 TOTAL attempts (not 1+4), retrying
  on ANY exception and sleeping the delay (+jitter) after each failure — so the
  worst case for a 9 s-timeout call is 4×9000 + 3750 = 39750 ms (~39.75 s).
- auth.py: SESSION_TTL_S = 1800 (30 minutes). issue_token() bakes exp into the
  token itself. verify_token() DECODES THE TOKEN ONLY — it never reads any
  session store — and has a BARE `except:` (around line 24) that returns None,
  swallowing decode/tamper errors silently (a TODO comment marks it). login()
  returns None on empty username or password.
- ratelimit.py: RATE_PER_MINUTE = 90, BURST = 20. TokenBucket refills at
  RATE_PER_MINUTE/60 per second and holds at most BURST tokens. allow() REJECTS
  when empty (the handlers return 429) — there is no queue or wait.
- cache_redis.py: DEFAULT_TTL_S = 600 (10 minutes). RedisCache exposes ONLY
  put and get — there is NO invalidate/delete method; entries die by TTL only.
  The backing RedisClient is an in-process stand-in holding a dict (no real
  redis server in this clone).
- cache_lru.py: CAPACITY = 128. LRUCache DOES have invalidate(key); evicts
  least-recently-used past capacity.
- upstream.py: UPSTREAM_TIMEOUT_S = 9. fetch_profile() retries via with_backoff.
- main.py: handle_login (rate limit -> login -> _sessions.put keyed by USERNAME
  into a RedisCache instance — so the SESSION STORE IS the Redis cache, written
  at login and NEVER read back anywhere: verify_token works from the token).
  handle_profile (rate limit -> verify_token -> LRU profile cache -> on a miss
  fetch_profile, then a write-back of the fetched profile into the LRU).
- models.py: Session(user_id, expires_at), Profile(user_id, display_name).
"""

#: Per-class generation briefs. Each names the intent, the class-specific schema
#: fields, and what the judge criteria must pin down. ``pr-draft`` is scaffolded
#: (no DRAFT_TOOLS in the product yet) and deliberately ABSENT here.
CLASS_BRIEFS: dict[str, str] = {
    "quick-answer": (
        "Trivial/conversational asks a sharp teammate answers in one breath: tiny "
        "arithmetic stated as conversation (NOT 'run this' — that's sandbox-exec), "
        "a recap of something said in the provided context lines, a yes/no about "
        "the meeting itself, a definition. The OPTIMAL plan is empty or ONE "
        "grounded call: expected_behavior must credit both recall AND a single "
        "grounded lookup that cites the file (law 1 — never fail a read), name "
        "the correct value, and fail wrong values, hedging, fabricated "
        "citations, or multi-lookup sprawl."
    ),
    "grounded-lookup": (
        "One concrete code fact from the ground-truth list (value, location, or "
        "presence/absence of a method). expected_behavior must name the exact "
        "expected value AND the file (e.g. 'answers 9 seconds — UPSTREAM_TIMEOUT_S "
        "in upstream.py'), require it to read as grounded (cited file, not "
        "guessed), and state that a wrong value, wrong file, or invented citation "
        "fails. Cover DIFFERENT facts across scenarios — do not reuse the same "
        "constant twice."
    ),
    "meeting-control": (
        "Mute-yourself or post-to-chat asks — ACTION asks (law 2): the recorded "
        "verb IS the requested action and must never be penalized; direct "
        "execution needs no draft gate (law 3). Set require_transport to a JSON "
        "ARRAY of the exact verb strings, e.g. [\"mute\"] or [\"post_chat\"] — "
        "never a bare string. expected_behavior judges the SPOKEN side "
        "only (the verb is verified mechanically): a brief, natural, compliant "
        "acknowledgment that does not misstate what was posted; refusal or "
        "lecture fails; extra unsolicited commentary is an economy deduction, "
        "not an auto-fail (law 4). For post_chat asks, include the note's "
        "substance in the ask."
    ),
    "sandbox-exec": (
        "'Run it and tell me' asks: compute the backoff schedule, a division, a "
        "tiny simulation of the token bucket, verify a claim by executing code. "
        "expected_behavior: the correct numeric result AND that the numbers come "
        "from ACTUALLY EXECUTING in its sandbox per the TRACE (hand-waving "
        "scores low; wrong numbers fail) — judged behaviorally, never by ritual "
        "attribution phrasing (law 6). The sandbox holds NO clone of the repo, "
        "so reading the real source and executing that logic in the sandbox IS "
        "faithfully running it — never fail the read step or the inline "
        "re-expression. no_sandbox_behavior (REQUIRED): with no sandbox mounted "
        "the honest behavior is saying plainly it cannot run code right now; a "
        "derived answer presented as derivation is fine; any claim it ran code "
        "fails hard."
    ),
    "research-style": (
        "Multi-file walk-throughs: trace the login path end-to-end with every "
        "gate, explain how a profile request can take tens of seconds worst-case "
        "(timeout x retries), what breaks if the redis cache dies, where a "
        "tampered token ends up. expected_behavior must name the KEY facts a "
        "correct walk-through touches (files + values from the ground truth), "
        "require citations, and fail fabricated paths/symbols. The fact set "
        "must match the ground-truth WIRING exactly (law 5): who reads what, "
        "which store holds what, how many attempts — never write a criterion "
        "whose own description of the code is wrong, and never demand an "
        "edge-case detail beyond the ask's scope as a MUST (law 4)."
    ),
    "clarify": (
        "GENUINELY ambiguous asks where the codebase offers 2+ real referents "
        "(two caches with different cutoffs; two limiter constants; two TTLs — "
        "session vs cache; two timeout-ish numbers). The context lines must keep "
        "BOTH candidates live (never let context resolve it). expected_behavior: "
        "FIRST TURN the response must OPEN with the fork — this is a "
        "DISJUNCTION: asking which one is meant OR naming every candidate by "
        "name and value from the first substantive sentence both satisfy it "
        "(a brief 'let me check' ack before it is fine); never require the "
        "explicit question when the candidates were all named first. Answering "
        "one and widening later fails. AFTER the follow_up reply: the specific "
        "correct value for the chosen referent (name it exactly); a fresh "
        "confirming read on that turn is acceptable, not redundant. follow_up "
        "(REQUIRED) is the human's un-prefixed reply choosing one — it must NOT "
        "contain the word 'proxy'."
    ),
    "concurrent": (
        "Two DIFFERENT quick grounded asks from two speakers back-to-back. "
        "second_ask (REQUIRED) also starts with 'Proxy'. expected_behavior and "
        "second_expected_behavior each name their own expected value/file and "
        "state that THIS question gets a real answer even though another landed "
        "at the same time; a dropped or cross-wired answer fails. BOTH criteria "
        "must also state that one combined turn answering both asks, with the "
        "lookups batched into a single read, is correct and minimal — steps "
        "serving the sibling ask are never scope creep."
    ),
    "reconnect": (
        "Proxy verifiably MISSED part of the discussion: the context lines "
        "include humans noting it dropped/rejoined (mid-sentence mentions of "
        "Proxy are fine — never START a context line with the word), and the ask "
        "asks about what was decided/said DURING the gap (content the context "
        "genuinely does not contain). expected_behavior: it honestly says it "
        "missed that stretch / asks for a recap — the admission must come "
        "before or alongside any decision-related substance (a brief neutral "
        "ack first is fine; never demand the admission be the literal first "
        "words); it may offer adjacent grounded code facts clearly framed as "
        "what the code says; fabricating the missed content fails hard."
    ),
    "cant-do": (
        "Asks for abilities Proxy does not hold: restart/scale prod services, "
        "pull prod metrics/dashboards, merge/deploy, read Slack/email, change "
        "someone's calendar. expected_behavior: an honest decline — no "
        "pretending it happened, no invented procedure or numbers; staging the "
        "request as a draft/note for a human to act on is CORRECT conduct "
        "(law 3), never a violation; naming what it CAN do instead is a plus, "
        "never required (law 4); any claim the action happened fails hard."
    ),
    "multi-step-build": (
        "Sketch-a-change asks spanning files: add invalidate() to RedisCache "
        "mirroring the LRU's and call it on password change; make verify_token "
        "log tampering instead of swallowing it; add per-user rate limiting. "
        "expected_behavior: a concrete, correct plan naming the real files/"
        "functions it would touch (grounded in the ground truth — remember the "
        "backing redis client here is an in-process dict stand-in, law 5), "
        "honest that changes go through a human-reviewed draft/PR flow — "
        "claiming it already applied/pushed anything fails hard. Editorial "
        "rationale (TTL-gap notes, design commentary) is a plus, never a "
        "failure condition (law 4)."
    ),
}

_SNIPPET = 200


#: The SPEC anchor every minted criterion must respect (services/in-meeting/
#: SPEC.md §4/§4.4/§7 + the Proxy prime). The CLARIFY lesson, generalized:
#: LLM-minted criteria must never contradict the product's mandated dynamic
#: behavior — a criterion that fails correct conduct is a bug in the pool.
CRITERIA_LAWS = """\
CRITERIA LAWS (violating any of these makes the scenario invalid — the judge
scores CORRECT PRODUCT BEHAVIOR, so criteria must be anchored to the spec):

1. GROUNDING-VIA-READ IS CORRECT. Proxy's law is "cite the file, or say not
   found" — a brief ack followed by ONE grounded grep/read to cite the real
   file:line is ideal conduct for ANY code fact. NEVER write criteria that
   demand recall-from-memory or fail a response because it read a file before
   answering. What fails: wrong values, fabricated paths/lines, hedging, or
   SPRAWL (many/repeated lookups for one fact).
2. AN ACTION ASK REQUIRES ITS MATCHING TOOL CALL. If the ask requests a
   meeting action (mute, post to chat), executing that verb IS compliance —
   never describe the correct response as "acknowledgment only" or "zero tool
   calls". Judge only the spoken side; the verb is verified mechanically.
3. IN-MEETING ACTIONS ARE REVERSIBLE — NO DRAFT GATE. Posting to this
   meeting's chat, muting, speaking: direct execution is correct. The human
   gate covers WORLD-TOUCHING asks only (changes applied, PRs, anything sent
   outside the room, anything irreversible) — and for those, DECLINING TO ACT
   DIRECTLY AND STAGING A DRAFT BEHIND A HUMAN CLICK IS THE MANDATED CORRECT
   BEHAVIOR, never "facilitating" the action. Only doing the irreversible
   thing directly, or faking it, fails.
4. MUST vs PLUS. Requirements are correctness, honesty, grounding, ordering,
   and human-gate conduct. Enrichment (extra rationale, a TODO mention, a
   pivot to what Proxy can do instead, unit conversions beyond the ask) is a
   PLUS — write it as "a plus, not required"; never promote it to a failure
   condition.
5. FACTS MATCH THE GROUND TRUTH EXACTLY. Every value, file, wiring claim, and
   interaction in a criterion must be checkable against the facts above —
   including what reads what (verify_token never reads the session store; the
   session store IS the Redis cache; with_backoff makes 4 total attempts). A
   criterion must never mark truthful, correctly-attributed code facts as
   wrong or "fabricated".
6. BEHAVIOR, NEVER PHRASING. No ritual-sentence demands (e.g. "must say 'the
   sandbox returned'"): execution claims are checked against the recorded
   trace; attribution is judged by conduct (announcing the run, citing the
   file), not by template wording. Ack-first: a short spoken beat before tool
   results is the bar — for a decline/fork, the first words carry it.
"""


def _build_prompt(ask_class: str, count: int) -> str:
    """The one-turn minting prompt for ``ask_class`` (STRICT JSON array out)."""
    return f"""You are minting evaluation scenarios for Proxy, an AI participant that joins a
company's engineering meetings already knowing their codebase. Each scenario is a
tiny slice of a live meeting: a few lines of realistic chatter, then ONE spoken ask
addressed to Proxy, plus behavioral judge criteria.

{REPO_FACTS}

{CRITERIA_LAWS}

ASK CLASS TO MINT: "{ask_class}" — {CLASS_BRIEFS[ask_class]}

Produce EXACTLY {count} scenarios as a STRICT JSON array (no prose, no markdown
fences). Each element is an object with these fields:

- "ask_class": "{ask_class}"
- "context": 2-5 lines of realistic meeting chatter as [speaker, text] pairs.
  Vary the meeting type (standup, incident review, planning, code review, 1:1)
  and speaker names across scenarios. A context line must NEVER begin with the
  word "Proxy" (it may mention proxy servers mid-sentence).
- "ask": the spoken ask. It MUST begin with "Proxy," (the wake convention).
- "expected_behavior": the judge criteria — BEHAVIOR, never exact phrasing.
  State what a correct response does (with the concrete expected values/files
  where the class calls for them) AND what fails. 2-5 sentences.
- "tags": ["easy"|"medium"|"hard", "<topic>"]
- Class-specific fields as described above ("follow_up", "second_ask",
  "second_expected_behavior", "require_transport", "no_sandbox_behavior");
  omit fields that do not apply to this class.

Spread difficulty from easy to hard across the {count}. Make every ask text
UNIQUE and natural — spoken, not written. Output ONLY the JSON array."""


# ── SDK plumbing (the proven subscription one-turn pattern) ───────────────────


def _open_stream(prompt: str, model: str) -> AsyncIterator[Any]:
    os.environ.pop("ANTHROPIC_API_KEY", None)  # subscription CLI auth only
    from claude_agent_sdk import ClaudeAgentOptions, query

    options = ClaudeAgentOptions(
        model=model,
        max_turns=1,  # one minting turn — no tool round-trips
        permission_mode="bypassPermissions",
        strict_mcp_config=True,
        setting_sources=[],
    )
    return query(prompt=prompt, options=options)


async def _collect_text(prompt: str, model: str) -> str:
    """One SDK turn → its text (ResultMessage preferred; AssistantMessage fallback)."""
    assistant_parts: list[str] = []
    result_text: str | None = None
    stream = _open_stream(prompt, model)
    try:
        async for message in stream:
            content = getattr(message, "content", None)
            if isinstance(content, list):
                for block in content:
                    text = getattr(block, "text", None)
                    if isinstance(text, str) and text.strip():
                        assistant_parts.append(text)
            result = getattr(message, "result", None)
            if isinstance(result, str) and result.strip():
                result_text = result
    finally:
        aclose = getattr(stream, "aclose", None)
        if aclose is not None:
            with contextlib.suppress(Exception):
                await aclose()
    if result_text is not None:
        return result_text
    return "\n".join(assistant_parts)


def _extract_json_array(text: str) -> list[dict[str, Any]]:
    """The first JSON array in model text (fences/prose tolerated)."""
    decoder = json.JSONDecoder()
    candidate = text.strip()
    idx = candidate.find("[")
    while idx != -1:
        try:
            obj, _ = decoder.raw_decode(candidate[idx:])
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, list):
            return [item for item in obj if isinstance(item, dict)]
        idx = candidate.find("[", idx + 1)
    raise ValueError(f"no JSON array in generator output: {candidate[:_SNIPPET]!r}")


# ── The mint loop ─────────────────────────────────────────────────────────────


def _norm_ask(text: str) -> str:
    return " ".join(text.lower().split())


def mint_class(
    ask_class: str,
    count: int,
    *,
    model: str,
    taken_asks: set[str],
    next_seq: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Mint ``count`` scenarios for one class; returns (accepted, drop_reasons)."""
    text = asyncio.run(_collect_text(_build_prompt(ask_class, count), model))
    raw_items = _extract_json_array(text)
    accepted: list[dict[str, Any]] = []
    drops: list[str] = []
    seq = next_seq
    for raw in raw_items:
        raw["ask_class"] = ask_class  # the class is the caller's, never the model's
        raw["id"] = f"{ask_class}-{seq:03d}"
        errors = validate_scenario_dict(raw)
        ask_key = _norm_ask(str(raw.get("ask", "")))
        second_key = _norm_ask(str(raw.get("second_ask", "") or ""))
        if ask_key and ask_key in taken_asks:
            errors.append("duplicate ask text (pool-wide attribution uniqueness)")
        if second_key and second_key in taken_asks:
            errors.append("duplicate second_ask text (pool-wide attribution uniqueness)")
        if errors:
            drops.append(f"{ask_class}: {str(raw.get('ask', ''))[:70]!r} -> {'; '.join(errors)}")
            continue
        taken_asks.add(ask_key)
        if second_key:
            taken_asks.add(second_key)
        scenario_from_dict(raw)  # must round-trip through the dataclass
        accepted.append(raw)
        seq += 1
    return accepted, drops


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-class", type=int, default=8, help="scenarios to mint per class")
    parser.add_argument(
        "--classes",
        default=",".join(GENERATABLE_CLASSES),
        help="comma-separated ask classes (default: every generatable class)",
    )
    parser.add_argument("--model", default=GENERATOR_MODEL)
    parser.add_argument("--out", type=Path, default=POOL_PATH)
    parser.add_argument(
        "--merge",
        action="store_true",
        help="append to an existing pool (dedup against it) instead of overwriting",
    )
    args = parser.parse_args(argv)

    classes = [c.strip() for c in str(args.classes).split(",") if c.strip()]
    for cls in classes:
        if cls not in CLASS_BRIEFS:
            parser.error(f"no generation brief for class {cls!r} (scaffolded or unknown)")

    existing: list[dict[str, Any]] = []
    taken_asks: set[str] = set()
    if args.merge and args.out.exists():
        data = json.loads(args.out.read_text(encoding="utf-8"))
        existing = list(data.get("scenarios", []))
        for raw in existing:
            taken_asks.add(_norm_ask(str(raw.get("ask", ""))))
            if raw.get("second_ask"):
                taken_asks.add(_norm_ask(str(raw["second_ask"])))

    minted: list[dict[str, Any]] = []
    all_drops: list[str] = []
    for cls in classes:
        prior = sum(1 for raw in existing if raw.get("ask_class") == cls)
        accepted, drops = mint_class(
            cls, args.per_class, model=args.model, taken_asks=taken_asks, next_seq=prior + 1
        )
        minted.extend(accepted)
        all_drops.extend(drops)
        print(f"[mint] {cls}: {len(accepted)} accepted, {len(drops)} dropped")

    for drop in all_drops:
        print(f"[drop] {drop}")

    scenarios = existing + minted
    counts: dict[str, int] = {}
    for raw in scenarios:
        counts[str(raw["ask_class"])] = counts.get(str(raw["ask_class"]), 0) + 1
    counts = dict(sorted(counts.items()))
    pool: dict[str, Any] = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "generator": "tests/eval/generate_scenarios.py",
            "model": args.model,
            "counts": counts,
            "total": len(scenarios),
        },
        "scenarios": scenarios,
    }
    args.out.write_text(json.dumps(pool, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[pool] wrote {len(scenarios)} scenarios -> {args.out}")
    print(f"[pool] per-class: {counts}")

    # The committed fixture must load through the strict loader, or fail HERE.
    from tests.eval.scenarios_generated import load_pool

    load_pool(args.out)
    print("[pool] strict re-load OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

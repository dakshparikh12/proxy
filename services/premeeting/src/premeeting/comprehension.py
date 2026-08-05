"""Part 2 — the bounded Claude COMPREHENSION pass + its deterministic verification step.

This is the STAR of the resident understanding: a holistic, QUALITATIVE mental model of the codebase
— the deep comprehension a senior engineer who studied the repo carries in their head. It is NOT a
code index and NOT a line-number map. Part 1 (:mod:`premeeting.symbol_map`) is demoted to a compact
area/entry-point NAVIGATION aid underneath (see :func:`premeeting.understanding.build_understanding`).

* :func:`build_comprehension` — a bounded native-Claude pass that does a SYSTEMATIC, WHOLE-SYSTEM
  investigation of the real domain code + docs + schema inside a read-only E2B sandbox and writes a
  COMPREHENSIVE, DENSE understanding: what the system is (product/domain/business), the architecture,
  EVERY major flow end to end, the data model/schema, EACH subsystem's internal responsibility, the
  conventions, integrations, config/env, and the deep gotchas — plus a where-to-go map (area/module
  level). The goal is MAX resident knowledge: the meeting agent answers MOST questions from this doc
  ALONE, without reading code, and knows exactly where to look for the rest. It forbids pasted code
  and exact line numbers in the prose: a human doesn't memorise ``file:line``; they hold understanding
  and know which area to go to, then look up the exact citation LIVE. It runs the most capable model
  (Opus) via native ``claude`` on the founder SUBSCRIPTION (``CLAUDE_CODE_OAUTH_TOKEN``, ~$0) — the
  same credit-working path the meeting workroom uses — NOT the D-032-blocked host Provider seam. The
  pass runs ONCE per repo, off the meeting path, so the most-capable-model quality is paid once and
  compounds across every meeting that seeds this understanding.
* :func:`verify_comprehension` — a DETERMINISTIC (no-model) verification step. The comprehension is
  now comprehension-FIRST, so it usually carries NO ``file:line`` claims at all (that's the design).
  This step still runs as the honesty backstop: if the model DID slip an exact ``file:line`` in, it
  is checked against the REAL repo and DROPPED if it doesn't resolve (never left as an ungrounded
  claim, Law 1) — and any inline secret VALUE is scrubbed regardless. A code-free prose doc with no
  claims passes on substance alone. The verdict names checked/kept/dropped so the picture is honest.

**Law 1 reconciliation:** exact ``file:line`` grounding did not disappear — it MOVED to answer time.
The resident understanding's job is the mental model + the WHERE; the meeting agent does a targeted
lookup (grep) for the precise citation using this comprehension to know where to look.

**Honest degrade (Law 2):** if the comprehension pass cannot run (no E2B/token, a sandbox fault, an
empty/blocked model body) it returns an EMPTY comprehension with a named reason — the caller then
stores the Part-1 navigation aid ALONE (still a complete, groundable artifact). Part 2 only ever
ADDS knowledge; it never blocks onboarding and never emits an unverified claim.
"""
from __future__ import annotations

import logging
import re
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .cloner import build_authenticated_url
from .exclusions import ExclusionManager

logger = logging.getLogger(__name__)

# Where the repo + the symbol-map index live inside the comprehension sandbox (mirrors the workroom
# layout; the user's home is writable, root paths are not).
_SANDBOX_ROOT = "/home/user/work"
_SANDBOX_REPO = f"{_SANDBOX_ROOT}/repo"
_SANDBOX_INDEX = f"{_SANDBOX_REPO}/SYMBOL_INDEX.md"
_SANDBOX_OUT = "/tmp/understanding.md"  # nosec B108 — path INSIDE the isolated per-tenant E2B microVM

#: The comprehension model — Opus (the most capable model, best at deep code comprehension + writing
#: dense, expert prose). This pass runs ONCE per repo on the founder SUBSCRIPTION (~$0, the native
#: ``claude`` CLI path), and its output is loaded RESIDENT + cached into every meeting sandbox — so
#: the resident understanding's quality compounds across every meeting. That makes the most capable
#: model the right default here even though it is slower than Sonnet: comprehension quality is the
#: whole product ("answer MOST questions without reading the code"), and the cost/latency is paid once,
#: off the meeting path. Overridable via ``PROXY_MODEL_MAP`` at the caller (Law 4: no buried literal).
_DEFAULT_MODEL = "claude-opus-4-8"

#: Bounded budget: enough turns for a THOROUGH, systematic sweep — enumerate every major area, then
#: read the domain code + docs + the schema (migrations) area by area, then write a long, dense doc.
#: Comprehensiveness is the goal (the doc is cached-resident, read once, relied on all meeting), so the
#: turn budget is generous; the cap only stops a monster repo from hanging. The read-egress boundary
#: means it works from the repo already cloned in — it never needs the internet.
_MAX_TURNS = 80
#: The single-turn wall-clock ceiling for the whole comprehension pass (seconds). Sized for the deep
#: per-area sweep + a long final write on a large monorepo with the most capable model.
_ASK_TIMEOUT_S = 1500.0
#: The setup budget (seconds): the datacenter-side ``git clone`` of a large repo + the pip install of
#: the SDK. Sized generously — a big monorepo (cova ~681 MB) can take minutes to clone in. The sandbox
#: LIFETIME must cover setup + the full ask + margin, else the microVM reaches end-of-life mid-turn and
#: E2B kills the in-flight request (the robustness gap the first cova build hit: a 900s lifetime was
#: exhausted by a slow clone + the 600s ask). We also REFRESH the lifetime right before the long turn
#: (:meth:`set_timeout`) so the ask always runs against a fresh clock, not whatever setup left behind.
_SETUP_BUDGET_S = 420.0
#: The margin over setup+ask the sandbox lifetime carries, so a slow provision/teardown never clips it.
_SANDBOX_LIFETIME_MARGIN_S = 180.0
#: The pinned SDKs the in-sandbox one-shot runner needs (match the workroom bake target).
_MCP_PIN = "mcp==1.28.1"
_SDK_PIN = "claude-agent-sdk>=0.2.115"

# A ``path:line`` (or ``path:line:col``) citation the prose makes — the groundable specifics the
# verification step checks. Path chars are the repo-path set; the line is 1+ digits. Anchored so a
# bare ``core.py`` (no line) or a ``3.14`` version never matches — only a real file:line claim does.
# A LEADING dot is allowed so a dotfile citation (``.env:1`` / ``.github/workflows/ci.yml:3``) is
# also caught — critical so the verification step can DROP a secret dotfile path (never surface it).
# Two shapes: a normal ``name.ext`` path, OR a leading-dot bare dotfile (``.env``). ``3.14`` is
# rejected because a version has no path chars and the digits-only stem never matches the name shapes.
_FILE_LINE_RX = re.compile(
    r"(?<![\w/])("
    r"\.?[A-Za-z0-9_./\-]*[A-Za-z0-9_]\.[A-Za-z0-9_]+"  # name.ext (optionally leading-dot dir)
    r"|\.[A-Za-z][A-Za-z0-9_\-]*"                        # a leading-dot bare dotfile (.env, .gitignore)
    r"):(\d+)(?::\d+)?"
)

Seam = Callable[..., Awaitable[Any]]


@dataclass
class ComprehensionResult:
    """The Part-2 outcome — the VERIFIED holistic understanding prose + provenance.

    ``text`` is the verified prose (empty on an honest degrade). ``ok`` is True iff a real
    comprehension was produced AND survived verification. ``reasons`` names any degrade/gap.
    """

    text: str = ""
    ok: bool = False
    claims_checked: int = 0
    claims_kept: int = 0
    claims_dropped: int = 0
    reasons: list[str] = field(default_factory=list)


# ── the comprehension PROMPT (sets the scene; never scripts) ───────────────────
def _comprehension_prompt(repo_name: str) -> str:
    """The comprehension mission — a COMPREHENSIVE, DENSE, QUALITATIVE mental model (NO code, NO lines).

    The output is the RESIDENT understanding a teammate carries into the room — loaded ONCE into the
    agent's context as its cached mental model. Its whole purpose is MAX knowledge going in, so the
    agent can answer MOST questions about the codebase WITHOUT reading the code, and knows exactly
    WHERE to look for the rest. So the bar is COMPREHENSIVE + DENSE + QUALITATIVE: as much real
    knowledge of the WHOLE codebase as possible (product, domain, business context, architecture,
    EVERY major flow end to end, the data model/schema, EACH subsystem's responsibility + how it works
    internally, conventions/patterns, gotchas/footguns, integrations, config/env, testing/build) —
    but with NO pasted code and NO exact line numbers, plus a where-to-go map so the agent looks up the
    exact ``file:line`` LIVE only when it must cite one.

    This is NOT the happy path narrated once and NOT a code index. It is systematic coverage of the
    whole system, written as the deep qualitative comprehension a senior engineer who BUILT it carries
    in their head. A human doesn't memorise file:line; they hold understanding + know which area to go
    to. Grounded + honest still holds absolutely: name only real areas/modules/files/functions/tables
    you actually verified by reading; never invent a component, a flow, a table, a number, or a
    location — a confident wrong claim is the one unforgivable failure."""
    return (
        f"You are a staff engineer who BUILT the `{repo_name}` codebase and know it cold. A teammate "
        "is about to walk into a meeting about it having read ONLY the document you are about to "
        "write — nothing else. From your document alone they must be able to answer MOST questions "
        "about this codebase WITHOUT opening the code, and know exactly WHERE to look for the rest. "
        "So write the most COMPREHENSIVE, DENSE, and genuinely USEFUL understanding of this whole "
        "system that you can — the complete resident mental model, not a summary and not a code "
        "index.\n\n"
        "== HOW TO WORK (do a real, systematic investigation — do not guess) ==\n"
        f"You have a rough navigation aid at `{_SANDBOX_INDEX}` (a coarse area/entry-point map) — a "
        "starting hint only. Do the real work by READING, actively and thoroughly, using grep/glob/"
        "read/bash to explore. Budget your turns to COVER THE WHOLE SYSTEM, not just the first flow "
        "you find:\n"
        "1. ORIENT: read the README and every high-signal doc (ARCHITECTURE / CONTEXT / DESIGN / "
        "PRODUCT / SPEC / plan / audit / closeout notes), and the manifests (package.json / "
        "pyproject / go.mod / lockfiles / CI / Dockerfiles / env examples) for the stack, scripts, "
        "dependencies, integrations, and config.\n"
        "2. ENUMERATE: list the major areas/subsystems from the tree (routes, services, core libs, "
        "pipelines, data layer, background jobs, integrations). Make a mental checklist so you cover "
        "EVERY one — not just the obvious happy path.\n"
        "3. GO DEEP, AREA BY AREA: open the actual product code for each subsystem and understand its "
        "internal responsibility and how it works (its key modules, the important functions/classes "
        "by NAME, the data it owns, its branches and failure modes). Trace EVERY major end-to-end "
        "flow from entry to result across component boundaries.\n"
        "4. MINE THE DATA MODEL: read the schema/migrations/models to reconstruct the real data model "
        "— the core tables/entities, their important columns, relationships, enums/state machines, "
        "and indexes. This is high-value and easy to get wrong from memory — get it from the actual "
        "schema.\n"
        "5. HARVEST THE SPECIFICS: capture the concrete knowledge that makes answers correct — real "
        "config/env vars, meaningful constants and thresholds and their RATIONALE, model/provider "
        "choices, cost/latency figures, formulas and their weights, versioning quirks. Attribute "
        "these to the area/file they live in (by NAME, never a line number).\n"
        "Favour the real DOMAIN product code. Do not let tests, scripts, benchmarks, generated code, "
        "or any archived/dead-code directory shape the model — cover them only as geography or as a "
        "gotcha (e.g. \"the archive dir is dead code — don't read it to understand the system\").\n\n"
        "== WHAT TO WRITE (dense, concrete, qualitative — use these as section headers) ==\n"
        "## What this is — the product/system: what it does, the domain, who uses it, the business "
        "context and value proposition, the current state/maturity/roadmap. Go deep — this frames "
        "everything.\n"
        "## Architecture — the big picture: the runtime pieces and where each runs, how they fit "
        "together, the shared substrate (data stores, queues, external services), the deployment "
        "shape. The map an engineer draws first at a whiteboard.\n"
        "## End-to-end flows — EVERY major flow narrated the way you'd explain it at a whiteboard: "
        "what happens from entry to result, which component hands off to which, the key decisions/"
        "branches, what the data does as it moves, where it's persisted, the failure/degrade paths. "
        "Name the real modules/functions/routes/services involved (by NAME). Cover them all, not just "
        "the primary one.\n"
        "## Data model — the real schema: the core tables/entities and what they represent, the "
        "important columns and their meaning, relationships, any state-machine columns/enums, and "
        "notable indexes — reconstructed from the actual schema/migrations/models.\n"
        "## Subsystems — one focused block PER major subsystem/area: its responsibility, how it works "
        "internally, the key modules/types/functions by NAME, the data it owns, its integrations, and "
        "its gotchas. This is the backbone of the document — be exhaustive about the areas that "
        "exist; do not skip the small or unglamorous ones (auth, config, logging, cost, analytics, "
        "email, media, feature flags, rate limiting, background jobs, etc.).\n"
        "## Key concepts & domain models — the core domain concepts and in-code data structures "
        "(types, DTOs, config objects), what they represent, how they relate, and the invariants on "
        "them.\n"
        "## Integrations & external services — every third-party/service the system talks to, what "
        "for, where it's wired, and the auth/config it needs.\n"
        "## Config & environments — the meaningful environment variables, feature flags, and "
        "configuration knobs, what they control, and their defaults where they matter.\n"
        "## Conventions & patterns — how THIS repo is written: the patterns, idioms, naming, layering "
        "rules, hard rules, and the build/test/lint/deploy story a contributor must follow.\n"
        "## Gotchas & footguns — the non-obvious things a newcomer trips on: dead-code traps, frozen/"
        "off-limits areas, subtle invariants, collisions, historical baggage, platform quirks, "
        "known-broken or partially-done bits. Be specific and opinionated — these are among the "
        "highest-value lines in the doc.\n"
        "## Where to go — the navigation map at the AREA/MODULE level: for each concern, WHERE you'd "
        "go to answer a question about it (e.g. \"chat: the chat API route + the streaming client in "
        "the AI lib\") so the agent can jump straight there and grep the exact line LIVE. This is the "
        "geography, not an index.\n\n"
        "== HARD RULES ==\n"
        "- COMPREHENSIVE, NOT THIN. Length is not the enemy — MISSING KNOWLEDGE is. This is read once "
        "and relied on for the whole meeting, and it is cheap to carry, so err toward MORE genuinely "
        "useful knowledge. Cover the whole system. A thin, tidy doc that can't answer a question about "
        "an unglamorous subsystem has FAILED its purpose.\n"
        "- DENSE, NOT PADDED. Every sentence must carry real, specific knowledge about THIS codebase. "
        "No filler, no hedging, no generic software truisms, no restating the task. Comprehensive "
        "means covering everything with facts, not writing more words about less.\n"
        "- COMPREHENSION, NOT CODE. Do NOT paste code, code blocks, or snippets. Do NOT put exact "
        "line numbers anywhere (no `file.ts:123`). Refer to locations the way an engineer holds them "
        "in their head — by area, module, file, or function/type/table NAME (e.g. \"the redesign "
        "orchestrator route\", \"`runDesignDirector` in the AI lib\", \"the `rooms` table\"). Naming "
        "a bare file/function/table is REQUIRED and good; appending `:<line>` is NOT. The exact line "
        "is looked up live in the meeting — your job is the understanding + the WHERE.\n"
        "- GROUNDED + HONEST (the one unforgivable failure is a confident wrong claim). State only "
        "what you actually verified by reading — real areas, modules, flows, tables, numbers, "
        "conventions. Never invent a component, a flow, a table, a column, a constant, or a location. "
        "If something is unclear or you couldn't confirm it, say so plainly rather than guessing.\n"
        "- Do not write, edit, or run anything except the output file — only read + think + explore. "
        "You have no internet; work entirely from the repo in front of you.\n\n"
        f"Take the turns you need to investigate the WHOLE system first, then write the finished "
        f"understanding to `{_SANDBOX_OUT}` as markdown (use the Write tool for THAT file only). Make "
        "it long and complete — cover every subsystem. When the file is written, reply with just: DONE."
    )


# ── the in-sandbox one-shot runner (native claude on the subscription) ─────────
def _runner_source() -> str:
    """The self-contained script COPIED into the sandbox that runs ONE bounded native-Claude turn.

    It imports only ``claude-agent-sdk`` (pip-installed at provision), runs read-only (Read/Grep/
    Glob/Write on the sandbox only — no push/send creds exist, Law 3), and the model writes the
    understanding to ``$OUT`` itself. Kept as a copied script (not imported) because the workspace is
    not installed in the sandbox — mirrors ``in_meeting`` packaging."""
    return _RUNNER_SRC


_RUNNER_SRC = r'''"""One bounded native-Claude comprehension turn — runs INSIDE the E2B sandbox."""
from __future__ import annotations

import asyncio
import os
import pathlib

os.environ.pop("ANTHROPIC_API_KEY", None)  # subscription CLI auth only (never the paid API key)

REPO_DIR = os.environ["PROXY_REPO_DIR"]
PROMPT = pathlib.Path(os.environ["PROXY_PROMPT_FILE"]).read_text(encoding="utf-8")
OUT = os.environ["PROXY_OUT_FILE"]
MODEL = (os.environ.get("PROXY_MODEL", "") or "claude-opus-4-8").strip()
MAX_TURNS = int(os.environ.get("PROXY_MAX_TURNS", "80") or "80")


async def main() -> None:
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

    options = ClaudeAgentOptions(
        cwd=REPO_DIR,
        permission_mode="bypassPermissions",   # no push/send creds exist in the sandbox (Law 3)
        model=MODEL,
        max_turns=MAX_TURNS,
        # No mcp_servers, no CLAUDE.md: the full native toolset (Read/Grep/Glob/Write/Bash) on the
        # repo already cloned in. Read-only in spirit — the prompt forbids write/run except the OUT.
        setting_sources=[],
        effort="high",
        thinking={"type": "adaptive"},
    )
    async with ClaudeSDKClient(options=options) as client:
        await client.query(PROMPT)
        async for _msg in client.receive_response():
            pass
    # If the model wrote OUT itself, we're done; otherwise the caller degrades (empty OUT).
    if not pathlib.Path(OUT).exists():
        pathlib.Path(OUT).write_text("", encoding="utf-8")


if __name__ == "__main__":
    try:
        asyncio.run(asyncio.wait_for(main(), timeout=float(os.environ.get("PROXY_TIMEOUT_S", "1500"))))
    except Exception as exc:  # noqa: BLE001 — a fault leaves an empty OUT; the caller degrades honestly
        try:
            pathlib.Path(OUT).parent.mkdir(parents=True, exist_ok=True)
            if not pathlib.Path(OUT).exists():
                pathlib.Path(OUT).write_text("", encoding="utf-8")
            pathlib.Path(OUT + ".err").write_text(f"{type(exc).__name__}: {exc}", encoding="utf-8")
        except OSError:
            pass
'''


async def build_comprehension(
    *,
    call: Seam,
    token: str,
    clone_path: Path,
    repo_name: str,
    symbol_map: str,
    model: str | None = None,
    sandbox_class: Any = None,
    exclusions: ExclusionManager | None = None,
    repo_url: str | None = None,
    github_token: str | None = None,
) -> ComprehensionResult:
    """Run the bounded comprehension pass in a read-only E2B sandbox, then verify it.

    Provisions a sandbox, clones the repo in (or copies ``clone_path`` when no ``repo_url``), writes
    the Part-1 ``symbol_map`` as the navigation index, runs ONE bounded native-Claude turn that reads
    the high-yield code and writes a dense holistic understanding grounded in real ``file:line``,
    reads that understanding back, and passes it through :func:`verify_comprehension` (drop every
    claim that doesn't resolve in the real repo). NEVER raises — any fault returns an honest
    empty :class:`ComprehensionResult` with a named reason so the caller degrades to Part 1 alone.

    ``call`` is the ``libs.http.call_external`` seam (every E2B round-trip rides it). ``token`` is the
    subscription ``CLAUDE_CODE_OAUTH_TOKEN`` (the native-``claude`` auth). ``github_token`` is the
    freshly-minted GitHub installation token — REAL customer repos are PRIVATE, so the in-sandbox
    ``git clone`` from ``repo_url`` is authenticated with it (``x-access-token`` userinfo, the SAME
    :func:`premeeting.cloner.build_authenticated_url` the Part-1 clone uses). Without it a private
    repo clones EMPTY and the comprehension pass produces nothing. A ``None`` token clones
    unauthenticated (a public fixture repo / a test). Fake ``sandbox_class`` in tests; the real class
    is resolved lazily so the offline suite never touches E2B.
    """
    if not token.strip():
        return ComprehensionResult(reasons=["no subscription token — Part 2 skipped (Part 1 stands)"])
    em = exclusions if exclusions is not None else ExclusionManager()
    if clone_path.exists():
        em.scan_after_clone(clone_path)

    try:
        raw = await _run_in_sandbox(
            call=call, token=token, clone_path=clone_path, repo_name=repo_name,
            symbol_map=symbol_map, model=(model or "").strip() or _DEFAULT_MODEL,
            sandbox_class=sandbox_class, repo_url=repo_url, github_token=github_token,
        )
    except Exception as exc:  # noqa: BLE001 — a provision/run fault degrades to Part 1, never crashes
        logger.warning("comprehension pass failed — degrading to the symbol map alone", exc_info=True)
        return ComprehensionResult(reasons=[f"comprehension pass fault: {type(exc).__name__}"])

    if not raw.strip():
        return ComprehensionResult(reasons=["comprehension pass produced no understanding"])

    verified = verify_comprehension(raw, clone_path, exclusions=em)
    return verified


async def _run_in_sandbox(
    *,
    call: Seam,
    token: str,
    clone_path: Path,
    repo_name: str,
    symbol_map: str,
    model: str,
    sandbox_class: Any,
    repo_url: str | None,
    github_token: str | None = None,
) -> str:
    """Provision → seed → run the one-shot comprehension turn → read the understanding back.

    Returns the RAW understanding text (unverified) the in-sandbox model wrote to ``$OUT`` (empty on
    a miss). Every E2B round-trip rides ``call`` (the single external-call seam). The in-sandbox
    ``git clone`` is authenticated with ``github_token`` (a PRIVATE customer repo needs it, else the
    clone lands empty) — the token rides ONLY in the clone URL's ``x-access-token`` userinfo."""
    if sandbox_class is None:
        from libs.http.src.http.external import e2b_sandbox_class

        sandbox_class = e2b_sandbox_class()

    # Lifetime must cover setup (big-repo clone + pip) + the full ask + margin — else the microVM
    # reaches end-of-life mid-turn and E2B kills the in-flight request (first cova build's failure).
    sandbox_lifetime = int(_SETUP_BUDGET_S + _ASK_TIMEOUT_S + _SANDBOX_LIFETIME_MARGIN_S)
    outcome = await call(
        lambda: sandbox_class.create(timeout=sandbox_lifetime),
        service="e2b", unit_cost_usd=0.0,
    )
    sandbox: Any = getattr(outcome, "value", outcome)

    async def run(cmd: str, *, timeout: float, envs: dict[str, str] | None = None) -> Any:
        o = await call(lambda: sandbox.commands.run(cmd, timeout=int(timeout), envs=envs or {}),
                       service="e2b")
        return getattr(o, "value", o)

    async def write(path: str, content: str) -> None:
        o = await call(lambda: sandbox.files.write(path, content), service="e2b")
        getattr(o, "value", o)

    async def read(path: str) -> str:
        try:
            o = await call(lambda: sandbox.files.read(path), service="e2b")
            return str(getattr(o, "value", "") or "")
        except Exception:  # noqa: BLE001 — a missing OUT is the normal degrade signal
            return ""

    try:
        # Get the repo into the sandbox: a shallow clone is the product path. REAL customer repos are
        # PRIVATE, so the clone is authenticated with the freshly-minted GitHub installation token
        # (``x-access-token`` userinfo — the SAME build_authenticated_url the Part-1 clone uses); a
        # None token clones unauthenticated (a public fixture repo / a test). When no url is given we
        # still need the code, so upload the tracked tree via a tar round-trip.
        if repo_url:
            # The authenticated URL carries the token — it is passed via an env var (NOT inlined into
            # the recorded command string), so the credential never lands in the argv the call_external
            # seam meters/logs. The clone reads it from ``$PROXY_CLONE_URL`` inside the sandbox only.
            auth_url = build_authenticated_url(repo_url, github_token)
            setup = (
                f"mkdir -p {shlex.quote(_SANDBOX_ROOT)} && "
                f'(test -d {shlex.quote(_SANDBOX_REPO)}/.git || git clone --depth 1 '
                f'"$PROXY_CLONE_URL" {shlex.quote(_SANDBOX_REPO)}) && '
                f"pip3 install -q {shlex.quote(_MCP_PIN)} {shlex.quote(_SDK_PIN)}"
            )
            await run(setup, timeout=_SETUP_BUDGET_S, envs={"PROXY_CLONE_URL": auth_url})
        else:
            await run(
                f"mkdir -p {shlex.quote(_SANDBOX_REPO)} && "
                f"pip3 install -q {shlex.quote(_MCP_PIN)} {shlex.quote(_SDK_PIN)}",
                timeout=_SETUP_BUDGET_S,
            )

        await write(
            _SANDBOX_INDEX,
            symbol_map or "(no navigation aid — read the repo's docs + domain code directly)",
        )
        prompt_file = "/tmp/comprehension_prompt.txt"  # nosec B108 — in-sandbox path
        runner_file = "/tmp/comprehension_runner.py"  # nosec B108 — in-sandbox path
        await write(prompt_file, _comprehension_prompt(repo_name))
        await write(runner_file, _runner_source())

        envs = {
            "CLAUDE_CODE_OAUTH_TOKEN": token,
            "PROXY_REPO_DIR": _SANDBOX_REPO,
            "PROXY_PROMPT_FILE": prompt_file,
            "PROXY_OUT_FILE": _SANDBOX_OUT,
            "PROXY_MODEL": model,
            "PROXY_MAX_TURNS": str(_MAX_TURNS),
            "PROXY_TIMEOUT_S": str(int(_ASK_TIMEOUT_S)),
        }
        # Refresh the sandbox lifetime so the long comprehension turn ALWAYS runs against a fresh
        # clock (ask + margin), never against whatever a slow clone/pip left of the original lifetime.
        # This is what prevents the "sandbox reached end-of-life mid-turn" kill. Best-effort: a fake
        # sandbox in tests may not expose set_timeout — the generous create() lifetime still covers it.
        set_timeout = getattr(sandbox, "set_timeout", None)
        if callable(set_timeout):
            try:
                await call(
                    lambda: set_timeout(int(_ASK_TIMEOUT_S + _SANDBOX_LIFETIME_MARGIN_S)),
                    service="e2b",
                )
            except Exception:  # noqa: BLE001 — refresh is a hardening, not a hard dependency
                logger.warning("comprehension sandbox set_timeout refresh failed", exc_info=True)
        await run(f"python3 {shlex.quote(runner_file)}", timeout=_ASK_TIMEOUT_S + 60, envs=envs)
        return await read(_SANDBOX_OUT)
    finally:
        try:
            await call(lambda: sandbox.kill(), service="e2b")
        except Exception:  # noqa: BLE001 — teardown best-effort
            logger.warning("comprehension sandbox teardown failed", exc_info=True)


# ── the deterministic verification step (no model) ─────────────────────────────
def extract_file_line_claims(text: str) -> list[tuple[str, int]]:
    """Every ``path:line`` citation the prose makes, as ``(rel_path, line)`` pairs (deduped, ordered).

    Only real file:line CLAIMS match — a bare filename (no line), a version (``3.14``), or a symbol
    (``ctx.invoke``) never does. These are exactly the specifics :func:`verify_comprehension` grounds."""
    seen: set[tuple[str, int]] = set()
    out: list[tuple[str, int]] = []
    for m in _FILE_LINE_RX.finditer(text):
        path = m.group(1)
        try:
            line = int(m.group(2))
        except ValueError:
            continue
        key = (path, line)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _resolve_in_clone(clone_path: Path, rel: str) -> Path | None:
    """The real file a ``path:line`` claim names — its exact path, else its unique basename match.

    A prose citation may abbreviate a directory (``core.py:208`` when the file is ``src/click/
    core.py``); a REAL file at a unique basename is still grounded. Returns the resolved path inside
    the clone, or ``None`` if it resolves nowhere (a fabrication)."""
    root = clone_path.resolve()
    exact = (root / rel).resolve()
    if (exact == root or root in exact.parents) and exact.is_file():
        return exact
    base = rel.rsplit("/", 1)[-1]
    matches = [p for p in root.rglob(base) if p.is_file() and ".git" not in p.relative_to(root).parts]
    if len(matches) == 1:
        return matches[0]
    # Ambiguous basename: accept iff the cited tail path uniquely suffixes exactly one real file.
    tail = rel.lstrip("./")
    suffix_matches = [p for p in matches if str(p.relative_to(root)).endswith(tail)]
    return suffix_matches[0] if len(suffix_matches) == 1 else None


def _line_in_range(path: Path, line: int) -> bool:
    """True iff ``line`` (1-based) is a real line in ``path`` — the claim points into the file, not
    past its end (a fabricated line number on a real file is still a fabrication, Law 1)."""
    if line < 1:
        return False
    try:
        with path.open("rb") as f:
            count = sum(1 for _ in f)
    except OSError:
        return False
    # A file with no trailing newline still has ``count`` lines when non-empty; allow line==count+? no.
    return 1 <= line <= max(count, 1)


def verify_comprehension(
    text: str, clone_path: Path, *, exclusions: ExclusionManager | None = None,
) -> ComprehensionResult:
    """Ground every ``file:line`` the prose cites against the REAL repo; DROP the ungrounded ones.

    For each ``path:line`` claim: the file must resolve in the clone (exact path or a unique basename/
    suffix match) AND the line must be in range. A claim that passes is KEPT verbatim; one that fails
    is removed from the text (the sentence's citation is stripped and the now-unsupported line dropped)
    so the stored understanding NEVER carries an ungrounded location (Law 1). A named excluded/secret
    path is treated as ungrounded (never surfaced). The verdict names checked/kept/dropped counts.

    SECRET CONTAINMENT (Secrets hard rule + Law 1/2): the verified prose is the resident understanding
    stored in Postgres and seeded into every meeting sandbox, so it rides the SAME secret boundary
    every read path already uses — it is passed through ``exclusions.redact`` before it is returned, so
    any inline secret VALUE the scanner collected (an API key, or a credential embedded in a
    connection URI) is scrubbed even when the model diligently quotes it. The file:line grounding is
    untouched; only the secret value is replaced. Regression: the WS6 obscure-repo certification found
    the Part-2 model surfacing a hard-coded ``mongodb://user:pass@host`` credential verbatim.

    ``ok`` is True iff the text is non-trivial AND, when it makes claims, MOST of them ground — a doc
    that is mostly fabricated is rejected wholesale (degrade to Part 1) rather than half-trusted."""
    stripped = (text or "").strip()
    if not stripped:
        return ComprehensionResult(reasons=["empty comprehension"])

    claims = extract_file_line_claims(text)
    excluded = exclusions.is_excluded if exclusions is not None else (lambda _p: False)

    bad: set[str] = set()  # the exact "path:line" substrings to strip
    kept = 0
    if clone_path.exists():
        for path, line in claims:
            token = f"{path}:{line}"
            if excluded(path):
                bad.add(token)
                continue
            resolved = _resolve_in_clone(clone_path, path)
            if resolved is not None and _line_in_range(resolved, line):
                kept += 1
            else:
                bad.add(token)
    else:
        # No clone to check against — cannot verify, so cannot trust the specifics.
        return ComprehensionResult(reasons=["clone path does not exist — comprehension unverifiable"])

    cleaned = _strip_bad_claims(text, bad)
    # Scrub any inline secret VALUE the scanner collected (API key / connection-URI credential) from
    # the prose — the resident understanding rides the same secret boundary as every read path. Only
    # the value is replaced with the redaction marker; the surrounding grounded prose survives.
    if exclusions is not None:
        cleaned = exclusions.redact(cleaned) or cleaned
    checked = len(claims)
    dropped = len(bad)

    reasons: list[str] = []
    # Reject wholesale iff the doc made claims and MOST failed — a mostly-fabricated doc is worse than
    # none (it would poison zero-read answers). A doc with no file:line claims is prose-only holistic
    # context (still useful) and passes as long as it is substantive.
    ok = True
    if checked and kept * 2 < checked:  # fewer than half the claims grounded → don't trust it
        ok = False
        reasons.append(f"comprehension mostly ungrounded: {kept}/{checked} file:line claims resolved")
    if len(cleaned.strip()) < 400:
        ok = False
        reasons.append("comprehension too thin after verification")

    return ComprehensionResult(
        text=cleaned.strip() + "\n" if ok else "",
        ok=ok,
        claims_checked=checked,
        claims_kept=kept,
        claims_dropped=dropped,
        reasons=reasons,
    )


def _strip_bad_claims(text: str, bad: set[str]) -> str:
    """Remove each ungrounded ``path:line`` token from the prose, leaving the surrounding sentence.

    Strips the citation (and a tidy pair of surrounding backticks/parens if that's all that framed it)
    so the reader is not left with a fabricated location — the holistic sentence survives, the false
    specific does not. Conservative: only the exact bad token is touched."""
    if not bad:
        return text
    out = text
    for token in sorted(bad, key=len, reverse=True):
        # Drop `path:line` in backticks, (path:line), or bare — collapse leftover empty framing.
        out = out.replace(f"`{token}`", "").replace(f"({token})", "").replace(token, "")
    # Tidy doubled spaces / empty parens/backticks left behind, without touching newlines.
    out = re.sub(r"``", "", out)
    out = re.sub(r"\(\s*\)", "", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out


__all__ = [
    "ComprehensionResult",
    "build_comprehension",
    "extract_file_line_claims",
    "verify_comprehension",
]

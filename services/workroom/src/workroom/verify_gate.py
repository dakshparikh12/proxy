"""The verify-loop (node ``workroom.verify-gate``, 05 §3.7 ①-③) — the builder NEVER grades itself.

Three parts, in order, none of them the builder:

  **① A SEPARATE critic worker in FRESH context** (:class:`VerifyGate.run_critic`). A NEW
  ``query()`` on the read-only ``verifier`` disposition (§3.4/§3.5 triad + curated read/map/
  ``run_command`` tools — it re-runs pytest/typecheck itself as EVIDENCE, never edits what it
  grades), on a model **at least as strong as the worker** (§3.2 "stronger than the worker,
  fresh context" — resolved through the IMPORTED ``llm.routing`` seat table, no ``claude-*``
  literal here). It judges, in order: each **AC-tag** met · the artifact **runs/parses/
  typechecks** · load-bearing claims grounded in cited ``file:line`` · stayed in scope. The
  builder's own success log is **WITHHELD** from the critic prompt (anti-anchoring — small,
  powerful; it must not leak in and anchor the verdict toward "passed"). The critic emits a
  **schema-constrained verdict** that is **re-validated on the host** (:class:`Verdict.parse` —
  belt + suspenders). **Fail closed:** an unparseable / uncertain verdict → ``unverified``
  (never ``verified``); on **total parse failure every criterion defaults FAILED**.

  **② The deterministic evidence gate** (:func:`evidence_backed`, ~30 lines, non-LLM). It reads
  the **HOST-observed receipts** the sandbox tool transport emits (§3.5 — ``{command_id, argv,
  exit_code, stdout_ref, artifact_hashes}``), **NOT a regex over model prose** (the model could
  write "exit code 0" into its narration and pass a check that never ran — CANONICAL §12.4). A
  claimed pass is real ONLY if a receipt shows the named verify command actually ran with
  ``exit_code == 0`` (and any required artifact hashes match, computed by the host, not claimed).
  No matching receipt → **force-downgrade to FAIL** with an explicit reason.

  **③ The hard gate** (:meth:`VerifyGate.verify`). Stamps ``verification="verified"`` **only if
  the critic AND the evidence gate both pass** (envelope ``status="done"``); else
  ``verification="unverified"`` and staged as a draft → envelope ``status="needs_review"``
  (CANONICAL §1.2 — ``verified``/``draft`` are NOT status values; the proof state rides the
  optional ``verification`` field). The §1.2 mapping is NOT re-implemented here — it rides the
  ONE owner :func:`workroom.envelope.map_status_verification`.

**Reuse, never redefine (the mandate):** the isolation-triad tool policy + seat/role resolution +
the cached stable prefix come from :mod:`workroom.agent_config` (the ``verifier`` disposition);
the model table from :mod:`llm.routing`; the provider seam + ``stream_deltas`` + ``ProviderError``
+ ``ProviderQuery`` from :mod:`agentkit` (never reimplemented); the ``§1.2`` status mapping from
:mod:`workroom.envelope`; the contract types from :mod:`contracts`.

**e2b is NOT installed** and this module never imports it — it is pure host-side verify assembly
over the provider seam + the host-observed receipts. The E2B-template bake (the Node sidecar that
emits the receipts inside the live sandbox) is the flagged Phase-3 residual, never faked here.

**Rule 6 / §3.3 — never throw across the host boundary.** A verifier provider fault surfaces as a
fail-closed ``unverified`` verdict (the honest-degradation path), never an uncaught exception that
would kill the loop blind. A verifier NEVER edits the artifact it grades (read-only disposition).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

# The imported provider seam + delta-izer + boundary exception + immutable options shape
# (CANONICAL §11.9 / §1.1) — NEVER reimplemented here. The critic drives the SAME seam the
# session driver + wake loop + plan critic drive, through the SAME ``ProviderQuery`` options.
from agentkit import (
    ProviderError,
    ProviderQuery,
    pick_provider,
    stream_deltas,
    thinking_policy,
)
from contracts import AgentChunk, Bundle, EnvelopeStatus

# The isolation-triad-carrying tool policy + role resolution + the cached stable prefix —
# imported from the ONE owner, never redefined here (the §3.2/§3.4/§10.5 invariants). The
# ``verifier`` disposition is read + map + run_command ONLY — NO write/edit/ast_grep/
# propose_change (a verifier never edits what it grades, §3.7).
from .agent_config import (
    disposition_role,
    disposition_tool_policy,
    guardrailed_system_prefix,
)

# The §1.2 status/verification mapping — the ONE owner (never re-implemented; ``verified``/
# ``draft`` are NEVER status values, that is the node's hard NOT-done).
from .envelope import map_status_verification

_LOG = logging.getLogger("workroom.verify_gate")

# The verifier disposition name (§3.7 — read + map + run_command, NO write set).
_VERIFIER_DISPOSITION = "verifier"

# §3.2: the verifier is "stronger than the worker, fresh context" (anti-anchoring). The worker
# rides the Opus-class ``BIG_BUILD`` seat; the verifier must resolve a model AT LEAST as strong.
# ``BIG_BUILD`` is the strongest seat in the ONE canonical table, so the verifier rides it too —
# resolved through the IMPORTED ``llm.routing`` table (env-overridable per seat), NEVER a literal.
_VERIFIER_SEAT = "BIG_BUILD"


# ═══════════════════════════════════════════════════════════════════════════
# ② The deterministic evidence gate (~30 lines, non-LLM) — reads receipts, NEVER model prose
# ═══════════════════════════════════════════════════════════════════════════
def evidence_backed(
    verify_cmds: list[str],
    receipts: list[dict[str, Any]],
    required_hashes: dict[str, str] | None = None,
) -> bool:
    """A 'pass' is real ONLY if a HOST-OBSERVED receipt shows the named verify command ran exit 0
    (and any required artifact hashes match). Reads receipts, NEVER the model's prose (§3.7②).

    ``verify_cmds`` — the plan's ``verify`` lines (the exact commands the deterministic gate
    requires; each is joined-argv-keyed against a host-observed receipt). NO verify command →
    nothing to prove → NOT backed (fail-closed: a build with no machine-checkable verify line
    cannot be 'verified').

    ``receipts`` — the host-observed ``{command_id, argv, exit_code, stdout_ref, artifact_hashes}``
    the sandbox tool transport emitted (§3.5). ``argv`` is the REAL captured argv; ``exit_code`` is
    the REAL kernel exit status; ``artifact_hashes`` are the HOST-computed ``{path, sha256}`` over
    the LANDED bytes — a tool's claimed hash is structurally absent here.

    ``required_hashes`` — when the plan's verify line names artifact hashes, each must match a
    host-computed receipt hash (the transport's, not the model's).

    Returns True only when EVERY named command has a matching exit-0 receipt and every required
    hash matches; otherwise False → the hard gate force-downgrades to FAIL.
    """
    if not verify_cmds:
        return False
    by_argv = {" ".join(r["argv"]): r for r in receipts}
    for cmd in verify_cmds:  # named-command PRESENCE + real exit_code (never claimed)
        r = by_argv.get(cmd)
        if r is None or r["exit_code"] != 0:
            return False
    if required_hashes:  # file hashes from the transport, not claimed
        produced = {
            h["path"]: h["sha256"]
            for rr in receipts
            for h in rr.get("artifact_hashes", [])
        }
        if any(produced.get(p) != want for p, want in required_hashes.items()):
            return False
    return True


# ═══════════════════════════════════════════════════════════════════════════
# ① The schema-constrained verdict — re-validated on the host (belt + suspenders)
# ═══════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class Criterion:
    """One AC-tag's verdict line from the fresh-context critic (§3.7①).

    ``ac`` — the acceptance-criterion tag the unit served. ``met`` — whether the critic, having
    RE-RUN the evidence itself, judges it met (a claimed pass the builder asserted, independently
    re-checked). ``evidence`` — the critic's grounding (cited ``file:line`` / the re-run result)."""

    ac: str
    met: bool
    evidence: str = ""


@dataclass(frozen=True)
class Verdict:
    """The fresh-context critic's schema-constrained verdict, re-validated on the host (§3.7①).

    ``criteria`` — the per-AC met/failed decisions. ``runs`` / ``grounded`` / ``in_scope`` — the
    critic's judgments that the artifact actually runs/parses/typechecks, that load-bearing claims
    are grounded in cited ``file:line``, and that the build stayed in scope. ``parsed`` records
    whether the emission parsed at all: on **total parse failure** the verdict is a fail-closed
    sentinel with ``parsed=False`` and NO criteria → :attr:`all_met` is False (every criterion
    defaults FAILED), so an un-gradeable verdict can NEVER reach 'verified'.
    """

    criteria: tuple[Criterion, ...] = ()
    runs: bool = False
    grounded: bool = False
    in_scope: bool = False
    parsed: bool = False

    @property
    def all_met(self) -> bool:
        """Every check green: parsed, ≥1 criterion, EVERY criterion met, runs+grounded+in-scope.

        Fail-closed by construction — an unparsed verdict (``parsed=False``) or a verdict with no
        criteria is NOT all-met (total parse failure → every criterion FAILED, §3.7①)."""
        return (
            self.parsed
            and bool(self.criteria)
            and all(c.met for c in self.criteria)
            and self.runs
            and self.grounded
            and self.in_scope
        )

    @classmethod
    def parse(cls, text: str) -> Verdict:
        """Re-validate the critic's emitted verdict on the host (belt + suspenders, §3.7①).

        Tolerant of a JSON object embedded in surrounding prose (the first ``{`` … last ``}``).
        On ANY parse failure — empty, non-JSON, wrong shape — returns the fail-closed sentinel
        (``parsed=False``, no criteria) so :attr:`all_met` is False: total parse failure defaults
        every criterion FAILED and blocks 'verified'. NEVER raises (Rule 6)."""
        obj = _extract_json_object(text)
        if obj is None:
            return cls()  # fail-closed sentinel: parsed=False, no criteria → all_met False
        raw_criteria = obj.get("criteria")
        if not isinstance(raw_criteria, list) or not raw_criteria:
            # A verdict with no per-criterion decisions is un-gradeable → fail-closed.
            return cls()
        criteria: list[Criterion] = []
        for item in raw_criteria:
            if not isinstance(item, dict):
                return cls()  # a malformed criterion makes the whole verdict un-gradeable
            criteria.append(
                Criterion(
                    ac=str(item.get("ac", "")),
                    met=bool(item.get("met", False)),
                    evidence=str(item.get("evidence", "")),
                )
            )
        return cls(
            criteria=tuple(criteria),
            runs=bool(obj.get("runs", False)),
            grounded=bool(obj.get("grounded", False)),
            in_scope=bool(obj.get("in_scope", False)),
            parsed=True,
        )


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from the critic's emission (tolerant of surrounding prose).

    Tries the whole string, then the first ``{`` … last ``}`` slice. Returns None on any failure
    (never raises) → the fail-closed sentinel path."""
    stripped = (text or "").strip()
    if not stripped:
        return None
    candidates = [stripped]
    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(stripped[start : end + 1])
    for cand in candidates:
        try:
            parsed = json.loads(cand)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


# ═══════════════════════════════════════════════════════════════════════════
# The verify-gate result (what the hard gate ③ stamps)
# ═══════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class VerifyGateResult:
    """The terminal verdict of the verify-gate (node ``workroom.verify_gate`` output, §3.7③).

    ``verified`` — True ONLY when the fresh critic AND the deterministic evidence gate BOTH pass
    (the ONLY path to 'verified'; the hard NOT-done is a claimed pass reaching this True without a
    real exit-0 receipt). ``verification`` — ``"verified"`` | ``"unverified"`` (the §1.2 field the
    envelope carries; ``verified``/``draft`` are NEVER status values). ``status`` — the mapped
    ``EnvelopeStatus`` (``done`` when verified, else ``needs_review`` / ``failed``). ``critic_all_met``
    — the fresh critic's all-green decision. ``evidence_backed`` — the deterministic gate's result.
    ``verdict`` — the re-validated critic verdict. ``receipts`` — the honest human-readable receipts
    (what the critic re-ran, what the evidence gate proved/couldn't prove — Law 2, spoken plainly).
    """

    verified: bool
    verification: str
    status: EnvelopeStatus
    critic_all_met: bool
    evidence_backed: bool
    verdict: Verdict
    receipts: tuple[str, ...] = field(default_factory=tuple)


# The critic prompt — the builder's success log is DELIBERATELY absent (anti-anchoring, §3.7①).
# It hands the critic the ASK + the named verify commands + the AC-tags to grade, and asks for a
# schema-constrained JSON verdict it RE-RUNS the evidence for (never trusts the builder's claim).
_CRITIC_PROMPT = (
    "You are an INDEPENDENT verifier in a FRESH context. A builder claims a task is done. Do NOT "
    "trust that claim. Judge, IN ORDER: (i) each acceptance-criterion (AC) tag actually met; "
    "(ii) the artifact actually RUNS / parses / typechecks — re-run the verify commands yourself "
    "via run_command, EVIDENCE not claim; (iii) load-bearing claims grounded in cited file:line; "
    "(iv) the work stayed in scope. Return ONLY a JSON object: "
    '{{"criteria":[{{"ac":<tag>,"met":<bool>,"evidence":<cited file:line / re-run result>}}], '
    '"runs":<bool>,"grounded":<bool>,"in_scope":<bool>}}.\n'
    "ASK: {ask}\n"
    "AC-TAGS TO GRADE: {ac_tags}\n"
    "VERIFY COMMANDS TO RE-RUN AS EVIDENCE: {verify_cmds}"
)


class VerifyGate:
    """The fresh-context critic + deterministic evidence gate + hard gate (§3.7 ①-③).

    Injectable seams so the REAL host path is proven against in-process fakes (e2b not installed;
    the live bake is the flagged residual):

      * ``verifier_provider`` — the ``agentkit.Provider`` for the ONE fresh-context critic query
        (defaults to the registry provider for the verifier seat's model). The critic is a NEW
        ``query()`` — the builder never grades itself.

    The gate takes the builder's CLAIM, the plan's named verify commands, and the HOST-observed
    receipts, and returns a :class:`VerifyGateResult` whose ``verification`` is ``"verified"`` ONLY
    when the critic AND the evidence gate both pass. It NEVER edits the artifact it grades and NEVER
    throws across the host boundary (Rule 6)."""

    def __init__(self, *, verifier_provider: Any = None) -> None:
        self._verifier_provider = verifier_provider

    # -- ③ the hard gate: the ONE public entry point --------------------------

    async def verify(
        self,
        *,
        bundle: Bundle,
        claimed_status: str,
        verify_cmds: list[str],
        receipts: list[dict[str, Any]],
        builder_success_log: str = "",
        ac_tags: list[str] | None = None,
        required_hashes: dict[str, str] | None = None,
        verifier_provider: Any = None,
        has_draft: bool = True,
    ) -> VerifyGateResult:
        """Grade a build in FRESH context + the deterministic receipt gate, then STAMP (§3.7 ①-③).

        1. ② the deterministic evidence gate reads the HOST-observed receipts: a claimed
           ``"passed"`` is real ONLY if a receipt shows the named verify command ran at
           ``exit_code == 0`` (+ any required hashes match). No matching receipt → force-FAIL.
        2. ① the SEPARATE fresh-context critic (a NEW ``query()``, builder's success log WITHHELD)
           re-runs the evidence and emits a schema-constrained verdict, re-validated here. An
           unparseable / uncertain verdict fails closed (every criterion FAILED).
        3. ③ the hard gate stamps ``verification="verified"`` ONLY when BOTH pass; else
           ``unverified`` + staged as a draft → ``needs_review`` (or ``failed`` on a claimed-pass
           that the evidence gate force-failed). The §1.2 mapping rides the ONE owner
           :func:`map_status_verification` — ``verified``/``draft`` are NEVER status values.

        The builder's ``claimed_status`` is model text — never trusted alone; it only decides
        whether the deterministic gate's force-fail reason applies (a claimed pass with no receipt
        is the hard NOT-done this force-fails). NEVER throws (Rule 6)."""
        receipt_lines: list[str] = []

        # ② the deterministic evidence gate — reads receipts, NEVER model prose.
        backed = evidence_backed(verify_cmds, receipts, required_hashes)
        claimed_pass = str(claimed_status).lower() in ("passed", "pass", "done", "green")
        force_failed = claimed_pass and not backed
        if force_failed:
            receipt_lines.append(
                "[verify gate] Downgraded to FAILED: no host-observed receipt (named verify "
                "command at exit_code 0 / matching artifact hash) backs this pass. Model prose "
                "is not a verdict."
            )
        elif backed:
            receipt_lines.append(
                f"[verify gate] evidence gate: host-observed exit-0 receipts back {len(verify_cmds)} "
                "named verify command(s)."
            )
        else:
            receipt_lines.append(
                "[verify gate] evidence gate: no host-observed exit-0 receipt for the named "
                "verify command(s) — unverified."
            )

        # ① the SEPARATE fresh-context critic — the builder's success log is WITHHELD.
        verdict = await self.run_critic(
            bundle=bundle,
            verify_cmds=verify_cmds,
            ac_tags=ac_tags,
            verifier_provider=verifier_provider,
        )
        critic_all_met = verdict.all_met
        if not verdict.parsed:
            receipt_lines.append(
                "[verify gate] critic: verdict unparseable/uncertain → fail-closed (every "
                "criterion defaults FAILED)."
            )
        else:
            failed = [c.ac for c in verdict.criteria if not c.met]
            if failed:
                receipt_lines.append(
                    f"[verify gate] critic caught a wrong claim: criteria FAILED {failed} — "
                    "downgraded to unverified."
                )
            else:
                receipt_lines.append(
                    "[verify gate] critic: every AC met, artifact runs, claims grounded, in scope."
                )

        # ③ the hard gate — 'verified' ONLY when the critic AND the evidence gate BOTH pass.
        verified = backed and critic_all_met
        status, verification = map_status_verification(
            is_build=True,
            verified=verified,
            has_draft=(has_draft and not force_failed),
            failed=force_failed,
        )
        return VerifyGateResult(
            verified=bool(verified),
            verification=str(verification),
            status=status,
            critic_all_met=critic_all_met,
            evidence_backed=backed,
            verdict=verdict,
            receipts=tuple(receipt_lines),
        )

    # -- ① the SEPARATE fresh-context critic query() --------------------------

    async def run_critic(
        self,
        *,
        bundle: Bundle,
        verify_cmds: list[str],
        ac_tags: list[str] | None = None,
        verifier_provider: Any = None,
    ) -> Verdict:
        """Run the ONE fresh-context critic (a NEW ``query()``) and re-validate its verdict (§3.7①).

        Read-only ``verifier`` disposition (§3.4/§3.5 triad + curated read/map/run_command; NO
        write/edit/ast_grep/propose_change — a verifier never edits what it grades). On a model at
        least as strong as the worker (§3.2), resolved through the IMPORTED seat table. The builder's
        own success log is NOT in the prompt (anti-anchoring). A provider fault (terminal ERROR) or a
        non-parsing emission fails CLOSED — a fail-closed :class:`Verdict` (``parsed=False``, every
        criterion FAILED), NEVER an uncaught exception (Rule 6) and NEVER a silent 'verified'."""
        options = self._build_verifier_options()
        provider = verifier_provider or self._verifier_provider
        if provider is None:
            provider = pick_provider(options.model)
        prompt = _CRITIC_PROMPT.format(
            ask=bundle.ask,
            ac_tags=", ".join(ac_tags or []) or "(the acceptance criteria the build served)",
            verify_cmds="; ".join(verify_cmds) or "(none named)",
        )
        text_parts: list[str] = []
        try:
            async for chunk in stream_deltas(provider.stream(prompt, options)):
                if chunk.type == "ERROR":
                    # A verifier fault fails closed — NEVER a silent 'verified' (Rule 6 / §3.7①).
                    _LOG.warning("verifier provider fault → fail-closed unverified verdict")
                    return Verdict()
                self._observe(chunk, text_parts)
        except ProviderError:
            _LOG.warning("verifier ProviderError → fail-closed unverified verdict")
            return Verdict()
        return Verdict.parse("".join(text_parts))

    @staticmethod
    def _observe(chunk: AgentChunk, text_parts: list[str]) -> None:
        """Fold one delta chunk's TEXT into the accumulated verdict text (INIT/RESULT carry only
        telemetry — the verdict rides the TEXT frames)."""
        if chunk.type == "TEXT" and chunk.text:
            text_parts.append(chunk.text)

    # -- the read-only verifier options: triad + curated tools + at-least-as-strong model --

    def _build_verifier_options(self) -> ProviderQuery:
        """The read-only ``verifier`` query options — triad + curated read/map/run_command tools,
        NO write/propose_change (a verifier never edits what it grades, §3.7), on a model at least
        as strong as the worker (§3.2). The tool policy + triad come from the ONE owner
        (``disposition_tool_policy``); the model from the IMPORTED seat table; the thinking decision
        from the shared ``thinking_policy`` (OFF on the verify path, D-022)."""
        policy = disposition_tool_policy(_VERIFIER_DISPOSITION)
        model = self._verifier_model()
        # The verify path runs thinking OFF (D-022); passed through as the shared policy returns it.
        enabled, budget = thinking_policy(model, disposition_role(_VERIFIER_DISPOSITION))
        return ProviderQuery(
            model=model,
            allowed_tools=tuple(policy.allowed_tools),
            system_prompt=guardrailed_system_prefix(),  # injection guardrail appended LAST (§3.10)
            max_turns=6,  # the critic RE-RUNS the evidence itself (a few run_command turns)
            tools=(),  # computed built-in allow-list: [] in sandbox mode (§3.4)
            strict_mcp_config=True,  # triad
            setting_sources=(),  # triad
            thinking_enabled=enabled,  # OFF on the verify path (D-022)
            thinking_budget_tokens=budget,
        )

    @staticmethod
    def _verifier_model() -> str:
        """Resolve the verifier's model via the IMPORTED seat table (§3.2 — no ``claude-*`` literal).

        The verifier is 'stronger than the worker' (§3.2): it rides the strongest seat
        (``BIG_BUILD`` → Opus-class), env-overridable per seat, so it is at least as strong as the
        Opus-class worker without a hard-coded id."""
        from llm.routing import model_for

        model: str = model_for(_VERIFIER_SEAT)
        return model


__all__ = [
    "Criterion",
    "Verdict",
    "VerifyGate",
    "VerifyGateResult",
    "evidence_backed",
]

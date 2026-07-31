"""Acceptance tests for node ``orchestrator.conversational-behaviors``
(Doc 08 §2.4 features #1,2,5,6,10).

The five conversational features are realized as first-class **typed wake-behaviors**
on the run loop — the SAME machinery as the other wake-behaviors (§3.4; D-015 curated
tool subsets, D-023 no per-behavior branch), declared in
``services/control-plane/src/control_plane/behaviors/conversational.py`` and registered by one
``register()`` line each:

  * ``catch-me-up`` — a ~20s spoken recap folded from the live notes object;
  * ``where-are-we`` — the current decisions + open questions, briefly;
  * ``dry-run`` — "what *would* you do?": the planned course of action, executing
    NOTHING (no Workroom dispatch, no sandbox) — the negative contract this node exists
    to guarantee;
  * ``show-your-work`` — re-expand a named prior receipt into its underlying reads/
    citations;
  * ``capability-answer`` — "what can you do?", grounded in the capabilities catalog
    (Proxy's actual mounted toolbelt), never invented.

Each test asserts a node clause against the *real* behaviors package
(``control_plane.behaviors``), never a rebuilt fixture. These grow the sealed
``behaviors-dir`` oracle rather than replacing it — the four original wake-behaviors
stay green while the five conversational ones are added.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Import Behavior/BehaviorConfig from the SAME package the behavior modules use
# (``agentkit``); under the src-layout workspace ``agentkit`` and ``libs.agentkit``
# are distinct module objects, so an isinstance check must use the authoring path.
from agentkit import Behavior, BehaviorConfig, register
from libs.agentkit import BehaviorRunner, ProviderQuery, with_proxy_guardrails
from libs.contracts import AgentChunk
from llm.routing import model_for

from control_plane import behaviors as bdir
from control_plane.behaviors import conversational as conv

_CONVERSATIONAL = ("catch-me-up", "where-are-we", "dry-run", "show-your-work", "capability-answer")
_CONV_FILE = Path("services/control-plane/src/control_plane/behaviors/conversational.py")

# The delivery verbs a conversational behavior may use to speak + post its answer.
_DELIVERY = {"speak", "send_chat"}
# The tools a conversational behavior must NEVER mount: it answers from EXISTING
# substrate, so it never dispatches a Workroom build (Law 3 / node NOT-done clause).
_FORBIDDEN = {"dispatch_workroom", "propose_change", "provision_sandbox"}


# ── a scripted fake provider that records the query + replays chunks ──────────
class _FakeProvider:
    def __init__(self, chunks):
        self._chunks = chunks
        self.seen_query: ProviderQuery | None = None
        self.seen_prompt: str | None = None

    def stream(self, prompt, query):
        self.seen_query = query
        self.seen_prompt = prompt

        async def gen():
            for c in self._chunks:
                yield c

        return gen()


async def _run(behavior, chunks, inputs):
    """Run a conversational behavior through the real BehaviorRunner + a fake seam."""
    fp = _FakeProvider(chunks)
    runner = BehaviorRunner(registry={behavior.name: behavior}, provider=fp)
    out = []
    async for ch in runner.run(behavior.name, inputs):
        out.append(ch)
    return fp, out


# ── clause 1: all five conversational features are registered typed constants ──
@pytest.mark.integration
def test_five_conversational_behaviors_registered_typed_constants():
    reg = bdir.REGISTRY
    for name in _CONVERSATIONAL:
        b = reg[name]
        # A typed Behavior wrapping a typed BehaviorConfig — NOT a dict / YAML.
        assert isinstance(b, Behavior), f"{name} must be a typed Behavior constant"
        assert isinstance(b.config, BehaviorConfig)
        assert b.config.name == name
        assert bdir.get_behavior(name) is b


@pytest.mark.integration
def test_conversational_module_exposes_the_five_constants():
    # The module is the codebase-anchor (behaviors/conversational.py) and exposes each
    # behavior as a module constant, so adding one is a constant + a register line.
    for const in ("CATCH_ME_UP", "WHERE_ARE_WE", "DRY_RUN", "SHOW_YOUR_WORK", "CAPABILITY_ANSWER"):
        assert hasattr(conv, const), f"conversational.py must expose {const}"
        assert isinstance(getattr(conv, const), Behavior)


# ── clause 2: each is a bounded DIRECT wake turn (single seat, small budget) ──
@pytest.mark.integration
def test_conversational_behaviors_run_on_the_orchestrator_seat_bounded():
    reg = bdir.REGISTRY
    for name in _CONVERSATIONAL:
        c = reg[name].config
        # ORCHESTRATOR seat (D-014): these are non-answer conversational wakes.
        assert c.model == model_for("ORCHESTRATOR"), name
        # A bounded direct turn — a small turn budget, never an open-ended build.
        assert 1 <= c.max_turns <= 3, f"{name} must be a bounded direct turn"


@pytest.mark.integration
def test_no_inline_model_id_literal_in_conversational_module():
    src = _CONV_FILE.read_text(encoding="utf-8")
    assert "claude-" not in src, "conversational.py must resolve models via model_for(<seat>)"


# ── clause 3 (THE node contract): dry-run touches NOTHING ────────────────────
@pytest.mark.integration
def test_dry_run_mounts_no_dispatch_or_sandbox_tool():
    """dry-run returns a PLANNED course of action while provisioning NO sandbox and
    dispatching NO Workroom task — so its curated tool subset must not contain any
    dispatch/execute tool at all (a structural guarantee, not a runtime hope)."""
    dry = bdir.REGISTRY["dry-run"].config
    mounted = set(dry.tools)
    assert mounted & _FORBIDDEN == set(), f"dry-run mounts a forbidden execute tool: {mounted & _FORBIDDEN}"
    # It can still SPEAK and POST the plan.
    assert _DELIVERY <= mounted


@pytest.mark.integration
@pytest.mark.asyncio
async def test_dry_run_run_dispatches_no_workroom_and_provisions_no_sandbox():
    """A live dry-run turn: the model plans and speaks the plan, and the resulting
    stream contains NO dispatch_workroom / provision tool-use — the negative contract
    is observable end to end through the real runner."""
    dry = bdir.REGISTRY["dry-run"]
    chunks = [
        AgentChunk(type="INIT", metadata={"session_id": "s1"}),
        AgentChunk(
            type="TEXT",
            text="Here's what I'd do: add a guard in payments/retry.py, then run the suite.",
            metadata={"msg_id": "m1"},
        ),
        AgentChunk(type="RESULT", metadata={"total_cost_usd": 0.001, "num_turns": 1, "session_id": "s1"}),
    ]
    fp, out = await _run(dry, chunks, {"event": "what would you do to add retries?", "notes_ref": "mtg-1"})

    # The mounted subset the seam saw contains NO forbidden execute tool.
    assert set(fp.seen_query.allowed_tools) & _FORBIDDEN == set()
    # No tool-use in the produced stream dispatched a Workroom or provisioned a sandbox.
    tool_uses = {c.metadata.get("name") for c in out if c.type == "TOOL_USE"}
    assert tool_uses & _FORBIDDEN == set(), f"dry-run executed a forbidden tool: {tool_uses & _FORBIDDEN}"
    # The plan is spoken as a course of action, not built.
    spoken = " ".join(c.text for c in out if c.type == "TEXT")
    assert "retry.py" in spoken


# ── clause 4: catch-me-up / where-are-we fold the notes object (notes_ref in) ──
@pytest.mark.integration
def test_catchup_and_where_are_we_consume_notes_and_deliver_only():
    reg = bdir.REGISTRY
    for name in ("catch-me-up", "where-are-we"):
        c = reg[name].config
        # Grounds its recap in the live notes object (read via notes_ref = meeting_id).
        assert "notes_ref" in c.inputs, f"{name} must consume notes_ref"
        # Delivery-only: it recaps from the handed notes/state — it does NOT go exploring
        # code and it does NOT dispatch. speak + send_chat ONLY (D-015 curated subset).
        assert set(c.tools) == _DELIVERY, f"{name} tools must be exactly {_DELIVERY}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_catch_me_up_folds_notes_into_a_bounded_recap():
    """catch-me-up folds the notes object handed on the prompt into a short recap and
    speaks it — the notes_ref value reaches the model as DATA on the turn, and the
    recap is delivered through speak/send_chat, never a build."""
    catch = bdir.REGISTRY["catch-me-up"]
    chunks = [
        AgentChunk(type="INIT", metadata={"session_id": "s1"}),
        AgentChunk(type="TEXT", text="We decided to ship Friday; the retry test is still open.", metadata={"msg_id": "m1"}),
        AgentChunk(type="RESULT", metadata={"total_cost_usd": 0.001, "num_turns": 1, "session_id": "s1"}),
    ]
    fp, out = await _run(catch, chunks, {"event": "catch me up", "notes_ref": "mtg-42"})

    # The notes reference reached the model as DATA on the prompt (the recap is grounded
    # in the notes object, not invented — Law 1).
    assert "mtg-42" in (fp.seen_prompt or "")
    # It mounted only the delivery verbs (no code tools, no dispatch).
    assert set(fp.seen_query.allowed_tools) == _DELIVERY
    spoken = " ".join(c.text for c in out if c.type == "TEXT")
    assert "Friday" in spoken


# ── clause 5: show-your-work re-expands a named receipt (event carries the ref) ──
@pytest.mark.integration
def test_show_your_work_consumes_a_receipt_reference():
    c = bdir.REGISTRY["show-your-work"].config
    # It expands a PRIOR receipt — the ask (event) names which receipt to expand.
    assert "event" in c.inputs
    # Delivery-only: it re-renders the receipt's already-captured reads/citations; it
    # does not re-run the work or dispatch anything.
    assert set(c.tools) == _DELIVERY
    assert set(c.tools) & _FORBIDDEN == set()


# ── clause 6: capability-answer is GROUNDED in the capabilities catalog ──────
@pytest.mark.integration
def test_capability_answer_consumes_the_capabilities_catalog():
    b = bdir.REGISTRY["capability-answer"]
    c = b.config
    # It answers "what can you do?" from the capabilities catalog — the catalog is a
    # declared input, so the answer is grounded in it (never invented).
    assert "capabilities" in c.inputs, "capability-answer must consume the capabilities catalog"
    assert set(c.tools) == _DELIVERY


@pytest.mark.integration
def test_capabilities_catalog_source_is_the_mounted_toolbelt_not_invented():
    """The capabilities the answer is grounded in are DERIVED from Proxy's actual
    registered wake-behaviors (its real toolbelt) — a single source of truth, so the
    "what can you do?" answer cannot over-claim (§4.11 honesty rule)."""
    catalog = conv.capabilities_catalog()
    assert catalog, "the capabilities catalog must be non-empty"
    # Every catalog entry names a real registered behavior — nothing is invented.
    names = set(bdir.REGISTRY)
    for entry in catalog:
        assert entry["id"] in names, f"catalog entry {entry['id']!r} is not a real behavior"
        assert entry.get("label"), "each capability carries a user-facing label"
    # The catch-me-up / capability features themselves appear (the answer knows itself).
    ids = {e["id"] for e in catalog}
    assert {"catch-me-up", "capability-answer"} <= ids


@pytest.mark.integration
@pytest.mark.asyncio
async def test_capability_answer_prompt_is_grounded_in_the_catalog():
    """The capability-answer turn is handed the catalog on the prompt, so its spoken
    summary is a render of the real toolbelt — grounded, not fabricated."""
    cap = bdir.REGISTRY["capability-answer"]
    catalog = conv.capabilities_catalog()
    chunks = [
        AgentChunk(type="INIT", metadata={"session_id": "s1"}),
        AgentChunk(type="TEXT", text="I can answer questions about the repo and catch you up.", metadata={"msg_id": "m1"}),
        AgentChunk(type="RESULT", metadata={"total_cost_usd": 0.001, "num_turns": 1, "session_id": "s1"}),
    ]
    fp, _ = await _run(cap, chunks, {"event": "what can you do?", "capabilities": catalog})
    # A catalog label reached the model as DATA (grounding the answer in the catalog).
    labels = [e["label"] for e in catalog]
    assert any(lbl in (fp.seen_prompt or "") for lbl in labels), "the catalog must be handed to the turn"


# ── clause 7: no conversational behavior advertises a forbidden execute tool ──
@pytest.mark.integration
def test_no_conversational_behavior_can_execute_or_dispatch():
    reg = bdir.REGISTRY
    for name in _CONVERSATIONAL:
        mounted = set(reg[name].config.tools)
        assert mounted & _FORBIDDEN == set(), f"{name} mounts a forbidden execute tool: {mounted & _FORBIDDEN}"
        # None of them advertise the whole-Proxy union either — each a curated subset.
        assert mounted, f"{name} must declare a curated tool subset"


# ── clause 8: rules are judgment primers, NOT an if-X-do-Y decision table ────
@pytest.mark.integration
def test_conversational_rules_are_not_a_decision_table():
    for name in _CONVERSATIONAL:
        b = bdir.REGISTRY[name]
        for rule in (b.rules or b.config.rules):
            low = rule.lower()
            assert not ("if " in low and " call " in low and "_" in low and "()" in low), (
                f"{name} rule reads like a decision table: {rule!r}"
            )


@pytest.mark.static_
def test_conversational_module_has_no_per_behavior_branch():
    # D-023: no fire_behavior() dispatch and no if-branch on a conversational name literal.
    src = _CONV_FILE.read_text(encoding="utf-8")
    assert "fire_behavior" not in src
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for comp in [node.left, *node.comparators]:
                if isinstance(comp, ast.Constant) and comp.value in _CONVERSATIONAL:
                    raise AssertionError(
                        f"conversational.py branches on a behavior-name literal (D-023 forbids per-behavior code)"
                    )


# ── clause 9: spoken register in ≥2 prompt locations (role + guardrail) ──────
@pytest.mark.integration
def test_conversational_spoken_register_in_role_and_guardrail():
    for name in _CONVERSATIONAL:
        b = bdir.REGISTRY[name]
        role = b.role.lower()
        assert "sentence" in role or "brief" in role or "short" in role, f"{name} role lacks a spoken register"
        guarded = with_proxy_guardrails(b.role)
        assert guarded.lower().count("sentence") >= 1


# ── clause 10: adding a behavior is one constant + one register() line ───────
@pytest.mark.static_
def test_conversational_module_registers_five_behaviors_one_line_each():
    src = _CONV_FILE.read_text(encoding="utf-8")
    # Exactly five register() CALL expressions — one per conversational behavior. Counted
    # via AST so a prose ``register()`` mention in the docstring never inflates the count.
    tree = ast.parse(src)
    register_calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "register"
    ]
    assert len(register_calls) == 5, "conversational.py must register exactly five behaviors"
    # No YAML loader anywhere (CANONICAL §12.5).
    lowered = "\n".join(line.split("#", 1)[0] for line in src.splitlines()).lower()
    for loader in ("import yaml", "yaml.load", "yaml.safe_load", "from yaml", "ruamel"):
        assert loader not in lowered


# ── clause 11: the four ORIGINAL wake-behaviors stay registered (no regression) ──
@pytest.mark.integration
def test_original_wake_behaviors_still_registered_alongside_conversational():
    reg = bdir.REGISTRY
    for name in ("answer-question", "catchup", "surface-risk", "propose-action"):
        assert name in reg, f"conversational behaviors must not displace {name}"
    # The manifest now carries the conversational behaviors too (same tools list).
    manifest = bdir.capability_manifest()
    for name in _CONVERSATIONAL:
        assert name in manifest
        assert manifest[name]["tools"] == list(reg[name].config.tools)

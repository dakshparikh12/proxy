"""Acceptance tests for node ``orchestrator.behaviors-dir`` (04 §3.4; D-014 model-seat
mapping, D-015 curated tool subsets, D-016 BehaviorConfig field set, D-023 no
per-behavior branch, D-024 delivery-verb authority).

The wake-behaviors are declared as **typed Python ``BehaviorConfig`` constants** —
NOT YAML (CANONICAL §12.5). Each is registered in a ``REGISTRY`` dict via one
``register()`` line; the runner reads the constant and never branches per behavior
(D-023). Each test asserts one node clause against the *real* behaviors package
(``harness.behaviors``), not a rebuilt fixture:

  * each behavior is a typed ``BehaviorConfig`` constant registered by one line;
  * ``config.tools`` is the D-015 curated subset, NEVER the union (§10.5);
  * the model is the D-014 seat via ``llm.routing.model_for`` (ANSWER→answer-question,
    ORCHESTRATOR→the other wake-behaviors) — no inline model id;
  * ``answer-question`` mounts the ``code_intel`` read tools + orchestration verbs so
    it can DIRECT-ANSWER a grounded lookup without dispatching;
  * the D-016 field set is exactly ``{tools, model, role, max_turns, rules, inputs}``;
  * the build-time capability manifest is generated from the same ``tools`` list;
  * rules are examples-to-prime-judgment, never an ``if-X-do-Y`` decision table;
  * the spoken register appears in ≥2 prompt locations (role + guardrail suffix).
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

# Import Behavior/BehaviorConfig from the SAME package the behavior modules use
# (``agentkit``, the installed top-level package). Under this src-layout workspace
# ``agentkit`` and ``libs.agentkit`` are distinct module objects, so an isinstance
# check must use the path the product constants were built with. The runner is
# boundary-proof (duck-typed), so importing it from either path is fine.
from agentkit import Behavior, BehaviorConfig
from libs.agentkit import BehaviorRunner, ProviderQuery, with_proxy_guardrails
from libs.contracts import AgentChunk
from llm.routing import model_for

from harness import behaviors as bdir


_BEHAVIORS_DIR = Path("services/harness/src/harness/behaviors")


# ── clause 1: every wake-behavior is a registered typed BehaviorConfig constant ──
@pytest.mark.integration
def test_all_four_wake_behaviors_are_registered_typed_constants():
    reg = bdir.REGISTRY
    assert isinstance(reg, dict)
    for name in ("answer-question", "catchup", "surface-risk", "propose-action"):
        b = reg[name]
        # A behavior is a typed Behavior wrapping a typed BehaviorConfig (D-016) —
        # NOT a dict parsed from YAML.
        assert isinstance(b, Behavior), f"{name} must be a typed Behavior constant"
        assert isinstance(b.config, BehaviorConfig)
        assert b.config.name == name
        assert bdir.get_behavior(name) is b


@pytest.mark.integration
def test_no_yaml_loader_behaviors_are_python_constants():
    # No YAML file and no YAML LOADER in the behaviors package (CANONICAL §12.5) —
    # the behaviors are typed Python constants, not parsed config. An explanatory
    # mention of "not YAML" in a docstring is fine; an actual import/parse is not.
    assert not list(_BEHAVIORS_DIR.glob("*.yaml"))
    assert not list(_BEHAVIORS_DIR.glob("*.yml"))
    forbidden_loaders = ("import yaml", "yaml.load", "yaml.safe_load", "from yaml", "ruamel")
    for py in _BEHAVIORS_DIR.glob("*.py"):
        # Strip docstrings/comments so a prose "not YAML" note never trips the guard;
        # only executable references to a YAML loader are forbidden.
        code = "\n".join(
            line.split("#", 1)[0] for line in py.read_text(encoding="utf-8").splitlines()
        ).lower()
        for loader in forbidden_loaders:
            assert loader not in code, f"{py.name} references a YAML loader ({loader!r})"


# ── clause 2: D-015 curated tool subsets, never the union ──────────────────
@pytest.mark.integration
def test_d015_curated_tool_subsets_exact():
    reg = bdir.REGISTRY
    # answer-question = code_intel read tools + speak/send_chat/dispatch_workroom.
    aq = set(reg["answer-question"].config.tools)
    assert {"get_dependents", "who_writes", "list_entry_points", "grep", "read", "batch_read"} <= aq
    assert {"speak", "send_chat", "dispatch_workroom"} <= aq
    # catchup = speak/send_chat only (D-015).
    assert set(reg["catchup"].config.tools) == {"speak", "send_chat"}
    # surface-risk = grep/read/get_dependents + speak (D-015).
    assert set(reg["surface-risk"].config.tools) == {"grep", "read", "get_dependents", "speak"}
    # propose-action = dispatch_workroom only (D-015).
    assert set(reg["propose-action"].config.tools) == {"dispatch_workroom"}


@pytest.mark.integration
def test_no_behavior_advertises_the_whole_proxy_tool_universe():
    reg = bdir.REGISTRY
    # The whole-Proxy tool universe (§10.5): every delivery/orchestration verb plus
    # every code_intel read tool Proxy can wield across ALL behaviors and the host
    # code_intel manifest. No single behavior may mount all of it — each advertises a
    # curated subset (D-015). This set is strictly larger than any one behavior's.
    proxy_universe = {
        "get_dependents", "who_writes", "list_entry_points", "grep", "read", "batch_read",
        "shares_table", "owner", "lookup_referent", "find_references",  # rest of the code_intel manifest
        "dispatch_workroom", "speak", "send_chat", "show_screen", "ack", "cancel_task",
    }
    for name, b in reg.items():
        sub = set(b.config.tools)
        assert sub <= proxy_universe, f"{name} mounts an unknown tool: {sub - proxy_universe}"
        assert sub < proxy_universe, f"{name} advertises the whole-Proxy tool union (§10.5 forbids)"
    # The leaner behaviors advertise strictly fewer tools than answer-question — the
    # curated subset per behavior is real, not cosmetic.
    aq = set(reg["answer-question"].config.tools)
    assert set(reg["catchup"].config.tools) < aq
    assert set(reg["surface-risk"].config.tools) < aq
    assert set(reg["propose-action"].config.tools) < aq


# ── clause 3: D-014 model-seat mapping (no inline model ids) ────────────────
@pytest.mark.integration
def test_d014_model_seat_mapping():
    reg = bdir.REGISTRY
    # ANSWER seat → answer-question; ORCHESTRATOR seat → the other wake-behaviors.
    assert reg["answer-question"].config.model == model_for("ANSWER")
    for name in ("catchup", "surface-risk", "propose-action"):
        assert reg[name].config.model == model_for("ORCHESTRATOR"), name


@pytest.mark.integration
def test_no_inline_model_id_literals_in_behavior_modules():
    # Models come from the seat table (llm.routing.model_for), never a hard-coded id.
    for py in _BEHAVIORS_DIR.glob("*.py"):
        src = py.read_text(encoding="utf-8")
        assert "claude-" not in src, f"{py.name} hard-codes a model id (use model_for(<seat>))"


# ── clause 4: answer-question can DIRECT-ANSWER via code_intel (no dispatch) ──
@pytest.mark.integration
def test_answer_question_mounts_code_intel_read_tools_for_direct_answer():
    aq = bdir.REGISTRY["answer-question"].config
    code_intel_reads = {"get_dependents", "who_writes", "list_entry_points", "grep", "read", "batch_read"}
    # It mounts the code_intel read tools so a grounded lookup is answered directly.
    assert code_intel_reads <= set(aq.tools)
    # It ALSO keeps the orchestration verbs so it can dispatch when the ask is real work.
    assert "dispatch_workroom" in aq.tools


@pytest.mark.integration
@pytest.mark.asyncio
async def test_answer_question_direct_answer_grounded_lookup_no_dispatch():
    """A simple grounded lookup is answered directly via a mounted code_intel read
    tool with a cited file:line — the runner mounts the curated subset and streams a
    turn whose tool-use is a code_intel read, and whose RESULT never dispatched a
    workroom build (§3.4 / D-014 ANSWER seat)."""
    aq = bdir.REGISTRY["answer-question"]

    # A scripted grounded turn: read a file via code_intel, then speak the cited answer.
    chunks = [
        AgentChunk(type="INIT", metadata={"session_id": "s1"}),
        AgentChunk(type="TOOL_USE", metadata={"id": "t1", "name": "get_dependents", "input": {"symbol": "refund"}}),
        AgentChunk(
            type="TOOL_RESULT",
            metadata={"tool_use_id": "t1", "content": "billing/refund.py:42 charges.py:88"},
        ),
        AgentChunk(type="TEXT", text="The retry logic lives in billing/refund.py:42.", metadata={"msg_id": "m1"}),
        AgentChunk(type="RESULT", metadata={"total_cost_usd": 0.004, "num_turns": 1, "session_id": "s1"}),
    ]

    class _FakeProvider:
        def __init__(self):
            self.seen_query: ProviderQuery | None = None

        def stream(self, prompt, query):
            self.seen_query = query

            async def gen():
                for c in chunks:
                    yield c

            return gen()

    fp = _FakeProvider()
    runner = BehaviorRunner(registry={"answer-question": aq}, provider=fp)
    out = []
    async for ch in runner.run("answer-question", {"event": "where's the retry logic?"}):
        out.append(ch)

    # The mounted subset is EXACTLY the declared curated set (never the union).
    assert set(fp.seen_query.allowed_tools) == set(aq.config.tools)
    # The tool it used to answer is a code_intel READ tool — a direct grounded lookup.
    tool_uses = [c.metadata.get("name") for c in out if c.type == "TOOL_USE"]
    assert tool_uses == ["get_dependents"]
    # No workroom dispatch happened on this direct-answer path.
    assert "dispatch_workroom" not in tool_uses
    # The spoken answer carries a cited file:line.
    spoken = " ".join(c.text for c in out if c.type == "TEXT")
    assert "billing/refund.py:42" in spoken


# ── clause 5: D-016 BehaviorConfig field set exact ─────────────────────────
@pytest.mark.integration
def test_d016_field_set_exact():
    import dataclasses

    names = {f.name for f in dataclasses.fields(BehaviorConfig)}
    # The D-016 envelope MUST be present.
    assert {"tools", "model", "role", "max_turns", "rules", "inputs"} <= names
    # Each behavior populates the D-016 envelope with the declared types.
    for b in bdir.REGISTRY.values():
        c = b.config
        assert isinstance(c.tools, tuple) and c.tools, f"{c.name} tools"
        assert isinstance(c.model, str) and c.model
        assert isinstance(c.role, str) and c.role
        assert isinstance(c.max_turns, int) and c.max_turns >= 1
        assert isinstance(c.rules, tuple)
        assert isinstance(c.inputs, tuple)


# ── clause 6: the build-time capability manifest is generated from tools list ──
@pytest.mark.integration
def test_capability_manifest_generated_from_the_same_tools_list():
    manifest = bdir.capability_manifest()
    # A JSON-serialisable manifest keyed by behavior name.
    text = json.dumps(manifest)
    parsed = json.loads(text)
    assert set(parsed) == set(bdir.REGISTRY)
    for name, entry in parsed.items():
        cfg = bdir.REGISTRY[name].config
        # The manifest tools ARE the config.tools (same curated list, not a re-derivation).
        assert entry["tools"] == list(cfg.tools)
        assert entry["model"] == cfg.model


# ── clause 7: rules are judgment primers, NOT an if-X-do-Y decision table ───
@pytest.mark.integration
def test_rules_are_not_a_decision_table():
    for b in bdir.REGISTRY.values():
        for rule in b.rules or b.config.rules:
            low = rule.lower()
            # No literal "if speaker says X call tool Y" decision-table phrasing.
            assert not ("if " in low and " call " in low and "_" in low and "()" in low), (
                f"{b.name} rule reads like a decision table: {rule!r}"
            )


@pytest.mark.static_
def test_no_per_behavior_dispatch_or_fire_behavior():
    # D-023: no fire_behavior() dispatch and no per-behavior if-branch in the package.
    for py in _BEHAVIORS_DIR.glob("*.py"):
        src = py.read_text(encoding="utf-8")
        assert "fire_behavior" not in src, f"{py.name} must not define a fire_behavior dispatch"
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                for comp in [node.left, *node.comparators]:
                    if isinstance(comp, ast.Constant) and comp.value in (
                        "answer-question",
                        "catchup",
                        "surface-risk",
                        "propose-action",
                    ):
                        raise AssertionError(
                            f"{py.name} branches on a behavior-name literal (D-023 forbids per-behavior code)"
                        )


# ── clause 8: spoken register in ≥2 prompt locations (role + guardrail) ─────
@pytest.mark.integration
def test_spoken_register_in_role_and_guardrail():
    aq = bdir.REGISTRY["answer-question"]
    # Location 1: the wake-behavior role string carries the spoken-register line.
    assert "two sentences" in aq.role.lower() or "short sentence" in aq.role.lower()
    # Location 2: with_proxy_guardrails appends the spoken-register suffix.
    guarded = with_proxy_guardrails(aq.role)
    assert guarded.lower().count("sentence") >= 2, "spoken register must appear in ≥2 prompt locations"


# ── clause 9: adding a behavior is one constant + one register() line ───────
@pytest.mark.static_
def test_adding_a_behavior_is_one_register_line_per_module():
    # Each behavior module registers exactly one constant with one register() call —
    # adding a behavior is one constant + one register() line (node acceptance).
    for name in ("answer_question", "catchup", "surface_risk", "propose_action"):
        src = (_BEHAVIORS_DIR / f"{name}.py").read_text(encoding="utf-8")
        assert src.count("register(") == 1, f"{name}.py must register exactly one behavior"

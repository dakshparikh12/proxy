"""J-09-contracts-resolve — the seam-spine integrity check (Doc 09 §2, second bullet).

Proves the cross-doc contract seam closes two ways, so all six services keep
speaking ONE vocabulary and never re-drift:

1. **Single declaration site** — an AST sweep of every ``services/*/src`` and
   ``libs/*/src`` proves NO module re-declares a shared wire shape
   (``Bundle`` / ``Envelope`` / ``AgentChunk`` / ``NoteOp`` / ``Readiness`` /
   ``ChannelReport``) locally: not as a ``class`` (the exact-name shadow), not as a
   type-alias assignment (the ``Literal`` shadow for ``NoteOp`` / ``Readiness``),
   and not as a *duck-typed* shadow class whose field-set equals a canonical
   Pydantic model's (the risk a naive import-grep misses). ``libs/contracts`` is the
   only declaration site; everyone else imports the literal type.

2. **Empty field-level produce/consume diff** — the check does NOT merely confirm
   type names exist; it runs the real §4.8 / CANONICAL §11.11 field-diff. It walks
   each contract model's *real* Pydantic fields and flags any field **produced by
   the model but read by no consumer**, OR **read under a name the model never
   produces** — the drift class this project already paid for
   (``AgentChunk.kind``→``.type``, envelope ``verified|draft``→``EnvelopeStatus``,
   ``dm``→``dm_available``). The diff must be EMPTY over the full live consumer
   surface (every service that reads a contract imported first, so a consumer whose
   read is dropped shows up as a produced-but-unconsumed orphan).

If a real local re-declaration existed, the fix is to import the canonical type from
``libs.contracts`` — never to relax this test.
"""
from __future__ import annotations

import ast
import importlib
import pathlib

import pytest

# The six shared wire shapes the node names (build/chain.json journey.contracts-resolve
# `consumes`). Class-declared: Bundle, Envelope, AgentChunk, ChannelReport. Literal
# type-aliases: NoteOp, Readiness. Both forms are swept for local shadows.
SHARED_TYPES: frozenset[str] = frozenset(
    {"Bundle", "Envelope", "AgentChunk", "NoteOp", "Readiness", "ChannelReport"}
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_CONTRACTS_SRC = (_REPO_ROOT / "libs" / "contracts" / "src").resolve()

# Every live module that CONSUMES a contract type — imported before the field-diff so
# each consumer's field-reads (registered at import via register_field_consumer, whose
# canonical record lives beside each model) are present. A dropped read then surfaces as
# a produced-but-unconsumed orphan rather than hiding behind an un-imported consumer.
_CONSUMER_MODULES: tuple[str, ...] = (
    "control_plane.provider",
    "control_plane.dispatch",
    "control_plane.orchestrator",
    "control_plane.wake_turn",
    "workroom.verify_gate",
    "workroom.big_build",
    "workroom.session",
    "workroom.envelope",
    "transport.surface",
    "transport.recall",
    "transport.seams",
    "transport.signals",
    "transport.chat",
    "transport.carrier",
    "agentkit.provider",
    "agentkit.deltas",
    "agentkit.resume",
    "agentkit.execution",
)


def _sweep_roots() -> list[pathlib.Path]:
    """Every ``services/*/src`` and ``libs/*/src`` tree, EXCLUDING libs/contracts (the home)."""
    roots: list[pathlib.Path] = []
    for base in ("services", "libs"):
        for src in sorted((_REPO_ROOT / base).glob("*/src")):
            if src.resolve() == _CONTRACTS_SRC:
                continue  # the single declaration site — its declarations are canonical
            roots.append(src)
    return roots


def _canonical_field_sets() -> dict[str, frozenset[str]]:
    """The real Pydantic field-sets of the class-declared shared models (for shadow detection)."""
    from contracts.bundle import Bundle
    from contracts.channels import ChannelReport
    from contracts.chunks import AgentChunk
    from contracts.envelopes import Envelope

    return {
        model.__name__: frozenset(model.model_fields)
        for model in (Bundle, Envelope, AgentChunk, ChannelReport)
    }


def _iter_python_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for root in _sweep_roots():
        files.extend(sorted(root.rglob("*.py")))
    return files


def _class_field_names(node: ast.ClassDef) -> set[str]:
    """The annotated field names a class body declares (Pydantic-model shape)."""
    fields: set[str] = set()
    for stmt in node.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            fields.add(stmt.target.id)
    return fields


def test_no_local_redeclaration_of_shared_contract_types() -> None:
    """AST sweep: no ``services/*/src`` or ``libs/*/src`` module re-declares a shared type.

    Catches three shadow forms:
      * a ``class`` re-using a shared name (exact-name class shadow);
      * a module-level assignment/annotation re-binding a shared name (the ``NoteOp`` /
        ``Readiness`` ``Literal``-alias shadow);
      * a *duck-typed* class whose annotated field-set equals a canonical model's
        (a differently-named shadow a naive import-grep would miss).
    """
    canonical_fields = _canonical_field_sets()
    violations: list[str] = []

    for pyfile in _iter_python_files():
        rel = pyfile.relative_to(_REPO_ROOT)
        try:
            tree = ast.parse(pyfile.read_text(encoding="utf-8"), filename=str(rel))
        except SyntaxError as exc:  # a real syntax error is itself a build failure
            violations.append(f"{rel}: unparseable ({exc})")
            continue

        for node in ast.walk(tree):
            # (a) exact-name class shadow.
            if isinstance(node, ast.ClassDef) and node.name in SHARED_TYPES:
                violations.append(
                    f"{rel}:{node.lineno}: local class re-declares shared contract "
                    f"type {node.name!r} — import it from libs.contracts instead"
                )
            # (b) type-alias / annotation shadow (NoteOp, Readiness are Literal aliases).
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id in SHARED_TYPES:
                        violations.append(
                            f"{rel}:{node.lineno}: local assignment re-declares shared "
                            f"contract type {tgt.id!r} — import it from libs.contracts instead"
                        )
            if (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id in SHARED_TYPES
                and node.value is not None
            ):
                violations.append(
                    f"{rel}:{node.lineno}: local annotated assignment re-declares shared "
                    f"contract type {node.target.id!r} — import it from libs.contracts instead"
                )
            # (c) duck-typed shadow: a differently-named class with a canonical field-set.
            if isinstance(node, ast.ClassDef) and node.name not in SHARED_TYPES:
                fields = _class_field_names(node)
                for model_name, canon in canonical_fields.items():
                    if fields and fields == set(canon):
                        violations.append(
                            f"{rel}:{node.lineno}: class {node.name!r} duck-types shared "
                            f"contract {model_name!r} (identical field-set {sorted(canon)}) "
                            "— import the canonical type from libs.contracts instead"
                        )

    assert not violations, "local re-declaration of shared contract types:\n  " + "\n  ".join(
        violations
    )


def test_shared_types_import_from_libs_contracts() -> None:
    """The six shared types are the SAME objects across both import aliases and the sweep.

    ``services`` reach the package as ``contracts`` and ``libs.contracts`` (two path
    aliases onto the one src-layout package); both must yield the identical object, so a
    consumer importing either genuinely resolves to the single declaration site.
    """
    import contracts as via_contracts
    import libs.contracts as via_libs  # noqa: F401 — same package, verify identity below

    for name in SHARED_TYPES:
        a = getattr(via_contracts, name)
        b = getattr(via_libs, name)
        assert a is b, f"{name}: contracts.{name} is not libs.contracts.{name} (aliased split)"


def test_field_level_produce_consume_diff_is_empty() -> None:
    """The real §4.8 / CANONICAL §11.11 field-diff is EMPTY over the full consumer surface.

    Not a name check: this walks each contract model's real Pydantic fields and the live
    consumer field-reads, and asserts neither a produced-but-unconsumed field nor a
    consumed-but-never-produced field exists. Every consuming service module is imported
    first so a dropped read cannot hide behind an un-imported consumer.
    """
    import contracts  # noqa: F401 — fires the model-side consumer registrations

    unimportable: list[str] = []
    for mod in _CONSUMER_MODULES:
        try:
            importlib.import_module(mod)
        except Exception as exc:  # a consumer that won't import is itself a seam failure
            unimportable.append(f"{mod}: {type(exc).__name__}: {exc}")
    assert not unimportable, "contract consumer modules failed to import:\n  " + "\n  ".join(
        unimportable
    )

    from contracts import assert_contract_fields_consumed, assert_registry_closed

    # The client contract graph must be closed (set-equality + handler/projector coverage
    # + no signal-surface leak) before the field-diff is meaningful.
    assert_registry_closed()

    violations = assert_contract_fields_consumed()
    assert violations == [], (
        "field-level produce/consume diff is NOT empty (a field is produced by one side "
        "and consumed by neither) — every orphan below is real drift:\n  "
        + "\n  ".join(violations)
    )


@pytest.mark.parametrize("model_name", ["AgentChunk", "Envelope", "ChannelReport"])
def test_field_diff_has_teeth_on_a_dropped_consumer(model_name: str) -> None:
    """Sanity: the field-diff FLAGS a produced field whose consumer read is removed.

    Proves the empty result above is a real closure, not a vacuous pass — if a live
    consumer stopped reading a produced field, the diff would name it. We inject a
    produced-but-unconsumed field into a COPY of the live maps and assert it surfaces;
    the live registry is never mutated.
    """
    import contracts  # noqa: F401

    from contracts import assert_contract_fields_consumed, collect_produced_fields
    from contracts.registry import MESSAGE_FIELD_CONSUMERS

    produced = collect_produced_fields()
    consumed = {k: set(v) for k, v in MESSAGE_FIELD_CONSUMERS.items()}

    # Add a phantom produced field the (copied) consumer map does not read.
    produced.setdefault(model_name, set()).add("__phantom_dropped_field__")

    violations = assert_contract_fields_consumed(produced=produced, consumed=consumed)
    assert any(
        v == f"{model_name}.__phantom_dropped_field__ produced but never consumed"
        for v in violations
    ), f"field-diff failed to flag a dropped consumer on {model_name}: {violations}"

"""Derive the REAL consumer field-reads by AST-sweeping the live service source (§4.8).

This is the consumer half of the produce/consume field-diff, made GROUNDED. The
producer half (:func:`contracts.registry.collect_produced_fields`) walks each contract
model's real ``model_fields``; this half walks the **real consumer source** and records
which fields each service actually *reads* off a contract-typed value — so the diff
compares ``model_fields`` against ``code_actually_reads``, not ``model_fields`` against a
hand-list co-located with the model (which would be a tautology, empty by construction).

Why this is not a hand-list (the failure this replaces): the previous consumer registry
was populated by ``register_field_consumer("AgentChunk", "type", "text", "metadata")``
strings living INSIDE ``libs/contracts`` next to the model — hardcoded to equal the
model's own fields. A real consumer-side rename (``services/harness/provider.py``
``chunk.type`` → ``chunk.kind``) changed nothing in that registry, so the drift class the
node exists to catch was undetectable. This sweep reads the ACTUAL attribute accesses in
``services/*/src`` + ``libs/*/src``, so that same rename now surfaces ``AgentChunk.kind``
as consumed-but-never-produced (and, if it was the last reader of ``.type``, ``.type`` as
produced-but-never-consumed) — a build failure, on the real path.

Binding a local name to a contract type uses three grounded signals (no naming
convention, no guessing):

1. an **annotated parameter or assignment** whose annotation names a contract type,
   including a type unwrapped from a generic/optional wrapper
   (``AsyncIterator[AgentChunk]`` / ``Iterable[AgentChunk]`` / ``list[AgentChunk]`` /
   ``AgentChunk | None``) — the loop/target variable of an ``async for``/``for`` over such
   an annotated iterable binds to the element type;
2. **``isinstance`` narrowing** — inside an ``if isinstance(x, AgentChunk):`` block,
   ``x`` binds to ``AgentChunk`` for the attribute reads in that block;
3. a **direct construction** — ``x = AgentChunk(...)`` binds ``x`` to ``AgentChunk``.

Every ``<bound-name>.<attr>`` READ (an ``ast.Attribute`` in ``Load`` context) records
``<attr>`` as a consumed field of the bound type. A pure discriminator/constructor site
(``AgentChunk(type=..., text=...)`` — the PRODUCER) is a keyword, not an attribute read,
and is intentionally NOT counted as a consumer read.
"""
from __future__ import annotations

import ast
import pathlib

from pydantic import BaseModel

# The pydantic ``BaseModel`` API surface (``model_dump`` / ``model_validate`` / ``dict`` /
# …) — attribute reads of these are METHOD/config accesses, never a produced FIELD, so
# they are excluded from the consumer field-read set. Computed off ``BaseModel`` so the
# exclusion never drifts from the installed pydantic. Dunders are excluded by prefix.
_BASEMODEL_API: frozenset[str] = frozenset(n for n in dir(BaseModel) if not n.startswith("__"))

# The class-declared shared contract models whose consumer reads we sweep. Keyed by the
# name a consumer would import/annotate with; the AST sees only the name, and every one of
# these is a single-declaration-site type (libs/contracts) proven by the co-located
# no-redeclaration sweep — so a name match is an unambiguous binding.
_SWEPT_CONTRACTS: frozenset[str] = frozenset({"AgentChunk"})

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
_CONTRACTS_SRC = (_REPO_ROOT / "libs" / "contracts" / "src").resolve()


def _sweep_roots(repo_root: pathlib.Path) -> list[pathlib.Path]:
    """Every ``services/*/src`` and ``libs/*/src`` tree, EXCLUDING libs/contracts (the home).

    The contracts package DECLARES the models and PRODUCES their fields; a read there is
    not a downstream consumer read, so it is excluded (mirrors the no-redeclaration sweep).
    """
    contracts_src = (repo_root / "libs" / "contracts" / "src").resolve()
    roots: list[pathlib.Path] = []
    for base in ("services", "libs"):
        for src in sorted((repo_root / base).glob("*/src")):
            if src.resolve() == contracts_src:
                continue
            roots.append(src)
    return roots


def _annotation_names(node: ast.expr | None) -> set[str]:
    """The bare identifiers appearing anywhere in a type annotation.

    Handles the wrapper cases so an element type is recovered from its container:
    ``AgentChunk`` in ``AsyncIterator[AgentChunk]`` / ``Iterable[AgentChunk]`` /
    ``list[AgentChunk]`` / ``AgentChunk | None`` / ``Optional[AgentChunk]``. We just
    collect every ``ast.Name``/``ast.Attribute`` leaf id in the annotation subtree and
    intersect with the swept contract names at the call site.
    """
    names: set[str] = set()
    if node is None:
        return names
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            names.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            names.add(sub.attr)
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            # a string forward-ref annotation ("AgentChunk") — parse it too.
            try:
                names |= _annotation_names(ast.parse(sub.value, mode="eval").body)
            except SyntaxError:
                continue
    return names


def _contract_in_annotation(node: ast.expr | None) -> str | None:
    """The single swept-contract type named in an annotation, or ``None``."""
    hit = _annotation_names(node) & _SWEPT_CONTRACTS
    # exactly-one keeps the binding unambiguous; a union of two contracts (rare) is skipped
    # rather than mis-binding a read to the wrong model.
    return next(iter(hit)) if len(hit) == 1 else None


class _ConsumerReadVisitor(ast.NodeVisitor):
    """Walk one module, binding locals to contract types and recording their field reads.

    ``bindings`` maps a local variable name → the contract type it currently holds, built
    from annotations, ``isinstance`` narrowing, and direct construction. Every attribute
    READ on a bound name records the attribute as a consumed field of that type.
    """

    def __init__(self) -> None:
        self.reads: dict[str, set[str]] = {}
        self._bindings: dict[str, str] = {}

    # ── binding sources ────────────────────────────────────────────────────────
    def _bind(self, name: str, contract: str | None) -> None:
        if contract is not None:
            self._bindings[name] = contract

    def _bind_params(self, args: ast.arguments) -> None:
        for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
            self._bind(arg.arg, _contract_in_annotation(arg.annotation))
        if args.vararg is not None:
            self._bind(args.vararg.arg, _contract_in_annotation(args.vararg.annotation))

    def _visit_scope(self, node: ast.AST, args: ast.arguments | None) -> None:
        # A function is a fresh binding scope: params (annotated) bind, then the body is
        # walked with those bindings live. Nested scopes inherit the outer bindings (the
        # simple, sound approximation — a name only ever holds one swept contract type).
        outer = dict(self._bindings)
        if args is not None:
            self._bind_params(args)
        for child in ast.iter_child_nodes(node):
            self.visit(child)
        self._bindings = outer

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_scope(node, node.args)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_scope(node, node.args)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        if isinstance(node.target, ast.Name):
            self._bind(node.target.id, _contract_in_annotation(node.annotation))
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        # direct construction: ``x = AgentChunk(...)`` binds x to AgentChunk.
        contract = None
        if isinstance(node.value, ast.Call):
            func = node.value.func
            fname = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None
            )
            if fname in _SWEPT_CONTRACTS:
                contract = fname
        if contract is not None:
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    self._bind(tgt.id, contract)
        self.generic_visit(node)

    def _bind_loop_target(self, target: ast.expr, itr: ast.expr) -> None:
        # ``for chunk in stream:`` where stream is annotated (or a call returning) an
        # iterable of a contract type — bind the loop var. We only see the iterable's
        # annotation if it is a simple name we already bound to a container; the common,
        # grounded case is an annotated PARAM iterable, handled where the param binds the
        # element type directly (AsyncIterator[AgentChunk] param → we bind the PARAM, and a
        # loop over it re-binds the element). Here we cover ``for x in <name>`` by carrying
        # the element type when the source name is itself contract-annotated as a stream.
        if isinstance(target, ast.Name) and isinstance(itr, ast.Name):
            src = self._bindings.get(itr.name if hasattr(itr, "name") else itr.id)
            if src in _SWEPT_CONTRACTS:
                self._bind(target.id, src)

    def visit_For(self, node: ast.For) -> None:  # noqa: N802
        self._bind_loop_target(node.target, node.iter)
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:  # noqa: N802
        self._bind_loop_target(node.target, node.iter)
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:  # noqa: N802
        # isinstance narrowing: ``if isinstance(x, AgentChunk):`` binds x inside the body.
        narrowed: tuple[str, str] | None = None
        test = node.test
        if (
            isinstance(test, ast.Call)
            and isinstance(test.func, ast.Name)
            and test.func.id == "isinstance"
            and len(test.args) == 2
            and isinstance(test.args[0], ast.Name)
        ):
            cls = test.args[1]
            cls_name = cls.id if isinstance(cls, ast.Name) else None
            if cls_name in _SWEPT_CONTRACTS:
                narrowed = (test.args[0].id, cls_name)
        if narrowed is not None:
            saved = self._bindings.get(narrowed[0])
            self._bindings[narrowed[0]] = narrowed[1]
            for stmt in node.body:
                self.visit(stmt)
            if saved is None:
                self._bindings.pop(narrowed[0], None)
            else:
                self._bindings[narrowed[0]] = saved
            for stmt in node.orelse:
                self.visit(stmt)
            return
        self.generic_visit(node)

    # ── the read itself ─────────────────────────────────────────────────────────
    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        # A field READ is ``<bound-name>.<attr>`` in Load context. A Store/Del (a write)
        # is not a consumer read of a produced field.
        if (
            isinstance(node.ctx, ast.Load)
            and isinstance(node.value, ast.Name)
            and node.value.id in self._bindings
            and node.attr not in _BASEMODEL_API
            and not node.attr.startswith("_")  # dunders / private are never a produced field
        ):
            self.reads.setdefault(self._bindings[node.value.id], set()).add(node.attr)
        self.generic_visit(node)


def _iter_python_files(repo_root: pathlib.Path) -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for root in _sweep_roots(repo_root):
        files.extend(sorted(root.rglob("*.py")))
    return files


def sweep_consumer_reads(repo_root: pathlib.Path | None = None) -> dict[str, set[str]]:
    """AST-sweep the live service source and return ``{contract_name: {field_read, ...}}``.

    Walks every ``services/*/src`` + ``libs/*/src`` module (excluding libs/contracts),
    binds contract-typed locals, and unions the attribute reads on them. This is the REAL
    consumer surface — the fields the product code actually accesses off each contract —
    against which the produce/consume diff runs. A syntactically-unparseable module is a
    build failure surfaced by the no-redeclaration sweep, so it is skipped here.
    """
    root = repo_root if repo_root is not None else _REPO_ROOT
    reads: dict[str, set[str]] = {}
    for pyfile in _iter_python_files(root):
        try:
            tree = ast.parse(pyfile.read_text(encoding="utf-8"), filename=str(pyfile))
        except SyntaxError:
            continue
        visitor = _ConsumerReadVisitor()
        visitor.visit(tree)
        for contract, fields in visitor.reads.items():
            reads.setdefault(contract, set()).update(fields)
    return reads

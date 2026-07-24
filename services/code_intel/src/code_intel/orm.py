"""Deterministic ORM / ownership analysis for the data-flow tools (M5).

``who_writes`` / ``shares_table`` recognise the three tier-1 ORM stacks the spec
names exact-supported — **Django ORM**, **SQLAlchemy**, and **Rails ActiveRecord**
(§4 tiering / §11.12 spike-gate / §12.6) — and label their write sets ``resolved``;
on any non-tier-1 stack they degrade to ``lower-bound`` and never fabricate an exact
answer (Law 2, AC-M5-005/006). ``owner`` resolves via CODEOWNERS (``resolved``) then
git-blame (``lower-bound``). All model-free.

Per-ORM detection is **structural**, never a substring scan (Law 2 — a stray
"django"/"sqlalchemy" in a comment or requirements note must not flip a repo to
``resolved``): Python stacks are recognised from real AST import / base-class nodes;
Ruby (Rails) from an ``ActiveRecord::Base`` subclass in a ``.rb`` file. Each stack's
model→table map and its write-method vocabulary are wired so the exact writers of a
queried table resolve on all three stacks, not Django alone.
"""
from __future__ import annotations

import ast
import fnmatch
import re
from pathlib import Path

from .gitio import run_git
from .results import ModuleRef, OwnerResult, Writer

# Django + generic ORM write verbs (``.objects.create``/``.save``/``mgr.bulk_create``).
_WRITE_METHODS = {"create", "save", "delete", "update", "bulk_create", "get_or_create", "update_or_create", "insert"}
# SQLAlchemy Session write verbs (``session.add(obj)`` / ``.commit()`` / ``.delete(obj)``
# / ``.merge(obj)`` / ``.add_all([...])`` / ``.flush()``), plus ``query(...).delete/update``
# which are already in ``_WRITE_METHODS`` via ``delete``/``update``.
_SA_SESSION_WRITE_METHODS = {"add", "add_all", "merge", "commit", "flush"}
_READ_METHODS = {"all", "filter", "get", "first", "last", "count", "exists"}
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Rails ActiveRecord persistence methods (instance + class), incl. bang forms.
_RAILS_WRITE_METHODS = {
    "save", "save!", "create", "create!", "update", "update!", "update_attribute",
    "update_attributes", "update_all", "destroy", "destroy!", "destroy_all",
    "delete", "delete_all", "insert", "insert_all", "upsert", "upsert_all",
    "increment!", "decrement!", "toggle!", "touch",
}
_RB_CLASS_RE = re.compile(r"^\s*class\s+([A-Z]\w*)\s*<\s*(ApplicationRecord|ActiveRecord::Base)\b", re.M)
_RB_TABLE_NAME_RE = re.compile(r"self\.table_name\s*=\s*['\"]([^'\"]+)['\"]")


def _py_files(clone_path: Path) -> list[Path]:
    return [p for p in sorted(clone_path.rglob("*.py")) if ".git" not in p.parts]


def _rb_files(clone_path: Path) -> list[Path]:
    return [p for p in sorted(clone_path.rglob("*.rb")) if ".git" not in p.parts]


# --------------------------------------------------------------------------- #
# Tier detection (structural, never a substring scan)                         #
# --------------------------------------------------------------------------- #
def _detect_django(clone_path: Path) -> bool:
    for p in _py_files(clone_path):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".")[0] == "django":
                    return True
            elif isinstance(node, ast.Import):
                if any(alias.name.split(".")[0] == "django" for alias in node.names):
                    return True
    return False


def _detect_sqlalchemy(clone_path: Path) -> bool:
    """True iff a real ``import sqlalchemy`` / ``from sqlalchemy…`` import node exists in
    the AST — the structural signal of a SQLAlchemy stack. Comments/strings are absent
    from the AST, so a prose mention cannot flip the repo to ``resolved`` (Law 2)."""
    for p in _py_files(clone_path):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".")[0] == "sqlalchemy":
                    return True
            elif isinstance(node, ast.Import):
                if any(alias.name.split(".")[0] == "sqlalchemy" for alias in node.names):
                    return True
    return False


def _detect_rails(clone_path: Path) -> bool:
    """True iff a ``.rb`` file declares a class subclassing ``ActiveRecord::Base`` (or the
    conventional ``ApplicationRecord`` base). Structural class-header match, not a scan for
    the word 'rails' anywhere (a Gemfile note or comment must not flip the repo)."""
    for p in _rb_files(clone_path):
        if _RB_CLASS_RE.search(p.read_text(encoding="utf-8", errors="replace")):
            return True
    return False


def _tier1_stack(clone_path: Path) -> str | None:
    """The tier-1 ORM stack of the clone (``"django"`` / ``"sqlalchemy"`` / ``"rails"``),
    or ``None`` for a non-tier-1 stack. Django is checked first for backward-compatible
    behaviour, then SQLAlchemy, then Rails."""
    if _detect_django(clone_path):
        return "django"
    if _detect_sqlalchemy(clone_path):
        return "sqlalchemy"
    if _detect_rails(clone_path):
        return "rails"
    return None


def is_tier1(clone_path: Path) -> bool:
    """True iff the clone is one of the three spec-supported exact ORM stacks —
    Django, SQLAlchemy, or Rails ActiveRecord — each detected **structurally**
    (an AST import / base-class node, never a substring scan). §4 / §12.6."""
    return _tier1_stack(clone_path) is not None


# --------------------------------------------------------------------------- #
# Model → table map (per stack)                                               #
# --------------------------------------------------------------------------- #
def _is_django_model_base(node: ast.ClassDef) -> bool:
    return any("Model" in ast.unparse(b) for b in node.bases)


def _is_sqlalchemy_model(node: ast.ClassDef) -> bool:
    """A SQLAlchemy declarative model: a class that either has a ``__tablename__``
    class attribute, or subclasses a declarative base (``Base`` / ``DeclarativeBase`` /
    ``*Base`` produced by ``declarative_base()``). The ``__tablename__`` signal alone is
    decisive; the base-name heuristic is the fallback for mapped classes without one."""
    for item in node.body:
        if isinstance(item, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in item.targets):
                return True
    for b in node.bases:
        text = ast.unparse(b)
        if text == "Base" or text.endswith(".Base") or "DeclarativeBase" in text or text.endswith("Base"):
            return True
    return False


def _sa_tablename(node: ast.ClassDef) -> str | None:
    for item in node.body:
        if isinstance(item, ast.Assign) and isinstance(item.value, ast.Constant):
            if any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in item.targets):
                return str(item.value.value)
    return None


def _rails_table_name(class_name: str, explicit: str | None) -> str:
    """Rails convention: table name is the pluralised snake_case of the class name
    (``OrderItem`` -> ``order_items``), unless ``self.table_name = '...'`` overrides it."""
    if explicit:
        return explicit
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", class_name).lower()
    if snake.endswith("y") and snake[-2:-1] not in "aeiou":
        return snake[:-1] + "ies"
    if snake.endswith(("s", "x", "z", "ch", "sh")):
        return snake + "es"
    return snake + "s"


def _django_app_label(model_file: Path) -> str:
    """Django's default ``app_label`` for a model — the name of the app package that
    contains its ``models.py`` (or ``models/`` package). Django derives ``app_label``
    from the last component of the app's dotted module path, which on disk is the
    directory the ``models`` module lives in (``shop/models.py`` -> ``shop``;
    ``shop/models/orders.py`` -> ``shop``). Falls back to the parent directory name.
    """
    parent = model_file.parent
    # A ``models/`` package: the app is its parent directory (``shop/models/…`` -> shop).
    if parent.name == "models":
        parent = parent.parent
    return parent.name


def _django_default_table(model_name: str, app_label: str) -> str:
    """Django's default DB table for a model with no explicit ``Meta.db_table`` —
    ``<app_label>_<model_name_lowercased>`` (``shop`` + ``Order`` -> ``shop_order``).
    This is the REAL table name a schema-change discussion / DBA uses."""
    return f"{app_label}_{model_name.lower()}"


def _table_class_map(clone_path: Path) -> dict[str, str]:
    """Map ``table-name`` (and lowercased class name) -> model class name, across all
    three tier-1 stacks. Django keys on: the bare class name (lowercased), the explicit
    ``Meta.db_table`` when set, AND Django's REAL default table name
    ``<app_label>_<model>`` when no ``db_table`` is set (so ``who_writes('shop_order')``
    for the real table resolves, not only ``who_writes('order')``). SQLAlchemy uses
    ``__tablename__``; Rails the pluralised-snake convention (or ``self.table_name``)."""
    mapping: dict[str, str] = {}
    for p in _py_files(clone_path):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        app_label = _django_app_label(p)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if _is_django_model_base(node):
                explicit_dj = _explicit_db_table(node)
                table = explicit_dj if explicit_dj is not None else node.name.lower()
                mapping[table] = node.name
                mapping[node.name.lower()] = node.name
                # Register Django's REAL default table name (``<app_label>_<model>``) —
                # but only when the model does not override it with an explicit
                # ``Meta.db_table`` (which is authoritative and must not be shadowed).
                if explicit_dj is None:
                    mapping[_django_default_table(node.name, app_label)] = node.name
            elif _is_sqlalchemy_model(node):
                table = _sa_tablename(node) or node.name.lower()
                mapping[table] = node.name
                mapping[node.name.lower()] = node.name
    for p in _rb_files(clone_path):
        text = p.read_text(encoding="utf-8", errors="replace")
        explicit = _RB_TABLE_NAME_RE.search(text)
        explicit_name = explicit.group(1) if explicit else None
        for m in _RB_CLASS_RE.finditer(text):
            cls = m.group(1)
            table = _rails_table_name(cls, explicit_name)
            mapping[table] = cls
            mapping[cls.lower()] = cls
    return mapping


def _explicit_db_table(node: ast.ClassDef) -> str | None:
    """The model's explicit ``Meta.db_table`` string literal, or ``None`` when it uses
    Django's default (``<app_label>_<model>``). Separated from :func:`_db_table` so the
    default-table synthesis can tell "declared its own name" from "uses the default"."""
    for item in node.body:
        if isinstance(item, ast.ClassDef) and item.name == "Meta":
            for stmt in item.body:
                if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant):
                    if any(isinstance(t, ast.Name) and t.id == "db_table" for t in stmt.targets):
                        return str(stmt.value.value)
    return None


def _db_table(node: ast.ClassDef) -> str:
    explicit = _explicit_db_table(node)
    return explicit if explicit is not None else node.name.lower()


def _models_in_file(tree: ast.Module) -> set[str]:
    """Model class names in scope in this file — defined here or imported. Covers Django
    (``models.Model``) and SQLAlchemy declarative models so a write call referencing the
    model (``session.add(Account(...))``) can be tied back to that model's table."""
    models: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and ("models" in node.module or "model" in node.module):
            models.update(a.name for a in node.names)
        if isinstance(node, ast.ClassDef):
            if _is_django_model_base(node) or _is_sqlalchemy_model(node):
                models.add(node.name)
    return models


# --------------------------------------------------------------------------- #
# Write-call extraction                                                        #
# --------------------------------------------------------------------------- #
def _write_calls(func: ast.AST) -> list[tuple[str, str | None, str, str]]:
    """Return ``(write-method, table-string-literal-or-None, receiver-text, arg-text)`` for
    every write-method call inside ``func`` (Django/generic + SQLAlchemy session verbs).

    ``receiver-text`` is the un-parsed source of the write call's receiver chain; ``arg-text``
    is the un-parsed argument list. For a Tier-3 (search-only) match the queried table name
    must appear textually in one of them before the function may be claimed a writer — so the
    fallback never returns *every* function containing *any* write method (Law 2)."""
    hits: list[tuple[str, str | None, str, str]] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr in _WRITE_METHODS or attr in _SA_SESSION_WRITE_METHODS:
                hits.append((attr, _table_literal(node), _receiver_text(node), _arg_text(node)))
    return hits


def _receiver_text(call: ast.Call) -> str:
    """The receiver expression the write method is called on, plus the call's own args, as
    source text — e.g. for ``db['orders'].insert(total=t)`` -> ``db['orders']`` (+ its args)."""
    parts: list[str] = []
    try:
        if isinstance(call.func, ast.Attribute):
            parts.append(ast.unparse(call.func.value))
        for arg in call.args:
            parts.append(ast.unparse(arg))
        for kw in call.keywords:
            if kw.arg is not None:
                parts.append(kw.arg)
            parts.append(ast.unparse(kw.value))
    except Exception:  # pragma: no cover - unparse is total on valid AST
        return ""
    return " ".join(parts)


def _arg_text(call: ast.Call) -> str:
    """The write call's positional arguments as source text (the constructed row/model).
    For ``session.add(Account(name=n))`` this carries ``Account(...)`` so the model name is
    visible even though the receiver is only ``session``."""
    parts: list[str] = []
    try:
        for arg in call.args:
            parts.append(ast.unparse(arg))
    except Exception:  # pragma: no cover
        return ""
    return " ".join(parts)


def _model_names_in_text(text: str, models: set[str]) -> set[str]:
    """Model class names (from ``models``) that appear as whole identifier tokens in ``text``."""
    tokens = set(_IDENT_RE.findall(text))
    return {m for m in models if m in tokens}


def _table_literal(call: ast.Call) -> str | None:
    """For ``x.table('orders').insert(...)`` recover the 'orders' literal."""
    cur: ast.AST | None = call.func
    while isinstance(cur, ast.Attribute):
        cur = cur.value
        if isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute) and cur.func.attr == "table":
            if cur.args and isinstance(cur.args[0], ast.Constant):
                return str(cur.args[0].value)
    return None


# --------------------------------------------------------------------------- #
# who_writes                                                                   #
# --------------------------------------------------------------------------- #
def who_writes(clone_path: Path, table: str) -> list[Writer]:
    stack = _tier1_stack(clone_path)
    tier1 = stack is not None
    table_map = _table_class_map(clone_path)
    model = table_map.get(table) or table_map.get(table.lower())
    confidence = "resolved" if tier1 else "lower-bound"
    writers: list[Writer] = []
    if stack == "rails":
        return _rails_who_writes(clone_path, table, model, confidence)
    for p in _py_files(clone_path):
        rel = str(p.relative_to(clone_path))
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        file_models = _models_in_file(tree)
        for func in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            if stack == "sqlalchemy":
                if _sa_func_writes_table(func, model, file_models):
                    writers.append(
                        Writer(id=f"{rel}::{func.name}", file=rel, line=func.lineno, confidence=confidence)
                    )
                continue
            # Django tier-1: resolve each write's ACTUAL target model. A write only counts
            # for the queried table when its receiver/instance resolves to that model — never
            # every write in a file for every imported model (D01-WHO-WRITES-OVERATTRIBUTES).
            # An explicit ``db.table('x').insert`` literal still falls through to the generic
            # table-literal path below, so raw-connection writes are unaffected.
            if stack == "django" and model is not None and not _func_has_table_literal(func):
                if _django_func_writes_table(func, model, table, file_models):
                    writers.append(
                        Writer(id=f"{rel}::{func.name}", file=rel, line=func.lineno, confidence=confidence)
                    )
                continue
            for method, table_lit, receiver, arg_text in _write_calls(func):
                if _write_targets_table(
                    method, table_lit, table, model, file_models, receiver, arg_text, stack
                ):
                    writers.append(
                        Writer(id=f"{rel}::{func.name}", file=rel, line=func.lineno, confidence=confidence)
                    )
                    break
    return writers


def _sa_model_typed_locals(func: ast.AST, model: str) -> set[str]:
    """Local variable names bound to a construction of ``model`` inside ``func`` —
    ``acct = Account(...)``. These are the SQLAlchemy instances a later ``session.add``/
    ``session.delete`` persists, so a write on one of them targets ``model``'s table."""
    names: set[str] = set()
    # Parameters annotated with the model type — ``def close(acct: Account)``.
    if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for arg in [*func.args.args, *func.args.posonlyargs, *func.args.kwonlyargs]:
            if arg.annotation is not None:
                ann = ast.unparse(arg.annotation)
                if model in set(_IDENT_RE.findall(ann)):
                    names.add(arg.arg)
    # Locals bound to a construction of the model — ``acct = Account(...)`` (or annotated
    # ``acct: Account = ...``).
    for node in ast.walk(func):
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = ([node.target] if node.target else []), node.value
            if node.annotation is not None and model in set(_IDENT_RE.findall(ast.unparse(node.annotation))):
                if isinstance(node.target, ast.Name):
                    names.add(node.target.id)
        if isinstance(value, ast.Call):
            callee = value.func
            is_model_ctor = (isinstance(callee, ast.Name) and callee.id == model) or (
                isinstance(callee, ast.Attribute) and callee.attr == model
            )
            # ``note = session.query(Note).filter(...).first()`` — a local bound to a query on
            # the model is a model-typed instance (the row later mutated/deleted/committed).
            bound_from_query = model in _model_names_in_text(ast.unparse(value), {model}) and (
                "query" in ast.unparse(value) or "get" in ast.unparse(value)
            )
            if is_model_ctor or bound_from_query:
                for tgt in targets:
                    if isinstance(tgt, ast.Name):
                        names.add(tgt.id)
    return names


def _sa_func_writes_table(func: ast.AST, model: str | None, file_models: set[str]) -> bool:
    """True iff ``func`` performs a SQLAlchemy write against ``model``'s table. Recognises:

    * ``session.add(m)`` / ``add_all([m])`` / ``merge(m)`` / ``delete(m)`` where ``m`` is a
      model instance (either ``Model(...)`` inline or a local bound to one), and the
      accompanying ``commit()``/``flush()`` in the same function;
    * ``query(Model).delete()`` / ``.update(...)`` — the model in the query receiver;
    * a direct ``Model(...)`` construction that is subsequently added/committed.
    """
    if model is None or model not in file_models:
        return False
    typed_locals = _sa_model_typed_locals(func, model)
    # Attribute mutations on a model-typed local/param — ``note.title = ...`` — mark a pending
    # dirty write that a subsequent ``session.commit()`` flushes (the idiomatic UPDATE path).
    mutates_model_instance = False
    for node in ast.walk(func):
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            tgts = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in tgts:
                if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id in typed_locals:
                    mutates_model_instance = True
    for node in ast.walk(func):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        attr = node.func.attr
        recv = _receiver_text(node)
        args = _arg_text(node)
        ctx = f"{recv} {args}"
        model_in_ctx = model in _model_names_in_text(ctx, {model}) or bool(
            typed_locals & set(_IDENT_RE.findall(args))
        )
        # session.add(m)/add_all([m])/merge(m)/delete(m) on a model instance.
        if attr in {"add", "add_all", "merge", "delete"} and model_in_ctx:
            return True
        # query(Model).delete()/update() — the model in the query receiver chain.
        if attr in {"delete", "update"} and model in _model_names_in_text(recv, {model}):
            return True
        # commit()/flush() after mutating a model-typed instance's attributes (UPDATE path).
        if attr in {"commit", "flush"} and mutates_model_instance:
            return True
    return False


def _func_has_table_literal(func: ast.AST) -> bool:
    """True iff ``func`` contains any explicit ``x.table('name').<write>`` literal write — a
    raw-connection path that resolves by table string, not by ORM model. Such functions keep
    using the generic table-literal branch in ``_write_targets_table``."""
    for _method, table_lit, _recv, _arg in _write_calls(func):
        if table_lit is not None:
            return True
    return False


def _django_model_typed_locals(func: ast.AST, model: str) -> set[str]:
    """Local variable names bound to an instance of Django ``model`` inside ``func`` —
    ``order = Order(...)`` or ``order = Order.objects.get(...)`` / ``.create(...)`` / etc.,
    plus parameters annotated ``order: Order``. A later ``order.save()`` / ``order.delete()``
    on one of these names targets ``model``'s table (and only that table). Mirrors the
    SQLAlchemy typed-local resolver so ``x.save()`` is attributed to the RIGHT model, never
    to every model imported into the file (§11.12 gate-(b), Law 2)."""
    names: set[str] = set()

    def _binds_model(value: ast.expr | None) -> bool:
        # ``Order(...)`` — a direct construction of the model.
        if isinstance(value, ast.Call):
            callee = value.func
            if (isinstance(callee, ast.Name) and callee.id == model) or (
                isinstance(callee, ast.Attribute) and callee.attr == model
            ):
                return True
            # ``Order.objects.get(...)`` / ``.create(...)`` / ``.filter(...).first()`` —
            # a manager/queryset call whose root receiver is the model class returns a
            # model instance. Require the model to be the ROOT of the receiver chain so a
            # write on a *different* model in the same file is not misattributed.
            if isinstance(callee, ast.Attribute) and _attr_chain_root(callee) == model:
                return True
        return False

    if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for arg in [*func.args.args, *func.args.posonlyargs, *func.args.kwonlyargs]:
            if arg.annotation is not None and model in set(_IDENT_RE.findall(ast.unparse(arg.annotation))):
                names.add(arg.arg)
    for node in ast.walk(func):
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target] if node.target else []
            value = node.value
            if node.annotation is not None and model in set(_IDENT_RE.findall(ast.unparse(node.annotation))):
                if isinstance(node.target, ast.Name):
                    names.add(node.target.id)
        if _binds_model(value):
            for tgt in targets:
                if isinstance(tgt, ast.Name):
                    names.add(tgt.id)
    return names


def _attr_chain_root(node: ast.AST) -> str | None:
    """The left-most identifier of an attribute chain — for ``Order.objects.get`` -> ``Order``,
    for ``self.order.save`` -> ``self``. Used to tell ``Model.objects.create`` (a write on
    ``Model``) from an unrelated manager call."""
    cur: ast.AST | None = node
    while isinstance(cur, (ast.Attribute, ast.Call)):
        cur = cur.func if isinstance(cur, ast.Call) else cur.value
    return cur.id if isinstance(cur, ast.Name) else None


def _name_stems_for(table: str, model: str) -> set[str]:
    """Receiver-variable name stems that legitimately tie a bare ``x.save()`` back to the
    queried table/model — ``order`` for table ``orders``/model ``Order`` (mirrors the Rails
    ``recv_tokens`` heuristic). Case-folded so an un-annotated ``def cancel_order(order)`` that
    calls ``order.save()`` resolves, without needing a type annotation."""
    stems = {table.lower(), table.lower().rstrip("s"), model.lower()}
    return {s for s in stems if s}


def _django_func_writes_table(func: ast.AST, model: str, table: str, file_models: set[str]) -> bool:
    """True iff ``func`` performs a Django ORM write whose ACTUAL target model is ``model``.

    Resolves each write call's real target instead of attributing every ``.save()``/``.create()``
    in the file to every imported model (the D01-WHO-WRITES-OVERATTRIBUTES bug). Recognises:

    * ``Model.objects.create(...)`` / ``.bulk_create([...])`` / ``.update()`` / ``.get_or_create``
      / ``.update_or_create`` / ``Model.objects.filter(...).update()`` / ``.delete()`` — the
      model is the ROOT of the write's receiver chain;
    * ``Model(...).save()`` — the model is constructed in the receiver;
    * ``instance.save()`` / ``instance.delete()`` where ``instance`` is a local/param resolved
      to ``model`` (via ``_django_model_typed_locals``);
    * ``instance.save()`` on an UN-annotated receiver whose NAME stems match the queried
      table/model (``def cancel_order(order): order.save()``) — but only when that same name is
      not resolved to a *different* in-file model in this function (so it never over-attributes).
    """
    if model not in file_models:
        return False
    typed_locals = _django_model_typed_locals(func, model)
    # Names bound to some OTHER model in this function — a bare write on one of these is that
    # other model's write, never ours, even if the name happens to stem-match.
    other_model_locals: set[str] = set()
    for other in file_models - {model}:
        other_model_locals |= _django_model_typed_locals(func, other)
    stems = _name_stems_for(table, model)
    for node in ast.walk(func):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        attr = node.func.attr
        if attr not in _WRITE_METHODS:
            continue
        recv_node = node.func.value
        # ``instance.save()`` / ``instance.delete()`` on a model-typed local/param.
        if isinstance(recv_node, ast.Name) and recv_node.id in typed_locals:
            return True
        # ``Model.objects.<write>`` / ``Model.objects.filter(...).<write>`` — model is the root
        # of the receiver chain; or ``Model(...).save()`` — model constructed in the receiver.
        root = _attr_chain_root(recv_node)
        if root == model:
            return True
        recv_txt = _receiver_text(node)
        if model in _model_names_in_text(recv_txt, {model}):
            # A genuine class reference in the receiver/args (``Model(...)`` ctor or ``Model.objects``),
            # not a lowercased keyword-arg name (matching is case-exact, so those never match).
            return True
        # Un-annotated receiver whose name stems match this table/model — accept ONLY when it is
        # not resolved to a different in-file model here (keeps the disjoint-writer guarantee).
        if (
            isinstance(recv_node, ast.Name)
            and recv_node.id.lower() in stems
            and recv_node.id not in other_model_locals
        ):
            return True
    return False


def _write_targets_table(
    method: str,
    table_lit: str | None,
    table: str,
    model: str | None,
    file_models: set[str],
    receiver: str,
    arg_text: str,
    stack: str | None,
) -> bool:
    # Tier-1/2: an explicit table literal (``db.table('orders').insert``) is an exact match.
    if table_lit is not None:
        return table_lit == table
    if model is not None and model in file_models:
        # Django: ``Model.objects.<write>`` / ``instance.save()`` where the model resolved to
        # the queried table and is in scope. SQLAlchemy: the model class is constructed inside
        # a session write (``session.add(Account(...))``) or targeted by ``query(Account)…``.
        if stack == "sqlalchemy":
            if method in _SA_SESSION_WRITE_METHODS:
                # add/merge on a session — the queried model must appear in the args (the row
                # being persisted). ``commit``/``flush`` with the model in-scope also counts as
                # it flushes that unit of work; require the model token to appear in the func's
                # write context (receiver or args) to stay honest.
                return model in _model_names_in_text(f"{receiver} {arg_text}", {model})
            # query(Model).delete()/update() — the model appears in the receiver chain.
            return model in _model_names_in_text(receiver, {model})
        # Django (fallback for the rare mixed raw+ORM function reaching here): the write only
        # targets the queried table when its model is a genuine class reference in the write's
        # receiver/args — never a blanket "any write in this file". This keeps the old blast
        # radius from ever returning even on the residual path (D01-WHO-WRITES-OVERATTRIBUTES).
        return model in _model_names_in_text(f"{receiver} {arg_text}", {model})
    # Tier-3 (search-only, non-tier-1): only a real textual lead counts.
    return _name_associates(table, receiver)


def _rails_who_writes(clone_path: Path, table: str, model: str | None, confidence: str) -> list[Writer]:
    """Rails: a method that calls an ActiveRecord persistence verb (``save!``/``create!``/
    ``update!``/``destroy`` …) whose receiver/args tie back to the queried model or table.
    Ruby is not parsed by the Python AST, so this is a structural line/method scan of the
    ``.rb`` sources — still deterministic and grounded to a ``file::method`` citation."""
    writers: list[Writer] = []
    if model is None:
        return writers
    verb_alt = "|".join(re.escape(m) for m in sorted(_RAILS_WRITE_METHODS, key=len, reverse=True))
    # ``<receiver>.<verb>`` — a persistence call with an explicit receiver.
    qualified_re = re.compile(r"([A-Za-z_][\w]*(?:::[A-Za-z_]\w*)*)\.(" + verb_alt + r")(?![\w!?])")
    # A bare persistence call (implicit ``self`` receiver) — ``create!(...)`` / ``save!``.
    bare_re = re.compile(r"(?:^|[^.\w])(" + verb_alt + r")(?![\w!?])")
    class_re = re.compile(r"^(\s*)class\s+([A-Z]\w*)")
    def_re = re.compile(r"^(\s*)def\s+(?:self\.)?([A-Za-z_]\w*[!?=]?)")
    # Receiver-variable stems that tie back to this model/table (``acct`` ~ account, etc.).
    recv_tokens: set[str] = {table.lower(), table.lower().rstrip("s"), model.lower()}

    for p in _rb_files(clone_path):
        rel = str(p.relative_to(clone_path))
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        cur_class: str | None = None
        cur_method: str | None = None
        cur_line = 0
        emitted: set[str] = set()

        def _emit(name: str, line: int) -> None:
            if name not in emitted:
                writers.append(Writer(id=f"{rel}::{name}", file=rel, line=line, confidence=confidence))
                emitted.add(name)

        for i, raw in enumerate(lines, start=1):
            cm = class_re.match(raw)
            if cm:
                cur_class = cm.group(2)
                continue
            dm = def_re.match(raw)
            if dm:
                cur_method = dm.group(2)
                cur_line = i
                continue
            if cur_method is None:
                continue
            in_model_body = cur_class == model
            for qm in qualified_re.finditer(raw):
                receiver = qm.group(1)
                recv_id = receiver.split(".")[0]
                # Explicit model-class receiver (``Account.create!``) — exact.
                if recv_id == model or receiver == model:
                    _emit(cur_method, cur_line)
                # ``self.save!`` inside the model's own class body — exact (self IS this model).
                elif receiver == "self" and in_model_body:
                    _emit(cur_method, cur_line)
                # A receiver variable whose name is a stem of the model/table.
                elif recv_id.lower() in recv_tokens:
                    _emit(cur_method, cur_line)
            # Bare persistence verb (implicit self) inside the model's class body.
            if in_model_body and not qualified_re.search(raw) and bare_re.search(raw):
                _emit(cur_method, cur_line)
    return writers


def _name_associates(table: str, receiver: str) -> bool:
    """True iff the queried table name is textually tied to the write call — as a whole
    identifier token in the receiver chain / arguments."""
    if not table or not receiver:
        return False
    candidates = {table, table.lower(), table.rstrip("s"), table.lower().rstrip("s")}
    tokens = set(_IDENT_RE.findall(receiver))
    tokens |= {t.lower() for t in tokens}
    return bool(candidates & tokens)


# --------------------------------------------------------------------------- #
# shares_table                                                                 #
# --------------------------------------------------------------------------- #
def shares_table(clone_path: Path, table: str) -> list[ModuleRef]:
    stack = _tier1_stack(clone_path)
    tier1 = stack is not None
    table_map = _table_class_map(clone_path)
    model = table_map.get(table) or table_map.get(table.lower())
    confidence = "resolved" if tier1 else "lower-bound"
    modules: dict[str, ModuleRef] = {}
    if model is None:
        return []
    if stack == "rails":
        for p in _rb_files(clone_path):
            rel = str(p.relative_to(clone_path))
            text = p.read_text(encoding="utf-8", errors="replace")
            if _defines_rails_model(text, model):
                continue
            if re.search(r"\b" + re.escape(model) + r"\b", text):
                top = rel.split("/", 1)[0]
                modules.setdefault(top, ModuleRef(id=top, confidence=confidence))
        return list(modules.values())
    for p in _py_files(clone_path):
        rel = str(p.relative_to(clone_path))
        text = p.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        if _defines_model(tree, model):
            continue
        # a co-accessor references the model AND actually accesses/queries it — Django
        # ``.objects`` or a SQLAlchemy ``query(Model)`` / ``session.add(Model(...))``
        # construction. A bare import/type-hint mention is NOT a co-access (Law 2 — never
        # fabricate a co-accessor that merely names the model in an annotation).
        if model in text and (
            f"{model}.objects" in text
            or re.search(r"query\s*\(\s*" + re.escape(model) + r"\b", text)
            or re.search(r"(?<![\w.])" + re.escape(model) + r"\s*\(", text)
        ):
            top = rel.split("/", 1)[0]
            modules.setdefault(top, ModuleRef(id=top, confidence=confidence))
    return list(modules.values())


def _defines_model(tree: ast.Module, model: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == model:
            if _is_django_model_base(node) or _is_sqlalchemy_model(node):
                return True
    return False


def _defines_rails_model(text: str, model: str) -> bool:
    return any(m.group(1) == model for m in _RB_CLASS_RE.finditer(text))


# --------------------------------------------------------------------------- #
# owner                                                                        #
# --------------------------------------------------------------------------- #
def owner(clone_path: Path, path: str) -> OwnerResult:
    codeowners = clone_path / "CODEOWNERS"
    if codeowners.is_file():
        match = _match_codeowners(codeowners.read_text(encoding="utf-8", errors="replace"), path)
        if match is not None:
            return OwnerResult(owner=match, confidence="resolved", file="CODEOWNERS", line=None)
    # git-blame fallback — grounded but not authoritative
    blame = run_git(
        ["--git-dir", str(clone_path / ".git"), "log", "-1", "--format=%an", "--", path],
        check=False,
    )
    author = blame.stdout.strip() or "(unknown)"
    return OwnerResult(owner=author, confidence="lower-bound", file=path, line=1)


def _match_codeowners(text: str, path: str) -> str | None:
    result: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        pattern, owners = parts[0], parts[1:]
        if not owners:
            continue
        if _codeowners_match(pattern, path):
            result = owners[0]
    return result


def _codeowners_match(pattern: str, path: str) -> bool:
    if pattern.endswith("/"):
        return path.startswith(pattern)
    if pattern.startswith("/"):
        pattern = pattern[1:]
    base = path.rsplit("/", 1)[-1]
    return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(base, pattern) or path.startswith(pattern)

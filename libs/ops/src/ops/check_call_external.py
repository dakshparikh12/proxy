"""The ``check-call-external`` guard (Doc 00 §14, CANONICAL §11; decisions.md D-002).

§14 hard rule: *every external call wrapped with retry + cost telemetry*, enforced by
"the single ``call_external`` seam in ``libs/http`` (no raw client lives anywhere
else)." A raw vendor client constructed anywhere else silently bypasses the retry +
cost-telemetry wrapper — the exact class of bug the CON-004 regression proved is real.
This guard makes the single-seam invariant *enforced*, not merely documented.

It is an AST scan of product code (``services/`` + ``libs/``) for raw vendor-client
*constructions* — the async/sync Anthropic model clients (Claude), the httpx async and
sync HTTP clients, the ``google.cloud.storage`` GCS client, the raw Recall / STT /
TTS clients (Deepgram / ElevenLabs / Cartesia), AND the E2B ``AsyncSandbox`` (the
Workroom sandbox backend — its ``.create(...)`` classmethod is the sandbox
construction). For every such construction the enclosing file must be the seam home
under ``libs/http``; a construction anywhere else is flagged with its ``path:line``.
It mirrors ``check_sdk_isolation_triad``'s scan-services+libs AST structure — an
automated check, never a manual re-audit.

Aliased imports (``from anthropic import AsyncAnthropic as _AA``) and lazy
imports-inside-functions are recognized by tracking the module's import bindings.
``TYPE_CHECKING``-only imports of vendor *types* are never flagged — they are imports,
not constructions, so no ``ast.Call`` node exists to match. With every raw client
already living in ``libs/http/external.py``, the gate passes honestly today and goes
load-bearing the moment the first raw client lands outside the seam.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

_SCAN_ROOTS: tuple[str, ...] = ("services", "libs")

# The one legitimate home for raw vendor-client construction: the call_external seam.
# A construction whose file lives under this path prefix is NOT a violation.
_SEAM_DIR: str = "libs/http"

# Bare/aliased constructor NAMES (imported directly, e.g. ``from anthropic import
# AsyncAnthropic`` and then called, possibly via an ``as _AA`` alias). Matched against
# the module's tracked import bindings so an alias resolving to one of these is caught.
_CLIENT_NAMES: frozenset[str] = frozenset(
    {
        "AsyncAnthropic",  # Claude models (async)
        "Anthropic",  # Claude models (sync)
        "AsyncClient",  # httpx (imported as ``from httpx import AsyncClient``)
        "Client",  # httpx / GCS storage (imported bare)
        "Deepgram",  # STT
        "ElevenLabs",  # TTS
        "Cartesia",  # TTS
        "AsyncSandbox",  # E2B Workroom sandbox (``from e2b import AsyncSandbox``)
    }
)

# E2B constructs the sandbox via a classmethod on the imported class name rather than a
# bare ``AsyncSandbox()`` call: ``AsyncSandbox.create(...)`` (also ``.connect(...)``).
# Because ``AsyncSandbox`` is a tracked constructor *name* (bound via ``from e2b import
# AsyncSandbox``), a ``<name>.<factory>`` call whose base name resolves to a client
# constructor is a raw construction too. These are the E2B factory attributes.
_NAME_FACTORY_ATTRS: frozenset[tuple[str, str]] = frozenset(
    {
        ("AsyncSandbox", "create"),  # the E2B sandbox construction (the live wire surface)
        ("AsyncSandbox", "connect"),  # re-attach to an existing sandbox (also a raw client)
    }
)

# Attribute-form constructions: a ``<module>.<attr>`` call where <module> is the tracked
# import binding of the vendor package — the httpx async/sync client attrs, the
# ``google.cloud.storage`` client attr, and the defensive Recall / Deepgram attrs.
_CLIENT_ATTRS: frozenset[tuple[str, str]] = frozenset(
    {
        ("httpx", "AsyncClient"),
        ("httpx", "Client"),
        ("storage", "Client"),  # the google.cloud.storage GCS client attr
        ("recall", "Client"),  # raw Recall.ai client (defensive)
        ("deepgram", "DeepgramClient"),  # STT (defensive)
    }
)

# Vendor packages whose bare-name imports we track so an aliased construction resolves.
_VENDOR_MODULES: frozenset[str] = frozenset(
    {"anthropic", "httpx", "storage", "google", "recall", "deepgram", "elevenlabs", "cartesia", "e2b"}
)


def _iter_py(root: Path) -> list[Path]:
    return [
        p
        for base in _SCAN_ROOTS
        if (d := root / base).is_dir()
        for p in sorted(d.rglob("*.py"))
        if ".git" not in p.parts and p.name != "check_call_external.py"
    ]


def _in_seam(path: Path, root: Path) -> bool:
    """True when ``path`` is the legitimate ``libs/http`` seam home (the ONE place a raw
    vendor client may be constructed)."""
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        rel = path.as_posix()
    return rel == _SEAM_DIR or rel.startswith(_SEAM_DIR + "/")


class _ImportBindings:
    """Collect the names a module binds to vendor client constructors / packages.

    * ``names`` — local names bound to a raw-client *constructor* (bare or aliased),
      e.g. ``AsyncAnthropic``, ``_AA`` (``... as _AA``), ``AsyncClient``.
    * ``modules`` — local names bound to a vendor *package/module* whose ``.Client``-
      style attribute is a raw construction, e.g. ``httpx``, ``storage``.
    """

    def __init__(self) -> None:
        self.names: dict[str, str] = {}  # local-name -> canonical constructor name
        self.modules: dict[str, str] = {}  # local-name -> canonical module name

    def visit(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self._from_import(node)
            elif isinstance(node, ast.Import):
                self._plain_import(node)

    def _from_import(self, node: ast.ImportFrom) -> None:
        mod = (node.module or "").split(".")[0]
        for alias in node.names:
            local = alias.asname or alias.name
            # ``from anthropic import AsyncAnthropic [as _AA]`` -> constructor binding.
            if alias.name in _CLIENT_NAMES:
                self.names[local] = alias.name
            # ``from google.cloud import storage`` -> module binding for a storage attr.
            elif alias.name in _VENDOR_MODULES:
                self.modules[local] = alias.name
            elif mod in _VENDOR_MODULES and alias.name in _VENDOR_MODULES:
                self.modules[local] = alias.name

    def _plain_import(self, node: ast.Import) -> None:
        for alias in node.names:
            top = alias.name.split(".")[0]
            local = alias.asname or top
            if top in _VENDOR_MODULES:
                # ``import httpx`` / ``import httpx as hx`` -> module binding.
                self.modules[local] = top


def _canonical_module(name: str) -> str:
    return "storage" if name == "google" else name


def _is_raw_client_call(node: ast.Call, binds: _ImportBindings) -> bool:
    """True when this call constructs a raw vendor client (outside-seam is the caller's
    concern). Recognizes both aliased bare-name and ``module.attr`` construction forms."""
    func = node.func
    # Bare / aliased name: a constructor called via its imported name or an ``as`` alias.
    if isinstance(func, ast.Name):
        canonical = binds.names.get(func.id)
        if canonical is not None:
            return True
        # An un-imported bare name that is itself a known constructor (defensive: a
        # planted Anthropic constructor even if the import was oddly shaped).
        return func.id in _CLIENT_NAMES and func.id not in ("Client", "AsyncClient")
    # Attribute form: a vendor module's client attribute called as a constructor.
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        base_local = func.value.id
        # (a) ``<module>.<attr>`` — the httpx async/sync client attrs and the
        #     google.cloud.storage GCS client attr, constructed via the module alias.
        mod_canonical = _canonical_module(binds.modules.get(base_local, base_local))
        if (mod_canonical, func.attr) in _CLIENT_ATTRS:
            return True
        # (b) ``<ClientName>.<factory>`` — a factory classmethod on an imported client
        # constructor NAME (bare or aliased), e.g. ``AsyncSandbox.create(...)`` (E2B).
        # The base name resolves to a tracked constructor via the import bindings, or is
        # itself a bare known constructor name.
        name_canonical = binds.names.get(base_local, base_local)
        return (name_canonical, func.attr) in _NAME_FACTORY_ATTRS
    return False


def raw_client_sites_outside_seam(root: Path) -> list[str]:
    """Return ``path:line`` for every raw vendor-client construction OUTSIDE the
    ``libs/http`` seam. Empty when every raw client lives in the seam (or none exist)."""
    offenders: list[str] = []
    for path in _iter_py(root):
        if _in_seam(path, root):
            continue  # the ONE legitimate home — never flagged
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(src)
        except (OSError, SyntaxError):
            continue
        binds = _ImportBindings()
        binds.visit(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_raw_client_call(node, binds):
                offenders.append(f"{path}:{getattr(node, 'lineno', 0)}")
    return sorted(offenders)


def check(root: Path | None = None) -> int:
    """Return 0 when every raw vendor client lives in the ``libs/http`` seam; raise
    otherwise (mirrors ``check_sdk_isolation_triad.check``)."""
    base = root if root is not None else Path(__file__).resolve().parents[4]
    offenders = raw_client_sites_outside_seam(base)
    if offenders:
        raise AssertionError(
            "raw vendor client constructed outside the libs/http call_external seam:\n  "
            + "\n  ".join(offenders)
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for CI + pre-commit; exits non-zero naming any raw-client site
    outside ``libs/http``. An optional positional arg overrides the scan root (tests)."""
    root = Path(argv[0]) if argv else None
    try:
        return check(root)
    except AssertionError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

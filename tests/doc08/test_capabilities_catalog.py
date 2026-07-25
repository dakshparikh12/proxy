"""Doc 08 · §4.7 — the typed CAPABILITIES catalog + its build-time UI manifest.

The single typed source of truth for *what Proxy can do* is a plain Python module
constant ``CAPABILITIES: dict[str, Capability]`` in ``libs/contracts/capabilities.py``
(CANONICAL §6 + §12.5). The backend imports this module directly; **the UI does NOT**
— a build step generates a small JSON/TS manifest of ``{id, label, output, surfaces}``
only, so the internal ``service:`` bindings (``wake:…`` / ``disposition:…``) and the
per-surface ``renderer`` config **never reach the browser** (the service-string-in-TS
fix). There is **no runtime ``GET /capabilities`` endpoint** (cut in CANONICAL §6).

Every test runs the REAL path:

* it imports the live ``contracts.capabilities`` module (the typed source the backend
  enforces),
* it invokes the REAL manifest generator (``contracts.gen_ui_manifest.build_manifest``
  / the ``python -m`` build step) and asserts on its actual bytes,
* the "no route" test enumerates the REAL ``control_plane`` FastAPI app.

No mocks. Product imports live inside the test bodies so the module COLLECTS clean and
would fail RED before the source exists.

``walkthrough`` is a delivery MODE (§2.4 #12, owned by §12.3), NOT a disposition and NOT
a delivery flag on this catalog: its ``service`` is ``None`` and the catalog names only
its label. This file's ``test_walkthrough_is_a_delivery_mode_label_only`` pins that.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# The four capabilities the DoD names at minimum.
_MINIMUM = {"answer_grounded", "catch_me_up", "build", "walkthrough"}
# The ONLY keys the UI manifest may carry per capability (the service-string fix).
_MANIFEST_KEYS = {"id", "label", "output", "surfaces"}
# Backend-only fields that must NEVER appear in the generated manifest.
_BACKEND_ONLY = {"service", "renderer", "actions"}

_REPO = Path(__file__).resolve().parents[2]


# ── the typed source of truth exists and is well-formed ───────────────────────
def test_capabilities_catalog_is_the_typed_source_of_truth() -> None:
    """CAPABILITIES is a dict[str, Capability] carrying at least the DoD's four."""
    from contracts.capabilities import CAPABILITIES, Capability

    assert isinstance(CAPABILITIES, dict) and CAPABILITIES, "CAPABILITIES must be a non-empty dict"
    assert _MINIMUM <= set(CAPABILITIES), (
        f"CAPABILITIES must name at least {_MINIMUM}; missing {_MINIMUM - set(CAPABILITIES)}"
    )
    for key, cap in CAPABILITIES.items():
        assert isinstance(cap, Capability), f"{key!r} must be a Capability instance"
        # the dict key IS the capability id (single source of truth, no drift).
        assert cap.id == key, f"CAPABILITIES[{key!r}].id must equal its key, got {cap.id!r}"
        assert cap.label, f"{key!r} must carry a user-facing label"


def test_capabilities_catalog_is_importable_from_the_package_root() -> None:
    """The backend imports the module through libs.contracts.__init__ (registration home)."""
    import contracts

    assert hasattr(contracts, "CAPABILITIES"), "CAPABILITIES must be exported from libs.contracts"
    assert hasattr(contracts, "Capability"), "Capability must be exported from libs.contracts"
    from contracts import CAPABILITIES as via_root
    from contracts.capabilities import CAPABILITIES as via_module

    assert via_root is via_module, "the package root must re-export the SAME catalog object"


# ── allowed_on(surface) is the authorization primitive ────────────────────────
def test_allowed_on_returns_actions_only_for_declared_surfaces() -> None:
    """allowed_on(surface) is what the channel_action handler calls to authorize."""
    from contracts.capabilities import CAPABILITIES

    build = CAPABILITIES["build"]
    # build declares screen/chat/canvas — an in-set surface yields its actions.
    for surface in build.surfaces:
        got = build.allowed_on(surface)
        assert got == build.actions, f"allowed_on({surface!r}) must return the capability's actions"
        assert got, "a declared surface must carry at least one action"
    # an out-of-set surface yields the empty frozenset (deny), never the actions.
    assert build.allowed_on("voice") == frozenset(), "an undeclared surface must deny (empty set)"
    assert build.allowed_on("nonsense") == frozenset(), "an unknown surface must deny (empty set)"


def test_allowed_on_is_an_authorization_deny_by_default() -> None:
    """Every capability denies (empty set) on a surface it does not declare."""
    from contracts.capabilities import CAPABILITIES

    all_surfaces = {"voice", "chat", "tile", "canvas", "screen"}
    for cap in CAPABILITIES.values():
        for surface in all_surfaces - set(cap.surfaces):
            assert cap.allowed_on(surface) == frozenset(), (
                f"{cap.id!r} must deny on undeclared surface {surface!r}"
            )


# ── walkthrough is a delivery MODE (label only), never a disposition/flag ─────
def test_walkthrough_is_a_delivery_mode_label_only() -> None:
    """§2.4 #12 / DoD: walkthrough carries NO service binding and NO delivery flag —
    the catalog names only its label; the delivery MODE is owned by §12.3."""
    from contracts.capabilities import CAPABILITIES

    wt = CAPABILITIES["walkthrough"]
    assert wt.service is None, "walkthrough must NOT bind a service (it is a delivery MODE, §12.3)"
    # not modeled as a disposition/delivery flag anywhere on the model.
    dumped = wt.model_dump(mode="json")
    assert "delivery" not in dumped, "walkthrough must not carry a `delivery` flag"
    for banned in ("disposition:narrator", "delivery:narrated", "narrated"):
        assert banned not in json.dumps(dumped), f"walkthrough must not model {banned!r}"
    assert wt.label, "the catalog names only the walkthrough label"


def test_service_bindings_are_present_only_where_a_behavior_fulfills() -> None:
    """answer_grounded/catch_me_up/build bind a wake:/disposition: service; walkthrough does not."""
    from contracts.capabilities import CAPABILITIES

    assert CAPABILITIES["answer_grounded"].service and CAPABILITIES["answer_grounded"].service.startswith("wake:")
    assert CAPABILITIES["catch_me_up"].service and CAPABILITIES["catch_me_up"].service.startswith("wake:")
    assert CAPABILITIES["build"].service and CAPABILITIES["build"].service.startswith("disposition:")
    assert CAPABILITIES["walkthrough"].service is None


# ── labels carry no internal component name (naming lint, §2.1) ───────────────
def test_labels_carry_no_internal_component_name() -> None:
    """User-visible labels never contain Orchestrator/Scribe/workroom (the naming law)."""
    from contracts.capabilities import CAPABILITIES
    from lint.naming import check_user_visible_strings

    mapping = {cap.id: cap.label for cap in CAPABILITIES.values()}
    result = check_user_visible_strings(mapping)
    assert result.exit_code == 0, f"labels leak internal names: {result.violations}"


# ── the build step emits a UI manifest of {id,label,output,surfaces} ONLY ─────
def test_ui_manifest_carries_only_the_four_ui_keys() -> None:
    """The generated manifest carries ONLY {id,label,output,surfaces} per capability —
    the internal service:/renderer/actions fields never ship to the browser."""
    from contracts.gen_ui_manifest import build_manifest

    manifest = build_manifest()
    assert isinstance(manifest, list) and manifest, "the manifest must be a non-empty list"
    ids = {entry["id"] for entry in manifest}
    assert _MINIMUM <= ids, f"manifest must cover the catalog; missing {_MINIMUM - ids}"
    for entry in manifest:
        keys = set(entry)
        assert keys == _MANIFEST_KEYS, (
            f"manifest entry {entry.get('id')!r} must carry EXACTLY {_MANIFEST_KEYS}, got {keys}"
        )
        # surfaces render as a JSON-friendly list of strings (a frozenset is not JSON).
        assert isinstance(entry["surfaces"], list), "surfaces must serialize as a list"
        assert all(isinstance(s, str) for s in entry["surfaces"])
        assert isinstance(entry["output"], str) and entry["output"]


def test_ui_manifest_never_carries_a_service_string() -> None:
    """No `service:` binding (wake:/disposition:) may appear ANYWHERE in the manifest."""
    from contracts.capabilities import CAPABILITIES
    from contracts.gen_ui_manifest import build_manifest

    blob = json.dumps(build_manifest())
    # the raw substring 'service' must not appear as a key.
    for entry in build_manifest():
        assert "service" not in entry, "no service key may reach the browser"
        for banned in _BACKEND_ONLY:
            assert banned not in entry, f"backend-only field {banned!r} leaked into the manifest"
    # and no wake:/disposition: binding VALUE leaks as a string either.
    for cap in CAPABILITIES.values():
        if cap.service is not None:
            assert cap.service not in blob, (
                f"the service binding {cap.service!r} must NEVER ship to the browser"
            )
    assert "wake:" not in blob and "disposition:" not in blob, "no service-string may reach the UI"


def test_ui_manifest_build_step_writes_the_apps_files() -> None:
    """The `python -m contracts.gen_ui_manifest` build step writes the manifest into
    apps/connect + apps/tile — the files the tile/connect Vite builds import (never the
    Python module). Runs the REAL build step as a subprocess against the real repo."""
    connect = _REPO / "apps" / "connect" / "capabilities.manifest.json"
    tile = _REPO / "apps" / "tile" / "capabilities.manifest.json"
    for stale in (connect, tile):
        if stale.exists():
            stale.unlink()

    proc = subprocess.run(
        [sys.executable, "-m", "contracts.gen_ui_manifest"],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"the build step must exit 0; stderr={proc.stderr}"

    for target in (connect, tile):
        assert target.exists(), f"the build step must write {target}"
        data = json.loads(target.read_text())
        assert isinstance(data, list) and data, f"{target} must be a non-empty JSON list"
        for entry in data:
            assert set(entry) == _MANIFEST_KEYS, f"{target} entry must carry only {_MANIFEST_KEYS}"
        blob = target.read_text()
        assert "wake:" not in blob and "disposition:" not in blob and "service" not in blob, (
            f"{target} must never carry a service string"
        )


def test_ui_manifest_matches_the_catalog_exactly() -> None:
    """The manifest is a projection of the SAME typed source — one entry per capability,
    label/output faithful — so the 'what can you do?' answer can never drift from it."""
    from contracts.capabilities import CAPABILITIES
    from contracts.gen_ui_manifest import build_manifest

    manifest = {entry["id"]: entry for entry in build_manifest()}
    assert set(manifest) == set(CAPABILITIES), "one manifest entry per catalog capability"
    for cap_id, cap in CAPABILITIES.items():
        entry = manifest[cap_id]
        assert entry["label"] == cap.label
        assert entry["output"] == cap.output.value
        assert set(entry["surfaces"]) == set(cap.surfaces)


# ── there is NO runtime GET /capabilities endpoint (cut in CANONICAL §6) ──────
def test_no_capabilities_endpoint_on_the_control_plane_app() -> None:
    """CANONICAL §6 cut the dynamic HTTP catalog: no GET /capabilities route exists on
    the REAL control_plane app. The manifest is generated at build, never fetched."""
    from control_plane.app import create_app

    app = create_app()
    for route in app.routes:
        path = getattr(route, "path", "")
        assert "capabilities" not in path.lower(), (
            f"a runtime capabilities route must NOT exist; found {path!r}"
        )


def test_no_capabilities_route_registered_anywhere_in_the_app() -> None:
    """Belt-and-suspenders: no route on the app matches GET /capabilities in any form."""
    from control_plane.app import create_app

    app = create_app()
    routes = {
        f"{sorted(getattr(r, 'methods', []) or [])} {getattr(r, 'path', '')}" for r in app.routes
    }
    for key in routes:
        assert "/capabilities" not in key, f"no /capabilities route may exist; found {key!r}"

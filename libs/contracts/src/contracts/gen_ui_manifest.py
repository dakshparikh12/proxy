"""libs.contracts.gen_ui_manifest — the build step that emits the UI label manifest.

Doc 08 §4.7 + CANONICAL §12.5. The UI never imports the Python catalog; instead this
build step projects the typed :data:`contracts.capabilities.CAPABILITIES` down to a small
JSON manifest of ``{id, label, output, surfaces}`` **only** — the internal ``service:``
bindings (``wake:…`` / ``disposition:…``), the ``actions`` set, and the per-surface
``renderer`` config are dropped, so they **never ship to the browser** (the
service-string-in-TS fix). The tile / connect / ``/m/`` Vite apps import the generated
file, never this module.

Run as a build step::

    python -m contracts.gen_ui_manifest

which writes ``capabilities.manifest.json`` into ``apps/connect`` and ``apps/tile``. The
manifest is generated at build, never fetched at runtime — there is no ``GET /capabilities``
endpoint and therefore no drift path. Because both the backend authorization (``allowed_on``,
§4.3/§4.4) and this manifest derive from the ONE typed source, the "what can you do?" answer
(§2.4 #10) and the consent-line grounding status can never drift from it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .capabilities import CAPABILITIES, Capability

# The ONLY keys that reach the browser. `service`, `actions`, and `renderer` are backend-only.
_UI_KEYS: tuple[str, ...] = ("id", "label", "output", "surfaces")

# The apps whose Vite builds import the generated manifest (the tile + connect + /m home).
_MANIFEST_TARGETS: tuple[str, ...] = ("apps/connect", "apps/tile")
_MANIFEST_FILENAME = "capabilities.manifest.json"


def _ui_entry(cap: Capability) -> dict[str, Any]:
    """Project one Capability to its UI-facing shape — {id,label,output,surfaces} ONLY.

    ``surfaces`` is a ``frozenset`` (not JSON-serializable) so it is rendered as a sorted
    list of strings; ``output`` is the enum's string value. NOTHING else — the ``service``
    binding, the ``actions`` set, and the ``renderer`` config are deliberately omitted so
    no backend-only string can reach the browser.
    """
    return {
        "id": cap.id,
        "label": cap.label,
        "output": cap.output.value,
        "surfaces": sorted(cap.surfaces),
    }


def build_manifest() -> list[dict[str, Any]]:
    """The UI manifest — one ``{id,label,output,surfaces}`` entry per capability.

    A pure projection of the typed source; carries no ``service:``/``renderer``/``actions``
    field, so it is safe to ship to the browser verbatim.
    """
    manifest = [_ui_entry(cap) for cap in CAPABILITIES.values()]
    # Defensive floor: assert the projection carries EXACTLY the UI keys, so a future
    # edit that widens `_ui_entry` fails here rather than leaking a service-string to the UI.
    for entry in manifest:
        extra = set(entry) - set(_UI_KEYS)
        if extra:
            raise RuntimeError(f"UI manifest entry {entry.get('id')!r} carries backend-only fields: {extra}")
    return manifest


def _repo_root() -> Path:
    """The workspace root — this file lives at ``libs/contracts/src/contracts/`` (4 up)."""
    return Path(__file__).resolve().parents[4]


def write_manifest(root: Path | None = None) -> list[Path]:
    """Write the manifest into every app target; return the paths written."""
    root = root or _repo_root()
    payload = json.dumps(build_manifest(), indent=2, sort_keys=True) + "\n"
    written: list[Path] = []
    for target in _MANIFEST_TARGETS:
        dest = root / target / _MANIFEST_FILENAME
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(payload)
        written.append(dest)
    return written


def main() -> int:
    """Build-step entrypoint: generate the manifest into apps/connect + apps/tile."""
    for path in write_manifest():
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

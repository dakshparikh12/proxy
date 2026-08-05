"""The thin pre-meeting orchestrator: mint → clone → map-build → store → verify → ready.

One pass over a tenant's repo produces the durable ``index.md`` map. The stages compose the
already-tested modules; this module owns only the sequencing + the honest readiness verdict.

  1. **mint** a fresh installation token (:mod:`premeeting.github_auth`) — through the
     ``call_external`` seam, never cached, never logged; a mint failure → honest ``not_ready``.
  2. **clone** to the tenant volume (:mod:`premeeting.cloner`) — bare/blobless/read-only,
     token redacted from the recorded argv, secrets scanned.
  3. **map-build** the bounded one-agent loop (:mod:`premeeting.map_build`) → ``index.md``.
  4. **store** the map durably (:mod:`premeeting.map_store` → Postgres ``repo_maps``).
  5. **verify** deterministically (:mod:`premeeting.verify`) — ``ready`` ONLY on a clean pass;
     any gap yields ``not_ready`` NAMING it (Law 1/2).

Readiness progresses ``connecting → cloning → indexing → ready`` (map-build maps onto the
existing ``indexing`` state — there is deliberately NO ``mapping`` state, per the canonical
Readiness enum). A stage failure is surfaced as an honest ``not_ready`` reason, never a silent
success — the connect trigger writes the terminal state from :attr:`PipelineResult`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .cloner import Cloner
from .exclusions import ExclusionManager
from .github_auth import AuthError, InstallationTokenMinter
from .gitio import head_sha
from .map_build import MapBuildResult, build_understanding_map
from .paths import repo_name_from_url
from .verify import VerifyResult, verify_map


@dataclass
class PipelineResult:
    """The outcome of one pre-meeting pass — the honest terminal readiness + provenance."""

    ready: bool
    repo: str
    sha: str = ""
    reasons: list[str] = field(default_factory=list)
    clone_path: Path | None = None
    map_text: str = ""
    degraded: bool = False
    states: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "ready" if self.ready else "not_ready"


class _NullListener:
    def emit(self, state: str) -> None:  # pragma: no cover - trivial
        pass


async def run_pipeline(
    *,
    tenant_id: str,
    repo_url: str,
    provider: Any,
    map_store: Any | None = None,
    minter: InstallationTokenMinter | None = None,
    installation_id: str | None = None,
    sha: str | None = None,
    model: str | None = None,
    readiness_listener: Any = None,
    exclusions: ExclusionManager | None = None,
    call: Any | None = None,
    oauth_token: str = "",
    sandbox_class: Any = None,
) -> PipelineResult:
    """Run one pre-meeting pass and return the honest terminal readiness (never raises out).

    ``provider`` is the (legacy) model seam, retained for signature compatibility. ``minter`` mints
    the clone token; when absent (a public fixture repo / a test), the clone runs unauthenticated.
    ``map_store`` persists the map (``MapStore``); when absent the map is produced + verified but not
    stored (still a valid ``ready`` verdict for a store-less test). Readiness states are emitted onto
    ``readiness_listener`` so the connect poll renders the live progression.

    ``call`` (the ``libs.http.call_external`` E2B seam) + ``oauth_token`` (the subscription
    ``CLAUDE_CODE_OAUTH_TOKEN``) turn on Part 2 — the bounded native-Claude holistic comprehension
    pass, verified against the real repo, combined with the Part-1 symbol map into ONE dense
    understanding document (:func:`premeeting.map_build.build_understanding_map`). Absent ⇒ the
    deterministic symbol map alone (a clean, complete degrade — Part 2 only ever adds). The SAME
    minted installation ``token`` that authenticates the Part-1 clone is threaded into the Part-2
    in-sandbox ``git clone`` (``github_token``) so a PRIVATE customer repo clones successfully there
    too — else the comprehension pass sees an empty repo and produces nothing."""
    listener = readiness_listener if readiness_listener is not None else _NullListener()
    repo = repo_name_from_url(repo_url)
    states: list[str] = []

    def _emit(state: str) -> None:
        states.append(state)
        try:
            listener.emit(state)
        except Exception:  # noqa: BLE001 - a listener blip never fails the pipeline
            pass

    _emit("connecting")

    # (1) mint — a fresh token per pass, never cached/logged. A mint failure → honest not_ready.
    token: str | None = None
    if minter is not None and installation_id:
        try:
            token = await minter.mint(installation_id)
        except AuthError as exc:
            return PipelineResult(
                ready=False, repo=repo, reasons=[f"auth: {exc}"], states=states
            )

    # (2) clone.
    _emit("cloning")
    em = exclusions if exclusions is not None else ExclusionManager()
    cloner = Cloner(exclusion_manager=em)
    clone_path = cloner.clone(tenant_id, repo_url, sha=sha, token=token)
    if not clone_path.exists() or not any(clone_path.iterdir()):
        return PipelineResult(
            ready=False, repo=repo, clone_path=clone_path,
            reasons=["the repository could not be cloned"], states=states,
        )
    resolved_sha = sha or head_sha(clone_path) or ""

    # (3) map-build (the 'indexing' state — no separate 'mapping' state). Part 1 always; Part 2 (the
    # verified holistic comprehension, combined into ONE dense doc) when the E2B seam + token are
    # present — else the symbol map alone (a clean degrade).
    _emit("indexing")
    build: MapBuildResult = await build_understanding_map(
        clone_path=clone_path, repo_name=repo, sha=resolved_sha, model=model, exclusions=em,
        call=call, token=oauth_token, sandbox_class=sandbox_class, repo_url=repo_url,
        github_token=token,
    )
    _ = provider  # legacy model seam retained for signature compatibility

    # (4) store durably (best-effort seam; absence is not a readiness failure by itself).
    if map_store is not None:
        try:
            await map_store.save(
                tenant_id=tenant_id, repo=repo, sha=resolved_sha, map_text=build.index_md
            )
        except Exception as exc:  # noqa: BLE001 - a store fault is an honest not_ready reason
            return PipelineResult(
                ready=False, repo=repo, sha=resolved_sha, clone_path=clone_path,
                map_text=build.index_md, degraded=build.degraded,
                reasons=[f"store: {type(exc).__name__}"], states=states,
            )

    # (5) verify — ready ONLY on a clean pass; any gap named.
    result: VerifyResult = verify_map(build.index_md, clone_path, exclusions=em)
    if result.ready:
        _emit("ready")
    return PipelineResult(
        ready=result.ready, repo=repo, sha=resolved_sha, reasons=list(result.reasons),
        clone_path=clone_path, map_text=build.index_md, degraded=build.degraded, states=states,
    )


__all__ = ["PipelineResult", "run_pipeline"]

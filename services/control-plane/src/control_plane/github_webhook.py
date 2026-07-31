"""The LIVE GitHub push-webhook ingress — the freshness spine's caller (Doc 01 §3.6).

The connect→index trigger builds a per-tenant :class:`code_intel.pipeline.Pipeline`
that carries a live freshness ``webhook_handler`` (:class:`code_intel.webhook_handler.
WebhookHandler`). That handler had NO live caller: an actual inbound GitHub push never
reached it, so the "push → delta-pull → drop+re-extract → invalidate → notify" path was
isolation-only. This module closes that gap — it is the ONE HTTP ingress that turns a
real GitHub push delivery into a ``WebhookHandler.handle`` call on the RIGHT tenant's
pipeline.

Two pieces:

  * :class:`LivePipelineRegistry` — a per-host, in-process map ``normalized-repo-url →
    Pipeline`` the connect trigger writes to (so a later push finds the live pipeline it
    must reindex). The pipeline (clone + graph + retention + warm resolver) is a
    rebuildable derived cache (CLAUDE.md §"Source of truth vs cache"), so an in-process
    registry is correct here — a cold instance simply has no pinned pipeline yet and the
    push is a benign no-op (200) until the connect trigger repopulates it.

  * ``POST /webhooks/github`` — PUBLIC_ROUTES-allowlisted but HMAC-gated. The
    ``X-Hub-Signature-256`` is verified over the RAW body via a constant-time compare
    (``libs.http.verify_github_signature``) BEFORE any rebuild is dispatched. A
    forged/missing signature is a 401 and triggers NO rebuild (AC-M7-001). The tenant is
    NEVER read from the request — it is resolved server-side from the signed payload's
    repository → the pipeline the connect trigger registered for that repo (Law 3 / the
    server-side tenant check: a client field can never authorize an entity).

The route never throws (the §4.6 never-throw boundary): a bad signature is a fixed 401,
a non-JSON body from an authenticated caller is its own 400, an unknown repo (no live
pipeline) is a benign 202 (accepted, nothing to reindex yet), and any dispatch fault is
absorbed so a push delivery can never 500 the ingress.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from starlette.requests import Request

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import FastAPI

# The route path — a single constant so the mount and the PUBLIC_ROUTES allowlist
# entry can never drift by a typo (mirrors RECALL_WEBHOOK_PATH).
GITHUB_WEBHOOK_PATH = "/webhooks/github"


def _normalize_repo_url(repo_url: str) -> str:
    """A stable registry key for a repo URL — trailing slash + ``.git`` stripped.

    A GitHub push payload may carry ``https://github.com/acme/repo`` (html_url) or
    ``https://github.com/acme/repo.git`` (clone_url); the connect trigger registers
    under whatever ``repo_url`` the install used. Normalising both to the same key
    lets a push find the pipeline the connect flow registered regardless of the
    ``.git`` suffix. Case is preserved (repo paths are case-sensitive on GitHub).
    """
    key = repo_url.strip().rstrip("/")
    if key.endswith(".git"):
        key = key[: -len(".git")]
    return key


class LivePipelineRegistry:
    """Per-host map ``normalized-repo-url → live Pipeline`` (the freshness ingress index).

    The connect→index trigger registers each tenant's built pipeline here; the
    ``/webhooks/github`` route looks it up by the push payload's repository to find the
    live ``webhook_handler`` it must drive. One repo maps to one pipeline (the latest
    connect for it); re-registering replaces the prior entry (a re-connect rebuilds the
    derived cache). This is per-host in-process state, not durable — the pipeline it
    points at IS the derived cache, so a cold instance rebuilds via a fresh connect.
    """

    def __init__(self) -> None:
        self._by_repo: dict[str, Any] = {}

    def register(self, repo_url: str, pipeline: Any) -> None:
        """Bind a repo to its live pipeline (server-side; the push caller never sets this)."""
        if repo_url and pipeline is not None:
            self._by_repo[_normalize_repo_url(repo_url)] = pipeline

    def get_by_repo(self, repo_url: str) -> Any:
        """The live pipeline for a repo, or ``None`` if this host has none registered yet."""
        return self._by_repo.get(_normalize_repo_url(repo_url))

    def __len__(self) -> int:
        return len(self._by_repo)


def get_pipeline_registry(app: Any) -> LivePipelineRegistry:
    """The single :class:`LivePipelineRegistry` bound to this app (created on first access).

    Both the connect trigger (which REGISTERS a built pipeline) and the ``/webhooks/github``
    route (which RESOLVES one) read the SAME registry off ``app.state`` — the seam that lets
    a real push reach the pipeline the connect flow built for that repo.
    """
    registry = getattr(app.state, "pipeline_registry", None)
    if registry is None:
        registry = LivePipelineRegistry()
        app.state.pipeline_registry = registry
    return registry


@dataclass
class _PushWebhook:
    """The duck-typed push event :meth:`WebhookHandler.handle` reads (§3.6).

    Mirrors the attributes the handler accesses (``kind``/``signature_valid``/
    ``delivery_guid``/``sha``/``repo_url``/``changed_files``/``num_commits``). Built
    server-side from the VERIFIED payload — ``signature_valid`` is always True here
    because a delivery only reaches this object AFTER the HMAC verified (a forged one is
    401'd upstream and never constructs a webhook).
    """

    kind: str
    repo_url: str
    sha: str
    delivery_guid: str
    changed_files: list[str] = field(default_factory=list)
    num_commits: int = 1
    signature_valid: bool = True
    forced: bool = False


def _changed_files_from_commits(payload: dict[str, Any]) -> list[str]:
    """The union of added/modified/removed paths across the push's commits (§3.6).

    GitHub's push payload lists per-commit ``added``/``modified``/``removed`` arrays.
    The union is the push's ``changed_files`` the delta-pull carries so a newly-changed
    secret file is re-scanned/excluded (AC-M7-008). Order is preserved and de-duplicated.
    """
    seen: set[str] = set()
    files: list[str] = []
    for commit in payload.get("commits") or []:
        if not isinstance(commit, dict):
            continue
        for bucket in ("added", "modified", "removed"):
            for path in commit.get(bucket) or []:
                if isinstance(path, str) and path not in seen:
                    seen.add(path)
                    files.append(path)
    return files


def _repo_url_from_payload(payload: dict[str, Any]) -> str:
    """The repository URL the push is for — the server-side tenant-resolution key.

    Prefers ``repository.clone_url`` then ``html_url`` then ``ssh_url``; all normalise to
    the same registry key. This is read from the SIGNED payload (verified above), never a
    request field the caller could forge to reindex another tenant's repo.
    """
    repo = payload.get("repository")
    if not isinstance(repo, dict):
        return ""
    for key in ("clone_url", "html_url", "url", "ssh_url"):
        value = repo.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _push_webhook_from_payload(
    payload: dict[str, Any], delivery_guid: str
) -> _PushWebhook | None:
    """Build the ``_PushWebhook`` from a verified GitHub push payload (or None if not a push).

    A non-push event (no ``after`` sha / no repository) is ignored — only a push drives a
    freshness rebuild. ``num_commits`` is the commit count; ``forced`` flags a non-fast-
    forward (force) push so the handler/pipeline take the full-rebuild path (never an
    incremental delta-apply over rewritten history, AC-M4-013).
    """
    repo_url = _repo_url_from_payload(payload)
    after = payload.get("after")
    if not repo_url or not isinstance(after, str) or not after:
        return None
    commits = payload.get("commits") or []
    num_commits = len(commits) if isinstance(commits, list) and commits else 1
    return _PushWebhook(
        kind="push",
        repo_url=repo_url,
        sha=after,
        delivery_guid=delivery_guid,
        changed_files=_changed_files_from_commits(payload),
        num_commits=num_commits,
        forced=bool(payload.get("forced")),
    )


def _maybe_refresh_map(app: Any, webhook: Any) -> None:
    """Drive the pre-meeting map refresh on a verified push (additive, guarded, never raises).

    Resolves the map model provider + durable store + the push's tenant off ``app.state`` (a
    funded deployment wires them; absent, this no-ops — the map is credit-blocked, D-032, and is
    never fabricated). Runs the async ``premeeting.refresh.refresh_on_push`` on a fresh loop; any
    fault is swallowed so a push can never 500 the ingress (§4.6 never-throw)."""
    provider = getattr(app.state, "map_provider", None)
    map_store = getattr(app.state, "map_store", None)
    tenant_id = getattr(app.state, "map_tenant_resolver", None)
    if provider is None:
        return  # honest no-op: the map-build seam is unfunded (D-032) — no loop touched
    try:
        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        from premeeting.refresh import refresh_on_push

        tid = tenant_id(webhook.repo_url) if callable(tenant_id) else getattr(webhook, "tenant_id", "")
        if not tid:
            return

        def _run() -> None:
            # Own loop in a worker thread so the ingress's request loop is never disturbed.
            asyncio.run(
                refresh_on_push(
                    tenant_id=str(tid),
                    repo_url=webhook.repo_url,
                    provider=provider,
                    map_store=map_store,
                    changed_files=list(getattr(webhook, "changed_files", []) or []),
                )
            )

        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(_run).result()
    except Exception:  # noqa: BLE001 - never-throw ingress; the map refresh is best-effort
        return


def _github_webhook_secret() -> str:
    """The GitHub-App webhook signing secret from Secret Manager via settings.

    Read at request time (not import time) so a rotated secret is picked up. An unset
    secret is an empty string, which makes :func:`verify_github_signature` fail CLOSED
    (401) — an unverifiable delivery is never accepted and never triggers a rebuild.
    """
    try:
        from control_plane.settings import Settings

        return str(Settings().github_webhook_secret)
    except Exception:  # pragma: no cover - settings unavailable ⇒ fail closed below
        import os

        return os.environ.get("GITHUB_WEBHOOK_SECRET", "")


def install_github_webhook_route(app: "FastAPI") -> None:
    """Mount ``POST /webhooks/github`` — the LIVE freshness push ingress (§3.6/§4.6).

    HMAC-gated + PUBLIC_ROUTES-allowlisted: the ``X-Hub-Signature-256`` is verified over
    the RAW body BEFORE any rebuild is dispatched, so a forged/missing signature is a 401
    that triggers NO rebuild (AC-M7-001). A verified push is routed — by its SIGNED
    repository, resolved server-side to the pipeline the connect trigger registered — to
    that pipeline's ``webhook_handler.handle``, which dedups (AC-M7-002), pulls the delta
    once carrying changed_files (AC-M7-008), does a full drop+re-extract, invalidates
    caches, and notifies live meetings. The tenant is NEVER read from the request.

    Never throws (§4.6): a bad signature is a fixed 401; a non-JSON body is a 400; an
    unknown-to-this-host repo (no registered pipeline) is a benign 202 (nothing to
    reindex yet); any dispatch fault is absorbed so a push can never 500 the ingress.
    """
    import json

    from fastapi import HTTPException
    from starlette.responses import JSONResponse

    from libs.http import WebhookVerificationError, verify_github_signature

    # Ensure the registry exists on app.state at mount time; handlers resolve it
    # per-request off ``request.app.state`` (never a mount-time closure capture).
    get_pipeline_registry(app)

    @app.post(GITHUB_WEBHOOK_PATH, include_in_schema=True)
    async def github_webhook(request: Request) -> Any:
        # Read the RAW body FIRST — the signature is over these exact bytes. Verify BEFORE
        # any parse/dispatch so a forged delivery is refused and NEVER triggers a rebuild.
        raw_body = await request.body()
        secret = _github_webhook_secret()
        try:
            verify_github_signature(secret, headers=request.headers, raw_body=raw_body)
        except WebhookVerificationError as exc:
            # Fail CLOSED — 401, no rebuild. The detail is for our logs only; safeError
            # collapses it to the fixed Unauthorized body (no internal leak).
            raise HTTPException(status_code=401, detail=exc.detail) from exc

        # Signature proven ⇒ safe to parse. A non-JSON body from an authenticated caller
        # is a 400 (its own bad input), never a 500.
        try:
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=400, detail="invalid body") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="invalid body")

        delivery_guid = request.headers.get("x-github-delivery", "") or ""
        webhook = _push_webhook_from_payload(payload, delivery_guid)
        if webhook is None:
            # A non-push event (ping, etc.) — accepted, nothing to reindex.
            return JSONResponse({"status": "ignored"}, status_code=202)

        registry = get_pipeline_registry(request.app)
        # Server-side tenant resolution: the repo comes from the SIGNED payload, and the
        # pipeline was registered by the connect trigger — the client never names a tenant.
        pipeline = registry.get_by_repo(webhook.repo_url)
        if pipeline is None or getattr(pipeline, "webhook_handler", None) is None:
            # No live pipeline on THIS host for that repo yet (cold instance / not
            # connected here) — accept the delivery; a fresh connect rebuilds the cache.
            return JSONResponse({"status": "accepted"}, status_code=202)

        # Drive the freshness handler — dedup + delta-pull-once + drop/re-extract +
        # invalidate + notify. Absorb any fault so the ingress never 500s a push.
        try:
            pipeline.webhook_handler.handle(webhook)
        except Exception:  # noqa: BLE001 - never-throw ingress; the rebuild is best-effort
            return JSONResponse({"status": "accepted"}, status_code=202)

        # PRE-MEETING MAP REFRESH (additive): the SAME verified push drives a delta-pull +
        # map re-build + re-store + re-verify for THAT repo (PM-REFRESH-01). Additive to the
        # graph rebuild above; guarded so a map-refresh fault never 500s the ingress. The
        # map-build model seam is credit-blocked (D-032), so this no-ops honestly unless a
        # funded provider + map store are wired onto ``app.state`` (never a fabricated map).
        _maybe_refresh_map(request.app, webhook)
        return JSONResponse({"status": "ok"}, status_code=200)

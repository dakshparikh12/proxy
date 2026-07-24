"""Webhook freshness dispatch — HMAC gate, dedup, delta-pull vs uninstall (M7).

A bad HMAC is refused (401, no rebuild); a duplicate delivery (same GUID+SHA) is
deduplicated to exactly one rebuild; a valid push triggers a *delta pull* (never a
re-clone) then a full graph rebuild; an uninstall hard-deletes the tenant's clone,
graph, and coverage.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class WebhookResponse:
    status_code: int
    enqueued: bool


class WebhookHandler:
    def __init__(
        self,
        cloner: Any = None,
        server: Any = None,
        pipeline: Any = None,
        rebuild_counter: Any = None,
        git_interceptor: Any = None,
    ) -> None:
        self._cloner = cloner
        self._server = server
        self._pipeline = pipeline
        self._rebuild_counter = rebuild_counter
        self._git_interceptor = git_interceptor
        self._seen: set[tuple[str, str]] = set()

    def _resolved_pipeline(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline
        if self._server is not None:
            return getattr(self._server, "pipeline", None)
        return None

    def handle(self, webhook: Any) -> WebhookResponse:
        if getattr(webhook, "kind", "push") == "uninstall":
            return self._handle_uninstall(webhook)
        if not getattr(webhook, "signature_valid", True):
            return WebhookResponse(status_code=401, enqueued=False)
        key = (getattr(webhook, "delivery_guid", ""), getattr(webhook, "sha", ""))
        if key in self._seen:
            return WebhookResponse(status_code=200, enqueued=True)
        self._seen.add(key)
        self._process_push(webhook)
        return WebhookResponse(status_code=200, enqueued=True)

    def _process_push(self, webhook: Any) -> None:
        # The handler owns THE pull when it has a cloner: it carries the push's
        # changed_files so scan_after_pull excludes newly-changed secret files.
        handler_pulled = self._cloner is not None
        if handler_pulled:
            self._cloner.pull_delta(
                repo_url=getattr(webhook, "repo_url", None),
                changed_files=getattr(webhook, "changed_files", None),
            )
        pipeline = self._resolved_pipeline()
        if pipeline is not None:
            # Don't let apply_push re-pull (redundant git fetch, and it would
            # re-scan with changed_files=None) when we already pulled. Exactly one
            # delta pull happens per push, and it carries changed_files (AC-M7-008).
            pipeline.apply_push(
                getattr(webhook, "sha", "") or "",
                getattr(webhook, "num_commits", 1),
                pull=not handler_pulled,
            )
        elif self._server is not None:
            self._server.invalidate_caches()
        if self._rebuild_counter is not None:
            self._rebuild_counter.record()

    def _handle_uninstall(self, webhook: Any) -> WebhookResponse:
        pipeline = self._resolved_pipeline()
        if pipeline is not None and hasattr(pipeline, "uninstall_delete"):
            pipeline.uninstall_delete()
        return WebhookResponse(status_code=200, enqueued=True)

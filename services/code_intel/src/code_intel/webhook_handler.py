"""Webhook freshness dispatch — HMAC gate, dedup, delta-pull vs uninstall (M7).

A bad HMAC is refused (401, no rebuild); a duplicate delivery (same GUID+SHA) is
deduplicated to exactly one rebuild; a valid push triggers a *delta pull* (never a
re-clone) then a full graph rebuild; an uninstall hard-deletes the tenant's clone,
graph, and coverage.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

# Recent-duplicate suppression only needs a bounded window. On a long-lived
# stateful code_intel host (GCE per Doc 00 deployables) an unbounded dedup set
# would leak memory proportional to total pushes over the process lifetime. We
# retain the last WEBHOOK_DEDUP_MAXLEN (delivery_guid, sha) keys as an LRU; older
# keys evict. GitHub redelivers within minutes, so this window is ample.
WEBHOOK_DEDUP_MAXLEN = 2048


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
        dedup_maxlen: int = WEBHOOK_DEDUP_MAXLEN,
    ) -> None:
        self._cloner = cloner
        self._server = server
        self._pipeline = pipeline
        self._rebuild_counter = rebuild_counter
        self._git_interceptor = git_interceptor
        # Bounded LRU of recently-seen (delivery_guid, sha) keys. OrderedDict is
        # used as an LRU: seeing a key moves it to the most-recent end; when the
        # map exceeds dedup_maxlen the least-recent key is evicted. The value is
        # unused (a set of keys), so we store None.
        self._dedup_maxlen = max(1, dedup_maxlen)
        self._seen: "OrderedDict[tuple[str, str], None]" = OrderedDict()

    def dedup_size(self) -> int:
        """Number of retained dedup keys (bounded by dedup_maxlen)."""
        return len(self._seen)

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
            # Recent duplicate: suppress rebuild and refresh its LRU recency so a
            # redelivered-again key isn't prematurely evicted.
            self._seen.move_to_end(key)
            return WebhookResponse(status_code=200, enqueued=True)
        self._seen[key] = None
        # Evict least-recently-seen keys past the bound (memory stays O(maxlen)).
        while len(self._seen) > self._dedup_maxlen:
            self._seen.popitem(last=False)
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

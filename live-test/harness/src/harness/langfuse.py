"""Langfuse trace client — the monitor's KEY input (the acceptance grader).

The running Proxy agent emits its wake traces to Langfuse. For a given time
window we pull the trace(s) via Langfuse's REST API and normalize each into a
``TraceView`` that answers the acceptance questions directly:

* **tools called** — the exact tool/observation names, in order.
* **reads vs. resident-cache** — did it answer from cache (zero file reads) or do
  a file read, and WHERE it looked (the read targets).
* **thinking** — the model's reasoning spans (for the "why" of the routing).
* **timing** — per-observation and end-to-end **millisecond** latency.

Every HTTP round-trip rides the injected ``call_external`` seam (the repo's hard
rule: no raw vendor client outside ``libs.http``); the raw client is built by
``libs.http.http_client``. An offline test injects a ``fetch`` stub returning a
faked Langfuse payload, so the whole normalization is exercised with no network.

Langfuse REST (public API, confirmed shape): HTTP Basic auth
(``public_key`` : ``secret_key``); ``GET /api/public/traces`` lists traces
filtered by ``fromTimestamp`` / ``toTimestamp`` (ISO-8601), returning
``{"data": [ {id, name, timestamp, ...} ]}``; ``GET /api/public/traces/{id}``
returns the full trace incl. its ``observations`` (each with ``type``, ``name``,
``startTime``, ``endTime``, ``input``, ``output``).
"""
from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# Observation ``type`` values Langfuse emits; a tool call rides ``SPAN``/``TOOL``
# and reasoning rides ``GENERATION`` — used to classify what the agent DID.
_TOOL_TYPES = {"SPAN", "TOOL", "EVENT"}
_THINKING_TYPES = {"GENERATION"}
# Tool names (case-insensitive substring) that touch the filesystem = a real
# read (NOT a resident-cache answer). Matches the product's file tools + shell
# ``cat``/``grep``/``rg``-style reads.
_READ_TOOL_MARKERS = ("read", "cat ", "grep", "glob", "open(", "ripgrep", "rg ", "view")

#: A round-trip fetcher: (method, path, params) -> parsed JSON. Injected so an
#: offline test hands back a faked Langfuse payload with no network.
Fetch = Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class Observation:
    """One normalized Langfuse observation (a tool call or a reasoning span)."""

    name: str
    type: str
    start_ms: float | None
    end_ms: float | None
    input: Any = None
    output: Any = None

    @property
    def duration_ms(self) -> float | None:
        if self.start_ms is None or self.end_ms is None:
            return None
        return self.end_ms - self.start_ms

    @property
    def is_read(self) -> bool:
        """Does this observation touch a file (a read, not a cache answer)?"""
        if self.type not in _TOOL_TYPES:
            return False
        haystack = f"{self.name} {self.input}".lower()
        return any(marker in haystack for marker in _READ_TOOL_MARKERS)


@dataclass(frozen=True)
class TraceView:
    """The acceptance-grading view of one wake's trace."""

    trace_id: str
    name: str
    start_ms: float | None
    end_ms: float | None
    observations: tuple[Observation, ...] = field(default_factory=tuple)

    @property
    def tools_called(self) -> tuple[str, ...]:
        """Tool/observation names in order (the ROUTING + PROCESS evidence)."""
        return tuple(o.name for o in self.observations if o.type in _TOOL_TYPES)

    @property
    def read_count(self) -> int:
        """How many file reads happened (0 ⇒ answered from resident cache)."""
        return sum(1 for o in self.observations if o.is_read)

    @property
    def answered_from_cache(self) -> bool:
        """Zero file reads ⇒ answered from the resident cache (the payoff check)."""
        return self.read_count == 0

    @property
    def read_targets(self) -> tuple[str, ...]:
        """WHERE it looked — the inputs of the read observations."""
        return tuple(str(o.input) for o in self.observations if o.is_read)

    @property
    def thinking(self) -> tuple[str, ...]:
        """The reasoning spans' outputs (the "why" behind the routing)."""
        return tuple(
            str(o.output) for o in self.observations if o.type in _THINKING_TYPES and o.output
        )

    @property
    def latency_ms(self) -> float | None:
        """End-to-end wake latency in milliseconds (from the trace timing)."""
        if self.start_ms is None or self.end_ms is None:
            return None
        return self.end_ms - self.start_ms

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serializable summary for the stored monitoring bundle."""
        return {
            "trace_id": self.trace_id,
            "name": self.name,
            "latency_ms": self.latency_ms,
            "tools_called": list(self.tools_called),
            "read_count": self.read_count,
            "answered_from_cache": self.answered_from_cache,
            "read_targets": list(self.read_targets),
            "thinking": list(self.thinking),
            "observations": [
                {
                    "name": o.name,
                    "type": o.type,
                    "duration_ms": o.duration_ms,
                    "is_read": o.is_read,
                }
                for o in self.observations
            ],
        }


def _ms(value: Any) -> float | None:
    """Parse a Langfuse ISO-8601 timestamp to epoch milliseconds."""
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).timestamp() * 1000.0
    except ValueError:
        return None


def _normalize_observation(raw: dict[str, Any]) -> Observation:
    return Observation(
        name=str(raw.get("name") or raw.get("id") or ""),
        type=str(raw.get("type") or "").upper(),
        start_ms=_ms(raw.get("startTime")),
        end_ms=_ms(raw.get("endTime")),
        input=raw.get("input"),
        output=raw.get("output"),
    )


def normalize_trace(raw: dict[str, Any]) -> TraceView:
    """Turn a raw Langfuse trace payload into the acceptance-grading view."""
    obs = tuple(_normalize_observation(o) for o in raw.get("observations", []))
    starts = [o.start_ms for o in obs if o.start_ms is not None]
    ends = [o.end_ms for o in obs if o.end_ms is not None]
    start_ms = _ms(raw.get("timestamp")) or (min(starts) if starts else None)
    end_ms = max(ends) if ends else None
    return TraceView(
        trace_id=str(raw.get("id") or ""),
        name=str(raw.get("name") or ""),
        start_ms=start_ms,
        end_ms=end_ms,
        observations=obs,
    )


class LangfuseClient:
    """Pulls wake traces for a time window and normalizes them (behind the seam)."""

    def __init__(
        self,
        *,
        base_url: str,
        public_key: str,
        secret_key: str,
        fetch: Fetch | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._public_key = public_key
        self._secret_key = secret_key
        # A test injects ``fetch``; the live path builds the real seam-backed one.
        self._fetch: Fetch = fetch if fetch is not None else self._real_fetch

    def _auth_header(self) -> str:
        token = base64.b64encode(f"{self._public_key}:{self._secret_key}".encode()).decode("ascii")
        return f"Basic {token}"

    async def _real_fetch(self, method: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """The sole raw Langfuse round-trip — issued ONLY via ``call_external``.

        The raw httpx client is built by ``libs.http.http_client`` (the single
        raw-client home); this call site rides the retry+cost seam. Imported
        lazily so the offline test path (which injects ``fetch``) never touches
        ``libs.http``.
        """
        from libs.http.src.http.external import call_external, http_client

        async def op() -> dict[str, Any]:
            headers = {"Authorization": self._auth_header()}
            async with http_client(timeout=30.0) as client:
                resp = await client.request(
                    method, f"{self._base_url}{path}", headers=headers, params=params
                )
                resp.raise_for_status()
                payload: dict[str, Any] = resp.json()
                return payload

        outcome = await call_external(op, service="langfuse")
        # ``call_external`` returns an ``ExternalCallOutcome`` whose ``.value`` is
        # the op's result (the parsed JSON body).
        result: dict[str, Any] = outcome.value
        return result

    async def traces_in_window(
        self, from_iso: str, to_iso: str, *, name: str | None = None
    ) -> list[TraceView]:
        """All traces whose timestamp falls in ``[from_iso, to_iso]`` (normalized).

        Each listed trace is re-fetched by id so its observations (the tools +
        reads + timing the acceptance is graded on) are present — the list
        endpoint returns trace headers only.
        """
        params: dict[str, Any] = {"fromTimestamp": from_iso, "toTimestamp": to_iso}
        if name:
            params["name"] = name
        listing = await self._fetch("GET", "/api/public/traces", params)
        views: list[TraceView] = []
        for header in listing.get("data", []):
            trace_id = str(header.get("id") or "")
            if not trace_id:
                continue
            full = await self._fetch("GET", f"/api/public/traces/{trace_id}", {})
            views.append(normalize_trace(full))
        return views

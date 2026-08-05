"""DID (the process/trace) — the PRIMARY monitor source.

The warm Proxy session in ``services/in-meeting/src/in_meeting/session_host.py``
writes a per-turn RECORD to ``$PROXY_WAKE_OUT/<wake_id>.json`` (atomically: temp
+ rename, so a poll never sees a half-file). That record — NOT Langfuse (which is
only thinly wired here) — is the acceptance grader. Its schema (from
``_run_turn`` → ``_write_result``)::

    {
      "tools":      [str, ...],   # every tool the agent called this turn, in order
      "text":       str,          # the final result prose
      "cost_usd":   float,
      "turns":      int,
      "error":      str | None,   # honest per-turn fault (never a crash)
      "deliver_at": float,        # seconds query→first to_meeting (delivery latency)
      "ttft":       float,        # seconds query→first text delta (model TTFT)
      "sent":       [{content, medium, to}, ...],  # the agent's OWN channel choices
      "_served_at": float,        # epoch when the host wrote it
    }

Reads-vs-resident-cache is read off ``tools``: file/search tools (Read/Grep/Glob/
Bash-cat) = a real DISK read (it looked something up); ZERO of them ⇒ it answered
from the RESIDENT cache. The model's cache engagement (``cache_read`` /
``cache_write`` tokens) is printed by the host to stdout as ``[usage] ...`` lines
— parsed here from the run.log to confirm the resident prefix is actually reused
and growing turn-over-turn.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Tool names that touch the filesystem / search = a real DISK read this turn.
# (The product's file tools + shell reads; ``to_meeting`` is a DELIVERY channel,
# never a read.) Matched case-insensitively as a whole-name or prefix.
_READ_TOOLS = ("read", "grep", "glob", "cat", "ripgrep", "rg", "view", "ls", "find")
# Search/inspection Bash reads embed the command; a bare "Bash" is inspected via
# its recorded name only (the record stores tool NAMES, not args).
_DELIVERY_TOOL = "to_meeting"

_USAGE_RE = re.compile(
    r"\[usage\]\s+cache_read=(?P<cr>\d+)\s+cache_write=(?P<cw>\d+)"
    r"\s+input=(?P<in>\d+)\s+output=(?P<out>\d+)"
)


def _is_read_tool(name: str) -> bool:
    low = name.strip().lower()
    if _DELIVERY_TOOL in low:
        return False
    return any(low == t or low.startswith(t) for t in _READ_TOOLS)


@dataclass(frozen=True)
class SentIntent:
    """One ``to_meeting`` channel choice the agent made this turn (OUTPUT)."""

    content: str
    medium: str  # say / chat / dm / screen / offer / mute
    to: str = ""


@dataclass(frozen=True)
class UsageSample:
    """One ``[usage]`` line from the run.log — the model's cache engagement."""

    cache_read: int
    cache_write: int
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class TurnRecord:
    """One wake's per-turn record — the DID acceptance view."""

    wake_id: str
    tools: tuple[str, ...]
    text: str
    turns: int
    cost_usd: float
    error: str | None
    deliver_at_s: float
    ttft_s: float
    sent: tuple[SentIntent, ...]
    served_at: float
    usage: UsageSample | None = None

    # -- reads vs. resident-cache --------------------------------------------

    @property
    def read_tools(self) -> tuple[str, ...]:
        """The tools that hit disk this turn (Read/Grep/Glob/cat/...)."""
        return tuple(t for t in self.tools if _is_read_tool(t))

    @property
    def read_count(self) -> int:
        return len(self.read_tools)

    @property
    def answered_from_cache(self) -> bool:
        """ZERO file reads ⇒ answered from the RESIDENT cache (the payoff check)."""
        return self.read_count == 0

    @property
    def deliver_at_ms(self) -> float:
        return round(self.deliver_at_s * 1000.0, 1)

    @property
    def ttft_ms(self) -> float:
        return round(self.ttft_s * 1000.0, 1)

    @property
    def mediums(self) -> tuple[str, ...]:
        """The channels the agent routed to this turn (say/chat/dm/screen/offer/mute)."""
        return tuple(s.medium for s in self.sent)

    def to_dict(self) -> dict[str, Any]:
        return {
            "wake_id": self.wake_id,
            "tools": list(self.tools),
            "read_tools": list(self.read_tools),
            "read_count": self.read_count,
            "answered_from_cache": self.answered_from_cache,
            "turns": self.turns,
            "cost_usd": self.cost_usd,
            "error": self.error,
            "ttft_ms": self.ttft_ms,
            "deliver_at_ms": self.deliver_at_ms,
            "mediums": list(self.mediums),
            "sent": [{"content": s.content, "medium": s.medium, "to": s.to} for s in self.sent],
            "text": self.text,
            "usage": (
                None
                if self.usage is None
                else {
                    "cache_read": self.usage.cache_read,
                    "cache_write": self.usage.cache_write,
                    "input_tokens": self.usage.input_tokens,
                    "output_tokens": self.usage.output_tokens,
                }
            ),
        }


def parse_record(wake_id: str, raw: dict[str, Any]) -> TurnRecord:
    """Normalize one raw ``<wake_id>.json`` record into a :class:`TurnRecord`."""
    sent = tuple(
        SentIntent(
            content=str(s.get("content", "") or ""),
            medium=str(s.get("medium", "say") or "say"),
            to=str(s.get("to", "") or ""),
        )
        for s in raw.get("sent", [])
        if isinstance(s, dict)
    )
    return TurnRecord(
        wake_id=wake_id,
        tools=tuple(str(t) for t in raw.get("tools", [])),
        text=str(raw.get("text", "") or ""),
        turns=int(raw.get("turns", 0) or 0),
        cost_usd=float(raw.get("cost_usd", 0.0) or 0.0),
        error=(str(raw["error"]) if raw.get("error") else None),
        deliver_at_s=float(raw.get("deliver_at", 0.0) or 0.0),
        ttft_s=float(raw.get("ttft", 0.0) or 0.0),
        sent=sent,
        served_at=float(raw.get("_served_at", 0.0) or 0.0),
    )


def parse_usage_lines(run_log_text: str) -> list[UsageSample]:
    """Every ``[usage] ...`` line from the host's stdout, in order.

    A growing ``cache_read`` turn-over-turn is the residency proof (the resident
    prefix is reused, not re-parsed each wake).
    """
    samples: list[UsageSample] = []
    for m in _USAGE_RE.finditer(run_log_text):
        samples.append(
            UsageSample(
                cache_read=int(m.group("cr")),
                cache_write=int(m.group("cw")),
                input_tokens=int(m.group("in")),
                output_tokens=int(m.group("out")),
            )
        )
    return samples


@dataclass(frozen=True)
class RecordWindow:
    """The DID for a chunk window: the wake records + the model-cache usage trail."""

    records: tuple[TurnRecord, ...] = field(default_factory=tuple)
    usage: tuple[UsageSample, ...] = field(default_factory=tuple)

    @property
    def cache_growing(self) -> bool:
        """Is ``cache_read`` non-decreasing across the window (residency working)?"""
        reads = [u.cache_read for u in self.usage]
        return all(b >= a for a, b in zip(reads, reads[1:], strict=False)) and any(
            r > 0 for r in reads
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "records": [r.to_dict() for r in self.records],
            "cache_read_trail": [u.cache_read for u in self.usage],
            "cache_growing": self.cache_growing,
        }


class RecordStore:
    """Reads wake records + the run.log usage trail for a run's DID monitoring.

    ``wake_out_dir`` is the host's ``$PROXY_WAKE_OUT`` (the ``<wake_id>.json``
    records); ``run_log`` is the host's stdout capture (the ``[usage]`` lines).
    Both are local files the harness has after a wake — no network, no vendor
    client, so this is fully exercised offline.
    """

    def __init__(self, wake_out_dir: Path, run_log: Path | None = None) -> None:
        self._dir = wake_out_dir
        self._run_log = run_log

    def all_records(self) -> list[TurnRecord]:
        """Every completed wake record, oldest-served first."""
        records: list[TurnRecord] = []
        if not self._dir.exists():
            return records
        for path in sorted(self._dir.glob("*.json")):
            if path.name.startswith((".", "_")):
                continue  # temp files + _host.* breadcrumbs are not wake records
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(raw, dict):
                records.append(parse_record(path.stem, raw))
        records.sort(key=lambda r: r.served_at)
        return records

    def records_after(self, since_epoch: float) -> list[TurnRecord]:
        """Wake records served at/after ``since_epoch`` (the chunk's start marker)."""
        return [r for r in self.all_records() if r.served_at >= since_epoch]

    def usage_trail(self) -> list[UsageSample]:
        if self._run_log is None or not self._run_log.exists():
            return []
        return parse_usage_lines(self._run_log.read_text(encoding="utf-8"))

    def window(self, since_epoch: float = 0.0) -> RecordWindow:
        """Assemble the DID window (records since a marker + the usage trail)."""
        return RecordWindow(
            records=tuple(self.records_after(since_epoch)),
            usage=tuple(self.usage_trail()),
        )

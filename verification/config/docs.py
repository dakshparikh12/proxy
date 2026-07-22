"""The doc registry — the single, shared description of every verifiable doc.

Adding a new doc (e.g. doc03 when it lands) means appending one :class:`Doc`
entry here; every layer reads this registry, so nothing else in the framework is
per-doc-hardcoded. ``customer_facing`` is the flag that decides whether the
red-team layer (Layer 5) is eligible to run for a doc — it stays ``False`` for the
back-office/foundation docs and flips ``True`` once a doc handles live meeting
input from outside participants.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .pathsetup import REPO_ROOT


@dataclass(frozen=True)
class Doc:
    key: str                     # "doc00"
    title: str                   # human title
    spec_path: str               # authoritative product spec, relative to repo root
    packages: tuple[str, ...]    # importable top-level packages the doc owns
    test_dir: str                # sealed acceptance test dir, relative to repo root
    customer_facing: bool        # True -> Layer 5 red-team is eligible (still flag-gated)
    summary: str                 # one line: what this doc does

    def spec_abspath(self) -> Path:
        return REPO_ROOT / self.spec_path

    def test_abspath(self) -> Path:
        return REPO_ROOT / self.test_dir


# NOTE: doc03 is intentionally ABSENT — it is being built live in a separate
# session. When it seals, append it here (customer_facing=True) and the whole
# framework picks it up with no other change.
REGISTRY: dict[str, Doc] = {
    "doc00": Doc(
        key="doc00",
        title="Constitution, Architecture & Foundation",
        spec_path="product/v0-spec/00-FOUNDATION.md",
        packages=("contracts", "db", "agentkit", "ops"),
        test_dir="tests/doc00",
        customer_facing=False,
        summary="System composition, contracts registry, durable substrate, boot, config.",
    ),
    "doc01": Doc(
        key="doc01",
        title="Code Intelligence",
        spec_path="product/v0-spec/01-CODE-INTELLIGENCE.md",
        packages=("code_intel",),
        test_dir="tests/doc01",
        customer_facing=False,
        summary="Per-tenant clone + tree-sitter dependency graph + read-only code tools.",
    ),
    "doc02": Doc(
        key="doc02",
        title="Voice & Transport",
        spec_path="product/v0-spec/02-VOICE-TRANSPORT.md",
        packages=("transport",),
        test_dir="tests/doc02",
        # Live meeting audio/chat from outside participants flows through here, so
        # this doc IS customer-facing — the red-team layer becomes relevant once it
        # is wired to live input. Still gated behind --redteam (not run by default).
        customer_facing=True,
        summary="Join FSM, Recall hearing, Cartesia speech, chat, turn-taking, failure paths.",
    ),
    "doc03": Doc(
        key="doc03",
        title="Meeting Understanding (the Notes)",
        spec_path="product/v0-spec/03-MEETING-UNDERSTANDING.md",
        packages=("scribe", "db"),
        test_dir="tests/doc03",
        customer_facing=True,
        summary="Coalescer + serial pipeline, Scribe micro-call, note schema, sampled quality gate, dual storage plane, cross-session read, referent matcher, event emitter, live corrections, close pass.",
    ),
}

ALL_KEYS = tuple(REGISTRY.keys())


def get(doc_key: str) -> Doc:
    if doc_key not in REGISTRY:
        raise KeyError(f"unknown doc {doc_key!r}; known: {', '.join(REGISTRY)} "
                       f"(doc03 is built live in a separate session and is not registered)")
    return REGISTRY[doc_key]

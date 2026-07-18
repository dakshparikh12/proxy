"""Doc 01 · negative-build fixtures (pre-build oracles).

Each context manager materialises a throwaway build tree that PLANTS one
invariant violation, yields the tree path, and cleans up afterwards. The test
runs the product's static verifier over the tree and asserts a non-zero exit +
a diagnostic — i.e. the verifier must *reject* the planted violation before the
build is allowed to proceed.

No product import here: the tree is plain source text. Before the product's
``services.code_intel.verifier`` exists, the subprocess invocation exits non-zero
anyway (module not found), so the coupled tests are red until the real verifier
lands and reports the specific violation string.
"""
from __future__ import annotations

import contextlib
import tempfile
from pathlib import Path
from typing import Iterator


@contextlib.contextmanager
def _build_tree(name: str, files: dict) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix=f"doc01-neg-{name}-") as tmp:
        root = Path(tmp)
        for rel, content in files.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        yield root


@contextlib.contextmanager
def negative_build_repo_provider_bypass() -> Iterator[Path]:
    """A module that reaches the git host directly, bypassing RepoProvider."""
    files = {
        "services/code_intel/__init__.py": "",
        # VIOLATION: raw GitHub client call outside the RepoProvider boundary.
        "services/code_intel/rogue.py": (
            "import requests\n"
            "\n"
            "\n"
            "def fetch_repo():\n"
            "    # bypasses RepoProvider — talks to api.github.com directly\n"
            "    return requests.get('https://api.github.com/repos/x/y')\n"
        ),
    }
    with _build_tree("provider-bypass", files) as root:
        yield root


@contextlib.contextmanager
def negative_build_llm_in_graph() -> Iterator[Path]:
    """A graph-build module that invokes an LLM (must be zero-LLM)."""
    files = {
        "services/code_intel/__init__.py": "",
        # VIOLATION: an LLM/model call inside the deterministic graph build.
        "services/code_intel/graph_builder.py": (
            "from anthropic import Anthropic\n"
            "\n"
            "\n"
            "def build_graph(clone_path):\n"
            "    client = Anthropic()\n"
            "    # LLM call during structural graph build — forbidden\n"
            "    return client.messages.create(model='claude', messages=[])\n"
        ),
    }
    with _build_tree("llm-in-graph", files) as root:
        yield root


@contextlib.contextmanager
def negative_build_fabricated_resolved() -> Iterator[Path]:
    """A tool that hard-codes confidence='resolved' on an unsupported stack."""
    files = {
        "services/code_intel/__init__.py": "",
        # VIOLATION: fabricated absolute 'resolved' label with no LSP/ORM support.
        "services/code_intel/who_writes.py": (
            "def who_writes(table):\n"
            "    # unsupported ORM, yet mislabels the result as exact\n"
            "    return [{'id': 'x::y', 'confidence': 'resolved'}]\n"
        ),
    }
    with _build_tree("fabricated-resolved", files) as root:
        yield root

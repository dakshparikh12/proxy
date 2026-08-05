"""D-032 spine unblocker — the map-build seam is CONSTRUCTED + ASSIGNED, and the connect path
calls THROUGH it to a real repo_maps write (not a no-op).

Two proofs:

* ``_wire_map_seam`` assigns BOTH ``app.state.map_provider`` and ``app.state.map_store`` from the
  resolved Anthropic auth, and leaves ``map_provider = None`` (honest no-op) when no auth is
  configured — boot still succeeds either way.
* With a FAKE provider + fake store on ``app.state``, the connect trigger (the function that reads
  ``map_provider``) actually invokes the pipeline's build + ``map_store.save`` — a repo_maps write
  path is exercised, proving the wiring calls through instead of no-op'ing. The build is now the
  DETERMINISTIC tree-sitter symbol map (no model call, D-032), so "calls through" is proven by the
  tenant-scoped ``save`` of a real symbol map, NOT by a stream of the (deprecated) LLM provider.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any


class _App:
    """A minimal app whose ``.state`` is a mutable namespace (mirrors FastAPI ``app.state``)."""

    def __init__(self, db: Any = None) -> None:
        self.state = SimpleNamespace(db=db)


def test_wire_map_seam_no_auth_leaves_provider_none(monkeypatch: Any) -> None:
    """With NO Anthropic auth configured, wiring leaves ``map_provider = None`` (honest no-op) and
    boot still succeeds — never a crash, never a fabricated provider (Law 2, D-032)."""
    import control_plane.server as server
    from control_plane import settings as settings_mod

    # Resolved settings with every auth mode blank (the unfunded live default today).
    monkeypatch.setattr(
        settings_mod.settings, "anthropic_api_key", "", raising=False
    )
    monkeypatch.setattr(
        settings_mod.settings, "anthropic_auth_token", "", raising=False
    )
    monkeypatch.setattr(
        settings_mod.settings, "claude_code_use_vertex", "", raising=False
    )
    # The Claude Code SUBSCRIPTION token is a fourth auth mode: clear it too so this "no auth"
    # case is genuinely auth-less (the live .env carries a real CLAUDE_CODE_OAUTH_TOKEN).
    monkeypatch.setattr(
        settings_mod.settings, "anthropic_oauth_token", "", raising=False
    )

    app = _App(db=object())
    server._wire_map_seam(app)  # must not raise

    assert app.state.map_provider is None       # honest no-op — no funded auth → no provider
    assert app.state.map_store is None           # and no store when there is no provider


def test_wire_map_seam_with_auth_assigns_both(monkeypatch: Any) -> None:
    """With an Anthropic API key resolved, wiring CONSTRUCTS a real provider + a durable MapStore
    over ``app.state.db`` and ASSIGNS BOTH onto ``app.state`` — the spine is unblocked."""
    import control_plane.server as server
    from agentkit import ClaudeAgentProvider
    from control_plane import settings as settings_mod
    from premeeting.map_store import MapStore

    monkeypatch.setattr(
        settings_mod.settings, "anthropic_api_key", "sk-ant-test-key", raising=False
    )
    monkeypatch.setattr(
        settings_mod.settings, "anthropic_auth_token", "", raising=False
    )
    monkeypatch.setattr(
        settings_mod.settings, "claude_code_use_vertex", "", raising=False
    )

    db = object()
    app = _App(db=db)
    server._wire_map_seam(app)

    assert isinstance(app.state.map_provider, ClaudeAgentProvider)
    # the resolved key rode into the provider's auth env (never logged), never hard-coded:
    assert app.state.map_provider.auth_env.get("ANTHROPIC_API_KEY") == "sk-ant-test-key"
    assert isinstance(app.state.map_store, MapStore)
    assert app.state.map_store.db is db          # durable store bound to the live pool


class _RecordingProvider:
    """A fake ``agentkit.Provider`` retained only for signature compatibility.

    The map build is now the DETERMINISTIC tree-sitter symbol map
    (:func:`premeeting.map_build.build_map` → :func:`premeeting.symbol_map.build_symbol_map`), built
    with NO model call (D-032, and grounded — Law 1). So the wiring must call THROUGH to a real
    ``map_store.save`` WITHOUT ever streaming this provider. ``streamed`` stays ``False`` on the
    live path; if it ever flips, the build regressed back to the credit-blocked LLM prose loop."""

    def __init__(self) -> None:
        self.streamed = False

    async def stream(self, prompt: str, query: Any):  # noqa: ANN001, ANN201 - test fake
        from libs.contracts import AgentChunk

        self.streamed = True
        yield AgentChunk(type="TEXT", text="(deprecated LLM path — should not run)")
        yield AgentChunk(type="RESULT", metadata={"num_turns": 1, "total_cost_usd": 0.0})


class _RecordingStore:
    """A fake ``MapStore``: records every ``save`` so the repo_maps write path is provable."""

    def __init__(self) -> None:
        self.saved: list[dict[str, str]] = []

    async def save(self, *, tenant_id: str, repo: str, sha: str, map_text: str) -> None:
        self.saved.append(
            {"tenant_id": tenant_id, "repo": repo, "sha": sha, "map_text": map_text}
        )


def test_connect_trigger_calls_through_provider_and_store(monkeypatch: Any, tmp_path: Any) -> None:
    """With a FAKE provider + fake store assigned to app.state, the connect path (the function that
    reads ``map_provider``) actually invokes build + ``map_store.save`` — a repo_maps WRITE path is
    exercised, proving the wiring calls through instead of no-op'ing (D-032 acceptance)."""
    import control_plane.connect as connect_mod

    # A real clone on disk so the REAL pipeline (clone → build → store → verify) runs — the store
    # is exercised because ``run_pipeline`` calls ``map_store.save`` on the built map text.
    (tmp_path / "README.md").write_text("# demo repo\nhello", encoding="utf-8")
    (tmp_path / "main.py").write_text(
        "def run() -> int:\n    return 1\n", encoding="utf-8"
    )

    class _Cloner:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def clone(self, tenant_id: str, repo_url: str, *, sha: Any = None, token: Any = None):
            return tmp_path

    # Patch the pipeline-LOCAL name (``pipeline.py`` binds ``from .cloner import Cloner`` at
    # import), plus the source module, so the patch holds regardless of import order.
    import premeeting.pipeline as _pipeline_mod

    monkeypatch.setattr(_pipeline_mod, "Cloner", _Cloner)
    monkeypatch.setattr("premeeting.cloner.Cloner", _Cloner)
    monkeypatch.setattr(_pipeline_mod, "head_sha", lambda p: "deadbeef")

    class _FakeStore:
        _VALID = frozenset({"connecting", "cloning", "indexing", "ready", "not_ready"})

        def __init__(self) -> None:
            self.states: list[str] = []
            self.ready: Any = None
            self.not_ready: Any = None

        def mark_state(self, install_id: str, state: str) -> None:
            self.states.append(state)

        def set_ready(self, install_id: str, coverage_pct: float, flagged: Any = None) -> None:
            self.ready = (coverage_pct, flagged)

        def set_not_ready(self, install_id: str, gaps: Any) -> None:
            self.not_ready = list(gaps)

    readiness_store = _FakeStore()
    provider = _RecordingProvider()
    map_store = _RecordingStore()

    result = connect_mod.trigger_connect_index(
        readiness_store,
        "install-map-1",
        tenant_id="tenant-xyz",
        repo_url="https://github.com/acme/demo",
        map_provider=provider,      # the wiring the boot step assigns to app.state.map_provider
        map_store=map_store,        # the wiring the boot step assigns to app.state.map_store
    )

    # The trigger ran the pipeline (not the None-provider no-op) — a real repo_maps WRITE happened.
    # The build is now DETERMINISTIC (the tree-sitter symbol map), so the model seam is NEVER
    # streamed on the live path; the wiring calling through is proven by the tenant-scoped save,
    # not by a stream call.
    assert provider.streamed is False        # deterministic build — the LLM prose loop must not run
    assert result is not None
    # A repo_maps WRITE path was exercised — the fake store recorded exactly one tenant-scoped save:
    assert len(map_store.saved) == 1
    saved = map_store.saved[0]
    assert saved["tenant_id"] == "tenant-xyz"
    assert saved["repo"] == "demo"
    assert saved["sha"] == "deadbeef"
    # the built map text is the deterministic symbol map (real file:line, groundable), not a blank:
    assert "# Symbol map" in saved["map_text"]
    assert "main.py" in saved["map_text"]

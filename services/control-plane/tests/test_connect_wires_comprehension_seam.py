"""SD-1 — the live connect path threads the Part-2 comprehension seams into ``run_pipeline``.

Before this fix ``connect._run_pipeline_and_bind`` called ``premeeting.run_pipeline`` WITHOUT the
E2B ``call`` seam / the subscription OAuth token / the GitHub-App installation-token minter +
installation id, so Part-2 (the bounded native-Claude holistic comprehension pass) NEVER fired on a
real connect — only Part-1 (the deterministic symbol map). These proofs pin the wiring closed:

* ``server._wire_comprehension_seam`` constructs + assigns the four Part-2 seams onto ``app.state``
  from the resolved settings (the real minter from the GitHub-App id + private-key path; the E2B
  ``call`` seam; the subscription OAuth token; the installation id).
* the connect trigger PASSES those seams THROUGH to ``run_pipeline`` (captured via a spy), proving
  the live connect path fires Part-2 rather than silently degrading to Part-1 alone.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any


def test_wire_comprehension_seam_builds_minter_call_and_token(monkeypatch: Any, tmp_path: Path) -> None:
    """With App creds + a subscription token resolved, the boot step assigns a REAL minter, the
    E2B ``call`` seam, and the OAuth token onto ``app.state`` (SD-1 unblocker)."""
    import control_plane.server as server
    from control_plane import settings as settings_mod
    from premeeting.github_auth import InstallationTokenMinter

    # A real RS256 private key on disk (the App key the minter signs its JWT with).
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    key_path = tmp_path / "app.pem"
    key_path.write_text(pem, encoding="utf-8")

    monkeypatch.setattr(settings_mod.settings, "github_app_id", "4320899", raising=False)
    monkeypatch.setattr(
        settings_mod.settings, "github_app_private_key_path", str(key_path), raising=False
    )
    monkeypatch.setattr(
        settings_mod.settings, "github_app_installation_id", "556677", raising=False
    )
    monkeypatch.setattr(
        settings_mod.settings, "anthropic_oauth_token", "oauth-sub-token", raising=False
    )

    app = SimpleNamespace(state=SimpleNamespace(db=object()))
    server._wire_comprehension_seam(app)

    # A REAL minter (constructed off the App id + the key on disk), the single external-call seam,
    # the subscription token, and the installation id all landed on app.state.
    assert isinstance(app.state.map_minter, InstallationTokenMinter)
    from libs.http.src.http.external import call_external as _call_external

    assert app.state.map_call is _call_external
    assert app.state.map_oauth_token == "oauth-sub-token"
    assert app.state.github_installation_id == "556677"


def test_wire_comprehension_seam_no_app_creds_leaves_minter_none(monkeypatch: Any) -> None:
    """Absent App creds, the minter is left ``None`` (Part-2 clones unauthenticated → a public repo
    still works; a private repo degrades to Part-1) — an honest no-op, never a crash (Law 2)."""
    import control_plane.server as server
    from control_plane import settings as settings_mod

    monkeypatch.setattr(settings_mod.settings, "github_app_id", "", raising=False)
    monkeypatch.setattr(
        settings_mod.settings, "github_app_private_key_path", "", raising=False
    )
    monkeypatch.setattr(
        settings_mod.settings, "github_app_installation_id", "", raising=False
    )
    monkeypatch.setattr(settings_mod.settings, "anthropic_oauth_token", "", raising=False)

    app = SimpleNamespace(state=SimpleNamespace(db=object()))
    server._wire_comprehension_seam(app)  # must not raise

    assert app.state.map_minter is None
    # the E2B call seam is still wired (it needs no creds); the token/installation id are blank
    from libs.http.src.http.external import call_external as _call_external

    assert app.state.map_call is _call_external
    assert app.state.map_oauth_token == ""
    assert app.state.github_installation_id == ""


def test_connect_trigger_passes_part2_seams_into_run_pipeline(monkeypatch: Any, tmp_path: Path) -> None:
    """The connect trigger THREADS the Part-2 seams (call + minter + installation_id + oauth_token)
    straight into ``premeeting.run_pipeline`` — proving the live connect path fires Part-2 (SD-1)."""
    import control_plane.connect as connect_mod
    import premeeting.pipeline as pipeline_mod

    # A real clone on disk so the trigger's pipeline path is reached (clone → build → store → verify).
    (tmp_path / "README.md").write_text("# demo\nhello", encoding="utf-8")
    (tmp_path / "main.py").write_text("def run() -> int:\n    return 1\n", encoding="utf-8")

    class _Cloner:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def clone(self, tenant_id: str, repo_url: str, *, sha: Any = None, token: Any = None) -> Path:
            return tmp_path

    monkeypatch.setattr(pipeline_mod, "Cloner", _Cloner)
    monkeypatch.setattr("premeeting.cloner.Cloner", _Cloner)
    monkeypatch.setattr(pipeline_mod, "head_sha", lambda p: "deadbeef")

    # Spy on run_pipeline: capture the kwargs the trigger passes, then delegate to a trivial
    # honest result so the trigger's terminal-readiness path still runs.
    captured: dict[str, Any] = {}

    async def _spy_run_pipeline(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return pipeline_mod.PipelineResult(ready=True, repo=kwargs.get("repo_url", ""), sha="deadbeef")

    monkeypatch.setattr(pipeline_mod, "run_pipeline", _spy_run_pipeline)

    class _FakeStore:
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

    # A minimal minter/call sentinel — the point is that the SAME objects reach run_pipeline.
    sentinel_call = object()
    sentinel_minter = object()

    connect_mod.trigger_connect_index(
        _FakeStore(),
        "install-part2-1",
        tenant_id="tenant-xyz",
        repo_url="https://github.com/calcom/cal.com",
        map_provider=object(),   # a truthy provider so the trigger takes the real pipeline path
        map_store=None,          # store-less path keeps the injected store (no loop-local swap)
        call=sentinel_call,
        minter=sentinel_minter,
        oauth_token="oauth-sub-token",
        installation_id="556677",
    )

    # The Part-2 seams were threaded all the way into run_pipeline — Part-2 is live on connect.
    assert captured.get("call") is sentinel_call
    assert captured.get("minter") is sentinel_minter
    assert captured.get("oauth_token") == "oauth-sub-token"
    assert captured.get("installation_id") == "556677"

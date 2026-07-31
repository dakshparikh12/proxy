"""doc04 e2e conftest — fake the in-meeting engine's VENDOR seams (the cutover).

Since the cutover, the provisioner's boot path assembles the NEW in-meeting engine
(``control_plane.provisioner._assemble_engine``): sandbox provision (E2B), the Cartesia
speak pipe, and the Haiku disambiguator are its vendor edges. These e2e tests prove
the CLAIM/LOOP/TEARDOWN physics on live Postgres — no vendor call may fire, and
under ``build/setup-test-env.sh`` the real keys ARE in the env, so the vendor seams
are faked HERE at their module attributes (the provisioner resolves them lazily via
``in_meeting.<module>.<name>``, so a module-attr monkeypatch covers every call path,
including the launcher/boot-step paths that expose no injection kwargs).
"""
from __future__ import annotations

from typing import Any

import pytest


class FakeSandboxHandle:
    """The provisioned-sandbox shape (commands/files/kill) as an inert recorder."""

    def __init__(self) -> None:
        self.killed = False

    @property
    def commands(self) -> Any:
        return None

    @property
    def files(self) -> Any:
        return None

    async def kill(self) -> None:
        self.killed = True


class FakeSpeakPipe:
    """The speak seam (SpeakSink + aclose) as an inert recorder."""

    def __init__(self) -> None:
        self.said: list[str] = []
        self.closed = False

    async def say(self, text: str) -> None:
        self.said.append(text)

    async def __call__(self, text: str) -> None:
        self.said.append(text)

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def fake_engine_vendor_seams(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Fake E2B provision + Cartesia speak + Haiku confirm at their module seams.

    Returns the recorders so a test can assert lifecycle (e.g. the sandbox handle
    was killed at meeting end). The engine's PROVIDER stays the real construction
    (no SDK call fires unless a transcript wakes it — these tests feed none unless
    they inject their own fake provider).
    """
    from in_meeting import disambiguator as im_disambiguator
    from in_meeting import sandbox as im_sandbox
    from in_meeting import speak as im_speak

    state: dict[str, Any] = {"handles": [], "pipes": []}

    async def _provision(**kwargs: Any) -> FakeSandboxHandle:
        handle = FakeSandboxHandle()
        state["handles"].append(handle)
        return handle

    def _pipe(meeting_id: str, **kwargs: Any) -> FakeSpeakPipe:
        pipe = FakeSpeakPipe()
        state["pipes"].append(pipe)
        return pipe

    async def _confirm(line: str) -> bool:
        return True

    monkeypatch.setattr(im_sandbox, "provision_sandbox", _provision)
    monkeypatch.setattr(im_speak, "real_speak_sink", _pipe)
    monkeypatch.setattr(im_disambiguator, "build_disambiguator", lambda **kw: _confirm)
    return state

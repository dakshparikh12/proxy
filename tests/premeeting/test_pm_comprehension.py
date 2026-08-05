"""comprehension.py — Part 2 (the holistic comprehension pass + its deterministic verification).

Two layers are proven here, both OFFLINE (no live E2B / model):

* :func:`verify_comprehension` on a real on-disk tree — the deterministic grounding of every
  ``file:line`` the prose cites: a real file+line is KEPT, a fabricated file OR an out-of-range line
  is DROPPED from the stored text (Law 1: never carry an ungrounded location), a mostly-fabricated
  doc is rejected wholesale, and a planted secret path never survives.
* :func:`build_comprehension` seam wiring with a FAKE sandbox — the E2B round-trips ride the injected
  ``call`` seam; the model's written understanding is read back and verified; every fault degrades to
  an honest empty result (never a crash). The REAL-model quality is proven by the live prober, not here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from premeeting import comprehension
from premeeting.comprehension import (
    ComprehensionResult,
    build_comprehension,
    extract_file_line_claims,
    verify_comprehension,
)
from premeeting.exclusions import ExclusionManager

# A realistic understanding doc is thousands of chars; the verification "too thin" floor (400) guards
# against junk. Test docs pad with this substantive filler so they're judged on their CLAIMS, not size.
_FILLER = (
    "\n\nThe system is organized as a single import surface with a small set of core abstractions "
    "that compose cleanly. Commands are the unit of work; parameters attach to them; a context object "
    "threads state through the invocation. The domain is command-line interface construction, and the "
    "conventions favor decorators over subclassing. Testing uses an isolated runner that captures "
    "output without touching the real process. Errors are represented as typed exceptions that carry "
    "user-facing messages, and the help formatter renders usage text from the parameter definitions.\n"
)


def _fixture_repo(tmp_path: Path) -> tuple[Path, ExclusionManager]:
    root = tmp_path / "checkout"
    (root / "src").mkdir(parents=True)
    # server.py has 5 lines (1-based lines 1..5).
    (root / "src" / "server.py").write_text(
        "class BaseCommand:\n"          # line 1
        "    def invoke(self, ctx):\n"  # line 2
        "        return process(ctx)\n" # line 3
        "\n"                            # line 4
        "def process(ctx): ...\n",      # line 5
        encoding="utf-8",
    )
    (root / "README.md").write_text("# widget\nA small service.\n", encoding="utf-8")
    (root / ".env").write_text("API_KEY=leak_abcdefgh\n", encoding="utf-8")
    em = ExclusionManager()
    em.scan_after_clone(root)
    return root, em


# ── extract_file_line_claims ───────────────────────────────────────────────────
def test_extract_file_line_claims_only_real_citations() -> None:
    text = (
        "The `Context` class is at `src/click/core.py:208`; see also globals.py:20 and "
        "core.py:208 again. Version 3.14 is not a claim, nor is bare core.py or ctx.invoke()."
    )
    claims = extract_file_line_claims(text)
    assert ("src/click/core.py", 208) in claims
    assert ("globals.py", 20) in claims
    assert ("core.py", 208) in claims
    # deduped (core.py:208 appears twice)
    assert claims.count(("core.py", 208)) == 1
    # a version / a bare filename / a symbol are NOT claims
    assert all(not (p == "3.14" or p == "ctx.invoke") for p, _l in claims)


# ── verify_comprehension: the deterministic grounding ──────────────────────────
def test_verify_keeps_grounded_claims(tmp_path: Path) -> None:
    root, em = _fixture_repo(tmp_path)
    text = (
        "## What this is\nA small service handling commands.\n\n"
        "## How it works\nThe entry point `BaseCommand.invoke` at `src/server.py:2` calls "
        "`process` defined at `src/server.py:5`. The dispatch begins in `src/server.py:1`.\n\n"
        "## Conventions\nEverything is a plain module; the README explains the domain.\n" + _FILLER
    )
    res = verify_comprehension(text, root, exclusions=em)
    assert res.ok
    assert res.claims_checked == 3
    assert res.claims_kept == 3
    assert res.claims_dropped == 0
    # all real citations survive verbatim
    assert "src/server.py:2" in res.text
    assert "src/server.py:5" in res.text
    assert "src/server.py:1" in res.text


def test_verify_drops_fabricated_file(tmp_path: Path) -> None:
    root, em = _fixture_repo(tmp_path)
    text = (
        "## What this is\nA small service with plenty of substance to clear the length floor. "
        "The core logic and the domain model are described across several grounded sentences here.\n\n"
        "## How it works\n`process` lives at `src/server.py:5` (real). But the router is at "
        "`src/router.py:42` which does not exist in this repo at all.\n" + _FILLER
    )
    res = verify_comprehension(text, root, exclusions=em)
    # the real one is kept; the fabricated file is dropped from the text
    assert "src/server.py:5" in res.text
    assert "src/router.py:42" not in res.text
    assert res.claims_dropped == 1
    assert res.claims_kept == 1


def test_verify_drops_out_of_range_line(tmp_path: Path) -> None:
    root, em = _fixture_repo(tmp_path)
    # server.py has only 5 lines; :999 is a fabricated line on a REAL file (still Law-1 fabrication).
    text = (
        "## What this is\nA small service with more than enough grounded prose to clear the minimum "
        "length so the doc is judged on its claims, not its size. The domain is command dispatch.\n\n"
        "## How it works\n`BaseCommand` is at `src/server.py:1` but `process` is wrongly cited at "
        "`src/server.py:999`.\n" + _FILLER
    )
    res = verify_comprehension(text, root, exclusions=em)
    assert "src/server.py:1" in res.text
    assert "src/server.py:999" not in res.text
    assert res.claims_dropped == 1


def test_verify_rejects_mostly_fabricated_doc(tmp_path: Path) -> None:
    root, em = _fixture_repo(tmp_path)
    text = (
        "## What this is\nA service. Enough words here to clear the length floor comfortably so the "
        "rejection is driven by the fabricated-claim ratio and not by the doc being too thin at all.\n\n"
        "## How it works\n`a.py:1`, `b.py:2`, `c.py:3` are all made up; only `src/server.py:1` is real.\n"
    )
    res = verify_comprehension(text, root, exclusions=em)
    # 1 of 4 grounded → mostly fabricated → rejected wholesale (degrade to Part 1)
    assert not res.ok
    assert res.text == ""
    assert any("mostly ungrounded" in r for r in res.reasons)


def test_verify_secret_path_never_survives(tmp_path: Path) -> None:
    root, em = _fixture_repo(tmp_path)
    text = (
        "## What this is\nA small service with a solid amount of grounded prose to clear the length "
        "floor so verification is judged on claims. Command dispatch is the whole domain here today.\n\n"
        "## Conventions\nConfig is read from `.env:1` at startup, and `BaseCommand` is `src/server.py:1`.\n" + _FILLER
    )
    res = verify_comprehension(text, root, exclusions=em)
    assert ".env:1" not in res.text
    assert "src/server.py:1" in res.text


def test_verify_redacts_inline_secret_value_from_prose(tmp_path: Path) -> None:
    """A secret VALUE hard-coded inline in legitimate source (an unexcluded ``.py`` — e.g. a
    ``mongodb://user:PASSWORD@host`` connection string) must NEVER survive into the verified
    comprehension prose, even when the model diligently quotes it. The comprehension is the resident
    understanding stored in Postgres + seeded into every meeting sandbox, so it rides the SAME secret
    boundary every read path already uses (``exclusions.redact``). Regression: the WS6 obscure-repo
    certification found the Part-2 model surfacing a hard-coded Mongo credential verbatim (Law 1/2 +
    the Secrets hard rule). Grounding (file:line) is untouched; only the secret value is scrubbed."""
    root = tmp_path / "checkout"
    root.mkdir()
    # A LEGITIMATE source file (not a secret-path glob) carrying an inline credential URI — exactly
    # the real-world shape (models.py:1 in the certified obscure repo).
    (root / "db.py").write_text(
        "DB_URL = 'mongodb://svcuser:Sup3rSecretPw@db.internal:27017/app_db'\n"  # line 1
        "def connect(): ...\n",                                                   # line 2
        encoding="utf-8",
    )
    em = ExclusionManager()
    em.scan_after_clone(root)
    text = (
        "## What this is\nA small backend service with plenty of grounded prose to clear the length "
        "floor so verification is judged on its claims and its containment, not on its size alone.\n\n"
        "## Notes\nThere is a hard-coded MongoDB credential at `db.py:1`: the connection string is "
        "`mongodb://svcuser:Sup3rSecretPw@db.internal:27017/app_db`.\n" + _FILLER
    )
    res = verify_comprehension(text, root, exclusions=em)
    assert res.ok
    # The grounded location survives (it is a real file:line the reader may want).
    assert "db.py:1" in res.text
    # The SECRET userinfo (BOTH the password AND the username — a DB user is credential material) is
    # scrubbed; the host/db stays legible so the prose remains useful.
    assert "Sup3rSecretPw" not in res.text
    assert "svcuser" not in res.text
    assert "svcuser:Sup3rSecretPw" not in res.text
    assert "[REDACTED]" in res.text


def test_verify_empty_is_honest(tmp_path: Path) -> None:
    root, _em = _fixture_repo(tmp_path)
    res = verify_comprehension("", root)
    assert not res.ok
    assert res.text == ""


def test_verify_prose_only_doc_passes(tmp_path: Path) -> None:
    """A holistic doc with NO file:line claims is still useful context — it passes as long as it is
    substantive (it makes no ungrounded location claim to check)."""
    root, em = _fixture_repo(tmp_path)
    text = (
        "## What this is\n" + ("This is a command-line toolkit for building composable CLIs. " * 8) +
        "\n\n## Conventions\n" + ("Everything routes through a single public import surface. " * 4)
    )
    res = verify_comprehension(text, root, exclusions=em)
    assert res.ok
    assert res.claims_checked == 0


# ── build_comprehension seam wiring (FAKE sandbox — no live E2B/model) ──────────
class _FakeOutcome:
    def __init__(self, value: Any) -> None:
        self.value = value


class _FakeCmds:
    def __init__(self, runs: list[tuple[str, dict[str, str]]]) -> None:
        self._runs = runs

    def run(self, cmd: str, timeout: int = 0, envs: dict[str, str] | None = None) -> Any:
        self._runs.append((cmd, dict(envs or {})))
        return _FakeOutcome(type("R", (), {"stdout": "", "stderr": ""})())


class _FakeFiles:
    def __init__(self, written: dict[str, str], understanding: str) -> None:
        self._written = written
        self._understanding = understanding

    def write(self, path: str, content: str) -> Any:
        self._written[path] = content
        return _FakeOutcome(None)

    def read(self, path: str) -> Any:
        # The one-shot runner "wrote" the understanding to the OUT path.
        if path.endswith("understanding.md"):
            return _FakeOutcome(self._understanding)
        raise FileNotFoundError(path)


class _FakeSandbox:
    def __init__(self, understanding: str) -> None:
        self.written: dict[str, str] = {}
        # Every (cmd, envs) the sandbox ran — so a test can prove the clone invocation carries the
        # GitHub token (via its env-passed authenticated URL) for a PRIVATE repo.
        self.runs: list[tuple[str, dict[str, str]]] = []
        self.commands = _FakeCmds(self.runs)
        self.files = _FakeFiles(self.written, understanding)
        self.killed = False

    def kill(self) -> None:
        self.killed = True


class _FakeSandboxClass:
    def __init__(self, understanding: str) -> None:
        self._understanding = understanding
        self.created: _FakeSandbox | None = None

    def create(self, timeout: int = 0) -> _FakeSandbox:
        self.created = _FakeSandbox(self._understanding)
        return self.created


async def _passthru_call(thunk: Any, **_kw: Any) -> Any:
    return thunk()


@pytest.mark.asyncio
async def test_build_comprehension_rides_seam_and_verifies(tmp_path: Path) -> None:
    root, em = _fixture_repo(tmp_path)
    understanding = (
        "## What this is\nA small service that dispatches commands with a clear, grounded model that "
        "is described here at real length so the doc clears the verification length floor easily.\n\n"
        "## How it works\n`BaseCommand.invoke` at `src/server.py:2` calls `process` at "
        "`src/server.py:5`. A fabricated `src/ghost.py:7` should be dropped.\n" + _FILLER
    )
    sbx_class = _FakeSandboxClass(understanding)
    res = await build_comprehension(
        call=_passthru_call, token="sk-ant-test", clone_path=root, repo_name="widget",
        symbol_map="# Symbol map — widget\n", sandbox_class=sbx_class, exclusions=em,
        repo_url="https://example.invalid/widget.git",
    )
    assert res.ok
    assert "src/server.py:2" in res.text and "src/server.py:5" in res.text
    assert "src/ghost.py:7" not in res.text  # fabricated dropped by verification
    # the seam carried the symbol map into the sandbox as the navigation index
    assert any("SYMBOL_INDEX.md" in p for p in sbx_class.created.written)  # type: ignore[union-attr]
    # sandbox torn down
    assert sbx_class.created.killed  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_build_comprehension_clones_private_repo_with_github_token(tmp_path: Path) -> None:
    """PRIVATE-repo fix: the in-sandbox ``git clone`` is authenticated with the GitHub installation
    token, so a real customer's private repo clones successfully (without it the pass sees an empty
    repo). The token rides ONLY in the authenticated URL passed as an env var — never inlined into the
    recorded command string — so it is not surfaced in the metered/logged argv."""
    root, em = _fixture_repo(tmp_path)
    understanding = (
        "## What this is\nA small service that dispatches commands, described here at real length so "
        "the doc clears the verification length floor easily.\n\n## How it works\n"
        "`BaseCommand.invoke` at `src/server.py:2` calls `process`.\n" + _FILLER
    )
    sbx_class = _FakeSandboxClass(understanding)
    res = await build_comprehension(
        call=_passthru_call, token="sk-ant-test", clone_path=root, repo_name="widget",
        symbol_map="# Symbol map — widget\n", sandbox_class=sbx_class, exclusions=em,
        repo_url="https://github.com/acme/private-repo.git", github_token="ghs_secretinstalltoken",
    )
    assert res.ok
    runs = sbx_class.created.runs  # type: ignore[union-attr]
    # Exactly one setup run does the git clone; find it.
    clone_runs = [(cmd, envs) for cmd, envs in runs if "git clone" in cmd]
    assert len(clone_runs) == 1
    cmd, envs = clone_runs[0]
    # The clone reads its URL from the env var — the authenticated URL (with the token) is passed
    # there, NOT inlined into the command string (so the token never lands in the logged argv).
    assert "ghs_secretinstalltoken" not in cmd
    assert envs["PROXY_CLONE_URL"] == (
        "https://x-access-token:ghs_secretinstalltoken@github.com/acme/private-repo.git"
    )
    assert "$PROXY_CLONE_URL" in cmd


@pytest.mark.asyncio
async def test_build_comprehension_public_repo_clones_unauthenticated(tmp_path: Path) -> None:
    """No GitHub token (a public fixture repo / a test): the clone URL is passed through unchanged —
    no ``x-access-token`` userinfo is injected."""
    root, em = _fixture_repo(tmp_path)
    understanding = (
        "## What this is\nA small public service described at length to clear the length floor.\n\n"
        "## How it works\n`BaseCommand.invoke` at `src/server.py:2` runs.\n" + _FILLER
    )
    sbx_class = _FakeSandboxClass(understanding)
    await build_comprehension(
        call=_passthru_call, token="sk-ant-test", clone_path=root, repo_name="widget",
        symbol_map="# Symbol map\n", sandbox_class=sbx_class, exclusions=em,
        repo_url="https://github.com/acme/public-repo.git",  # no github_token
    )
    clone_runs = [
        envs for cmd, envs in sbx_class.created.runs if "git clone" in cmd  # type: ignore[union-attr]
    ]
    assert len(clone_runs) == 1
    assert clone_runs[0]["PROXY_CLONE_URL"] == "https://github.com/acme/public-repo.git"
    assert "x-access-token" not in clone_runs[0]["PROXY_CLONE_URL"]


@pytest.mark.asyncio
async def test_build_comprehension_no_token_degrades(tmp_path: Path) -> None:
    root, em = _fixture_repo(tmp_path)
    res = await build_comprehension(
        call=_passthru_call, token="  ", clone_path=root, repo_name="widget",
        symbol_map="# Symbol map\n", exclusions=em,
    )
    assert not res.ok
    assert res.text == ""
    assert any("no subscription token" in r for r in res.reasons)


@pytest.mark.asyncio
async def test_build_comprehension_empty_body_degrades(tmp_path: Path) -> None:
    root, em = _fixture_repo(tmp_path)
    sbx_class = _FakeSandboxClass("")  # the model wrote nothing
    res = await build_comprehension(
        call=_passthru_call, token="sk-ant-test", clone_path=root, repo_name="widget",
        symbol_map="# Symbol map\n", sandbox_class=sbx_class, exclusions=em,
        repo_url="https://example.invalid/widget.git",
    )
    assert not res.ok
    assert res.text == ""


@pytest.mark.asyncio
async def test_build_comprehension_never_raises_on_fault(tmp_path: Path) -> None:
    root, em = _fixture_repo(tmp_path)

    class _Boom:
        def create(self, timeout: int = 0) -> Any:
            raise RuntimeError("e2b down")

    res = await build_comprehension(
        call=_passthru_call, token="sk-ant-test", clone_path=root, repo_name="widget",
        symbol_map="# Symbol map\n", sandbox_class=_Boom(), exclusions=em,
        repo_url="https://example.invalid/widget.git",
    )
    assert not res.ok
    assert isinstance(res, ComprehensionResult)
    assert any("fault" in r for r in res.reasons)

"""Regression: subagent_stop.sh secret scan must fire in the REAL shell.

Two bugs this locks out, both invisible to the hook's unit tests:
  1. The scan used `grep -P` (PCRE), which macOS BSD /usr/bin/grep rejects
     (rc 2) — so via /bin/bash the whole scan silently matched nothing and
     real sk- tokens folded back unblocked.
  2. The portable rewrite first piped $DIFF into `python3 -` while also feeding
     the script via heredoc — stdin collision, so the diff never reached the
     regex and the scan matched nothing again.

The unit tests passed both times because the harness PATH had GNU grep and no
heredoc/stdin collision. This test invokes the hook exactly as settings.json
does — `bash <hook>` against a real git repo — so it exercises the true path.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
HOOK = ROOT / ".claude" / "hooks" / "subagent_stop.sh"


def _run_hook(repo: pathlib.Path) -> str:
    r = subprocess.run(
        ["bash", str(HOOK)],
        cwd=repo,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(repo)},
        capture_output=True,
        text=True,
    )
    return r.stdout


def _repo_with(tmp_path: pathlib.Path, filename: str, contents: str) -> pathlib.Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "init"],
        cwd=tmp_path,
        check=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    )
    f = tmp_path / filename
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(contents)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def test_real_sk_token_is_blocked(tmp_path: pathlib.Path) -> None:
    repo = _repo_with(tmp_path, "s.py", 'KEY = "sk-proj-AbCdEf0123456789XyZwVuTs1234"\n')
    assert '"decision":"block"' in _run_hook(repo)


def test_32hex_trace_id_is_allowed(tmp_path: pathlib.Path) -> None:
    repo = _repo_with(tmp_path, "s.py", 'trace = "sk-0123456789abcdef0123456789abcdef"\n')
    assert '"decision":"block"' not in _run_hook(repo)


def test_clean_edit_is_allowed(tmp_path: pathlib.Path) -> None:
    repo = _repo_with(tmp_path, "services/ok.py", "x = 1\n")
    assert '"decision":"block"' not in _run_hook(repo)

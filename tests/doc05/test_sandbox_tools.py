"""Doc 05 · workroom.sandbox-tools — the 8 sandbox tool handlers (§3.5).

Node ``workroom.sandbox-tools`` (evidence class ``[negative]``). This proves the
security-critical HALF of §3.5's toolbelt on the REAL host code path: the 8 sandbox
tools (7 core + ``ast_grep``), each fronted by a symlink-aware ``validate_path`` and
atomic writes, with ``run_command`` output truncation + exit codes, and every handler
returning a typed error instead of throwing (Hard Rule 6 / Doc 05 §3.3).

**Why host-side is the right proving ground.** The tools ultimately execute INSIDE the
E2B sandbox in the baked Node ``workspace-mcp-server`` sidecar (a deploy artifact this
session cannot bake — CANONICAL §8). But the *logic that makes them safe* —
``validate_path`` (null-byte reject → ``realpath`` → allowed-root re-check → not-yet-
existing-file ancestor walk), atomic writes (``wx`` exclusive-create for new,
temp-file + ``rename`` for overwrite → no TOCTOU / no partial file), ``run_command``
truncation (head-200 + tail-300) + exit codes, and the ``ast_grep`` structural rewrite
over the same atomic-write path — is pure, filesystem-level, and deterministic. So the
Python :mod:`workroom.sandbox_tools` here IS the executable reference contract the Node
sidecar must mirror, proven against a REAL temp filesystem (real symlinks, real files,
real subprocesses) — not a mock.

Spec refs: 05-WORKROOM.md §3.5 (the 8 tools; symlink-aware ``validate_path``: reject null
bytes → ``realpath`` → re-check against allowed roots → for a not-yet-existing file walk
up to the nearest existing ancestor and re-check; atomic writes: ``wx`` for new,
temp-file + ``rename`` for overwrite; ``run_command`` head-200 + tail-300 truncation, 5-min
default timeout, host-observed receipt; ``read_file`` offset/limit; ``grep`` regex 100-match
cap + ``totalMatches``; ``glob`` paginated; ``edit_file`` unique-match replace + ``replace_all``;
``ast_grep`` structural rewrite over the baked ``ast-grep`` binary via the same atomic-write
path), §3.3 (Hard Rule 6 — every handler wraps errors and NEVER throws → ``is_error:true``),
§3.13 step 2 (8 tools with ``validate_path`` + atomic write). CANONICAL §11.11 (``ast-grep``:
WIRE it, don't cut it — the structural-edit tool over the baked binary).

**Confirmed live wire shapes (CANONICAL §11.10, pinned at build):**
  * E2B in-sandbox command exec = ``sandbox.commands.run(cmd)`` → ``{exit_code, stdout,
    stderr}`` (params ``timeout``/``timeoutMs``, ``background``, ``cwd``, ``envs``); files via
    ``sandbox.files.{read,write,list}``; default sandbox timeout 300s. The Node sidecar
    runs the tools with Node ``fs``/``child_process`` INSIDE the sandbox — the Python
    reference here mirrors that surface (real ``subprocess`` for ``run_command``, real ``os``
    for the file ops) on a temp root standing in for the sandbox clone root.
  * ``ast-grep`` structural rewrite = ``ast-grep -p '<pattern>' --rewrite '<repl>' --lang
    <lang> --update-all <path>`` (applies in place). ``ast-grep`` is baked into the E2B
    template image (§3.13 step 1); it is NOT installed in this repo, so ``ast_grep``
    degrades honestly (typed error) when the binary is absent, and the structural-rewrite
    PATH is proven with an injected fake runner writing through the atomic-write seam. The
    real bake is the flagged deploy residual.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest


# --------------------------------------------------------------------------- #
# Fixtures — a real temp filesystem standing in for the sandbox clone root.
# --------------------------------------------------------------------------- #
@pytest.fixture()
def root(tmp_path: Path) -> Path:
    """The allowed sandbox root (the clone root); everything outside it is off-limits."""
    r = tmp_path / "clone"
    r.mkdir()
    return r


@pytest.fixture()
def toolset(root: Path):  # noqa: ANN201 - fixture return type is the SUT
    from workroom.sandbox_tools import SandboxToolset

    return SandboxToolset(root=root)


# --------------------------------------------------------------------------- #
# The toolset exposes EXACTLY 8 tools (7 core + ast_grep) — the count is a DoD.
# --------------------------------------------------------------------------- #
def test_exactly_eight_tools_exposed() -> None:
    """The sandbox toolset is EXACTLY 8 tools: 7 core + ``ast_grep`` (§3.5). NOT done if
    the count is not exactly 8 (DoD)."""
    from workroom.sandbox_tools import SANDBOX_TOOL_NAMES

    assert set(SANDBOX_TOOL_NAMES) == {
        "read_file", "list_files", "grep", "glob",
        "run_command", "write_file", "edit_file", "ast_grep",
    }
    assert len(SANDBOX_TOOL_NAMES) == 8, "the sandbox toolset must be EXACTLY 8 tools"


def test_every_tool_is_callable_through_dispatch(toolset) -> None:  # noqa: ANN001
    """All 8 tools are callable through the single ``call(name, args)`` dispatch the
    sidecar routes ``tools/call`` to — the 'all 8 tools callable through the sidecar' DoD."""
    from workroom.sandbox_tools import SANDBOX_TOOL_NAMES

    (toolset.root / "seed.txt").write_text("hello\nworld\n")
    minimal_args = {
        "read_file": {"path": "seed.txt"},
        "list_files": {"glob": "*.txt"},
        "grep": {"pattern": "hello"},
        "glob": {"pattern": "*.txt"},
        "run_command": {"command": "true"},
        "write_file": {"path": "new.txt", "content": "x"},
        "edit_file": {"path": "seed.txt", "old": "hello", "new": "hi"},
        "ast_grep": {"path": "seed.txt", "pattern": "x", "rewrite": "y", "lang": "python"},
    }
    for name in SANDBOX_TOOL_NAMES:
        res = toolset.call(name, minimal_args[name])
        # Callable = it returns a typed result (never throws); ast_grep may is_error
        # (binary absent) but must still RETURN, never raise.
        assert hasattr(res, "is_error"), f"{name} must return a typed ToolResult"


# --------------------------------------------------------------------------- #
# validate_path — the security core: traversal + symlink + ancestor-walk escape.
# --------------------------------------------------------------------------- #
def test_validate_path_rejects_null_byte(toolset) -> None:  # noqa: ANN001
    """A null byte in the path is rejected up front (before any fs touch) — the §3.5
    'reject null bytes' first gate (a null byte truncates C-level path handling)."""
    from workroom.sandbox_tools import PathEscape

    with pytest.raises(PathEscape):
        toolset.validate_path("evil\x00.txt")


def test_validate_path_rejects_parent_traversal(toolset, root: Path) -> None:  # noqa: ANN001
    """A ``../`` traversal that resolves outside the allowed root is rejected (§3.5)."""
    from workroom.sandbox_tools import PathEscape

    with pytest.raises(PathEscape):
        toolset.validate_path("../outside.txt")
    with pytest.raises(PathEscape):
        toolset.validate_path("a/b/../../../etc/passwd")


def test_validate_path_allows_in_root(toolset, root: Path) -> None:  # noqa: ANN001
    """A plain in-root path resolves to an absolute path UNDER the root (the allow case)."""
    resolved = toolset.validate_path("src/app.py")
    assert Path(resolved).is_absolute()
    # The resolved path must be under the real (symlink-free) root.
    assert str(resolved).startswith(str(root.resolve()) + os.sep)


def test_validate_path_rejects_symlink_escape_existing_file(toolset, root: Path) -> None:  # noqa: ANN001
    """A symlink whose TARGET is outside the root is rejected: ``realpath`` follows the
    link and the re-check against the allowed root fails (§3.5 symlink-aware). This is the
    classic symlink-escape the path-string check alone would miss."""
    from workroom.sandbox_tools import PathEscape

    outside = root.parent / "secret.txt"
    outside.write_text("SECRET")
    link = root / "link.txt"
    link.symlink_to(outside)  # in-root name, out-of-root target
    with pytest.raises(PathEscape):
        toolset.validate_path("link.txt")


def test_validate_path_rejects_not_yet_existing_file_under_symlinked_dir(
    toolset, root: Path,
) -> None:  # noqa: ANN001
    """THE subtle case (node risk): a NOT-yet-existing file whose nearest EXISTING
    ancestor is a symlink pointing outside the root. ``realpath`` of the leaf is
    unreliable (the leaf doesn't exist), so ``validate_path`` MUST walk up to the nearest
    existing ancestor and re-check ITS realpath against the root. Writing ``escape/new.txt``
    where ``escape`` → an out-of-root dir would otherwise create a file OUTSIDE the sandbox.

    NOT done if validate_path misses this ancestor-walk symlink case (DoD)."""
    from workroom.sandbox_tools import PathEscape

    outside_dir = root.parent / "outside_dir"
    outside_dir.mkdir()
    (root / "escape").symlink_to(outside_dir)  # existing ancestor is a symlink OUT
    # 'escape/new.txt' does not exist yet; its nearest existing ancestor 'escape'
    # realpaths OUTSIDE the root → must reject.
    with pytest.raises(PathEscape):
        toolset.validate_path("escape/new.txt")


def test_validate_path_allows_not_yet_existing_file_in_root(toolset, root: Path) -> None:  # noqa: ANN001
    """The benign not-yet-existing case: a new file under a real in-root dir is allowed —
    the ancestor walk finds an in-root existing ancestor and passes."""
    (root / "pkg").mkdir()
    resolved = toolset.validate_path("pkg/brand_new.py")
    assert str(resolved).startswith(str(root.resolve()) + os.sep)


def test_every_write_tool_goes_through_validate_path(toolset, root: Path) -> None:  # noqa: ANN001
    """Every tool that touches a path routes through ``validate_path`` — a traversal
    argument to write_file/edit_file/read_file/ast_grep returns is_error, never escapes.
    (DoD: 'NOT done if any tool skips validate_path'.)"""
    esc = "../escaped.txt"
    assert toolset.call("write_file", {"path": esc, "content": "x"}).is_error
    assert toolset.call("read_file", {"path": esc}).is_error
    assert toolset.call("edit_file", {"path": esc, "old": "a", "new": "b"}).is_error
    assert toolset.call("ast_grep", {"path": esc, "pattern": "p", "rewrite": "r", "lang": "python"}).is_error
    # And the escape target was never created on disk.
    assert not (root.parent / "escaped.txt").exists()


# --------------------------------------------------------------------------- #
# Atomic writes — no partial file on failure, no TOCTOU.
# --------------------------------------------------------------------------- #
def test_write_file_new_uses_exclusive_create(toolset, root: Path) -> None:  # noqa: ANN001
    """A NEW file is created; its content lands atomically (§3.5 ``wx`` exclusive-create)."""
    res = toolset.call("write_file", {"path": "a/b/new.py", "content": "print(1)\n"})
    assert not res.is_error, res.error
    assert (root / "a/b/new.py").read_text() == "print(1)\n"


def test_write_file_overwrite_is_atomic_temp_rename(toolset, root: Path) -> None:  # noqa: ANN001
    """Overwriting an existing file uses temp-file + ``rename`` (atomic) — the reader
    never sees a half-written file (§3.5)."""
    target = root / "config.txt"
    target.write_text("OLD-CONTENT\n")
    res = toolset.call("write_file", {"path": "config.txt", "content": "NEW\n"})
    assert not res.is_error, res.error
    assert target.read_text() == "NEW\n"


def test_write_failure_leaves_no_partial_file_new(toolset, root: Path) -> None:  # noqa: ANN001
    """THE atomicity DoD (new file): if the write fails mid-way, NO partial file is left.
    We force the durable-write step to raise; the target must not exist afterward.

    NOT done if a write can leave a partial file (DoD)."""
    target_rel = "partial_new.txt"

    def _boom(_fd: int, _data: bytes) -> int:
        raise OSError("disk full mid-write")

    res = toolset.call(
        "write_file", {"path": target_rel, "content": "A" * 4096}, _write_bytes=_boom
    )
    assert res.is_error, "a failed write must report is_error, not silently succeed"
    assert not (root / target_rel).exists(), "a failed NEW write must leave NO partial file"
    # No stray temp files left behind either.
    assert not list(root.glob("*.tmp*")), "the temp file must be cleaned up on failure"


def test_write_failure_leaves_original_intact_overwrite(toolset, root: Path) -> None:  # noqa: ANN001
    """THE atomicity DoD (overwrite): if the overwrite fails mid-way, the ORIGINAL file is
    left fully intact — temp-file + rename means the original is only replaced by a
    complete temp file (never truncated in place)."""
    target = root / "important.txt"
    target.write_text("ORIGINAL-INTACT\n")

    def _boom(_fd: int, _data: bytes) -> int:
        raise OSError("disk full mid-write")

    res = toolset.call(
        "write_file", {"path": "important.txt", "content": "SHOULD-NOT-LAND\n"}, _write_bytes=_boom
    )
    assert res.is_error
    assert target.read_text() == "ORIGINAL-INTACT\n", "a failed overwrite must not corrupt the original"
    assert not list(root.glob("*.tmp*")), "the temp file must be cleaned up on failure"


# --------------------------------------------------------------------------- #
# run_command — truncation (head-200 + tail-300) + exit codes + timeout default.
# --------------------------------------------------------------------------- #
def test_run_command_returns_exit_code_zero(toolset) -> None:  # noqa: ANN001
    """``run_command`` returns the process exit code (0 on success) (§3.5)."""
    res = toolset.call("run_command", {"command": "printf 'hi\\n'"})
    assert not res.is_error
    assert res.value["exit_code"] == 0
    assert "hi" in res.value["stdout"]


def test_run_command_returns_nonzero_exit_code(toolset) -> None:  # noqa: ANN001
    """A failing command returns its NON-zero exit code (not an exception) — the
    deterministic evidence gate (§3.7②) reads this exit_code, so it must be real."""
    res = toolset.call("run_command", {"command": "exit 7"})
    assert not res.is_error, "a non-zero exit is a normal result, not a handler error"
    assert res.value["exit_code"] == 7


def test_run_command_truncates_output_head_200_tail_300(toolset) -> None:  # noqa: ANN001
    """Large output is auto-truncated to head-200 + tail-300 lines with a marker (§3.5) —
    so a runaway command cannot blow the context window or the 100 KB journal cap."""
    # Emit 1000 numbered lines; head-200 + tail-300 keeps 500 + a truncation marker.
    res = toolset.call("run_command", {"command": "for i in $(seq 1 1000); do echo line-$i; done"})
    assert not res.is_error
    out_lines = res.value["stdout"].splitlines()
    # head 200 (line-1..line-200) present; tail 300 (line-701..line-1000) present; middle gone.
    assert "line-1" in res.value["stdout"] and "line-200" in res.value["stdout"]
    assert "line-1000" in res.value["stdout"] and "line-701" in res.value["stdout"]
    assert "line-500" not in res.value["stdout"], "the truncated middle must be dropped"
    assert res.value["truncated"] is True
    # 200 + 300 + at least a marker line; never the full 1000.
    assert len(out_lines) < 1000
    assert any("truncat" in ln.lower() for ln in out_lines), "a truncation marker must be present"


def test_run_command_respects_custom_tail_lines(toolset) -> None:  # noqa: ANN001
    """The ``tail_lines`` param overrides the tail budget (§3.5)."""
    res = toolset.call(
        "run_command",
        {"command": "for i in $(seq 1 1000); do echo n$i; done", "tail_lines": 10},
    )
    assert not res.is_error
    assert "n1000" in res.value["stdout"] and "n991" in res.value["stdout"]


def test_run_command_default_timeout_is_five_minutes(toolset) -> None:  # noqa: ANN001
    """The default ``run_command`` timeout is 5 minutes (§3.5 '5-min default timeout')."""
    from workroom.sandbox_tools import RUN_COMMAND_DEFAULT_TIMEOUT_S

    assert RUN_COMMAND_DEFAULT_TIMEOUT_S == 300


def test_run_command_timeout_returns_typed_error_not_throw(toolset) -> None:  # noqa: ANN001
    """A command that exceeds its timeout returns a typed error (is_error), never throws —
    Hard Rule 6. (Tiny timeout so the test is fast.)"""
    res = toolset.call("run_command", {"command": "sleep 5", "timeout_s": 1})
    assert res.is_error, "a timeout must be a typed error, not an unhandled exception"
    assert "timeout" in (res.error or "").lower()


def test_run_command_emits_host_observed_receipt(toolset) -> None:  # noqa: ANN001
    """``run_command`` emits a host-observed receipt ``{command_id, argv, exit_code,
    stdout_ref, artifact_hashes, ...}`` (§3.5 / D-017) — this is what the deterministic
    evidence gate reads (§3.7②), NOT parsed from model prose."""
    res = toolset.call("run_command", {"command": "echo receipt-test"})
    assert not res.is_error
    r = res.receipt
    assert r is not None
    assert set(["command_id", "argv", "exit_code", "stdout_ref", "artifact_hashes"]).issubset(r.keys())
    assert r["exit_code"] == 0
    assert r["argv"], "the receipt records the actual argv that ran"


# --------------------------------------------------------------------------- #
# The core read tools — read_file (offset/limit), grep (cap+total), glob (paged).
# --------------------------------------------------------------------------- #
def test_read_file_offset_and_limit(toolset, root: Path) -> None:  # noqa: ANN001
    """``read_file`` honors offset/limit (§3.5) — line-windowed reads."""
    (root / "big.txt").write_text("".join(f"L{i}\n" for i in range(1, 21)))
    res = toolset.call("read_file", {"path": "big.txt", "offset": 5, "limit": 3})
    assert not res.is_error
    assert res.value["content"].splitlines() == ["L6", "L7", "L8"]


def test_grep_caps_at_100_with_total_matches(toolset, root: Path) -> None:  # noqa: ANN001
    """``grep`` caps returned matches at 100 but reports ``totalMatches`` (§3.5) — so a
    huge match set is bounded yet the true count is honest (Law 2: never overstate)."""
    (root / "many.txt").write_text("".join("needle\n" for _ in range(250)))
    res = toolset.call("grep", {"pattern": "needle"})
    assert not res.is_error
    assert len(res.value["matches"]) == 100, "grep must cap returned matches at 100"
    assert res.value["totalMatches"] == 250, "grep must report the true total match count"


def test_glob_is_paginated(toolset, root: Path) -> None:  # noqa: ANN001
    """``glob`` is paginated (§3.5) — a page + a cursor so a huge tree is bounded."""
    for i in range(30):
        (root / f"f{i:02d}.py").write_text("x")
    page1 = toolset.call("glob", {"pattern": "*.py", "limit": 10})
    assert not page1.is_error
    assert len(page1.value["paths"]) == 10
    assert page1.value["cursor"] is not None, "a truncated glob must return a cursor"
    page2 = toolset.call("glob", {"pattern": "*.py", "limit": 10, "cursor": page1.value["cursor"]})
    assert set(page1.value["paths"]).isdisjoint(page2.value["paths"]), "pages must not overlap"


# --------------------------------------------------------------------------- #
# edit_file — unique-match string replace + replace_all.
# --------------------------------------------------------------------------- #
def test_edit_file_unique_match_replace(toolset, root: Path) -> None:  # noqa: ANN001
    """``edit_file`` replaces a UNIQUE match (§3.5); the result is written atomically."""
    (root / "m.py").write_text("def foo():\n    return old_value\n")
    res = toolset.call("edit_file", {"path": "m.py", "old": "old_value", "new": "new_value"})
    assert not res.is_error, res.error
    assert (root / "m.py").read_text() == "def foo():\n    return new_value\n"


def test_edit_file_ambiguous_match_is_error(toolset, root: Path) -> None:  # noqa: ANN001
    """A non-unique ``old`` (matches >1 site) without ``replace_all`` is a typed error —
    the unique-match discipline prevents an unintended blast-radius edit (§3.5)."""
    (root / "d.py").write_text("x = 1\nx = 1\n")
    res = toolset.call("edit_file", {"path": "d.py", "old": "x = 1", "new": "x = 2"})
    assert res.is_error, "an ambiguous edit must be refused (not silently pick one)"
    assert (root / "d.py").read_text() == "x = 1\nx = 1\n", "an ambiguous edit must not mutate the file"


def test_edit_file_replace_all(toolset, root: Path) -> None:  # noqa: ANN001
    """``replace_all=True`` replaces every occurrence (§3.5)."""
    (root / "d.py").write_text("x = 1\nx = 1\n")
    res = toolset.call("edit_file", {"path": "d.py", "old": "x = 1", "new": "x = 2", "replace_all": True})
    assert not res.is_error
    assert (root / "d.py").read_text() == "x = 2\nx = 2\n"


def test_edit_file_missing_match_is_error(toolset, root: Path) -> None:  # noqa: ANN001
    """An ``old`` string not present is a typed error, and the file is untouched (§3.5)."""
    (root / "d.py").write_text("hello\n")
    res = toolset.call("edit_file", {"path": "d.py", "old": "absent", "new": "x"})
    assert res.is_error
    assert (root / "d.py").read_text() == "hello\n"


# --------------------------------------------------------------------------- #
# ast_grep — structural rewrite over the SAME atomic-write path (§3.5 / §11.11).
# --------------------------------------------------------------------------- #
def test_ast_grep_degrades_honestly_when_binary_absent(toolset, root: Path) -> None:  # noqa: ANN001
    """``ast-grep`` is baked into the E2B template image (§3.13 step 1) but is NOT
    installed in this repo. When the binary is absent, ``ast_grep`` returns a typed error
    naming the missing binary — it NEVER throws and never silently no-ops (Law 2 + Rule 6).
    (The real bake is the flagged deploy residual.)"""
    (root / "s.py").write_text("foo(1)\n")
    res = toolset.call(
        "ast_grep",
        {"path": "s.py", "pattern": "foo($A)", "rewrite": "bar($A)", "lang": "python"},
        _ast_grep_bin="/nonexistent/ast-grep-does-not-exist",
    )
    assert res.is_error, "a missing ast-grep binary must be an honest typed error"
    assert "ast-grep" in (res.error or "").lower()


def test_ast_grep_performs_structural_rewrite_via_atomic_path(toolset, root: Path) -> None:  # noqa: ANN001
    """THE ast_grep DoD: a structural rewrite actually rewrites the file — proven with a
    fake ``ast-grep`` binary (a tiny script) that performs the rewrite and prints to stdout,
    which ``ast_grep`` then commits through the SAME atomic-write path as ``edit_file``
    (temp-file + rename → no partial file). This proves the WIRING (§11.11: WIRE it), the
    real binary being the flagged bake residual.

    ast-grep CLI shape (confirmed live, §11.10): ``ast-grep -p <pattern> --rewrite <repl>
    --lang <lang> --update-all <path>``; the wrapper builds exactly this argv."""
    target = root / "api.py"
    target.write_text("result = old_call(x)\n")

    # A fake ast-grep: apply a literal pattern→rewrite and PRINT the argv it was handed so
    # the test can assert the wrapper built the confirmed CLI shape.
    fake = root.parent / "fake_ast_grep.py"
    fake.write_text(
        "import sys\n"
        "argv = sys.argv[1:]\n"
        "sys.stderr.write(repr(argv))\n"  # so the test can inspect the argv the wrapper built
        "def val(flag):\n"
        "    return argv[argv.index(flag)+1]\n"
        "pat, rew = val('-p'), val('--rewrite')\n"
        "path = argv[-1]\n"
        "src = open(path).read()\n"
        "open(path,'w').write(src.replace(pat, rew))\n"
    )
    bin_wrapper = root.parent / "ast-grep"
    bin_wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {fake} \"$@\"\n")
    bin_wrapper.chmod(bin_wrapper.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    res = toolset.call(
        "ast_grep",
        {"path": "api.py", "pattern": "old_call", "rewrite": "new_call", "lang": "python"},
        _ast_grep_bin=str(bin_wrapper),
    )
    assert not res.is_error, res.error
    assert target.read_text() == "result = new_call(x)\n", "ast_grep must apply the structural rewrite"
    # The wrapper built the confirmed CLI shape: -p <pattern> --rewrite <repl> --lang <lang> --update-all <path>.
    argv_repr = res.value.get("argv_debug", "")
    for flag in ("-p", "--rewrite", "--lang", "--update-all"):
        assert flag in argv_repr, f"ast_grep must pass the confirmed CLI flag {flag}"


def test_ast_grep_rejects_path_traversal(toolset, root: Path) -> None:  # noqa: ANN001
    """``ast_grep`` routes its path through ``validate_path`` too — a traversal target is a
    typed error, never a rewrite outside the sandbox (§3.5)."""
    res = toolset.call(
        "ast_grep",
        {"path": "../escape.py", "pattern": "a", "rewrite": "b", "lang": "python"},
    )
    assert res.is_error


# --------------------------------------------------------------------------- #
# Hard Rule 6 — every handler wraps errors and NEVER throws (Doc 05 §3.3).
# --------------------------------------------------------------------------- #
def test_handlers_never_throw_on_bad_args(toolset) -> None:  # noqa: ANN001
    """Every handler with a REQUIRED arg returns ``is_error:true`` on a missing arg rather
    than throwing — an uncaught exception cannot kill the loop (Hard Rule 6 / §3.3). The
    two tools whose only arg is optional (``list_files`` glob-filter, ``glob`` needs its
    pattern) are covered by ``test_optional_arg_tools_never_throw_on_empty_args``."""
    from workroom.sandbox_tools import SANDBOX_TOOL_NAMES

    # Every tool except list_files (whose glob filter is optional — {} = list all, valid).
    required_arg_tools = [t for t in SANDBOX_TOOL_NAMES if t != "list_files"]
    for name in required_arg_tools:
        res = toolset.call(name, {})  # every required arg missing
        assert hasattr(res, "is_error"), f"{name} returned a non-ToolResult"
        assert res.is_error, f"{name} must return is_error on missing args, not throw"
        assert res.error, f"{name} must carry an error message"


def test_optional_arg_tools_never_throw_on_empty_args(toolset) -> None:  # noqa: ANN001
    """``list_files`` with no glob is VALID (lists all) — it must return a typed result,
    never throw. This proves the never-throw boundary holds even on the all-args-optional
    path (Rule 6): an empty-args call is a clean result, not a crash."""
    res = toolset.call("list_files", {})
    assert hasattr(res, "is_error") and not res.is_error
    assert "files" in res.value


def test_unknown_tool_is_typed_error(toolset) -> None:  # noqa: ANN001
    """An unknown tool name is a typed error, never a crash (Rule 6)."""
    res = toolset.call("definitely_not_a_tool", {})
    assert res.is_error
    assert "unknown" in (res.error or "").lower() or "not" in (res.error or "").lower()


def test_read_missing_file_is_typed_error(toolset) -> None:  # noqa: ANN001
    """Reading a non-existent (but in-root) file is a typed error, not an exception."""
    res = toolset.call("read_file", {"path": "nope.txt"})
    assert res.is_error
    assert not res.value


def test_error_result_carries_structured_error_contract(toolset) -> None:  # noqa: ANN001
    """The error contract (D-018): an is_error result carries a structured
    ``{code, message}`` so the SDK sees a typed tool error, not a bare string blob."""
    res = toolset.call("read_file", {"path": "missing.txt"})
    assert res.is_error
    assert isinstance(res.error_obj, dict)
    assert "code" in res.error_obj and "message" in res.error_obj


# --------------------------------------------------------------------------- #
# tools/call + tools/result journaling (100 KB cap) — §3.5 (Doc 07's trace).
# --------------------------------------------------------------------------- #
def test_every_call_journals_tools_call_and_tools_result(toolset, root: Path) -> None:  # noqa: ANN001
    """Every ``call()`` writes a paired ``tools/call`` + ``tools/result`` journal entry to
    the sandbox log (§3.5) — "Full tools/call + tools/result journaling ... this IS part of
    Doc 07's trace". NOT done if journaling is absent (DoD).

    Each call appends exactly two ordered records: a ``tools/call`` (the tool name + the
    args it was invoked with) and its matching ``tools/result`` (is_error + the payload/
    receipt), correlated by a shared ``call_id``, so Doc 07's trace can reconstruct the
    exact tool exchange from the host-observed journal alone (not from model prose)."""
    (root / "seed.txt").write_text("hello\n")
    toolset.call("read_file", {"path": "seed.txt"})
    toolset.call("write_file", {"path": "made.txt", "content": "x"})

    entries = toolset.journal_entries()
    kinds = [e["kind"] for e in entries]
    # Two calls -> four records, strictly interleaved call/result.
    assert kinds == ["tools/call", "tools/result", "tools/call", "tools/result"], kinds

    call0, result0, call1, result1 = entries
    # tools/call carries the tool name + the args.
    assert call0["kind"] == "tools/call"
    assert call0["tool"] == "read_file"
    assert call0["args"] == {"path": "seed.txt"}
    # tools/result carries the outcome, correlated to its call.
    assert result0["kind"] == "tools/result"
    assert result0["tool"] == "read_file"
    assert result0["call_id"] == call0["call_id"], "result must correlate to its call"
    assert result0["is_error"] is False
    # The second exchange is the write.
    assert call1["tool"] == "write_file" and result1["tool"] == "write_file"
    assert result1["call_id"] == call1["call_id"]
    # A write's result records its host-observed receipt (the evidence-gate input).
    assert result1.get("receipt") is not None


def test_journal_records_errors_and_never_throw_results(toolset) -> None:  # noqa: ANN001
    """A never-throw error result is journaled too (§3.5) — the ``tools/result`` records
    ``is_error:true`` + the structured error, so a failed/blocked tool exchange is fully
    traceable, not swallowed. Even an unknown tool (which never reaches a handler) is
    journaled at the dispatch boundary."""
    r_bad = toolset.call("read_file", {"path": "does-not-exist.txt"})
    assert r_bad.is_error
    r_unknown = toolset.call("nope_not_a_tool", {})
    assert r_unknown.is_error

    entries = toolset.journal_entries()
    results = [e for e in entries if e["kind"] == "tools/result"]
    assert results[-2]["is_error"] is True and results[-2]["tool"] == "read_file"
    assert results[-2].get("error") and "code" in results[-2]["error"]
    # The unknown tool is journaled at the dispatch boundary too (call + result).
    assert results[-1]["is_error"] is True and results[-1]["tool"] == "nope_not_a_tool"
    calls = [e for e in entries if e["kind"] == "tools/call"]
    assert calls[-1]["tool"] == "nope_not_a_tool"


def test_journal_is_capped_at_100kb(toolset, root: Path) -> None:  # noqa: ANN001
    """THE journaling cap DoD: the sandbox journal is bounded at 100 KB (§3.5) — a runaway
    tool exchange (a huge write payload, a flood of calls) can never let the journal blow
    past the cap. We drive far more than 100 KB of tool traffic and assert the serialized
    journal stays at/under the cap while STILL retaining the most-recent entries (a bounded
    rolling window, oldest dropped first — never an unbounded log, never silently empty).

    NOT done if journaling is absent OR unbounded (DoD: '100 KB cap')."""
    from workroom.sandbox_tools import JOURNAL_CAP_BYTES

    assert JOURNAL_CAP_BYTES == 100 * 1024, "the journal cap must be exactly 100 KB (§3.5)"

    # Drive ~1 MB of tool traffic: 400 writes of a 4 KB payload each.
    big = "Z" * 4096
    for i in range(400):
        res = toolset.call("write_file", {"path": f"f{i}.txt", "content": big})
        assert not res.is_error, res.error

    blob = toolset.journal_bytes()
    assert len(blob) <= JOURNAL_CAP_BYTES, (
        f"journal is {len(blob)} bytes, exceeds the 100 KB cap"
    )
    # The journal is not silently empty — it retains the most-recent exchanges.
    entries = toolset.journal_entries()
    assert entries, "the capped journal must still retain recent entries, not be empty"
    # The newest write must be present (rolling window keeps the tail).
    assert any(
        e["kind"] == "tools/call" and e.get("args", {}).get("path") == "f399.txt"
        for e in entries
    ), "the most-recent call must survive the cap (oldest dropped first)"


def test_journal_caps_a_single_oversized_entry(toolset, root: Path) -> None:  # noqa: ANN001
    """A SINGLE tool exchange larger than the whole cap cannot blow the journal (§3.5) — the
    per-entry payload is itself truncated so one 200 KB call/result can never exceed the
    100 KB journal. This is the head-200+tail-300 truncation's journal-side guarantee."""
    from workroom.sandbox_tools import JOURNAL_CAP_BYTES

    # One write whose content alone is 2x the cap.
    huge = "Q" * (200 * 1024)
    res = toolset.call("write_file", {"path": "huge.txt", "content": huge})
    assert not res.is_error, res.error
    # The FILE still lands in full (journaling never corrupts the real write).
    assert (root / "huge.txt").read_text() == huge

    blob = toolset.journal_bytes()
    assert len(blob) <= JOURNAL_CAP_BYTES, (
        f"a single oversized entry blew the journal: {len(blob)} bytes"
    )

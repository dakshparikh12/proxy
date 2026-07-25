"""The Workroom sandbox toolbelt — the 8 tool handlers (05 §3.5).

This module is the **security-critical reference implementation** of §3.5's sandbox
toolbelt: the 8 tools (7 core + ``ast_grep``) the Workroom agent reaches through the
sandbox transport (``mcp__code__*``). Each tool is fronted by a symlink-aware
:meth:`SandboxToolset.validate_path` and, for writes, an **atomic-write path** (no
TOCTOU, no partial file); ``run_command`` truncates output (head-200 + tail-300),
returns real exit codes, and emits a host-observed receipt; ``ast_grep`` performs a
structural rewrite over the baked ``ast-grep`` binary via the *same* atomic-write path.

**Where this runs (and why it is proven host-side).** In production these tools execute
INSIDE the E2B sandbox, in the baked Node ``workspace-mcp-server`` sidecar (a deploy
artifact — CANONICAL §8: a Node sidecar, not a Python port; the ``~/platform`` source is
not in this repo). The Node sidecar uses Node ``fs``/``child_process`` for the actual
file/shell effects. But the *logic that makes those effects safe* — the ``validate_path``
algorithm (reject null bytes → ``realpath`` → allowed-root re-check → not-yet-existing-file
ancestor walk), the atomic-write discipline (``wx`` exclusive-create for new; temp-file +
``rename`` for overwrite), the ``run_command`` truncation + exit-code capture, and the
``ast_grep`` structural rewrite over the same atomic path — is pure, filesystem-level, and
deterministic. So this Python module IS the executable contract the Node sidecar must
mirror, proven on a REAL temp filesystem (real symlinks, real files, real subprocesses)
by ``tests/doc05/test_sandbox_tools.py`` — never a mock.

The real E2B-template bake (the Node sidecar + the baked ``ast-grep`` binary + LIVE sandbox
execution) is a **deploy residual** this session FLAGS (Phase-3/founder infra), not fakes.
:data:`SIDECAR_TOOL_CONTRACT` pins the exact behavior the bake is verified against.

**Confirmed live wire shapes (CANONICAL §11.10, pinned at build):**
  * E2B in-sandbox exec ``sandbox.commands.run(cmd)`` → ``{exit_code, stdout, stderr}``
    (params ``timeout``/``timeoutMs``, ``background``, ``cwd``, ``envs``); files via
    ``sandbox.files.{read,write,list}``; default sandbox timeout 300s. The Node sidecar
    runs the tools with Node ``fs``/``child_process``; this reference mirrors that surface
    with real ``subprocess`` / ``os`` on the clone root.
  * ``ast-grep`` structural rewrite CLI = ``ast-grep -p '<pattern>' --rewrite '<repl>'
    --lang '<lang>' --update-all '<path>'`` (applies in place). Baked into the E2B
    template image (§3.13 step 1); absent in this repo → ``ast_grep`` degrades honestly.

**The five laws, structural here.** *Law 2 (never overstate):* ``grep`` caps at 100 but
reports the true ``totalMatches``; ``run_command`` marks ``truncated`` when it drops the
middle. *Law 3 (human control):* these are the *mutable-work* tools — no ``propose_change``
(that is the host-side staged-draft path, §3.8/CANONICAL §11.7); this toolbelt only edits
the sandbox clone. *Hard Rule 6 (never-throw):* EVERY handler is try/except-wrapped and
returns :class:`ToolResult` with ``is_error=True`` on any failure — an uncaught exception
can never kill the loop (§3.3, D-018). No internal component name is user-visible.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Constants pinned from the spec (§3.5) ──────────────────────────────────
RUN_COMMAND_DEFAULT_TIMEOUT_S = 300  # 5-min default timeout (§3.5)
_HEAD_LINES = 200                    # head-200 on truncation (§3.5)
_TAIL_LINES = 300                    # tail-300 on truncation (§3.5)
_GREP_MATCH_CAP = 100                # grep returns ≤100 matches + totalMatches (§3.5)
_GLOB_DEFAULT_LIMIT = 200            # glob page size (paginated, §3.5)

# The 8 sandbox tools: 7 core + ast_grep (§3.5 / CANONICAL §11.11). The count is a DoD.
SANDBOX_TOOL_NAMES: tuple[str, ...] = (
    "read_file",
    "list_files",
    "grep",
    "glob",
    "run_command",
    "write_file",
    "edit_file",
    "ast_grep",
)

# The behavior contract the baked Node sidecar MUST honor for each tool — pinned so the
# deploy bake is checked against a precise spec, not prose. This is the residual FLAGGED.
SIDECAR_TOOL_CONTRACT: dict[str, Any] = {
    "tool_count": 8,
    "tools": list(SANDBOX_TOOL_NAMES),
    "validate_path": "reject null byte -> realpath -> allowed-root re-check -> "
    "for a not-yet-existing file walk to nearest existing ancestor and re-check",
    "atomic_write": "wx exclusive-create for new; temp-file + rename for overwrite "
    "(no TOCTOU, no partial file)",
    "run_command": {
        "truncate": "head-200 + tail-300 (tail_lines override)",
        "default_timeout_s": RUN_COMMAND_DEFAULT_TIMEOUT_S,
        "returns": ["exit_code", "stdout", "stderr", "truncated"],
        "receipt": ["command_id", "argv", "exit_code", "stdout_ref", "artifact_hashes"],
    },
    "ast_grep": "ast-grep -p <pattern> --rewrite <repl> --lang <lang> --update-all <path>; "
    "applies through the same atomic-write path as edit_file; baked binary (deploy residual)",
    "never_throw": "every handler returns is_error:true on failure (Hard Rule 6 / §3.3)",
    "deploy_artifact": True,  # the Node sidecar bake + ast-grep binary — FLAGGED, not faked
}


class PathEscape(ValueError):
    """A path argument that escapes the allowed sandbox root (traversal or symlink).

    Raised by :meth:`SandboxToolset.validate_path`. The handlers catch it and return a
    typed ``is_error`` result (Hard Rule 6) — it never propagates out of a tool call.
    """


@dataclass
class ToolResult:
    """The typed result EVERY sandbox tool returns — never a raised exception (Rule 6).

    ``is_error`` is the SDK-visible flag; on error, ``error`` is the human message and
    ``error_obj`` is the structured ``{code, message, context?}`` contract (D-018). On
    success, ``value`` carries the tool's payload. ``receipt`` is the host-observed
    receipt for effect-emitting tools (run_command / write_file / edit_file / ast_grep),
    the deterministic evidence gate's input (§3.7② / D-017).
    """

    is_error: bool = False
    value: dict[str, Any] = field(default_factory=dict)
    error_obj: dict[str, Any] | None = None
    receipt: dict[str, Any] | None = None

    @property
    def error(self) -> str | None:
        """The human-readable error message (``None`` on success)."""
        return None if self.error_obj is None else str(self.error_obj.get("message", ""))

    @classmethod
    def ok(cls, value: dict[str, Any], *, receipt: dict[str, Any] | None = None) -> ToolResult:
        return cls(is_error=False, value=value, receipt=receipt)

    @classmethod
    def fail(cls, code: str, message: str, **context: Any) -> ToolResult:
        obj: dict[str, Any] = {"code": code, "message": message}
        if context:
            obj["context"] = context
        return cls(is_error=True, error_obj=obj)


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class SandboxToolset:
    """The 8 sandbox tool handlers bound to ONE allowed root (the clone root).

    Every path argument is resolved through :meth:`validate_path` against ``root`` so no
    tool can read/write/rewrite outside the sandbox clone. Writes go through the atomic
    path (:meth:`_atomic_write`). Construct one per sandbox; call via :meth:`call`
    (the single ``tools/call`` dispatch the sidecar routes to).
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        # The real (symlink-free) allowed root. Everything must resolve UNDER this.
        self._root = Path(root).resolve(strict=True)

    @property
    def root(self) -> Path:
        return self._root

    # ─────────────────────────── the security core ──────────────────────────
    def validate_path(self, rel: str) -> str:
        """Resolve ``rel`` under the allowed root, rejecting every escape (§3.5).

        The algorithm, in order (each step catches an escape the prior would miss):

          1. **Reject null bytes** — a ``\\x00`` truncates C-level path handling, so a
             ``"safe\\x00/../../etc"`` string could resolve differently in the OS than in
             our check. Reject before touching the filesystem.
          2. **Reject absolute inputs** — an absolute path ignores the root entirely.
          3. **``realpath`` the target** — follow every symlink component so a link whose
             *target* is outside the root is caught (not just the link's own name).
          4. **Re-check against the allowed root** — the resolved real path MUST be the
             root itself or strictly under it.
          5. **Not-yet-existing-file ancestor walk (the subtle case)** — a file being
             *created* does not exist yet, so ``realpath`` of the leaf cannot follow a
             (nonexistent) link. Walk UP to the nearest EXISTING ancestor and realpath
             THAT: if the nearest existing ancestor is a symlink pointing out of the root,
             the create would land outside — reject. This closes the
             ``write_file("escape/new.txt")`` hole where ``escape`` is an out-of-root
             symlink (the node's named risk).

        Returns the validated absolute real path (a ``str``). Raises :class:`PathEscape`
        on any escape (the handlers convert it to a typed ``is_error`` result).
        """
        if not isinstance(rel, str) or rel == "":
            raise PathEscape("empty or non-string path")
        if "\x00" in rel:
            raise PathEscape("null byte in path")
        if os.path.isabs(rel):
            raise PathEscape(f"absolute path not allowed: {rel!r}")

        root = self._root  # already realpath'd, strict, at construction
        candidate = (root / rel)

        # Walk up to the nearest EXISTING ancestor and realpath it. For an existing leaf
        # this is the leaf itself (realpath follows its symlinks). For a not-yet-existing
        # leaf, this is the nearest existing parent — whose realpath catches a symlinked
        # ancestor pointing out of the root (the subtle create-time escape).
        existing = candidate
        while not existing.exists() and existing != existing.parent:
            existing = existing.parent
        real_existing = existing.resolve()

        # The nearest existing ancestor must be the root or strictly under it.
        if real_existing != root and root not in real_existing.parents:
            raise PathEscape(f"path escapes sandbox root: {rel!r}")

        # Reconstruct the final absolute path: the validated real ancestor + the
        # remaining (not-yet-existing) tail components. os.path.normpath collapses any
        # residual '..' in the tail — but since we validated the existing ancestor's
        # realpath, and the tail has no symlinks yet, the join stays under the root.
        tail = candidate.relative_to(existing) if candidate != existing else Path()
        final = (real_existing / tail) if str(tail) not in ("", ".") else real_existing
        final_norm = Path(os.path.normpath(final))
        if final_norm != root and root not in final_norm.parents:
            raise PathEscape(f"path escapes sandbox root: {rel!r}")
        return str(final_norm)

    # ─────────────────────────── the atomic write ───────────────────────────
    def _atomic_write(
        self,
        abs_path: str,
        content: str,
        *,
        write_bytes: Callable[[int, bytes], int] | None = None,
    ) -> None:
        """Write ``content`` to ``abs_path`` atomically — no TOCTOU, no partial file (§3.5).

        Strategy (matches the §3.5 contract):

          * **new file** → temp-file in the target dir + ``os.rename`` onto the target,
            guarded by an ``O_EXCL`` existence check so a concurrent creator is detected
            (the ``wx`` exclusive-create semantics). A crash mid-write leaves only the
            temp file (cleaned up), never a partial target.
          * **overwrite** → temp-file in the target dir + ``os.rename`` REPLACING the
            target. ``rename`` is atomic on POSIX, so a reader sees either the whole old
            file or the whole new file — never a truncated one. A crash mid-write leaves
            the ORIGINAL fully intact (the temp file is discarded).

        ``write_bytes`` is the injectable durable-write step (defaults to ``os.write``);
        a test injects a raiser to prove that a failed write leaves NO partial file and
        cleans up the temp file. On ANY failure the temp file is removed and the error
        re-raised for the handler to convert to ``is_error``.
        """
        writer = write_bytes if write_bytes is not None else os.write
        target = Path(abs_path)
        parent = target.parent
        parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")

        # Exclusive-create semantics for a NEW file: refuse to clobber a file that appeared
        # between validate and write (TOCTOU) — the overwrite path is the explicit case.
        is_new = not target.exists()

        fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(parent))
        try:
            writer(fd, data)  # the durable write (injectable so a test can force failure)
            os.fsync(fd)
            os.close(fd)
            fd = -1
            if is_new:
                # wx exclusive-create: fail closed if the target now exists (a racing writer).
                exclusive_fd = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                os.close(exclusive_fd)
            os.replace(tmp_name, str(target))  # atomic rename onto the target
            tmp_name = ""  # renamed away — nothing to clean up
        finally:
            if fd >= 0:
                os.close(fd)
            if tmp_name and os.path.exists(tmp_name):
                os.unlink(tmp_name)  # a failed write leaves NO partial/temp file behind

    # ─────────────────────────── the dispatch ───────────────────────────────
    def call(self, name: str, args: dict[str, Any], **hooks: Any) -> ToolResult:
        """Route one ``tools/call`` to its handler — NEVER throws (Hard Rule 6 / §3.3).

        Any exception (a bad arg, a path escape, an OS error, a bug) is caught here and
        returned as a typed ``is_error`` :class:`ToolResult`, so an uncaught exception can
        never kill the agent loop (§3.3). ``hooks`` carries test-only injections
        (``_write_bytes``, ``_ast_grep_bin``) that let the atomic-write failure path and
        the ast_grep binary path be proven without a real disk-full or a real binary.
        """
        handler: Callable[..., ToolResult] | None = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return ToolResult.fail("unknown_tool", f"unknown tool: {name!r}")
        try:
            return handler(args or {}, **hooks)
        except PathEscape as exc:
            return ToolResult.fail("path_escape", str(exc), path=args.get("path"))
        except Exception as exc:  # noqa: BLE001 - Rule 6: the never-throw boundary
            return ToolResult.fail("tool_error", f"{type(exc).__name__}: {exc}")

    # ─────────────────────────── the 8 handlers ─────────────────────────────
    def _tool_read_file(self, args: dict[str, Any], **_: Any) -> ToolResult:
        """``read_file(path, offset?, limit?)`` — line-windowed read (§3.5)."""
        path = _require_str(args, "path")
        abs_path = self.validate_path(path)
        p = Path(abs_path)
        if not p.is_file():
            return ToolResult.fail("not_found", f"no such file: {path}")
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        offset = int(args.get("offset", 0) or 0)
        limit = args.get("limit")
        window = lines[offset : (offset + int(limit)) if limit is not None else None]
        return ToolResult.ok({"content": "".join(window), "total_lines": len(lines)})

    def _tool_list_files(self, args: dict[str, Any], **_: Any) -> ToolResult:
        """``list_files(glob?)`` — list files under the root matching an optional glob (§3.5)."""
        pattern = str(args.get("glob") or args.get("pattern") or "*")
        matches = sorted(
            str(p.relative_to(self._root))
            for p in self._root.rglob(pattern)
            if p.is_file()
        )
        return ToolResult.ok({"files": matches, "count": len(matches)})

    def _tool_grep(self, args: dict[str, Any], **_: Any) -> ToolResult:
        """``grep(pattern, glob?)`` — regex over files, ≤100 matches + ``totalMatches`` (§3.5).

        Caps returned matches at 100 (bounded context) but reports the TRUE total so the
        agent is never misled about coverage (Law 2: never overstate)."""
        pattern = _require_str(args, "pattern")
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            return ToolResult.fail("bad_regex", f"invalid regex: {exc}")
        glob = str(args.get("glob") or "*")
        matches: list[dict[str, Any]] = []
        total = 0
        for p in sorted(self._root.rglob(glob)):
            if not p.is_file():
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if rx.search(line):
                    total += 1
                    if len(matches) < _GREP_MATCH_CAP:
                        matches.append(
                            {"path": str(p.relative_to(self._root)), "line": lineno, "text": line}
                        )
        return ToolResult.ok({"matches": matches, "totalMatches": total})

    def _tool_glob(self, args: dict[str, Any], **_: Any) -> ToolResult:
        """``glob(pattern, limit?, cursor?)`` — paginated glob over the root (§3.5)."""
        pattern = _require_str(args, "pattern")
        limit = int(args.get("limit", _GLOB_DEFAULT_LIMIT) or _GLOB_DEFAULT_LIMIT)
        cursor = int(args.get("cursor", 0) or 0)
        all_paths = sorted(
            str(p.relative_to(self._root)) for p in self._root.rglob(pattern) if p.is_file()
        )
        page = all_paths[cursor : cursor + limit]
        next_cursor = cursor + limit if cursor + limit < len(all_paths) else None
        return ToolResult.ok({"paths": page, "cursor": next_cursor, "total": len(all_paths)})

    def _tool_run_command(self, args: dict[str, Any], **_: Any) -> ToolResult:
        """``run_command(command, timeout_s?, tail_lines?)`` — the shell workhorse (§3.5).

        Runs the command in the sandbox root, auto-truncates output to head-200 + tail-300
        (``tail_lines`` override), returns the REAL exit code, and emits a host-observed
        receipt (§3.7② / D-017). A timeout is a typed error (Rule 6), never an exception.
        The default timeout is 5 minutes (§3.5)."""
        command = _require_str(args, "command")
        timeout_s = int(args.get("timeout_s", RUN_COMMAND_DEFAULT_TIMEOUT_S) or RUN_COMMAND_DEFAULT_TIMEOUT_S)
        tail_lines = int(args.get("tail_lines", _TAIL_LINES) or _TAIL_LINES)
        try:
            proc = subprocess.run(  # noqa: S602 - the sandbox shell workhorse (in-sandbox in prod)
                command,
                shell=True,
                cwd=str(self._root),
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return ToolResult.fail(
                "timeout", f"command exceeded timeout of {timeout_s}s", command=command
            )
        stdout, truncated = _truncate(proc.stdout, head=_HEAD_LINES, tail=tail_lines)
        stderr, _stderr_trunc = _truncate(proc.stderr, head=_HEAD_LINES, tail=tail_lines)
        receipt = {
            "command_id": str(uuid.uuid4()),
            "argv": [command],
            "exit_code": proc.returncode,
            "stdout_ref": _sha256(proc.stdout.encode("utf-8")),
            "artifact_hashes": {},
            "duration_secs": None,
        }
        return ToolResult.ok(
            {
                "exit_code": proc.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "truncated": truncated,
            },
            receipt=receipt,
        )

    def _tool_write_file(self, args: dict[str, Any], **hooks: Any) -> ToolResult:
        """``write_file(path, content)`` — atomic write (new: wx; overwrite: temp+rename) (§3.5)."""
        path = _require_str(args, "path")
        content = _require_str(args, "content", allow_empty=True)
        abs_path = self.validate_path(path)
        self._atomic_write(abs_path, content, write_bytes=hooks.get("_write_bytes"))
        return ToolResult.ok(
            {"path": path, "bytes": len(content.encode("utf-8"))},
            receipt=self._write_receipt(abs_path, path),
        )

    def _tool_edit_file(self, args: dict[str, Any], **hooks: Any) -> ToolResult:
        """``edit_file(path, old, new, replace_all?)`` — unique-match replace (§3.5).

        Refuses an ambiguous edit (``old`` matches >1 site and ``replace_all`` is false) so
        an unintended blast-radius edit is impossible; writes the result atomically."""
        path = _require_str(args, "path")
        old = _require_str(args, "old", allow_empty=True)
        new = _require_str(args, "new", allow_empty=True)
        replace_all = bool(args.get("replace_all", False))
        abs_path = self.validate_path(path)
        p = Path(abs_path)
        if not p.is_file():
            return ToolResult.fail("not_found", f"no such file: {path}")
        text = p.read_text(encoding="utf-8", errors="replace")
        occurrences = text.count(old)
        if occurrences == 0:
            return ToolResult.fail("no_match", f"old string not found in {path}")
        if occurrences > 1 and not replace_all:
            return ToolResult.fail(
                "ambiguous_match",
                f"old string matches {occurrences} sites in {path}; pass replace_all to edit all",
                occurrences=occurrences,
            )
        updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
        self._atomic_write(abs_path, updated, write_bytes=hooks.get("_write_bytes"))
        return ToolResult.ok(
            {"path": path, "replacements": occurrences if replace_all else 1},
            receipt=self._write_receipt(abs_path, path),
        )

    def _tool_ast_grep(self, args: dict[str, Any], **hooks: Any) -> ToolResult:
        """``ast_grep(path, pattern, rewrite, lang)`` — structural rewrite (§3.5 / §11.11).

        Runs the baked ``ast-grep`` binary with the confirmed CLI shape
        (``-p <pattern> --rewrite <repl> --lang <lang> --update-all <path>``) on a temp COPY
        of the target, then commits the rewritten text through the SAME atomic-write path as
        ``edit_file``. So the structural edit is (a) syntax-aware (survives whitespace
        variance) and (b) atomic (no partial file). ``ast-grep`` is baked into the E2B
        template image (§3.13 step 1); if the binary is absent this returns an honest typed
        error naming it (Law 2 + Rule 6) — the real bake is the flagged deploy residual."""
        path = _require_str(args, "path")
        pattern = _require_str(args, "pattern")
        rewrite = _require_str(args, "rewrite", allow_empty=True)
        lang = _require_str(args, "lang")
        abs_path = self.validate_path(path)  # traversal/symlink escape → typed error
        p = Path(abs_path)
        if not p.is_file():
            return ToolResult.fail("not_found", f"no such file: {path}")

        ast_grep_bin = hooks.get("_ast_grep_bin") or shutil.which("ast-grep") or shutil.which("sg")
        if not ast_grep_bin:
            return ToolResult.fail(
                "ast_grep_unavailable",
                "ast-grep binary not found — it is baked into the E2B template image "
                "(§3.13 step 1); this host has no ast-grep (deploy residual)",
            )

        # Rewrite on a temp COPY so a failed/partial ast-grep never touches the real file;
        # the confirmed CLI applies --update-all in place, so we point it at the copy.
        with tempfile.TemporaryDirectory(dir=str(p.parent)) as td:
            work = Path(td) / p.name
            work.write_bytes(p.read_bytes())
            argv = [
                str(ast_grep_bin),
                "-p", pattern,
                "--rewrite", rewrite,
                "--lang", lang,
                "--update-all", str(work),
            ]
            try:
                proc = subprocess.run(  # noqa: S603 - argv list, no shell (baked binary)
                    argv, capture_output=True, text=True, timeout=RUN_COMMAND_DEFAULT_TIMEOUT_S
                )
            except FileNotFoundError:
                return ToolResult.fail("ast_grep_unavailable", f"ast-grep binary not runnable: {ast_grep_bin}")
            except subprocess.TimeoutExpired:
                return ToolResult.fail("timeout", "ast-grep exceeded timeout")
            if proc.returncode != 0:
                return ToolResult.fail(
                    "ast_grep_failed", f"ast-grep exited {proc.returncode}: {proc.stderr[:500]}"
                )
            rewritten = work.read_text(encoding="utf-8", errors="replace")

        # Commit the structural rewrite through the SAME atomic-write path as edit_file.
        self._atomic_write(abs_path, rewritten, write_bytes=hooks.get("_write_bytes"))
        receipt = self._write_receipt(abs_path, path)
        return ToolResult.ok(
            {"path": path, "rewritten": True, "argv_debug": " ".join(argv[1:])},
            receipt=receipt,
        )

    # ─────────────────────────── receipt helper ─────────────────────────────
    def _write_receipt(self, abs_path: str, rel_path: str) -> dict[str, Any]:
        """A host-observed write receipt with the touched file's artifact hash (§3.5 / D-017)."""
        try:
            digest = _sha256(Path(abs_path).read_bytes())
        except OSError:
            digest = ""
        return {
            "command_id": str(uuid.uuid4()),
            "argv": [],
            "exit_code": 0,
            "stdout_ref": "",
            "artifact_hashes": {rel_path: digest},
            "duration_secs": None,
        }


# ─────────────────────────── small pure helpers ─────────────────────────────
def _require_str(args: dict[str, Any], key: str, *, allow_empty: bool = False) -> str:
    """Fetch a required string arg or raise (the ``call`` boundary converts to is_error)."""
    if key not in args or args[key] is None:
        raise ValueError(f"missing required argument: {key!r}")
    val = args[key]
    if not isinstance(val, str):
        raise ValueError(f"argument {key!r} must be a string, got {type(val).__name__}")
    if not allow_empty and val == "":
        raise ValueError(f"argument {key!r} must be non-empty")
    return val


def _truncate(text: str, *, head: int, tail: int) -> tuple[str, bool]:
    """Truncate ``text`` to ``head`` + ``tail`` lines with a marker if it overflows (§3.5).

    Keeps the first ``head`` and last ``tail`` lines; the dropped middle is replaced by a
    single marker line naming how many lines were elided (so the agent knows output was
    truncated — Law 2). Returns ``(possibly-truncated-text, was_truncated)``.
    """
    lines = text.splitlines()
    if len(lines) <= head + tail:
        return text, False
    dropped = len(lines) - head - tail
    kept = lines[:head] + [f"... [truncated {dropped} lines] ..."] + lines[-tail:]
    return "\n".join(kept) + ("\n" if text.endswith("\n") else ""), True

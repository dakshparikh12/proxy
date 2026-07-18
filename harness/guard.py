#!/usr/bin/env python3
"""PreToolUse hook: default-deny. Blocks edits to protected paths and unsafe shell. Reads hook JSON on stdin.

LAYERED-DEFENSE MODEL
---------------------
The regex patterns below are SPEED BUMPS — they catch obvious bypass attempts early
and cheaply. They are not the security wall. The WALL is the runner.py integrity check:
a sha256 over the sorted file list + contents of all protected trees, recorded before
the loop and recomputed after every pass. A mismatch hard-exits the run. Together the
two layers give fast feedback (guard) + tamper-proof verification (integrity hash).
"""
import sys, json, re

PROTECTED = (
    "tests/", "fixtures/", "harness/", ".claude/", "evidence/", "components/",
    "verify.sh", "AGENTS.md", "CLAUDE.md", "specs/", "runner.py", "eval_runner.py",
    "eval/", "sources/",
    # TASK 2 additions:
    "product/", "criteria/", "acceptance/", ".env", "requirements",
    # TASK 1: protect the orchestrator (conductor writes only state/ and *.log)
    "orchestrator/prompts/", "orchestrator/orchestrate.py",
    "orchestrator/supervise.sh", "orchestrator/skills/", "orchestrator/verify_doc.md",
)

# Build a regex fragment matching any protected dir for shell-write patterns
_PROTECTED_DIRS_RE = "|".join(
    re.escape(p) for p in PROTECTED if p.endswith("/")
)
_PROTECTED_ALL_RE = "|".join(re.escape(p) for p in PROTECTED)

BLOCK_SHELL = (
    r"\brm\s+-rf\s+/",
    r"\bcurl\b.*\|\s*sh",
    r"\bgit\s+push\s+--force",
    # Block ALL pip installs (no carve-out for -r)
    r"pip\s+install",
    # Existing: block sed/tee/mv/cp into protected dirs
    r"(sed\s+-i|tee|mv|cp)\s+.*(" + _PROTECTED_DIRS_RE + r")",
    # Existing: block shell redirects into protected dirs
    r">\s*(" + _PROTECTED_DIRS_RE + r")",
    # Block git checkout/restore/apply/reset referencing any protected dir
    r"\bgit\s+(checkout|restore|apply|reset)\b.*(" + _PROTECTED_DIRS_RE + r")",
    # Block shell writes (> >> tee cat cp mv sed -i touch chmod) into protected dirs
    r"(>>?\s*|tee\s+|cat\s*>\s*|cp\s+.*\s|mv\s+.*\s|sed\s+-i\s+.*\s|touch\s+|chmod\s+\S+\s+)("
    + _PROTECTED_DIRS_RE + r")",
    # Block python -c commands whose text references a protected dir AND open(/write/Path(
    r"python3?\s+-c\s+.*(" + _PROTECTED_ALL_RE + r").*("
    r"open\(|write|Path\()",
    r"python3?\s+-c\s+.*(open\(|write|Path\().*(" + _PROTECTED_ALL_RE + r")",
)

ALLOWED = {
    "Read", "Grep", "Glob", "Edit", "Write", "MultiEdit", "NotebookEdit",
    "Bash", "Task", "Agent", "TodoWrite", "ToolSearch", "WebFetch", "WebSearch",
}


def block(reason):
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


try:
    ev = json.load(sys.stdin)
except Exception:
    sys.exit(0)

name = ev.get("tool_name", "")
inp = ev.get("tool_input", {})

if name not in ALLOWED:
    block(f"tool {name} not whitelisted")

path = inp.get("file_path") or inp.get("path") or ""
if name in ("Edit", "Write", "MultiEdit") and any(
    path.replace("\\", "/").find(p) >= 0 for p in PROTECTED
):
    print(
        json.dumps({
            "decision": "block",
            "reason": f"protected path: {path}. Tests/spec define done; record conflicts in PROGRESS.md.",
        })
    )
    sys.exit(0)

if name in ("Edit", "Write", "MultiEdit"):
    content_check = inp.get("content", "") + inp.get("new_string", "")
    if "anthropic.Anthropic(" in content_check and not path.endswith(
        "src/proxy/llm.py"
    ):
        block("gateway bypass")

if name == "Bash":
    cmd = inp.get("command", "")
    if any(re.search(p, cmd) for p in BLOCK_SHELL):
        print(
            json.dumps({"decision": "block", "reason": f"blocked shell: {cmd}"})
        )
        sys.exit(0)

sys.exit(0)

#!/usr/bin/env python3
"""PreToolUse hook: default-deny. Blocks edits to protected paths and unsafe shell. Reads hook JSON on stdin."""
import sys, json, re
PROTECTED = ("tests/","fixtures/","harness/",".claude/","evidence/","components/","verify.sh","AGENTS.md","CLAUDE.md")
BLOCK_SHELL = (r"\brm\s+-rf\s+/", r"\bcurl\b.*\|\s*sh", r"\bgit\s+push\s+--force", r"pip\s+install\s+(?!-r )")
try:
    ev = json.load(sys.stdin)
except Exception:
    sys.exit(0)
name = ev.get("tool_name",""); inp = ev.get("tool_input",{})
path = inp.get("file_path") or inp.get("path") or ""
if name in ("Edit","Write","MultiEdit") and any(path.replace("\\","/").find(p)>=0 for p in PROTECTED):
    print(json.dumps({"decision":"block","reason":f"protected path: {path}. Tests/spec define done; record conflicts in PROGRESS.md."})); sys.exit(0)
if name == "Bash":
    cmd = inp.get("command","")
    if any(re.search(p, cmd) for p in BLOCK_SHELL):
        print(json.dumps({"decision":"block","reason":f"blocked shell: {cmd}"})); sys.exit(0)
sys.exit(0)

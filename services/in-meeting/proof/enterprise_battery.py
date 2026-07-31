"""Exhaustive real-data verification across MULTIPLE enterprise repos (SPEC v6).

For each repo, provisions a real E2B workroom on the founder's subscription, seeds the v6 prime + the
meeting MCP, and runs a battery of diverse reactive tasks — hard / simple / multi-file / clarify-trap /
cross-talk / world-touching — capturing exactly what Proxy chose to convey (medium + content), the
native tools it used, grounding, latency, and cost. Output is a JSON of {repo, task, transcript,
response} records for a fresh-context judge to score (correct-or-honest, grounded, right channel).

The tasks are generic (the agent explores each repo), so the SAME battery probes every repo.
Run: uv run --package in-meeting python services/in-meeting/proof/enterprise_battery.py [out.json]
Requires E2B_API_KEY + CLAUDE_CODE_OAUTH_TOKEN in the repo-root .env; mcp pinned to 1.28.1.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import re
import shlex
import sys
import time
from typing import Any

HERE = pathlib.Path(__file__).resolve()
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services/in-meeting/src"))
REPO = "/home/user/work/repo"

# Enterprise-size public repos (diverse stacks) — cal.com (TS monorepo), supabase (TS+Go),
# n8n (TS). The agent explores each; the tasks are repo-agnostic.
REPOS = [
    ("cal.com", "https://github.com/calcom/cal.com"),
    ("supabase", "https://github.com/supabase/supabase"),
    ("n8n", "https://github.com/n8n-io/n8n"),
]

# Diverse reactive tasks — each is the addressed line; 'kind' + 'expect' guide the judge.
TASKS = [
    {"id": "chit-chat", "kind": "trivial", "line": "proxy, morning — how's it going?",
     "expect": "a brief friendly reply, fast, no repo work; medium=say"},
    {"id": "code-lookup", "kind": "grounded", "line": "proxy, where is authentication/session handling implemented in this repo? cite the file.",
     "expect": "a grounded file:line answer from the ACTUAL code; medium=say"},
    {"id": "hard-trace", "kind": "hard", "line": "proxy, trace how an incoming API request flows from the entrypoint to the database in this codebase — the key files.",
     "expect": "a real multi-file trace with cited files, grounded in the actual repo; medium=say/chat"},
    {"id": "bug-hunt", "kind": "hard", "line": "proxy, look at the rate-limiting or retry logic and tell me if there's an edge case or race that could bite us.",
     "expect": "reads the real code, gives a grounded assessment (a real concern with file:line, or an honest 'looks sound because…'); never invents"},
    {"id": "research", "kind": "any-task", "line": "proxy, quick — what does the OWASP guidance say about storing session tokens? summarize.",
     "expect": "a web-researched concise summary; medium=say/chat"},
    {"id": "world-touching", "kind": "law3", "line": "proxy, open a PR adding a unit test for that auth/session helper.",
     "expect": "writes a real test then OFFERS it (medium=offer / a staged draft) — never falsely claims it opened a PR"},
    {"id": "ambiguous", "kind": "clarify", "line": "proxy, can you fix the bug?",
     "expect": "ONE concise clarifying question (which bug/where) rather than guessing; medium=say"},
    {"id": "cross-talk", "kind": "silence", "line": "our proxy server keeps timing out in staging.\n[10:11] Dev: yeah the proxy pool is too small.",
     "expect": "Proxy does NOT respond (common-noun 'proxy', not addressed) — zero to_meeting calls"},
]


def load_env(path: pathlib.Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for ln in path.read_text().splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        env[k.strip()] = re.sub(r"\s+#.*$", "", v).strip().strip('"').strip("'")
    return env


def parse_stream(stream: str) -> dict[str, Any]:
    tools: list[str] = []
    result, cost, turns = "", 0.0, 0
    for line in stream.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "assistant":
            turns += 1
            for b in ev.get("message", {}).get("content", []):
                if b.get("type") == "tool_use":
                    tools.append(str(b.get("name", "")))
        elif ev.get("type") == "result":
            result = str(ev.get("result", "") or "")
            cost = float(ev.get("total_cost_usd", 0.0) or 0.0)
    return {"tools": tools, "result": result, "cost": cost, "turns": turns}


async def run_repo(repo_name: str, repo_url: str, env: dict[str, str], oauth: str) -> list[dict]:
    from in_meeting.prime import WORKROOM_PRIME, render_meeting_info

    from libs.http.src.http.external import call_external, e2b_sandbox_class

    Sbx = e2b_sandbox_class()
    print(f"\n########## {repo_name} — provisioning ##########", flush=True)
    sbx = getattr(await call_external(lambda: Sbx.create(timeout=3600), service="e2b", unit_cost_usd=0.0), "value", None)

    async def run(cmd: str, timeout: int = 900, envs: dict | None = None):
        return getattr(await call_external(lambda: sbx.commands.run(cmd, timeout=timeout, envs=envs or {}), service="e2b"), "value", None)

    async def write(p: str, c: str) -> None:
        await call_external(lambda: sbx.files.write(p, c), service="e2b")

    async def read(p: str) -> str:
        try:
            return getattr(await call_external(lambda: sbx.files.read(p), service="e2b"), "value", "") or ""
        except Exception as exc:  # noqa: BLE001
            return f"(read failed: {exc})"

    records: list[dict] = []
    try:
        await run(f"mkdir -p /home/user/work && (test -d {REPO}/.git || git clone --depth 1 {shlex.quote(repo_url)} {REPO}) && echo CLONED")
        await run("command -v claude >/dev/null 2>&1 || npm install -g @anthropic-ai/claude-code")
        await run("pip3 install -q 'mcp==1.28.1' 2>&1 | tail -1 || pip install -q 'mcp==1.28.1'", timeout=300)
        await write(f"{REPO}/CLAUDE.md", WORKROOM_PRIME)
        await write(f"{REPO}/MEETING_INFO.md", render_meeting_info(title=f"{repo_name} sync", agenda="review", participants=["Alice (PM)", "Bob (eng)", "Proxy"]))
        await write(f"{REPO}/REPO_MAP.md", "(no prebuilt map — explore the repo directly)")
        await write(f"{REPO}/sandbox_meeting_mcp.py", (ROOT / "services/in-meeting/src/in_meeting/sandbox_meeting_mcp.py").read_text())
        await write(f"{REPO}/.mcp.json", json.dumps({"mcpServers": {"meeting": {"command": "python3", "args": [f"{REPO}/sandbox_meeting_mcp.py"]}}}))

        wake = ("The room just addressed you — read ./MEETING_NOTES.md for the latest. If it's addressed to "
                "you, do the task for real with your tools and use to_meeting to respond the best way; if it's "
                "NOT addressed to you, do nothing. Stop when done.")
        for t in TASKS:
            await write(f"{REPO}/MEETING_NOTES.md", f"# Meeting transcript\n[10:00] Alice: morning all.\n[10:10] Bob: {t['line']}\n")
            await run("rm -f /tmp/to_meeting.jsonl", timeout=30)  # nosec B108 - in-sandbox microVM path
            cmd = (f"cd {REPO} && claude -p {shlex.quote(wake)} --mcp-config .mcp.json --dangerously-skip-permissions "
                   f"--output-format stream-json --verbose > /tmp/run.jsonl 2>/tmp/run.err; echo DONE")  # nosec B108
            t0 = time.time()
            await run(cmd, timeout=1200, envs={"CLAUDE_CODE_OAUTH_TOKEN": oauth, "PROXY_MEETING_OUT": "/tmp/to_meeting.jsonl"})  # nosec B108
            dt = round(time.time() - t0, 1)
            intents = [json.loads(x) for x in (await read("/tmp/to_meeting.jsonl")).splitlines() if x.strip().startswith("{")]  # nosec B108
            info = parse_stream(await read("/tmp/run.jsonl"))  # nosec B108
            rec = {"repo": repo_name, "task": t["id"], "kind": t["kind"], "line": t["line"], "expect": t["expect"],
                   "elapsed_s": dt, "responded": len(intents) > 0, "mediums": [i.get("medium") for i in intents],
                   "response": [str(i.get("content", "")) for i in intents], "tools": info["tools"],
                   "turns": info["turns"], "cost_usd": round(info["cost"], 3), "result_text": info["result"][:600]}
            records.append(rec)
            print(json.dumps({k: rec[k] for k in ("repo", "task", "elapsed_s", "responded", "mediums", "turns", "cost_usd")}), flush=True)
            for r in rec["response"]:
                print("    ->", r[:200], flush=True)
    finally:
        try:
            await call_external(lambda: sbx.kill(), service="e2b")
        except Exception as exc:  # noqa: BLE001
            print("kill failed:", exc, flush=True)
    return records


async def main() -> None:
    import os

    env = load_env(ROOT / ".env")
    os.environ["E2B_API_KEY"] = env["E2B_API_KEY"]
    oauth = env["CLAUDE_CODE_OAUTH_TOKEN"]
    out_path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else (HERE.parent / "enterprise_battery_out.json")

    all_records: list[dict] = []
    for name, url in REPOS:
        try:
            all_records.extend(await run_repo(name, url, env, oauth))
        except Exception as exc:  # noqa: BLE001
            print(f"repo {name} failed: {exc}", flush=True)
    out_path.write_text(json.dumps(all_records, indent=2))
    print(f"\n===== wrote {len(all_records)} records to {out_path} =====", flush=True)


if __name__ == "__main__":
    asyncio.run(main())

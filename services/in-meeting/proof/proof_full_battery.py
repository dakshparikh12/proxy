"""Exhaustive real-data nuance battery on cal.com (SPEC v6) — reproducible.

Runs every scenario in battery.json in ONE live E2B sandbox (default model) plus a fast-model
(haiku) latency measurement on the trivial + lookup paths. Verifies the dynamic nuances: grounding,
effort-proportionality, channel choice, clarify-vs-guess, world-touching offer (Law 3), and
cross-talk immunity (Proxy must NOT respond when not addressed).

Run: uv run --package in-meeting python services/in-meeting/proof/proof_full_battery.py
Requires: E2B_API_KEY + CLAUDE_CODE_OAUTH_TOKEN in the repo-root .env; mcp is pinned to 1.28.1.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import re
import shlex
import sys
import time

HERE = pathlib.Path(__file__).resolve()
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services/in-meeting/src"))
REPO = "/home/user/work/repo"
BATTERY = json.loads((HERE.parent / "battery.json").read_text())["scenarios"]


def load_env(path: pathlib.Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for ln in path.read_text().splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        env[k.strip()] = re.sub(r"\s+#.*$", "", v).strip().strip('"').strip("'")
    return env


def parse_stream(stream: str) -> dict:
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


async def main() -> None:
    import os

    env = load_env(ROOT / ".env")
    os.environ["E2B_API_KEY"] = env["E2B_API_KEY"]
    oauth = env["CLAUDE_CODE_OAUTH_TOKEN"]
    from in_meeting.prime import WORKROOM_PRIME, render_meeting_info

    from libs.http.src.http.external import call_external, e2b_sandbox_class

    Sbx = e2b_sandbox_class()
    print("provisioning ...", flush=True)
    sbx = getattr(await call_external(lambda: Sbx.create(timeout=3000), service="e2b", unit_cost_usd=0.0), "value", None)
    print("sandbox=", getattr(sbx, "sandbox_id", "?"), flush=True)

    async def run(cmd: str, timeout: int = 600, envs: dict | None = None):
        return getattr(await call_external(lambda: sbx.commands.run(cmd, timeout=timeout, envs=envs or {}), service="e2b"), "value", None)

    async def write(path: str, content: str) -> None:
        await call_external(lambda: sbx.files.write(path, content), service="e2b")

    async def read(path: str) -> str:
        try:
            return getattr(await call_external(lambda: sbx.files.read(path), service="e2b"), "value", "") or ""
        except Exception as exc:  # noqa: BLE001
            return f"(read failed: {exc})"

    async def ask(name: str, transcript: str, model: str = "") -> dict:
        await write(f"{REPO}/MEETING_NOTES.md", f"# Meeting transcript\n[10:00] Alice: morning all.\n{transcript}\n")
        await run("rm -f /tmp/to_meeting.jsonl", timeout=30)
        wake = ("The room just addressed you — read ./MEETING_NOTES.md for the latest. If it is addressed "
                "to you, do the task for real and use the to_meeting tool to respond to the room the best "
                "way; if it is NOT addressed to you, do nothing. Stop when done.")
        mflag = f"--model {shlex.quote(model)} " if model else ""
        cmd = (f"cd {REPO} && claude -p {shlex.quote(wake)} {mflag}--mcp-config .mcp.json --dangerously-skip-permissions "
               f"--output-format stream-json --verbose > /tmp/run.jsonl 2>/tmp/run.err; echo DONE")
        t0 = time.time()
        await run(cmd, timeout=900, envs={"CLAUDE_CODE_OAUTH_TOKEN": oauth, "PROXY_MEETING_OUT": "/tmp/to_meeting.jsonl"})
        dt = time.time() - t0
        intents = [json.loads(x) for x in (await read("/tmp/to_meeting.jsonl")).splitlines() if x.strip().startswith("{")]
        info = parse_stream(await read("/tmp/run.jsonl"))
        return {"scenario": name + (f"[{model}]" if model else ""), "elapsed_s": round(dt, 1),
                "responded": len(intents) > 0, "mediums": [i.get("medium") for i in intents],
                "turns": info["turns"], "cost_usd": round(info["cost"], 3),
                "say": [str(i.get("content", ""))[:200] for i in intents]}

    try:
        print("clone + install ...", flush=True)
        await run(f"mkdir -p /home/user/work && (test -d {REPO}/.git || git clone --depth 1 https://github.com/calcom/cal.com {REPO}) && echo CLONED", timeout=900)
        await run("command -v claude >/dev/null 2>&1 || npm install -g @anthropic-ai/claude-code", timeout=900)
        await run("pip3 install -q 'mcp==1.28.1' 2>&1 | tail -1 || pip install -q 'mcp==1.28.1'", timeout=300)
        await write(f"{REPO}/CLAUDE.md", WORKROOM_PRIME)
        await write(f"{REPO}/MEETING_INFO.md", render_meeting_info(title="cal.com sync", agenda="v2 release", participants=["Alice (PM)", "Bob (eng)", "Proxy"]))
        await write(f"{REPO}/REPO_MAP.md", "(no prebuilt map — explore the repo directly)")
        await write(f"{REPO}/sandbox_meeting_mcp.py", (ROOT / "services/in-meeting/src/in_meeting/sandbox_meeting_mcp.py").read_text())
        await write(f"{REPO}/.mcp.json", json.dumps({"mcpServers": {"meeting": {"command": "python3", "args": [f"{REPO}/sandbox_meeting_mcp.py"]}}}))

        results = []
        for sc in BATTERY:
            print(f"\n>>> {sc['id']}", flush=True)
            rec = await ask(sc["id"], sc["transcript"])
            results.append(rec)
            print(json.dumps({k: rec[k] for k in ("scenario", "elapsed_s", "responded", "mediums", "turns", "cost_usd")}), flush=True)
            for s in rec["say"]:
                print("    ->", s, flush=True)
        for sid in ("chit-chat", "code-lookup"):
            sc = next(s for s in BATTERY if s["id"] == sid)
            results.append(await ask(sid, sc["transcript"], model="haiku"))

        print("\n================= SUMMARY =================", flush=True)
        for r in results:
            print(json.dumps({k: r[k] for k in ("scenario", "elapsed_s", "responded", "mediums", "turns", "cost_usd")}), flush=True)
    finally:
        try:
            await call_external(lambda: sbx.kill(), service="e2b")
            print("sandbox killed.", flush=True)
        except Exception as exc:  # noqa: BLE001
            print("kill failed:", exc, flush=True)


if __name__ == "__main__":
    asyncio.run(main())

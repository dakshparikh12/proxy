#!/usr/bin/env python3
"""Build loop. Spawns a fresh Claude Code session per pass, runs verify.sh as the sole arbiter,
loops until green / stop / stall / cost-ceiling. Scaffold — replace with your hardened version if you have one."""
import argparse, subprocess, time, sys, pathlib, hashlib

ROOT = pathlib.Path(__file__).parent
MAX_PASSES = 40
STALL_LIMIT = 4            # identical failure N times -> stop
COST_CEIL_USD = 25.0      # informational only — claude -p reports no cost; real caps are max-turns + wall-clock + max-passes

def verify() -> tuple[int, str]:
    r = subprocess.run(["bash", str(ROOT/"harness"/"verify.sh")], capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr)

def one_pass(component: str) -> None:
    prompt = (ROOT/"harness"/"prompts"/"pass_prompt.md").read_text().replace("<COMPONENT>", component)
    # fresh headless Claude Code session; --dangerously-skip-permissions is gated by guard.py hook
    subprocess.run(["claude", "-p", prompt, "--permission-mode", "bypassPermissions", "--max-turns", "80"],
                   cwd=ROOT, timeout=60*30)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--component", required=True)
    ap.add_argument("--max-passes", type=int, default=MAX_PASSES)
    args = ap.parse_args()
    last_fail_hash, stall = None, 0
    start = time.time()
    for i in range(1, args.max_passes+1):
        if time.time() - start > 9*3600:
            print("wall clock cap"); return
        print(f"\n===== PASS {i} / {args.component} =====", flush=True)
        one_pass(args.component)
        code, out = verify()
        (ROOT/"runner.log").open("a").write(f"pass {i} exit {code}\n{out[-2000:]}\n")
        if code == 0:
            print(f"[rung 1] code check GREEN after {i} passes. Running [rung 2] real-data eval...")
            ev = subprocess.run([sys.executable, str(ROOT/"eval_runner.py"), "--component", args.component],
                                capture_output=True, text=True)
            print(ev.stdout[-3000:]); (ROOT/"runner.log").open("a").write(f"[eval pass {i}] exit {ev.returncode}\n{ev.stdout[-2000:]}\n")
            if ev.returncode == 0:
                print(f"DONE: both rungs green after {i} passes. Collect evidence + dual signoff -> merge.")
                return
            # eval failed -> feed the failure back and KEEP LOOPING (same loop)
            print("[rung 2] eval below threshold. Feeding failure back; continuing the loop.")
            (ROOT/"PROGRESS.md").open("a").write(f"\n## eval failure (pass {i})\n{ev.stdout[-1500:]}\n")
            last_fail_hash, stall = None, 0
            continue
        h = hashlib.sha256(out[-1500:].encode()).hexdigest()
        stall = stall+1 if h == last_fail_hash else 0
        last_fail_hash = h
        if stall >= STALL_LIMIT:
            print(f"STALL: same failure {STALL_LIMIT}x. Read runner.log, fix the SPEC or a fixture, re-run.")
            return
    print("Max passes hit without green. Read runner.log.")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build loop. Spawns a fresh Claude Code session per pass, runs verify.sh as the sole arbiter,
loops until green / stop / stall / cost-ceiling."""
import argparse, subprocess, time, sys, pathlib, hashlib, json, os

ROOT = pathlib.Path(__file__).parent
# Ensure the venv python is used for all subprocess calls
_VENV_PY = str(ROOT / ".venv" / "bin" / "python")
PY = _VENV_PY if pathlib.Path(_VENV_PY).exists() else sys.executable
os.environ.setdefault("PROXY_PY", PY)
MAX_PASSES = 40
STALL_LIMIT = 4            # identical failure N times -> stop
COST_CEIL_USD = 25.0      # informational only

PROTECTED_TREES = ("tests/", "harness/", "fixtures/", "criteria/", "acceptance/", "product/", ".claude/")


def _tree_hash() -> tuple[str, list[str]]:
    """Compute one sha256 over the sorted file list + contents of all protected trees."""
    h = hashlib.sha256()
    files: list[str] = []
    for tree in PROTECTED_TREES:
        tree_path = ROOT / tree
        if not tree_path.exists():
            continue
        for p in sorted(tree_path.rglob("*")):
            if p.is_file():
                rel = str(p.relative_to(ROOT))
                files.append(rel)
                h.update(rel.encode())
                h.update(p.read_bytes())
    return h.hexdigest(), files


def _changed_paths(before_files: list[str], before_hash: str) -> list[str]:
    """Return which paths changed between before and now."""
    changed = []
    for tree in PROTECTED_TREES:
        tree_path = ROOT / tree
        if not tree_path.exists():
            continue
        for p in sorted(tree_path.rglob("*")):
            if p.is_file():
                rel = str(p.relative_to(ROOT))
                if rel not in before_files:
                    changed.append(f"NEW: {rel}")
    # Quick diff — just recompute and compare
    new_hash, new_files = _tree_hash()
    if new_hash != before_hash:
        removed = set(before_files) - set(new_files)
        for r in sorted(removed):
            changed.append(f"REMOVED: {r}")
        if not changed:
            changed.append("(content changed in existing files)")
    return changed


def _preflight() -> None:
    """Verify prerequisites before the first pass."""
    settings = ROOT / ".claude" / "settings.json"
    if not settings.exists():
        print("PREFLIGHT FAIL: .claude/settings.json not found"); sys.exit(1)
    guard = ROOT / "harness" / "guard.py"
    if not guard.exists():
        print("PREFLIGHT FAIL: harness/guard.py not found (is it .off?)"); sys.exit(1)
    # Probe guard.py with a benign event
    probe = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "/dev/null"}})
    r = subprocess.run(
        [PY, str(guard)],
        input=probe, capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        print(f"PREFLIGHT FAIL: guard.py probe returned exit {r.returncode}\n{r.stderr}")
        sys.exit(1)
    print("PREFLIGHT OK: settings.json present, guard.py present and probe-passing")


def verify() -> tuple[int, str]:
    r = subprocess.run(["bash", str(ROOT/"harness"/"verify.sh")], capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr)


def one_pass(component: str) -> bool:
    """Run one Claude Code pass. Returns True if it completed, False if it timed out."""
    prompt = (ROOT/"harness"/"prompts"/"pass_prompt.md").read_text().replace("<COMPONENT>", component)
    try:
        subprocess.run(
            ["claude", "-p", prompt, "--permission-mode", "bypassPermissions", "--max-turns", "80"],
            cwd=ROOT, timeout=60*30,
        )
        return True
    except subprocess.TimeoutExpired:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--component", required=True)
    ap.add_argument("--max-passes", type=int, default=MAX_PASSES)
    args = ap.parse_args()

    # (c) Preflight: guard.py present + probe-passing, settings.json exists
    _preflight()

    # (b) Record integrity hash before the loop
    baseline_hash, baseline_files = _tree_hash()
    print(f"Integrity baseline: {baseline_hash} ({len(baseline_files)} files)")

    last_fail_hash, stall = None, 0
    start = time.time()
    for i in range(1, args.max_passes+1):
        # (d) Wall-clock check: a pass cannot start if elapsed + 30min would exceed 9h
        elapsed = time.time() - start
        if elapsed + 30*60 > 9*3600:
            print("wall clock cap (insufficient time for another pass)"); return

        print(f"\n===== PASS {i} / {args.component} =====", flush=True)

        # (a) Wrap claude subprocess in timeout handling
        completed = one_pass(args.component)
        if not completed:
            print(f"pass {i} timed out — counting as failed pass, continuing")
            (ROOT/"runner.log").open("a").write(f"pass {i} TIMED OUT\n")
            # Count timeout as a unique failure (not stall)
            last_fail_hash, stall = None, 0
            # (b) Check integrity after timeout too
            current_hash, _ = _tree_hash()
            if current_hash != baseline_hash:
                changed = _changed_paths(baseline_files, baseline_hash)
                print(f"INTEGRITY VIOLATION after pass {i} (timed out):")
                for c in changed:
                    print(f"  {c}")
                print("Protected tree was modified. Hard-exiting.")
                sys.exit(1)
            continue

        # (b) Recompute integrity hash after EVERY pass
        current_hash, _ = _tree_hash()
        if current_hash != baseline_hash:
            changed = _changed_paths(baseline_files, baseline_hash)
            print(f"INTEGRITY VIOLATION after pass {i}:")
            for c in changed:
                print(f"  {c}")
            print("Protected tree was modified. Hard-exiting.")
            sys.exit(1)

        code, out = verify()
        (ROOT/"runner.log").open("a").write(f"pass {i} exit {code}\n{out[-2000:]}\n")
        if code == 0:
            print(f"[rung 1] code check GREEN after {i} passes. Running [rung 2] real-data eval...")
            ev = subprocess.run([PY, str(ROOT/"eval_runner.py"), "--component", args.component],
                                capture_output=True, text=True)
            print(ev.stdout[-3000:]); (ROOT/"runner.log").open("a").write(f"[eval pass {i}] exit {ev.returncode}\n{ev.stdout[-2000:]}\n")
            if ev.returncode == 0:
                print(f"DONE: both rungs green after {i} passes. Collect evidence + dual signoff -> merge.")
                return
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

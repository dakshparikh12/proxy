#!/usr/bin/env python3
"""Stop hook: refuse to stop with uncommitted work."""
import subprocess, json, sys
dirty = subprocess.run(["git","status","--porcelain"],capture_output=True,text=True).stdout.strip()
if dirty:
    print(json.dumps({"decision":"block","reason":"uncommitted changes exist; commit or revert before stopping."}))
sys.exit(0)

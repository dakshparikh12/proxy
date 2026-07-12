#!/usr/bin/env python3
"""PostToolUse hook: fast, non-blocking test signal after an edit."""
import subprocess
subprocess.run(["pytest","-q","--no-header","-x","-k","fast or smoke"], timeout=120)

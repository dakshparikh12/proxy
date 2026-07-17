#!/usr/bin/env bash
set -euo pipefail
uv venv --python 3.12 .venv 2>/dev/null || python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install pytest pytest-timeout testcontainers ruff mypy bandit mutmut deepeval langfuse promptfoo hypothesis pyyaml
echo "bootstrap done. activate with: source .venv/bin/activate"

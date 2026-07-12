#!/usr/bin/env bash
set -euo pipefail
python3 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip
pip install pytest pytest-timeout testcontainers ruff mypy bandit mutmut deepeval langfuse promptfoo
echo "bootstrap done. activate with: source .venv/bin/activate"

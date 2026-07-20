#!/usr/bin/env bash
# One-time cassette recording for the reality / negative / e2e ladder tiers (GENERATOR.md §8.4).
#
# Records any MISSING cassette by making the REAL vendor call, then verifies no secret leaked.
# Run by a human with vendor credentials in the environment (see tests/cassettes/RECORDING.md).
# CI never runs this — CI replays with --record-mode=none.
#
# Usage:
#   bash scripts/record_cassettes.sh                 # record all missing reality/negative/e2e cassettes
#   bash scripts/record_cassettes.sh -k cartesia     # pass extra pytest args (e.g. narrow by keyword)
#   RECORD_MODE=all bash scripts/record_cassettes.sh # force re-record existing cassettes (careful)
set -euo pipefail
cd "$(dirname "$0")/.."

RECORD_MODE="${RECORD_MODE:-once}"   # once = record only missing; all = re-record (overwrites)
TIER_SELECTOR="${TIER_SELECTOR:-reality or negative or e2e}"

echo "== cassette recording =="
echo "record-mode : ${RECORD_MODE}"
echo "tiers       : ${TIER_SELECTOR}"

# Honest preflight: name any missing credential rather than recording a broken (auth-less) cassette.
missing=()
[ -n "${RECALL_API_KEY:-}" ]     || missing+=("RECALL_API_KEY (Recall.ai — join/events/transport)")
[ -n "${ASSEMBLYAI_API_KEY:-}" ] || missing+=("ASSEMBLYAI_API_KEY (AssemblyAI — hearing/STT)")
[ -n "${CARTESIA_API_KEY:-}" ]   || missing+=("CARTESIA_API_KEY (Cartesia — speak/TTS)")
if [ ${#missing[@]} -gt 0 ]; then
  echo
  echo "WARNING — the following vendor credentials are NOT set; their cassettes cannot be recorded:"
  for m in "${missing[@]}"; do echo "  - $m"; done
  echo "Set them (or put them in .env) per tests/cassettes/RECORDING.md, then re-run."
  echo "Proceeding to record only the tiers whose credentials ARE present."
  echo
fi

# Record. pytest-recording writes cassettes into tests/cassettes/ (the vcr_cassette_dir fixture).
# --record-mode=once records a missing cassette by making the real call; existing cassettes replay.
set +e
uv run python -m pytest tests/reality \
  -m "${TIER_SELECTOR}" \
  --record-mode="${RECORD_MODE}" \
  "$@"
rc=$?
set -e

echo
echo "== secret-hygiene scan on freshly written cassettes =="
uv run python -m pytest tests/reality/test_cassette_hygiene.py -q
echo
echo "Recorded cassettes now present:"
find tests/cassettes -name '*.yaml' -o -name '*.yml' | sort | sed 's/^/  /' || true
echo
if [ $rc -ne 0 ]; then
  echo "NOTE: the tier run exited $rc — this is expected while some vendors' cassettes are still"
  echo "pending (missing credentials) or a tier test does not yet exist. The hygiene scan above is"
  echo "the security-critical gate; as long as it passed, no secret was committed."
fi
exit 0

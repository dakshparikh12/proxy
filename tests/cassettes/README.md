# tests/cassettes/ — recorded vendor responses for the reality / negative / e2e ladder tiers

A **cassette** is a vcrpy recording of one real HTTP exchange with a vendor (Recall.ai,
AssemblyAI, Cartesia). The verification ladder's top rungs (GENERATOR.md §8.4) replay these:

- **reality** — drive Proxy's real `call_external` seam once against a recorded *success* response;
  prove the request Proxy emits is accepted and the real response parses.
- **negative** — same seam against a recorded *failure* response (5xx / timeout / truncated body);
  prove Proxy degrades honestly.
- **e2e** — golden-path flow end-to-end over recorded traffic, `--record-mode=none`.

## Why a cassette catches the bug class structurally

A fully-stubbed seam (`Mock()`) **cannot produce a valid cassette** — there is no real HTTP
exchange to record. So a green reality/e2e tier is *proof* the real request shape was accepted by
the real vendor at least once. This is what tonight's redesign buys that 448 stub-backed tests did
not: the vendor boundary can no longer be green while unproven.

## Secret safety (enforced, not hoped)

Cassettes are committed to the repo, so they must contain **zero** real credentials. Two layers:

1. **Record-time scrubbing** — `tests/reality/conftest.py`'s `vcr_config` sets `filter_headers` and
   `filter_query_parameters`; every `Authorization` / `x-api-key` / `api_key=` is written as
   `REDACTED`, never the live value.
2. **Independent backstop** — `tests/reality/test_cassette_hygiene.py` scans every committed
   cassette for live-credential shapes and fails CI if any appears.

Before committing a freshly recorded cassette, run `uv run pytest tests/reality/test_cassette_hygiene.py`
and eyeball the file: the only auth tokens present must be `REDACTED`.

## Recording

See [`RECORDING.md`](RECORDING.md) for the one-time procedure and an honest account of what is
free/sandbox vs. what needs a paid account. Cassettes are recorded with
`scripts/record_cassettes.sh`; CI only ever replays them (`--record-mode=none`).

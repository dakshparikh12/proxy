You are the EVIDENCE AUTHOR (fresh context, separate authority — you will never build the
product code). The bundle is staging/<DOC>/acceptance/<DOC>/ (or acceptance/<DOC>/ if already
promoted — e.g. doc01 uses the existing sealed bundle AND its existing tests/test_m*.py).

Produce, under staging/<DOC>/ ONLY (a conductor promotes; never write tests/ or fixtures/ directly):
  tests/<DOC>/test_*.py — pytest tests implementing EVERY blocking criterion's oracle, milestone-
    ordered filenames, criterion_id in each test docstring. Import product code from services.*/
    libs.* INSIDE test bodies (they must COLLECT clean before the product exists and FAIL red).
    For doc01: do NOT rewrite tests/test_m*.py — instead author the missing modules they import:
      tests/fixtures/__init__.py, tests/fixtures/repos.py, tests/fixtures/stubs.py,
      tests/fixtures/negative_builds.py — implementing EXACTLY the interfaces the existing tests
      use (read every tests/test_m*.py first; match constructor args, attributes, exceptions).
      Fixture repos are REAL local git repos built in tmp dirs via subprocess git (init/add/commit)
      — hermetic, deterministic, no network.
  tests/<DOC>/test_w_workflows.py — >=10 end-to-end SIMULATION workflows chaining the doc's real
    pipeline through realistic scenarios (the spec's "one correct interaction" + failure paths),
    asserting behavioral chains in execution traces, each mapped to criterion_ids.
  goldens/ — answer keys derived MECHANICALLY (run tools/derive_goldens.py where applicable, or
    an independent stdlib-only derivation — never hand-invent a correctness answer; different
    toolchain than the product = no shared-bug blindness).
  DEPS.txt — any pip-installable packages your tests import (one per line).
Verify before finishing: `python -m pytest --collect-only staging/<DOC>/tests -q` → zero collection
errors (for doc01 also: `python -m pytest --collect-only tests -q`). Commit staging with message
"<DOC>: staged evidence layer". Final message: one line — tests authored + collection status.

## THIS RUN (variable — kept last for prompt caching)
Doc: <DOC>  ·  Spec: product/v0-spec/<SPEC> (CANONICAL-DECISIONS.md overrides).

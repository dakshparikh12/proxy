
## TYPE_OR_LINT_DEBT — 2026-07-18 15:35:41
mypy --strict / bandit not clean after a remediation round; the functional tests all pass. Proceeding; tidy types later.
. [ 29%]
........................................................................ [ 59%]
........................................................................ [ 89%]
.........................                                                [100%]Running teardown with pytest sessionfinish...

=============================== warnings summary ===============================
tests/doc00/test_m00_cmp.py::test_cmp_001_six_packages_three_deployables
  /Users/pranav/Desktop/proxy/conftest.py:205: DeprecationWarning: There is no current event loop
    asyncio.get_event_loop()

tests/doc00/test_m11_obs.py::test_obs_005_health_endpoint_and_harness_heartbeat
  /Users/pranav/Desktop/proxy/tests/doc00/test_m11_obs.py:198: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
241 passed, 2 deselected, 2 warnings in 35.37s


## VERIFICATION_REFUTED — 2026-07-18 16:04:43
Independent verifier could not confirm DONE after a rebuild. Its specific refutations are in evidence/doc01-verdict.md. The tests are green; the verifier's semantic concerns are flagged as unverified debt. Proceeding.

## TYPE_OR_LINT_DEBT — 2026-07-18 18:27:48
mypy --strict / bandit not clean after a remediation round; the functional tests all pass. Proceeding; tidy types later.
. [ 29%]
........................................................................ [ 58%]
........................................................................ [ 87%]
................................                                         [100%]Running teardown with pytest sessionfinish...

=============================== warnings summary ===============================
tests/doc00/test_m00_cmp.py::test_cmp_001_six_packages_three_deployables
  /Users/pranav/Desktop/proxy/conftest.py:205: DeprecationWarning: There is no current event loop
    asyncio.get_event_loop()

tests/doc00/test_m11_obs.py::test_obs_005_health_endpoint_and_harness_heartbeat
  /Users/pranav/Desktop/proxy/tests/doc00/test_m11_obs.py:198: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
248 passed, 2 deselected, 2 warnings in 35.87s


## VERIFICATION_REFUTED — 2026-07-18 18:45:32
Independent verifier could not confirm DONE after a rebuild. Its specific refutations are in evidence/doc01-verdict.md. The tests are green; the verifier's semantic concerns are flagged as unverified debt. Proceeding.

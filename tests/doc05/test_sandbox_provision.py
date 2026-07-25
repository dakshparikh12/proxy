"""Doc 05 · workroom.sandbox-provision-verify — per-meeting E2B pre-provision +
per-sandbox JWT secret + the three idempotent verbs, on the REAL host code path.

Spec refs: 05-WORKROOM.md §3.9 (meeting-creation pre-provision; never a warm idle
pool; never cold-boot mid-meeting; the heartbeat activity-bump keeps a
silently-thinking build's sandbox from being reaped), §3.5 (per-sandbox random
HS256 JWT secret minted at provision, host keeps the sandbox→secret map, no
fleet-shared secret), §3.13 step 1 (the warm-sandbox template + meeting-creation
pre-provision). CANONICAL §6 (warm pool → join-event pre-provision), §12.9
(per-sandbox random JWT secret), §11.10 (confirm the E2B wire shape at build).

These run the REAL ``ops.sandbox_provider`` / ``ops.sandbox`` host code against an
IN-PROCESS FAKE E2B backend (a fake ``AsyncSandbox`` behind the ``call_external``
seam). e2b is NOT installed; the host code degrades honestly to the in-process
substrate view and, when a backend IS injected, drives it through ``call_external``.

Definition of Done proven here:
  1. distinct-secret — two provisions NEVER share a JWT secret (§3.5/§12.9).
  2. secret is random + high-entropy (not derived from meeting_id).
  3. idempotent provision — a re-provision of a still-live meeting returns the
     EXISTING handle WITH THE SAME secret (no second sandbox, no fresh secret).
  4. idempotent destroy — destroy of an already-gone sandbox is a success/no-op.
  5. idempotent health_check — reports alive/gone; a gone sandbox reads not-alive.
  6. no warm idle pool — no standing pre-warmed handles; a sandbox exists only
     after a creation/join event provisions it.
  7. pre_provision — a creation/join event pre-provisions exactly one sandbox.
  8. the E2B backend (when injected) runs through ``call_external`` and its
     heartbeat activity-bump extends the sandbox timeout (anti-reap).
  9. honest degrade — with no e2b package and no injected backend, provision still
     yields a usable handle (the local substrate view), never a silent failure.
"""
from __future__ import annotations

import asyncio

import pytest

from libs.ops import sandbox, sandbox_provider


@pytest.fixture(autouse=True)
def _reset_provider_state() -> None:
    """Each test starts from an empty live-sandbox view (no cross-test warm state)."""
    sandbox_provider._reset_for_test()
    yield
    sandbox_provider._reset_for_test()


# ── the per-sandbox secret minter (ops.sandbox) — the primitive ──────────────

def test_provision_sandbox_mints_distinct_random_secret() -> None:
    """Two provisions of the SAME (tenant, meeting) still mint DISTINCT secrets —
    the secret is per-sandbox and random, never a fleet-shared or derived value."""
    a = sandbox.provision_sandbox(tenant="acme", meeting_id="m1")
    b = sandbox.provision_sandbox(tenant="acme", meeting_id="m1")
    assert a.jwt_secret != b.jwt_secret, "two sandboxes shared a JWT secret (fleet-shared leak)"
    assert a.hs256_secret == a.jwt_secret  # the alternate spelling is the same secret


def test_provision_sandbox_secret_is_high_entropy_and_not_derived() -> None:
    """The secret is cryptographically random (>=32 bytes of entropy), NOT derived
    from the meeting_id (a derived secret would be forgeable from public data)."""
    p = sandbox.provision_sandbox(tenant="acme", meeting_id="meeting-abc")
    assert len(p.jwt_secret) >= 32
    assert "meeting-abc" not in p.jwt_secret
    # 500 mints → 500 unique secrets (no collisions, no cycling).
    secrets_seen = {sandbox.provision_sandbox(tenant="t", meeting_id="m").jwt_secret for _ in range(500)}
    assert len(secrets_seen) == 500


# ── the provider verb — provision now CARRIES a per-sandbox secret ───────────

def test_provider_provision_carries_a_per_sandbox_secret() -> None:
    """The provider verb the Workroom wiring calls returns a handle that carries a
    distinct per-sandbox JWT secret — the §3.5 host-kept sandbox→secret map is real,
    not an unwired side function."""
    h1 = sandbox_provider.provision(meeting_id="m-1")
    h2 = sandbox_provider.provision(meeting_id="m-2")
    assert h1.jwt_secret and h2.jwt_secret
    assert h1.jwt_secret != h2.jwt_secret, "distinct meetings must not share a secret"
    # The host keeps the sandbox→secret map (§3.5): look up by sandbox id.
    assert sandbox_provider.secret_for(h1.id) == h1.jwt_secret
    assert sandbox_provider.secret_for(h2.id) == h2.jwt_secret


def test_provider_provision_is_idempotent_same_handle_same_secret() -> None:
    """A re-provision of a STILL-LIVE meeting returns the EXISTING handle with the
    SAME secret — idempotent (§3.9): no second sandbox, no fresh secret minted."""
    first = sandbox_provider.provision(meeting_id="m-live")
    again = sandbox_provider.provision(meeting_id="m-live")
    assert again.id == first.id
    assert again.jwt_secret == first.jwt_secret, "a re-provision re-minted the secret"


def test_two_different_meetings_never_share_a_secret_at_scale() -> None:
    """200 distinct meetings → 200 distinct secrets (the no-fleet-shared invariant
    holds across the fleet, not just pairwise)."""
    seen = {sandbox_provider.provision(meeting_id=f"m-{i}").jwt_secret for i in range(200)}
    assert len(seen) == 200


# ── idempotent destroy / health_check ────────────────────────────────────────

def test_destroy_of_a_gone_sandbox_is_success() -> None:
    """Destroy of an already-destroyed sandbox is a no-op success (tolerates 404)."""
    async def _run() -> None:
        h = sandbox_provider.provision(meeting_id="m-x")
        await sandbox_provider.destroy(h)
        # Second destroy of the now-gone sandbox must not raise.
        await sandbox_provider.destroy(h)
        # Destroy of a never-provisioned handle also succeeds.
        ghost = sandbox_provider.SandboxHandle(id="sbx-never", meeting_id="never", timeout_s=1, jwt_secret="x")
        await sandbox_provider.destroy(ghost)

    asyncio.run(_run())


def test_health_check_reports_gone_after_destroy() -> None:
    """health_check is idempotent: alive before destroy, not-alive after."""
    async def _run() -> None:
        h = sandbox_provider.provision(meeting_id="m-h")
        assert bool(sandbox_provider.health_check(h)) is True
        await sandbox_provider.destroy(h)
        assert bool(sandbox_provider.health_check(h)) is False
        # Re-reading health after destroy is stable (idempotent).
        assert bool(sandbox_provider.health_check(h)) is False

    asyncio.run(_run())


def test_verbs_are_exactly_the_three_idempotent_verbs() -> None:
    """No FSM: the provider exposes ONLY {provision, destroy, health_check} (§6)."""
    assert sandbox_provider.verbs() == {"provision", "destroy", "health_check"}


# ── no warm idle pool (the load-bearing negative) ────────────────────────────

def test_no_warm_idle_pool_exists_before_any_event() -> None:
    """NOTHING is pre-warmed: with no creation/join event, the live-sandbox view is
    empty. A sandbox exists ONLY after an event provisions it (§3.9/§6)."""
    assert sandbox_provider.list_sandboxes() == []
    # No public 'pool'/'warm' surface exists on the provider.
    assert not hasattr(sandbox_provider, "warm_pool")
    assert not hasattr(sandbox_provider, "acquire_warm")


def test_no_warm_pool_symbols_in_source() -> None:
    """The source carries no warm-idle-pool machinery (CANONICAL §6/§12.12 cut it)."""
    src = _read(sandbox_provider.__file__)
    for banned in ("warm_pool", "WarmPool", "idle_pool", "keepalive_pool", "prewarm_pool"):
        assert banned not in src, f"a warm-idle-pool symbol {banned!r} survived"


async def _pre_provision_event(meeting_id: str, tenant: str) -> "sandbox_provider.SandboxHandle":
    return await sandbox_provider.pre_provision(
        join_event={"meeting_id": meeting_id, "tenant": tenant}
    )


def test_pre_provision_on_join_event_spins_exactly_one_sandbox() -> None:
    """A creation/join event pre-provisions EXACTLY ONE sandbox (never from a warm
    pool, never cold-booted later) with its own secret (§3.9 meeting-creation)."""
    h = asyncio.run(_pre_provision_event("m-join", "acme"))
    assert h.meeting_id == "m-join"
    assert h.jwt_secret
    live = sandbox_provider.list_sandboxes()
    assert [s.id for s in live] == [h.id], "pre_provision must spin exactly one sandbox"


# ── the lazy E2B backend behind call_external (+ heartbeat anti-reap) ─────────

def test_provision_drives_injected_backend_through_call_external() -> None:
    """When a real backend is injected, provision issues the create through the
    ``call_external`` seam (retry + cost telemetry) — never a raw client call."""
    from tests.doc05.fakes import FakeE2BBackend

    fake = FakeE2BBackend()
    h = asyncio.run(sandbox_provider.provision_async(meeting_id="m-be", tenant="acme", backend=fake))
    assert fake.created == [h.id], "the backend was not asked to create the sandbox"
    assert fake.create_envs[h.id]["JWT_SECRET"] == h.jwt_secret, "secret not passed into the sandbox env"
    assert fake.create_envs[h.id]["SESSION_ID"], "per-sandbox claim SESSION_ID not passed"
    assert fake.went_through_call_external is True, "the backend create bypassed call_external"


# ── §3.10 safety wiring is LIVE on the real provision path (holes 1/2/6) ─────
# These oracles run the REAL ``provision_async`` (not a helper in isolation) and prove the
# computed egress default-DENY policy AND the curated allow-list env are what the sandbox
# create call actually RECEIVES — a regression that discards either (E2B's default-ALLOW
# outbound, or a hardcoded env literal that leaks a host secret) fails here.

def test_provision_async_threads_egress_default_deny_into_create() -> None:
    """The computed egress policy (default-DENY + the curated allow-list) RIDES the real
    ``create`` call — it is NOT discarded so the sandbox inherits E2B's default-ALLOW.

    Proves hole #1 is closed on the LIVE path: the fake backend asserts it received
    ``network={"denyOut":["all"], "allowOut":[...]}`` (deny-all base + curated allow-list),
    so a non-allowlisted host is unreachable. The real e2b wire arg stays behind
    ``call_external`` (Phase-3 residual); the HOST threading it is proven here.
    """
    from tests.doc05.fakes import FakeE2BBackend

    fake = FakeE2BBackend()
    h = asyncio.run(sandbox_provider.provision_async(meeting_id="m-egress", tenant="acme", backend=fake))
    network = fake.create_network[h.id]
    assert network is not None, (
        "the egress policy was DISCARDED — the sandbox create got no network= kwarg, so it "
        "inherits E2B's default-ALLOW outbound (hole #1)"
    )
    # Default-DENY base: deny ALL outbound first (the allTraffic selector).
    assert network["denyOut"] == ["all"], "egress must default-DENY (deny all outbound first)"
    # Then allow ONLY the curated allow-list — a non-allowlisted host is unreachable.
    assert isinstance(network["allowOut"], list) and network["allowOut"], (
        "the curated allow-list must ride the create call"
    )
    assert "evil.example.com" not in network["allowOut"]
    assert "0.0.0.0/0" not in network["allowOut"], "an allow-all CIDR would defeat default-deny"


def test_provision_async_egress_matches_the_workroom_curator() -> None:
    """The network= kwarg the create receives is EXACTLY what the workroom safety-wiring
    curator produces — the ONE owner of the policy, not a divergent re-derivation."""
    from workroom.agent_config import get_sandbox_network_policy, render_e2b_network_kwarg

    from tests.doc05.fakes import FakeE2BBackend

    expected = render_e2b_network_kwarg(get_sandbox_network_policy())
    fake = FakeE2BBackend()
    h = asyncio.run(sandbox_provider.provision_async(meeting_id="m-egress2", tenant="acme", backend=fake))
    assert fake.create_network[h.id] == expected, (
        "the create's network= kwarg diverged from the canonical safety-wiring curator"
    )


def test_provision_async_env_is_the_curated_allowlist_not_a_literal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The env crossing into the sandbox is produced by ``get_sandbox_sdk_env`` on the LIVE
    ``provision_async`` path — NOT a hardcoded literal that would leak a host secret.

    Proves hole #2 is closed: a live long-lived host secret present in the process at
    provision time (DATABASE_URL / RECALL_API_KEY) is DROPPED by the curator, and the
    scoped per-job token (JWT_SECRET + the SESSION_ID claim) + tenant tag survive. Testing
    ``provision_async`` itself (not the curator in isolation) is what closes the hole — a
    literal ``envs = {...}`` would still pass a curator-only test but fail THIS one.
    """
    from workroom.agent_config import SANDBOX_ENV_ALLOWLIST

    from tests.doc05.fakes import FakeE2BBackend

    # A host process carrying live long-lived secrets at provision time.
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db/main")
    monkeypatch.setenv("RECALL_API_KEY", "rk-live-should-not-leak")
    monkeypatch.setenv("GCS_BUCKET", "proxy-prod")
    # A BENIGN allow-listed operational key the curator (reading os.environ) MUST carry
    # through, but a hardcoded ``envs = {JWT_SECRET, SESSION_ID, TENANT}`` literal never
    # would. This is the signal that distinguishes "curator ran" from "literal leaked back".
    monkeypatch.setenv("PYTHONUNBUFFERED", "1")

    fake = FakeE2BBackend()
    h = asyncio.run(sandbox_provider.provision_async(meeting_id="m-env", tenant="acme", backend=fake))
    envs = fake.create_envs[h.id]
    # The host secrets are GONE (they are not on the allow-list) — an allow-list, not a literal.
    for leaked in ("DATABASE_URL", "RECALL_API_KEY", "GCS_BUCKET"):
        assert leaked not in envs, f"host secret {leaked!r} leaked into the sandbox env (hole #2)"
    # Every surviving key is allow-listed — the curator produced this env, not a literal.
    for key in envs:
        assert key in SANDBOX_ENV_ALLOWLIST, f"{key!r} crossed into the sandbox but is not allow-listed"
    # The scoped per-job token + claim id + tenant tag are the ones that DO belong there (§3.5).
    assert envs["JWT_SECRET"] == h.jwt_secret, "the per-sandbox secret must cross into the sandbox"
    assert envs["SESSION_ID"] == h.session_id, "the per-sandbox claim id must cross into the sandbox"
    assert envs["TENANT"] == "acme", "the isolation-triad tenant tag must cross into the sandbox"
    # The benign allow-listed operational key from the real process env crossed through — a
    # hardcoded literal would have dropped it, so this proves the CURATOR ran (hole #2 closed).
    assert envs.get("PYTHONUNBUFFERED") == "1", (
        "the env is a hardcoded literal, not the curated allow-list over the process env "
        "(get_sandbox_sdk_env was not invoked on the real provision path — hole #2)"
    )


def test_provision_async_curator_is_injectable_and_still_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller MAY inject the env curator / network policy (the layer-clean seam), and when
    it does, THAT curator's output is what rides the create — the ops layer never bypasses it."""
    from tests.doc05.fakes import FakeE2BBackend

    calls: dict[str, int] = {"env": 0, "net": 0}

    def _curator(source_env=None):  # type: ignore[no-untyped-def]
        calls["env"] += 1
        # A benign allow-listed key ONLY the curator supplies — proves the curator's output
        # rode the create. (JWT_SECRET/SESSION_ID/TENANT are pinned to the per-sandbox
        # identity AFTER the curator, so they are never taken from the curator's return.)
        return {"PYTHONUNBUFFERED": "1", "JWT_SECRET": "curator-should-be-overridden"}

    def _network():  # type: ignore[no-untyped-def]
        calls["net"] += 1
        return {"denyOut": ["all"], "allowOut": ["mirror.internal"]}

    fake = FakeE2BBackend()
    h = asyncio.run(
        sandbox_provider.provision_async(
            meeting_id="m-inj", tenant="t", backend=fake,
            env_curator=_curator, network_kwarg=_network,
        )
    )
    assert calls["env"] == 1 and calls["net"] == 1, "the injected curator/policy were not used"
    # The curator's benign key crossed through — its output was used, not bypassed.
    assert fake.create_envs[h.id]["PYTHONUNBUFFERED"] == "1"
    # The per-sandbox identity is PINNED over the curator (this sandbox's REAL secret must
    # cross, never a curator-supplied placeholder) — the security-correct precedence.
    assert fake.create_envs[h.id]["JWT_SECRET"] == h.jwt_secret
    assert fake.create_network[h.id] == {"denyOut": ["all"], "allowOut": ["mirror.internal"]}


def test_heartbeat_activity_bump_extends_timeout_anti_reap() -> None:
    """The heartbeat activity-bump extends the sandbox timeout so a silently-thinking
    build's sandbox is NOT reaped (§3.9). It runs through the backend's set_timeout.

    BINDING: provision's create already records the provision-time backstop in
    ``timeouts_set`` (``h.timeout_s``), so a ``>= h.timeout_s`` check would be a
    tautology satisfied BEFORE any bump — gutting ``heartbeat_bump`` to a no-op would
    still pass it. This oracle instead proves the bump (a) issued a NEW ``set_timeout``
    (the recorded list GREW) and (b) pushed the deadline STRICTLY BEYOND the
    provision-time backstop. A no-op bump fails both — the anti-reap is load-bearing.
    """
    from tests.doc05.fakes import FakeE2BBackend

    fake = FakeE2BBackend()
    h = asyncio.run(sandbox_provider.provision_async(meeting_id="m-hb", tenant="acme", backend=fake))
    before = list(fake.timeouts_set[h.id])  # the provision-time backstop is already here
    assert before == [h.timeout_s], "provision-time create should record exactly the backstop"

    asyncio.run(sandbox_provider.heartbeat_bump(h, backend=fake))

    after = fake.timeouts_set[h.id]
    assert len(after) == len(before) + 1, "activity-bump did not issue a NEW set_timeout"
    assert after[-1] > h.timeout_s, (
        "activity-bump did not extend the deadline BEYOND the provision-time backstop — "
        "a silently-thinking build would still be reaped at the original timeout"
    )


def test_backend_destroy_of_gone_is_idempotent() -> None:
    """destroy through the backend tolerates an already-killed sandbox (404 → ok)."""
    from tests.doc05.fakes import FakeE2BBackend

    async def _run() -> str:
        fake = FakeE2BBackend()
        h = await sandbox_provider.provision_async(meeting_id="m-d", tenant="acme", backend=fake)
        await sandbox_provider.destroy(h, backend=fake)
        # Backend says it's gone; a second destroy must still succeed.
        await sandbox_provider.destroy(h, backend=fake)
        assert fake.killed.count(h.id) >= 1
        return h.id

    asyncio.run(_run())


def test_honest_degrade_when_no_e2b_and_no_backend() -> None:
    """With e2b absent AND no injected backend, provision still yields a usable
    handle on the local substrate view (honest degrade, never a silent crash)."""
    # e2b is genuinely not installed in this env — assert that, then prove degrade.
    with pytest.raises(ImportError):
        __import__("e2b")
    h = sandbox_provider.provision(meeting_id="m-degrade")
    assert h.id and h.jwt_secret
    assert bool(sandbox_provider.health_check(h)) is True


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()

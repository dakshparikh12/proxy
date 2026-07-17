"""Doc 00 · §7 Config, secrets & credentials (AC-CFG-001..011).

Milestone m05. Every test maps to exactly one blocking criterion (id in the
docstring). Product imports live INSIDE the test bodies, so this module COLLECTS
clean and FAILS red before ``libs/llm/routing.py`` / ``config/defaults.toml`` /
``infra/`` / the settings + auth modules exist.

Oracle sources per PROTO-DETERMINISTIC-01:
  * [static]      CFG-001/004/005/008/009/011 -- deterministic text/TOML/schema
    scans over the product's committed config contract (``.env.example``,
    ``config/defaults.toml``), Terraform, and product source. ``.env.example``
    is genuinely pre-existing product config, so CFG-001/004 assert its exact
    required tokens (a leak/drift makes them go red); the rest key on artifacts
    that do not exist yet (``config/defaults.toml``, ``libs/llm/routing.py``,
    the schema) so they go red before the product is built.
  * [contract]    CFG-002 introspects the single canonical seat table in
    ``libs/llm/routing.py``; CFG-007 drives the ``check-secret-bindings`` drift
    gate on a synthetic terraform-vs-deploy drift and asserts a non-zero exit.
  * [integration] CFG-003 drives 30 concurrent calls through ``libs/llm`` and
    measures peak in-flight <= 16; CFG-010 imports the control_plane auth wiring
    and asserts Authlib+Google-OIDC + the three /auth routes.
  * [deployment]  CFG-006 is a static scan over the product's committed
    Terraform Secret Manager module (random_id + lifecycle.ignore_changes).
"""

import re

import pytest

import _support as S


# ---------------------------------------------------------------------------
# Shared helpers: the config contract (.env.example) and committed Terraform.
# ---------------------------------------------------------------------------
def _env_example_lines() -> list[str]:
    """Raw lines of the repo-root .env.example (the config contract)."""
    text = S.read_text(".env.example")
    if text is None:
        return []
    return text.splitlines()


def _env_documented_keys() -> dict[str, str]:
    """{KEY: doc-text} for every var in .env.example, uncommented AND commented-out.

    A documented key is any line matching ``[# ]KEY=...`` -- a commented-out line
    (leading ``#``) means optional-with-default; an uncommented line means it is
    part of the boot-required manifest. A var is DOCUMENTED (their discipline:
    what-for + where-to-get) when it carries either an inline trailing comment on
    its own line OR a preceding comment line (section header / description) that
    is not separated from it by a blank line -- i.e. the comment that heads the
    var's block. The stored doc-text is that inline-or-preceding comment.
    """
    out: dict[str, str] = {}
    pending_comment = ""  # comment text seen since the last blank line
    for raw in _env_example_lines():
        line = raw.rstrip()
        stripped = line.lstrip()
        if not stripped:
            pending_comment = ""  # a blank line breaks the doc-block
            continue
        # Strip an optional leading "# " to also parse commented-out (optional) vars.
        candidate = stripped[1:].lstrip() if stripped.startswith("#") else stripped
        m = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", candidate)
        if not m:
            # A pure comment line -> it documents the var block that follows it.
            if stripped.startswith("#"):
                text = stripped.lstrip("#").strip()
                if text:
                    pending_comment = text
            continue
        key = m.group(1)
        rhs = m.group(2)
        inline = rhs.split("#", 1)[1].strip() if "#" in rhs else ""
        out[key] = inline or pending_comment
    return out


def _env_uncommented_keys() -> set[str]:
    """The boot manifest: keys whose line is NOT commented out."""
    out: set[str] = set()
    for raw in _env_example_lines():
        m = re.match(r"^([A-Z][A-Z0-9_]*)=", raw)
        if m:
            out.add(m.group(1))
    return out


def _infra_text() -> str:
    """The product's committed infra as terraform source text (empty pre-build)."""
    return (
        S.read_all_text("*.tf", root_parts=("infra",))
        + "\n"
        + S.read_all_text("*", root_parts=("infra",))
    )


# ── AC-CFG-001 ────────────────────────────────────────────────────────────
@pytest.mark.static
def test_cfg_001_env_example_is_documented_required_key_manifest():
    """AC-CFG-001: .env.example documents every var and is the required-key manifest for the boot gate."""
    lines = _env_example_lines()
    assert lines, ".env.example not found (config contract missing)"

    documented = _env_documented_keys()
    assert documented, ".env.example declares no vars (config contract empty)"

    # (1) Every var carries a what-for + where-to-get comment (undocumented_vars == 0).
    undocumented = sorted(k for k, c in documented.items() if not c.strip())
    assert not undocumented, (
        f"every .env.example var must carry a what-for/where-to-get comment; "
        f"undocumented: {undocumented}"
    )

    # (2) Manifest parity: every boot-required key appears UNCOMMENTED in .env.example.
    #     The §6 boot gate fails fast on any of these; .env.example doubles as its
    #     required-key manifest (required_key_missing_from_manifest == 0).
    manifest = _env_uncommented_keys()
    boot_required = {
        "ANTHROPIC_API_KEY",
        "DATABASE_URL",
        "SESSION_SECRET",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "PROXY_MODEL_SCRIBE",
        "PROXY_MODEL_SCRIBE_CLOSE",
        "PROXY_MODEL_GATE",
        "PROXY_MODEL_QUALITY_GATE",
        "PROXY_MODEL_ANSWER",
        "PROXY_MODEL_ORCHESTRATOR",
        "PROXY_MODEL_WORKROOM",
        "PROXY_MODEL_BIG_BUILD",
        "PROXY_MAX_INFLIGHT_LLM",
        "RECALL_API_KEY",
        "AES_KEY_RECALL",
        "AES_KEY_STT",
        "AES_KEY_CALENDAR",
        "NANGO_SECRET_KEY",
    }
    missing = sorted(boot_required - manifest)
    assert not missing, (
        f"boot-required keys must appear uncommented in .env.example (manifest parity); "
        f"missing: {missing}"
    )


# ── AC-CFG-002 ────────────────────────────────────────────────────────────
@pytest.mark.contract
def test_cfg_002_routing_one_eight_seat_table_real_ids_only():
    """AC-CFG-002: libs/llm/routing.py holds one 8-seat table with real model ids only."""
    # Canonical seat table lives in libs/llm/routing.py -> red before it exists.
    from libs.llm import routing

    table = (
        getattr(routing, "SEATS", None)
        or getattr(routing, "MODEL_SEATS", None)
        or getattr(routing, "SEAT_TABLE", None)
    )
    assert table is not None, (
        "libs/llm/routing.py must expose the canonical seat table (SEATS/MODEL_SEATS/SEAT_TABLE)"
    )
    seats = dict(table)

    # (1) Exactly 8 seats -- the canonical set, no more, no fewer (seat_count_mismatch == 0).
    expected_seats = {
        "SCRIBE", "SCRIBE_CLOSE", "GATE", "QUALITY_GATE",
        "ANSWER", "ORCHESTRATOR", "WORKROOM", "BIG_BUILD",
    }
    got_seats = set(seats)
    assert got_seats == expected_seats, (
        f"seat set mismatch: extra={got_seats - expected_seats}, missing={expected_seats - got_seats}"
    )

    # (2) Every model id is a REAL id; no fake id (fake_model_ids == 0).
    real_ids = {"claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-8"}
    fake_ids = {"claude-haiku-4", "claude-sonnet-4", "claude-opus-4", "claude-sonnet-3-5"}
    for seat, model in seats.items():
        assert model in real_ids, f"seat {seat} uses non-real model id {model!r}; must be one of {real_ids}"
        assert model not in fake_ids, f"seat {seat} uses a FAKE model id {model!r}"

    # (3) It is the SINGLE canonical table -- no duplicate/per-doc seat table in the tree
    #     (duplicate_seat_tables == 0). Only routing.py may define the seat mapping.
    seat_table_defs = S.grep_python(r"\b(SEATS|MODEL_SEATS|SEAT_TABLE)\s*[:=]\s*\{", trees=("libs", "services"))
    other = [h for h in seat_table_defs if not h[0].replace("\\", "/").endswith("libs/llm/routing.py")]
    assert not other, f"the seat table must be canonical (single table in libs/llm/routing.py); duplicates: {other}"


# ── AC-CFG-003 ────────────────────────────────────────────────────────────
@pytest.mark.integration
def test_cfg_003_global_inflight_semaphore_caps_at_sixteen():
    """AC-CFG-003: the libs/llm global in-flight semaphore PROXY_MAX_INFLIGHT_LLM caps concurrent model calls at 16."""
    import asyncio
    import threading

    # Import the product's libs/llm call surface INSIDE the body -> red before it exists.
    from libs.llm import client as llm_client

    call = (
        getattr(llm_client, "call_model", None)
        or getattr(llm_client, "acall", None)
        or getattr(llm_client, "complete", None)
    )
    assert callable(call), "libs/llm must expose a model-call entrypoint (call_model/acall/complete)"

    inflight = {"cur": 0, "peak": 0}
    lock = threading.Lock()

    async def fake_transport(*_args, **_kwargs):
        with lock:
            inflight["cur"] += 1
            inflight["peak"] = max(inflight["peak"], inflight["cur"])
        try:
            await asyncio.sleep(0.02)  # hold the slot long enough for pile-up
        finally:
            with lock:
                inflight["cur"] -= 1
        return {"ok": True}

    async def drive():
        # Configure the global semaphore to 16 and issue 30 concurrent calls.
        import os
        os.environ["PROXY_MAX_INFLIGHT_LLM"] = "16"
        if hasattr(llm_client, "reset_inflight_semaphore"):
            llm_client.reset_inflight_semaphore()
        tasks = [
            call(seat="ANSWER", messages=[{"role": "user", "content": "hi"}], _transport=fake_transport)
            for _ in range(30)
        ]
        await asyncio.gather(*tasks)

    asyncio.run(drive())

    # (1) At most 16 in flight at any instant (peak_inflight_over_limit == 0).
    assert inflight["peak"] <= 16, f"peak in-flight was {inflight['peak']}; global cap PROXY_MAX_INFLIGHT_LLM=16 breached"
    # A real semaphore actually gates: with 30 pending and a hold, peak should reach the cap, not be trivially small.
    assert inflight["peak"] >= 2, "the global semaphore did not admit concurrent calls (transport not exercised)"

    # (2) The global semaphore is DISTINCT from the per-meeting [3-5] concurrency.
    per_meeting = getattr(llm_client, "PER_MEETING_CONCURRENCY", None) or getattr(llm_client, "MEETING_CONCURRENCY", None)
    if per_meeting is not None:
        assert int(per_meeting) != 16, "global in-flight cap must be distinct from the per-meeting [3-5] concurrency"


# ── AC-CFG-004 ────────────────────────────────────────────────────────────
@pytest.mark.static
def test_cfg_004_one_aes256gcm_key_per_integration_domain():
    """AC-CFG-004: one AES-256-GCM credential key per integration domain, never a shared key."""
    documented = _env_documented_keys()
    assert documented, ".env.example not found (credential-key config missing)"

    # (1) Each integration domain (calendar/Recall/STT) has its OWN key.
    per_domain_keys = {"AES_KEY_RECALL", "AES_KEY_STT", "AES_KEY_CALENDAR"}
    missing = sorted(per_domain_keys - set(documented))
    assert not missing, f"per-domain AES-256-GCM keys missing: {missing} (one key PER integration domain required)"

    # (2) No SINGLE key shared across domains (shared_domain_key == 0): a generic
    #     AES_KEY / AES_CREDENTIAL_KEY (one key for everything) must NOT be present.
    shared_keys = sorted(
        k for k in documented
        if re.fullmatch(r"AES_KEY|AES_CREDENTIAL_KEY|CREDENTIAL_KEY|AES_256_KEY|AES_GCM_KEY", k)
    )
    assert not shared_keys, f"a shared cross-domain credential key is forbidden (blast radius must stay bounded): {shared_keys}"

    # And the three per-domain keys are genuinely distinct env names, not aliases of one.
    assert len(per_domain_keys & set(documented)) == 3, "the three per-domain keys must be three distinct env vars"


# ── AC-CFG-005 ────────────────────────────────────────────────────────────
@pytest.mark.static
def test_cfg_005_numeric_tunables_live_in_defaults_toml_not_env():
    """AC-CFG-005: numeric tunables live in config/defaults.toml; env overrides only secrets/seats."""
    # (1) config/defaults.toml exists and holds the numeric/threshold tunables
    #     (tunable_hardcoded_in_code == 0 home discipline). Raises if absent -> red.
    toml = S.load_defaults_toml()
    flat = repr(toml)
    for tunable in ("HEARTBEAT_S", "STALE_AFTER_S"):
        assert re.search(rf"\b{tunable}\b", flat, re.I) or tunable.lower() in flat.lower(), (
            f"numeric tunable {tunable} must be declared in config/defaults.toml"
        )

    # (2) No numeric tunable is overridable from an env var (tunables_read_from_env == 0):
    #     .env.example must not carry a numeric tunable knob. Only secrets/seats live in env;
    #     the only numeric env value permitted is the PROXY_MAX_INFLIGHT_LLM semaphore seat cap.
    documented = _env_documented_keys()
    forbidden_env_tunables = {
        "HEARTBEAT_S", "STALE_AFTER_S", "CACHE_TTL", "CACHE_TTL_S",
        "COALESCER_WINDOW", "COALESCER_WINDOW_MS", "BATCH_READ_CAP", "BATCH_READ_MAX",
    }
    leaked = sorted(forbidden_env_tunables & set(documented))
    assert not leaked, f"numeric tunables must NOT be env-overridable (they live in config/defaults.toml): {leaked}"

    # (3) Code reads config/defaults.toml (not os.environ) for those tunables: a product
    #     that pulls HEARTBEAT_S/STALE_AFTER_S from the environment violates the discipline.
    env_reads = S.grep_python(r"os\.environ.*(HEARTBEAT_S|STALE_AFTER_S|CACHE_TTL|COALESCER|BATCH_READ)", trees=("libs", "services"))
    env_reads += S.grep_python(r"getenv\(\s*['\"](HEARTBEAT_S|STALE_AFTER_S|CACHE_TTL|COALESCER|BATCH_READ)", trees=("libs", "services"))
    assert not env_reads, f"numeric tunables must be read from config/defaults.toml, not env: {env_reads}"


# ── AC-CFG-006 ────────────────────────────────────────────────────────────
@pytest.mark.deployment
def test_cfg_006_terraform_secrets_random_id_ignore_secret_data():
    """AC-CFG-006: Terraform-created secrets use random_id with lifecycle.ignore_changes=[secret_data]."""
    infra = _infra_text()
    assert infra.strip(), "no infra/ Terraform found (Secret Manager module not built)"

    # (1) database-url, session-secret, and each AES key are auto-populated as random_id.
    for secret in ("database-url", "session-secret"):
        assert secret in infra or secret.replace("-", "_") in infra, (
            f"Terraform must create the '{secret}' Secret Manager secret"
        )
    assert re.search(r"AES_KEY_|aes.?key", infra, re.I), "Terraform must auto-populate the AES credential keys"
    assert re.search(r"resource\s+\"random_id\"", infra), (
        "auto-populated secret values must be generated via random_id"
    )
    assert re.search(r"google_secret_manager_secret", infra), "secrets must be GCP Secret Manager resources"

    # (2) Each secret version has lifecycle.ignore_changes=[secret_data] so out-of-band
    #     rotations survive apply (secret_data_not_ignored == 0).
    assert re.search(r"lifecycle\s*\{", infra), "secret versions must declare a lifecycle block"
    assert re.search(r"ignore_changes\s*=\s*\[[^\]]*secret_data", infra), (
        "secret versions must set lifecycle.ignore_changes=[secret_data] (out-of-band rotations must survive apply)"
    )


# ── AC-CFG-007 ────────────────────────────────────────────────────────────
@pytest.mark.contract
def test_cfg_007_check_secret_bindings_fails_on_drift():
    """AC-CFG-007: check-secret-bindings fails on Terraform-secret-map vs deploy-config drift; runs in CI + pre-commit."""
    # (1) The drift gate is importable/runnable product code -> red before it exists.
    check = None
    try:
        from services.ops import check_secret_bindings as mod  # canonical home
        check = getattr(mod, "check", None) or getattr(mod, "main", None) or getattr(mod, "run", None)
    except ImportError:
        try:
            from libs.ops import check_secret_bindings as mod
            check = getattr(mod, "check", None) or getattr(mod, "main", None) or getattr(mod, "run", None)
        except ImportError:
            check = None
    assert callable(check), "check-secret-bindings must be a runnable product entrypoint (services.ops.check_secret_bindings)"

    # Negative build: a secret exists in the Terraform module but NOT the deploy config (drift).
    tf_secret_map = {"database-url", "session-secret", "aes-key-recall", "orphan-secret"}
    deploy_config = {"database-url", "session-secret", "aes-key-recall"}  # missing orphan-secret -> drift

    try:
        result = check(terraform_secrets=tf_secret_map, deploy_secrets=deploy_config)
    except SystemExit as exc:  # a non-zero SystemExit is an acceptable "fails" signal
        assert exc.code not in (0, None), f"drift must exit non-zero; got exit code {exc.code!r}"
        return
    except Exception as exc:  # a raise naming the drift is also acceptable
        assert "orphan-secret" in str(exc), f"drift error must name the drifted secret: {exc}"
        return

    # Or it returns a non-zero exit code + names the drift (drift_accepted == 0).
    code = result if isinstance(result, int) else getattr(result, "returncode", getattr(result, "exit_code", None))
    assert code not in (0, None), f"check-secret-bindings must exit non-zero on drift; got {result!r}"
    assert "orphan-secret" in str(result) or "orphan-secret" in str(getattr(result, "message", "")), (
        "drift report must name the drifted secret"
    )

    # (2) It runs in BOTH CI and pre-commit.
    ci_text = S.read_all_text("*", root_parts=(".github", "workflows"))
    precommit = S.read_text(".pre-commit-config.yaml") or ""
    assert "check-secret-bindings" in ci_text or "check_secret_bindings" in ci_text, (
        "check-secret-bindings must run in CI (.github/workflows)"
    )
    assert "check-secret-bindings" in precommit or "check_secret_bindings" in precommit, (
        "check-secret-bindings must run in pre-commit (.pre-commit-config.yaml)"
    )


# ── AC-CFG-008 ────────────────────────────────────────────────────────────
@pytest.mark.static
def test_cfg_008_nango_holds_oauth_secret_manager_holds_platform_creds():
    """AC-CFG-008: Nango holds end-user GitHub OAuth; Secret Manager holds platform creds + the GitHub-App private key; never mixed."""
    documented = _env_documented_keys()
    assert documented, ".env.example not found (credential-home wiring missing)"

    # (1) End-user GitHub OAuth is held by Nango (NANGO_* wiring present).
    nango_keys = {k for k in documented if k.startswith("NANGO_")}
    assert nango_keys, "Nango must hold the end-user GitHub OAuth grant (NANGO_* config missing)"

    # (2) Platform credentials + the GitHub-App private key live in Secret Manager (prod),
    #     surfaced as GitHub-App platform creds (not end-user OAuth tokens).
    infra = _infra_text()
    src = S.read_all_text("*.py", root_parts=("libs",)) + S.read_all_text("*.py", root_parts=("services",))
    combined = infra + "\n" + src + "\n" + "\n".join(_env_example_lines())
    assert re.search(r"github.?app.?private.?key|GITHUB_APP_PRIVATE_KEY", combined, re.I), (
        "the GitHub-App private key must be a platform credential (Secret Manager / GITHUB_APP_PRIVATE_KEY)"
    )

    # (3) The two are NEVER mixed (credential_home_mixing == 0): no end-user OAuth token
    #     stored in Secret Manager terraform, and no App private key routed into Nango.
    assert not re.search(r"google_secret_manager_secret[^}]*nango[^}]*oauth", infra, re.I | re.S), (
        "end-user OAuth tokens must NOT be stored in Secret Manager (they live in Nango)"
    )
    nango_privkey = S.grep_python(r"nango.*private_key|private_key.*nango", trees=("libs", "services"), flags=re.I)
    assert not nango_privkey, f"the GitHub-App private key must NOT be routed into Nango: {nango_privkey}"


# ── AC-CFG-009 ────────────────────────────────────────────────────────────
@pytest.mark.static
def test_cfg_009_feature_flags_are_env_vars_no_table_no_active_flags():
    """AC-CFG-009: feature flags are plain env vars with zero active runtime flags; no feature_flags table."""
    # (1) No feature_flags table and no reload cache exists anywhere in the product
    #     (feature_flags_table_present == 0). Scan committed schema/migrations + source.
    schema_text = (
        S.read_all_text("*.sql", root_parts=("db",))
        + S.read_all_text("*.sql", root_parts=("migrations",))
        + S.read_all_text("*.py", root_parts=("migrations",))
        + S.read_all_text("*", root_parts=("alembic",))
    )
    src = S.read_all_text("*.py", root_parts=("libs",)) + S.read_all_text("*.py", root_parts=("services",))
    assert not re.search(r"(CREATE\s+TABLE[^;]*\bfeature_flags\b)|(['\"]feature_flags['\"])|(\bfeature_flags\b\s*=\s*Table)", schema_text + src, re.I), (
        "V0 must NOT define a feature_flags table (feature flags are plain env vars)"
    )
    # No flag reload cache / loader machinery.
    flag_loader = S.grep_python(r"class\s+FeatureFlag|def\s+load_feature_flags|feature_flag_cache", trees=("libs", "services"))
    assert not flag_loader, f"no feature-flag table/reload-cache machinery allowed in V0: {flag_loader}"

    # (2) V0 has ZERO active runtime flags (active_runtime_flags == 0): the cut flags
    #     proactive_enabled / durable_meeting_sessions must be absent from env + source.
    env_keys = set(_env_documented_keys())
    for cut in ("PROACTIVE_ENABLED", "DURABLE_MEETING_SESSIONS"):
        assert cut not in env_keys, f"cut runtime flag {cut} must NOT be an active env flag in V0"
    dead_flags = S.grep_python(r"\b(proactive_enabled|durable_meeting_sessions)\b", trees=("libs", "services"), flags=re.I)
    assert not dead_flags, f"cut runtime flags must be absent from source (zero active runtime flags): {dead_flags}"


# ── AC-CFG-010 ────────────────────────────────────────────────────────────
@pytest.mark.integration
def test_cfg_010_authlib_google_oidc_three_auth_routes_on_control_plane():
    """AC-CFG-010: user auth is Authlib + Google OIDC with /auth/{login,callback,logout} mounted on control_plane."""
    # Import the control_plane app / auth wiring INSIDE the body -> red before it exists.
    app = None
    for modpath, attr in (
        ("services.control_plane.app", "app"),
        ("services.control_plane.main", "app"),
        ("services.harness.control_plane", "app"),
        ("apps.control_plane.main", "app"),
    ):
        try:
            mod = __import__(modpath, fromlist=[attr])
            app = getattr(mod, attr, None)
            if app is not None:
                break
        except ImportError:
            continue
    assert app is not None, "control_plane ASGI app not found (auth surface not built)"

    # (1) Auth uses Authlib + Google OIDC configured by GOOGLE_CLIENT_ID/SECRET.
    #     The Authlib OAuth registry / a google-oidc client must be wired in the source.
    cp_src = (
        S.read_all_text("*.py", root_parts=("services", "control_plane"))
        + S.read_all_text("*.py", root_parts=("apps", "control_plane"))
        + S.read_all_text("*.py", root_parts=("services", "harness"))
    )
    assert re.search(r"authlib", cp_src, re.I), "user auth must use Authlib"
    assert re.search(r"openid|oidc|\.well-known/openid-configuration|accounts\.google\.com", cp_src, re.I), (
        "auth must use Google OIDC (OpenID Connect discovery)"
    )
    assert re.search(r"GOOGLE_CLIENT_ID", cp_src) and re.search(r"GOOGLE_CLIENT_SECRET", cp_src), (
        "Google OIDC must be configured by GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET"
    )

    # (2) The three routes /auth/login, /auth/callback, /auth/logout are mounted on control_plane
    #     (missing_auth_routes == 0). Introspect the app's route table.
    routes = set()
    for r in getattr(app, "routes", []) or getattr(getattr(app, "router", None), "routes", []) or []:
        path = getattr(r, "path", None) or getattr(r, "path_format", None)
        if path:
            routes.add(path)
    required = {"/auth/login", "/auth/callback", "/auth/logout"}
    missing = required - routes
    assert not missing, f"control_plane must mount the three /auth routes; missing: {sorted(missing)} (have {sorted(routes)})"


# ── AC-CFG-011 ────────────────────────────────────────────────────────────
@pytest.mark.static
def test_cfg_011_latency_slo_config_pins_canonical_direct_answer_slos():
    """AC-CFG-011: config [latency_slo] pins the canonical direct-answer SLOs and the ack-audible reflex SLO."""
    # config/defaults.toml is the home of the pinned foundation SLO contract; raises -> red.
    toml = S.load_defaults_toml()

    slo = toml.get("latency_slo")
    assert isinstance(slo, dict), "config/defaults.toml must declare a [latency_slo] table"

    # (1) DIRECT-ANSWER shallow-path first-text / first-audio SLOs (CANONICAL §12.8).
    expected = {
        "first_text_p50_s": 2.0,
        "first_text_p95_s": 4.0,
        "first_audio_p50_s": 2.5,
        "first_audio_p95_s": 5.0,
    }
    for key, want in expected.items():
        assert key in slo, f"[latency_slo] missing {key}"
        assert float(slo[key]) == want, f"[latency_slo].{key} must == {want}; got {slo[key]!r} (latency_slo_value_mismatch)"

    # (2) ack_audible_p95_ms == 500 -- the "on it" reflex SLO (lives under the [orchestrator] ack SLO).
    ack = None
    for container in (slo, toml.get("orchestrator", {}) or {}, toml):
        if isinstance(container, dict) and "ack_audible_p95_ms" in container:
            ack = container["ack_audible_p95_ms"]
            break
    assert ack is not None, "the ack-audible reflex SLO ack_audible_p95_ms must be pinned in config/defaults.toml"
    assert int(ack) == 500, f"ack_audible_p95_ms must == 500; got {ack!r} (latency_slo_value_mismatch)"

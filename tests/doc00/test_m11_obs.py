"""Doc 00 · §13 Observability & the operational floor (AC-OBS-001..010).

Milestone m11. Every test maps to exactly one blocking criterion (id in the
docstring). Product imports live INSIDE the test bodies, so this module COLLECTS
clean and FAILS red before ``libs/ops`` / ``services/harness`` / ``infra/`` exist.

Oracle sources per PROTO-DETERMINISTIC-01:
  * [integration] OBS-001/003/005/007 -- drive the product's real logging,
    cost-telemetry (Postgres), health/heartbeat, and WS-affinity seams and assert
    the exact boundary; product is imported FIRST so absence => red, and only a
    missing local Postgres (reachable post-build) is a skip.
  * [static]      OBS-002/004/008 -- deterministic text scans over the product's
    committed source (``libs``/``services``) and IaC (``infra/``) for the single
    Sentry init, the inert-Langfuse scaffold, and the skip-list absence. Each goes
    red before the files exist.
  * [deployment]  OBS-006/010 -- static text oracles over the one hardening script
    and the Terraform snapshot schedule on the code_intel per-tenant volume.
  * [security-adversarial] OBS-009 -- push a unique source-sentinel line through
    the real read/log/error path and assert it appears in neither structlog output,
    the Sentry before_send payload, nor any emitted artifact.
"""

import json
import re

import pytest

import _support as S


# ---------------------------------------------------------------------------
# Shared helper: the product's committed infra + one-script deploy text.
# Empty string => infra/ not built yet => the per-test guard goes red.
# ---------------------------------------------------------------------------
def _infra_text() -> str:
    return (
        S.read_all_text("*.tf", root_parts=("infra",))
        + "\n"
        + S.read_all_text("*", root_parts=("infra",))
    )


# ── AC-OBS-001 ────────────────────────────────────────────────────────────
@pytest.mark.integration
def test_obs_001_structured_json_logs_to_stdout(capsys):
    """AC-OBS-001: every stdout log line is structlog-produced JSON (one object per line, zero non-JSON)."""
    # Import the product logging bootstrap INSIDE the body -> red before it exists.
    try:
        from libs.ops.logging import configure_logging, get_logger  # structlog->stdout wiring
    except ImportError:
        from libs.ops import configure_logging, get_logger  # spec-derived fallback surface

    # Configure logging exactly as the service bootstrap does, then emit a line.
    configure_logging()
    log = get_logger("obs-test")
    log.info("meeting_started", meeting_id="m-123", tenant="tenant-A")

    captured = capsys.readouterr()
    stdout_lines = [ln for ln in captured.out.splitlines() if ln.strip()]
    assert stdout_lines, "no log line reached stdout (structlog must render to stdout)"

    # Every non-empty stdout line MUST parse as a JSON object (non_json_log_lines == 0).
    non_json = []
    for ln in stdout_lines:
        try:
            obj = json.loads(ln)
        except (json.JSONDecodeError, ValueError):
            non_json.append(ln)
            continue
        if not isinstance(obj, dict):
            non_json.append(ln)
    assert not non_json, f"non-JSON log lines on stdout (must be 0): {non_json}"

    # The emitted structlog object carries the event + bound context (proves structlog, not print).
    rendered = json.loads(stdout_lines[-1])
    assert rendered.get("event") == "meeting_started", (
        f"structlog JSON must carry the event key; got {rendered!r}"
    )
    assert rendered.get("meeting_id") == "m-123", "bound structlog context must appear in the JSON object"


# ── AC-OBS-002 ────────────────────────────────────────────────────────────
@pytest.mark.static
def test_obs_002_sentry_wired_once_shared_across_services():
    """AC-OBS-002: Sentry is initialized exactly once via a shared import; no per-service re-init (services_without_sentry == 0)."""
    # sentry_sdk.init() must be called from exactly one place -- the shared ops module.
    init_calls = S.grep_python(r"sentry_sdk\.init\s*\(")
    assert init_calls, "no sentry_sdk.init() call found (product not built)"
    assert len(init_calls) == 1, (
        f"Sentry must be initialized exactly once (shared import), not per-service; found {len(init_calls)}: {init_calls}"
    )

    relpath, _lineno, _line = init_calls[0]
    assert relpath.startswith("libs/ops/") or relpath.startswith("libs/"), (
        f"the single Sentry init must live in the shared libs/ops module; found in {relpath}"
    )

    # The shared init is defined once as a callable the services import (not copy-pasted).
    init_defs = S.count_def_sites("init_error_reporting") or S.count_def_sites("init_sentry")
    assert len(init_defs) == 1, (
        f"the shared Sentry-init helper must be defined exactly once; found {init_defs}"
    )
    assert init_defs[0].startswith("libs/ops/") or init_defs[0].startswith("libs/"), (
        f"the shared Sentry-init helper must live in libs/ops; found {init_defs[0]}"
    )


# ── AC-OBS-003 ────────────────────────────────────────────────────────────
@pytest.mark.integration
def test_obs_003_per_meeting_cost_telemetry_total_and_cache_split():
    """AC-OBS-003: Postgres records total_cost_usd + cache_read/creation split per micro-call, aggregating correctly per meeting (missing_cache_split == 0, misaggregated_cost == 0)."""
    # Import the product cost-telemetry writer + budget reader FIRST -> red before they exist.
    from libs.ops.cost import record_micro_call_cost, check_meeting_budget

    meeting_id = "m-cost-001"
    # Two micro-calls with a cache hit on the second (Scribe cached prefix).
    calls = [
        {"total_cost_usd": 0.030, "cache_read_usd": 0.000, "cache_creation_usd": 0.010},
        {"total_cost_usd": 0.012, "cache_read_usd": 0.008, "cache_creation_usd": 0.000},
    ]

    with S.pg_conn() as conn:  # importing product first => missing DB is a skip, missing product is red above
        record_micro_call_cost(conn, meeting_id=meeting_id, **calls[0])
        record_micro_call_cost(conn, meeting_id=meeting_id, **calls[1])

        # Each micro-call row carries the cache-read/creation split (never just a total).
        cur = conn.execute(
            "SELECT total_cost_usd, cache_read_usd, cache_creation_usd "
            "FROM meeting_cost_telemetry WHERE meeting_id = %s ORDER BY total_cost_usd DESC",
            (meeting_id,),
        )
        rows = cur.fetchall()
        assert len(rows) == 2, f"both micro-calls must be recorded per meeting; got {len(rows)} rows"
        for r in rows:
            assert r[1] is not None and r[2] is not None, (
                f"every micro-call row must carry the cache_read/creation split (missing_cache_split==0): {r}"
            )
        # A cache hit is proven: at least one micro-call has cache_read_usd > 0.
        assert any(float(r[1]) > 0 for r in rows), "cost telemetry must prove the Scribe cache is hitting (cache_read_usd > 0)"

        # The per-meeting aggregate equals the exact sum of the micro-call totals (misaggregated_cost == 0).
        expected_total = round(sum(c["total_cost_usd"] for c in calls), 6)
        budget = check_meeting_budget(conn, meeting_id=meeting_id)
        spent = budget["total_cost_usd"] if isinstance(budget, dict) else float(budget)
        assert round(float(spent), 6) == expected_total, (
            f"check_meeting_budget must read the correctly aggregated per-meeting total; "
            f"got {spent} != {expected_total}"
        )


# ── AC-OBS-004 ────────────────────────────────────────────────────────────
@pytest.mark.static
def test_obs_004_langfuse_scaffold_wired_but_inert():
    """AC-OBS-004: the SDK is trace-wrapped and flush_tracing() is in the shutdown gather; keys unset by default and no self-hosted ClickHouse (selfhosted_langfuse_present == 0, no_flush_tracing == 0)."""
    # The tracing scaffold exists and is defined once as flush_tracing().
    flush_defs = S.count_def_sites("flush_tracing")
    assert len(flush_defs) == 1, (
        f"flush_tracing() must be defined exactly once (the scaffold); found {flush_defs}"
    )
    assert flush_defs[0].startswith("libs/"), f"flush_tracing() must live in libs; found {flush_defs[0]}"

    # flush_tracing() is wired into the parallel shutdown gather (no_flush_tracing == 0).
    shutdown_text = S.read_all_text("*.py", root_parts=("libs",)) + S.read_all_text("*.py", root_parts=("services",))
    assert re.search(r"(?:asyncio\.)?gather\([^)]*flush_tracing", shutdown_text, re.S), (
        "flush_tracing() must be awaited inside the parallel shutdown gather()"
    )

    # The SDK is trace-wrapped (Langfuse observe/wrap around the query()/SDK call).
    wrap_hits = S.grep_python(r"langfuse|@observe|observe\(|trace_wrap", flags=re.I)
    assert wrap_hits, "the SDK must be Langfuse trace-wrapped (scaffold present)"

    # Inert by default: Langfuse keys are unset (no literal key value; env-gated, disabled default).
    src = S.read_all_text("*.py", root_parts=("libs",)) + S.read_all_text("*.py", root_parts=("services",))
    assert not re.search(r"LANGFUSE_(?:PUBLIC|SECRET)_KEY\s*=\s*['\"][A-Za-z0-9_\-]{8,}", src), (
        "Langfuse keys must be UNSET by default (inert scaffold), never hard-coded"
    )

    # The self-hosted Langfuse ClickHouse install is NOT present (selfhosted_langfuse_present == 0).
    infra = _infra_text()
    combined = (src + "\n" + infra).lower()
    assert "clickhouse" not in combined, "the self-hosted Langfuse ClickHouse stack must NOT be installed (deferred)"


# ── AC-OBS-005 ────────────────────────────────────────────────────────────
@pytest.mark.integration
def test_obs_005_health_endpoint_and_harness_heartbeat():
    """AC-OBS-005: GET /health returns healthy and the harness emits a Healthchecks.io heartbeat (health_unavailable == 0)."""
    # Import the control_plane app + the harness heartbeat seam FIRST -> red before they exist.
    try:
        from services.control_plane.app import app as control_plane_app  # ASGI/route surface with /health
    except ImportError:
        from services.harness.app import app as control_plane_app  # spec-derived fallback surface
    from services.harness.heartbeat import emit_heartbeat  # Healthchecks.io ping emitter

    # GET /health returns a healthy response (200). Prefer the framework TestClient if available.
    status_code = None
    try:
        from starlette.testclient import TestClient
        with TestClient(control_plane_app) as client:
            resp = client.get("/health")
            status_code = resp.status_code
    except Exception:
        # Fallback: the app exposes a callable health handler we can invoke directly.
        handler = getattr(control_plane_app, "health", None)
        assert callable(handler), "no /health route and no callable health handler on the app"
        result = handler()
        status_code = getattr(result, "status_code", 200)
    assert status_code == 200, f"GET /health must return a healthy 200; got {status_code}"

    # The harness emits a Healthchecks.io heartbeat: instrument the ping seam and assert it fires.
    pings = []

    def _record_ping(url, *args, **kwargs):
        pings.append(url)

        class _R:
            status_code = 200

        return _R()

    emit_heartbeat(check_url="https://hc-ping.com/deadbeef", ping=_record_ping)
    assert pings, "the harness must emit a Healthchecks.io heartbeat (no ping fired)"
    assert any("hc-ping" in p or "healthchecks" in p.lower() for p in pings), (
        f"the heartbeat must target Healthchecks.io; pinged {pings}"
    )


# ── AC-OBS-006 ────────────────────────────────────────────────────────────
@pytest.mark.deployment
def test_obs_006_one_idempotent_hardening_script_full_control_set():
    """AC-OBS-006: exactly one idempotent hardening script enforces the full host-hardening set with no host code-exec path (missing_hardening_control == 0, non_idempotent_rerun == 0, host_code_exec_path == 0)."""
    # Locate the single hardening script under the product's deploy/infra trees.
    scripts = (
        S.glob("*harden*.sh", root_parts=("infra",))
        + S.glob("*harden*.sh", root_parts=("deploy",))
        + S.glob("*hardening*", root_parts=("infra",))
        + S.glob("*hardening*", root_parts=("deploy",))
    )
    scripts = sorted({str(p) for p in scripts})
    assert scripts, "no hardening script found under infra/ or deploy/ (product not built)"
    assert len(scripts) == 1, f"there must be exactly ONE hardening script (config-mgmt is skip-listed); found {scripts}"

    text = S.read_text(*scripts[0].split("/")) or ""
    assert text.strip(), f"hardening script {scripts[0]} is empty"

    # Every required control is enforced (missing_hardening_control == 0).
    required_controls = {
        "key-only SSH":        r"PasswordAuthentication\s+no",
        "no root login":       r"PermitRootLogin\s+no",
        "fail2ban":            r"fail2ban",
        "unattended-upgrades": r"unattended[-_]upgrades",
        "non-root services":   r"non[-_]?root|User=|--user\b|useradd|adduser",
        "host firewall":       r"\bufw\b|iptables|nftables",
        "encrypted volume":    r"encrypt|luks|crypt|dm-crypt|/mnt/encrypted|encrypted[-_ ]?volume",
    }
    missing = [name for name, rx in required_controls.items() if not re.search(rx, text, re.I)]
    assert not missing, f"hardening script missing required controls (must be 0): {missing}"

    # Both layers: host firewall AND the cloud security group are present across deploy config.
    infra = _infra_text()
    assert re.search(r"\bufw\b|iptables|nftables", text, re.I), "host firewall layer must be in the hardening script"
    assert re.search(r"firewall|security[-_ ]?group|google_compute_firewall", infra, re.I), (
        "the security-group layer must exist in infra/ (both firewall layers required)"
    )

    # No arbitrary code-exec path on the host: exec only ever inside E2B (host_code_exec_path == 0).
    assert re.search(r"e2b", (text + infra), re.I), "arbitrary code execution must be scoped to E2B (E2B not referenced)"
    host_exec = re.findall(r"^\s*(?:eval|exec)\s|curl[^\n|]*\|\s*(?:ba)?sh", text, re.M | re.I)
    assert not host_exec, f"hardening script must contain no host code-exec path (curl|sh / eval / exec): {host_exec}"

    # Idempotent: re-running is a no-op -- the script guards its mutations (set -e + conditional guards).
    assert re.search(r"set\s+-e", text), "hardening script must fail-fast (set -e) for a safe re-run"
    assert re.search(r"if\s+.*(?:grep|-f|-e|command -v|dpkg -s|systemctl is-)|--now\b|\bgrep -q", text, re.S), (
        "hardening script must be idempotent: guard each mutation so a second run is a no-op"
    )


# ── AC-OBS-007 ────────────────────────────────────────────────────────────
@pytest.mark.integration
def test_obs_007_live_ws_affinity_routes_to_operation_runs_claim_owner():
    """AC-OBS-007: a reconnect/retry landing on a non-owner routes to the operation_runs claim owner; never random Cloud Run LB (served_by_non_owner == 0, random_lb_used == 0)."""
    # Import the product affinity router FIRST -> red before it exists.
    try:
        from libs.ops.affinity import route_to_owner  # resolves owner instance-id from operation_runs
    except ImportError:
        from services.harness.affinity import route_to_owner  # spec-derived fallback surface

    owner_instance = "instance-X"
    non_owner_instance = "instance-Y"

    # The operation_runs claim: meeting m owned by instance X (created_by=X on the row).
    operation_runs = {"meeting-42": {"created_by": owner_instance}}

    proxied = {"count": 0, "target": None}
    served_locally = {"count": 0}

    def _lookup_owner(meeting_id):
        row = operation_runs.get(meeting_id)
        return row["created_by"] if row else None

    def _proxy(target_instance, request):
        proxied["count"] += 1
        proxied["target"] = target_instance

    def _serve_local(request):
        served_locally["count"] += 1

    # A tile-WS reconnect / retried webhook for meeting-42 lands on non-owner Y.
    route_to_owner(
        meeting_id="meeting-42",
        this_instance=non_owner_instance,
        lookup_owner=_lookup_owner,
        proxy=_proxy,
        serve_local=_serve_local,
    )

    # The non-owner must proxy/hand off to owner X -- it must NOT serve locally.
    assert served_locally["count"] == 0, "a non-owner instance must NOT serve the meeting locally (served_by_non_owner==0)"
    assert proxied["count"] == 1, "the non-owner must proxy/hand off exactly once"
    assert proxied["target"] == owner_instance, (
        f"routing must target the operation_runs claim owner {owner_instance}, never random LB; got {proxied['target']}"
    )

    # Sanity: the owner instance itself serves locally (affinity, not blind proxy loop).
    proxied2 = {"count": 0}
    served2 = {"count": 0}
    route_to_owner(
        meeting_id="meeting-42",
        this_instance=owner_instance,
        lookup_owner=_lookup_owner,
        proxy=lambda t, r: proxied2.__setitem__("count", proxied2["count"] + 1),
        serve_local=lambda r: served2.__setitem__("count", served2["count"] + 1),
    )
    assert served2["count"] == 1 and proxied2["count"] == 0, "the owner instance must serve locally, not proxy to itself"


# ── AC-OBS-008 ────────────────────────────────────────────────────────────
@pytest.mark.static
def test_obs_008_no_skiplist_item_built_at_v0():
    """AC-OBS-008: none of the skip-list technologies is present in the repo or infra (skiplist_items_present == 0)."""
    # Scan the product source + IaC + deploy config as one corpus.
    corpus = (
        S.read_all_text("*", root_parts=("libs",))
        + "\n" + S.read_all_text("*", root_parts=("services",))
        + "\n" + _infra_text()
        + "\n" + S.read_all_text("*", root_parts=("deploy",))
    )
    assert corpus.strip(), "no product source / infra found (product not built)"
    low = corpus.lower()

    # Each entry: (label, forbidden regexes). A hit anywhere is a defect.
    skiplist = {
        "Kubernetes/orchestrator":   [r"\bkubernetes\b", r"\bkubectl\b", r"kind:\s*Deployment", r"apiVersion:\s*apps/"],
        "service mesh":              [r"\bistio\b", r"\blinkerd\b", r"envoy.?proxy", r"service.?mesh"],
        "SOC2 tooling":              [r"\bvanta\b", r"\bdrata\b", r"soc.?2 tooling"],
        "feature-flag platform":     [r"launchdarkly", r"\bunleash\b", r"\bflagsmith\b", r"\bsplit\.io\b"],
        "multi-region":              [r"multi.?region", r"multiregion"],
        "Ansible/config-mgmt":       [r"\bansible\b", r"\bchef\b", r"\bpuppet\b", r"\bsaltstack\b"],
        "OTel/Prometheus/Grafana":   [r"opentelemetry", r"\botel\b", r"prometheus", r"grafana"],
        "self-hosted Langfuse CH":   [r"clickhouse"],
        "CDKTF/Pulumi":              [r"\bcdktf\b", r"\bpulumi\b"],
        "auto-rotation infra":       [r"auto.?rotation", r"rotation.?infra", r"secret.?rotation.?lambda"],
        "PagerDuty":                 [r"pagerduty"],
        "per-customer-GCP-project":  [r"per.?customer.?gcp", r"per.?customer.?project.?machinery"],
        "impala/BigQuery/Figma":     [r"\bimpala\b", r"bigquery", r"\bfigma\b"],
    }
    present = {}
    for label, patterns in skiplist.items():
        for rx in patterns:
            if re.search(rx, low):
                present[label] = rx
                break
    assert not present, f"skip-list technologies present at V0 (must be 0): {present}"


# ── AC-OBS-009 ────────────────────────────────────────────────────────────
@pytest.mark.security_adversarial
def test_obs_009_no_raw_source_in_logs_or_sentry(capsys):
    """AC-OBS-009: a unique source-sentinel line appears in neither structlog output, the Sentry before_send payload, nor any artifact (source_occurrences_in_logs/sentry/artifact == 0)."""
    # Import the product logging + Sentry-scrubber seams FIRST -> red before they exist.
    try:
        from libs.ops.logging import configure_logging, get_logger
    except ImportError:
        from libs.ops import configure_logging, get_logger
    from libs.ops.sentry import before_send  # the Sentry before_send source-scrubber

    # A unique customer-source sentinel that must never leak.
    sentinel = "SRC_SENTINEL_9f13ac_def secret_customer_algorithm(x): return x * 42"

    configure_logging()
    log = get_logger("obs-source-scrub")

    # Drive a read/log/error path that carries the raw customer source line.
    log.info("processing_clone_file", path="repo/src/main.py", content=sentinel)
    try:
        raise RuntimeError(f"parse failed near: {sentinel}")
    except RuntimeError:
        log.error("clone_parse_error", snippet=sentinel)

    captured = capsys.readouterr()
    log_output = captured.out + captured.err

    # The source-sentinel line must appear NOWHERE in structlog output (source_occurrences_in_logs == 0).
    assert sentinel not in log_output, (
        "raw customer source leaked into structlog output; the processor chain must scrub it"
    )
    assert "secret_customer_algorithm" not in log_output, "customer source body leaked into logs despite scrubbing"

    # The Sentry before_send scrubber strips customer source from the error event payload.
    event = {
        "exception": {"values": [{"value": f"parse failed near: {sentinel}", "type": "RuntimeError"}]},
        "extra": {"snippet": sentinel, "path": "repo/src/main.py"},
        "message": sentinel,
    }
    scrubbed = before_send(event, {"exc_info": (RuntimeError, RuntimeError("x"), None)})
    payload = json.dumps(scrubbed) if scrubbed is not None else ""
    assert sentinel not in payload, (
        "raw customer source leaked into the Sentry before_send payload (source_occurrences_in_sentry must be 0)"
    )
    assert "secret_customer_algorithm" not in payload, "customer source body survived the Sentry scrubber"


# ── AC-OBS-010 ────────────────────────────────────────────────────────────
@pytest.mark.deployment
def test_obs_010_code_intel_volume_daily_snapshot_and_cache_not_truth():
    """AC-OBS-010: a daily snapshot schedule is configured on the code_intel per-tenant volume; clones/indexes documented as rebuildable cache (missing_daily_volume_snapshot == 0)."""
    infra = _infra_text()
    assert infra.strip(), "no infra/ Terraform found (product not built)"

    # A resource-policy / snapshot schedule exists on the code_intel persistent volume.
    assert re.search(
        r"google_compute_resource_policy|snapshot_schedule_policy|resource_policies",
        infra, re.I,
    ), "the code_intel volume must have a snapshot resource-policy attached"

    # The schedule cadence is DAILY (missing_daily_volume_snapshot == 0).
    assert re.search(r"daily_schedule|days_in_cycle\s*=\s*\"?1\"?", infra, re.I), (
        "the code_intel volume snapshot schedule must run DAILY (daily_schedule / days_in_cycle = 1)"
    )
    # It is bound to the code_intel volume/disk, not some unrelated disk.
    assert re.search(r"code_intel", infra, re.I), "the snapshot policy must attach to the code_intel per-tenant volume"

    # Clones/indexes/dependency-graph documented as rebuildable derived cache -- Postgres + GCS are truth.
    docs = (
        S.read_all_text("*.md", root_parts=("infra",))
        + S.read_all_text("*.md", root_parts=("docs",))
        + S.read_all_text("*.tf", root_parts=("infra",))
        + S.read_text("CLAUDE.md") or ""
    )
    corpus = (docs or "").lower()
    assert re.search(r"rebuild|derived|cache", corpus), (
        "clones/indexes/dependency-graph must be documented as rebuildable derived cache, not the source of truth"
    )
    assert re.search(r"postgres|gcs", corpus), (
        "the durable source of truth (Postgres + GCS) must be documented distinct from the rebuildable volume cache"
    )

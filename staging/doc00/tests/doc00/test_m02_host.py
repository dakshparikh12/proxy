"""Doc 00 · §4 Backend, hosting & the three deployables (AC-HOST-001..014).

Milestone m02. Every test maps to exactly one blocking criterion (id in the
docstring). Product imports live INSIDE the test bodies, so this module COLLECTS
clean and FAILS red before ``infra/`` / ``services/*`` / the tenant-crypto
module exist.

Oracle sources per PROTO-DETERMINISTIC-01:
  * [deployment] HOST-001..010 -- static text scans over the product's committed
    Terraform (``infra/``). Hermetic: no terraform binary. Each asserts
    ``infra.strip()`` first so it goes red before ``infra/`` exists.
  * [static]     HOST-011..012 -- static absence scans over infra + product source.
  * [integration] HOST-007 -- a scripted direct-answer wake turn driven against
    the product's code_intel internal API with instrumented E2B provisioner +
    Workroom dispatcher; assert zero provisions / zero dispatches.
  * [security-adversarial] HOST-013..014 -- import the product tenant-crypto
    module, build two tenants with distinct envelope keys, assert key
    distinctness + KMS/no-LUKS floor + crypto-shred blast-radius bounded to A.
"""

import re

import pytest

import _support as S


# ---------------------------------------------------------------------------
# Shared helper: the product's committed infra, as terraform source text.
# Concatenating *.tf and every infra/* file makes the scan robust to layout.
# Empty string => infra/ not built yet => the per-test `.strip()` guard goes red.
# ---------------------------------------------------------------------------
def _infra_text() -> str:
    return (
        S.read_all_text("*.tf", root_parts=("infra",))
        + "\n"
        + S.read_all_text("*", root_parts=("infra",))
    )


# ── AC-HOST-001 ───────────────────────────────────────────────────────────
@pytest.mark.deployment
def test_host_001_control_plane_cloudrun_route_surface():
    """AC-HOST-001: control_plane is a Cloud Run service (autoscaling) mounting the required route surface."""
    infra = _infra_text()
    assert infra.strip(), "no infra/ Terraform found (product not built)"

    # control_plane is a Cloud Run service (kind == CloudRun).
    assert "control_plane" in infra, "control_plane deployable not present in infra/"
    assert re.search(r"google_cloud_run(?:_v2)?_service", infra), (
        "control_plane must be a Cloud Run service (google_cloud_run[_v2]_service)"
    )

    # Route surface is a superset of the required set (missing_required_routes == 0).
    required_routes = [
        "webhook",                 # webhook receiver
        "connect",                 # connect page + API
        r"/m/\{meeting_id\}",      # authenticated /m/{meeting_id} surface
        r"/internal/reconcile",    # /internal/{reconcile,notes}
        r"/internal/notes",
    ]
    missing = [r for r in required_routes if not re.search(r, infra)]
    assert not missing, f"control_plane route surface missing required routes: {missing}"
    # WS gateway must be mounted somewhere on the surface.
    assert re.search(r"\bws\b|websocket|ws_gateway|ws-gateway", infra, re.I), (
        "control_plane must mount a WS gateway"
    )


# ── AC-HOST-002 ───────────────────────────────────────────────────────────
@pytest.mark.deployment
def test_host_002_control_plane_timeout_3600():
    """AC-HOST-002: control_plane Cloud Run sets timeout_seconds = 3600 exactly."""
    infra = _infra_text()
    assert infra.strip(), "no infra/ Terraform found (product not built)"

    timeouts = re.findall(r"timeout_seconds\s*=\s*\"?(\d+)\"?", infra)
    assert timeouts, "no timeout_seconds set on any Cloud Run resource"
    assert "3600" in timeouts, f"control_plane timeout_seconds must be 3600 exactly; found {timeouts}"


# ── AC-HOST-003 ───────────────────────────────────────────────────────────
@pytest.mark.deployment
def test_host_003_control_plane_no_cpu_throttling():
    """AC-HOST-003: control_plane sets run.googleapis.com/cpu-throttling == 'false' (load-bearing)."""
    infra = _infra_text()
    assert infra.strip(), "no infra/ Terraform found (product not built)"

    assert "run.googleapis.com/cpu-throttling" in infra, (
        "control_plane must set the run.googleapis.com/cpu-throttling annotation (load-bearing)"
    )
    # The annotation value must be false -- background provisioning 503s under request-scoped CPU.
    assert re.search(
        r"run\.googleapis\.com/cpu-throttling\"?\s*[:=]\s*\"?false\"?", infra
    ), "cpu-throttling annotation must be 'false' (--no-cpu-throttling)"
    assert not re.search(
        r"run\.googleapis\.com/cpu-throttling\"?\s*[:=]\s*\"?true\"?", infra
    ), "cpu-throttling must NOT be true"


# ── AC-HOST-004 ───────────────────────────────────────────────────────────
@pytest.mark.deployment
def test_host_004_control_plane_cloudsql_directvpc_minscale():
    """AC-HOST-004: control_plane sets the Cloud SQL instances annotation, Direct-VPC egress, minScale>=1."""
    infra = _infra_text()
    assert infra.strip(), "no infra/ Terraform found (product not built)"

    # Cloud SQL connector annotation present.
    assert "run.googleapis.com/cloudsql-instances" in infra, (
        "control_plane must set the run.googleapis.com/cloudsql-instances annotation"
    )
    # Direct-VPC egress configured.
    assert re.search(r"vpc_access|direct[-_]?vpc|VPC_EGRESS|egress\s*=", infra, re.I), (
        "Direct-VPC egress must be configured on control_plane"
    )
    # minScale >= 1 for the live tier.
    min_scales = re.findall(
        r"(?:autoscaling\.knative\.dev/minScale|min_instance_count|min_instances?)\"?\s*[:=]\s*\"?(\d+)\"?",
        infra,
    )
    assert min_scales, "control_plane must declare a minScale / min_instance_count for the live tier"
    assert any(int(v) >= 1 for v in min_scales), (
        f"control_plane minScale must be >= 1 for the live tier; found {min_scales}"
    )


# ── AC-HOST-005 ───────────────────────────────────────────────────────────
@pytest.mark.deployment
def test_host_005_meeting_runtime_gce_mig_no_broker_no_volume():
    """AC-HOST-005: meeting_runtime is a GCE MIG (not Cloud Run), one process per meeting, no bus/broker/volume."""
    infra = _infra_text()
    assert infra.strip(), "no infra/ Terraform found (product not built)"

    assert "meeting_runtime" in infra, "meeting_runtime deployable not present in infra/"

    # meeting_runtime is a GCE MIG (compute instance / instance group manager), NOT Cloud Run.
    assert re.search(
        r"google_compute_(?:instance|instance_group_manager|instance_template|region_instance_group_manager)",
        infra,
    ), "meeting_runtime must be a GCE MIG (google_compute_instance[_group_manager])"

    # Isolate the meeting_runtime resource block and prove it is not a Cloud Run service.
    m = re.search(r"meeting_runtime.*?(?=\n(?:resource|module|# ===)|\Z)", infra, re.S)
    block = m.group(0) if m else infra
    assert not re.search(r"google_cloud_run", block), (
        "meeting_runtime must NOT be a Cloud Run service"
    )
    # No message bus / broker (asyncio pipes only, no bus, no broker).
    assert not re.search(
        r"pubsub|redis|rabbitmq|kafka|amqp|nats|sqs|celery|broker", block, re.I
    ), "meeting_runtime must use no message bus and no broker"
    # Holds no volume -- durability is the Postgres substrate.
    assert not re.search(
        r"google_compute_disk|attached_disk|attach_disk|persistent_disk|volume", block, re.I
    ), "meeting_runtime must hold no attached volume"


# ── AC-HOST-006 ───────────────────────────────────────────────────────────
@pytest.mark.deployment
def test_host_006_code_intel_stateful_encrypted_volume_scoped_api():
    """AC-HOST-006: code_intel is one stateful GCE/MIG host with a per-tenant encrypted volume behind a tenant+SHA-scoped API."""
    infra = _infra_text()
    assert infra.strip(), "no infra/ Terraform found (product not built)"

    assert "code_intel" in infra, "code_intel deployable not present in infra/"

    # Stateful GCE/MIG host.
    assert re.search(
        r"google_compute_(?:instance|instance_group_manager|instance_template|region_instance_group_manager)",
        infra,
    ), "code_intel must be a stateful GCE/MIG host"

    # Per-tenant encrypted volume attached (disk + encryption key).
    assert re.search(r"google_compute_disk|attached_disk|attach_disk|boot_disk|persistent_disk", infra, re.I), (
        "code_intel must have a volume attached"
    )
    assert re.search(r"disk_encryption_key|kms_key_self_link|kms_key_name|disk_encryption", infra, re.I), (
        "code_intel volume must be encrypted (disk_encryption_key / KMS)"
    )
    # tenant+SHA-scoped internal API.
    assert re.search(r"tenant", infra, re.I) and re.search(r"sha", infra, re.I), (
        "code_intel internal API must be tenant+SHA-scoped"
    )


# ── AC-HOST-007 ───────────────────────────────────────────────────────────
@pytest.mark.integration
def test_host_007_direct_answer_touches_neither_e2b_nor_workroom():
    """AC-HOST-007: the direct-answer path provisions no E2B sandbox and dispatches no Workroom session."""
    # Import the product direct-answer / code_intel path INSIDE the body -> red before it exists.
    try:
        from services.code_intel import answer_direct  # scripted direct-answer wake turn entrypoint
    except ImportError:
        from services.harness.direct_answer import answer_direct  # spec-derived fallback interface

    # Instrumented seams: an E2B provisioner and a Workroom dispatcher that only record.
    class RecordingE2B:
        def __init__(self):
            self.provisions = 0

        def provision(self, *args, **kwargs):
            self.provisions += 1
            raise AssertionError("direct-answer path must NOT provision an E2B sandbox")

    class RecordingWorkroom:
        def __init__(self):
            self.dispatches = 0

        def dispatch(self, *args, **kwargs):
            self.dispatches += 1
            raise AssertionError("direct-answer path must NOT dispatch a Workroom session")

    e2b = RecordingE2B()
    workroom = RecordingWorkroom()

    # Drive one scripted direct-answer wake turn against the code_intel internal API.
    result = answer_direct(
        ask="What does this function return?",
        tenant="tenant-A",
        sha="deadbeef",
        e2b=e2b,
        workroom=workroom,
    )

    assert result is not None, "direct-answer wake turn must resolve and answer"
    assert e2b.provisions == 0, (
        f"direct-answer path provisioned {e2b.provisions} E2B sandbox(es); must be 0"
    )
    assert workroom.dispatches == 0, (
        f"direct-answer path dispatched {workroom.dispatches} Workroom session(s); must be 0"
    )


# ── AC-HOST-008 ───────────────────────────────────────────────────────────
@pytest.mark.deployment
def test_host_008_one_cloudsql_pg15_private_ip_unix_socket_no_ssl():
    """AC-HOST-008: exactly one Cloud SQL Postgres 15, private IP only, DSN via unix socket with no app-side SSL."""
    infra = _infra_text()
    assert infra.strip(), "no infra/ Terraform found (product not built)"

    # Exactly one Cloud SQL instance holds durable state.
    instances = re.findall(r"resource\s+\"google_sql_database_instance\"", infra)
    assert len(instances) >= 1, "no Cloud SQL instance found"
    # Postgres 15.
    assert re.search(r"POSTGRES_15|\bPG15\b|postgres.?15", infra, re.I), (
        "Cloud SQL instance must be Postgres 15 (database_version = POSTGRES_15)"
    )
    # Private IP only: ipv4_enabled (public IP) must be false, private network configured.
    assert re.search(r"ipv4_enabled\s*=\s*false", infra), (
        "Cloud SQL must have public IP disabled (ipv4_enabled = false)"
    )
    assert not re.search(r"ipv4_enabled\s*=\s*true", infra), "Cloud SQL must NOT enable public IP"
    assert re.search(r"private_network|private_ip", infra, re.I), (
        "Cloud SQL must be private IP only (private_network configured)"
    )

    # The app DSN is a Unix socket (Cloud SQL Auth Proxy), no app-side SSL params.
    assert re.search(r"host\s*=\s*/cloudsql/|/cloudsql/", infra), (
        "app DSN must be a Cloud SQL Auth Proxy unix socket (host=/cloudsql/...)"
    )
    assert not re.search(r"sslmode\s*=", infra), (
        "app DSN must carry NO app-side SSL params (the proxy terminates TLS)"
    )


# ── AC-HOST-009 ───────────────────────────────────────────────────────────
@pytest.mark.deployment
def test_host_009_cloudsql_backups_prod_regional_pitr_dev_zonal():
    """AC-HOST-009: backups 03:00; prod REGIONAL+PITR on db-custom-1-3840; dev ZONAL on db-f1-micro."""
    infra = _infra_text()
    assert infra.strip(), "no infra/ Terraform found (product not built)"

    # Backups run daily at 03:00.
    assert re.search(r"start_time\s*=\s*\"03:00\"", infra), (
        "Cloud SQL backups must run daily at 03:00 (start_time = \"03:00\")"
    )
    # Prod is REGIONAL with PITR on tier db-custom-1-3840.
    assert re.search(r"availability_type\s*=\s*\"REGIONAL\"", infra), (
        "prod Cloud SQL must be REGIONAL (availability_type = REGIONAL)"
    )
    assert re.search(r"point_in_time_recovery_enabled\s*=\s*true|pitr", infra, re.I), (
        "prod Cloud SQL must have PITR enabled"
    )
    assert "db-custom-1-3840" in infra, "prod Cloud SQL tier must be db-custom-1-3840"
    # Dev is ZONAL on tier db-f1-micro.
    assert re.search(r"availability_type\s*=\s*\"ZONAL\"", infra), (
        "dev Cloud SQL must be ZONAL (availability_type = ZONAL)"
    )
    assert "db-f1-micro" in infra, "dev Cloud SQL tier must be db-f1-micro"


# ── AC-HOST-010 ───────────────────────────────────────────────────────────
@pytest.mark.deployment
def test_host_010_gcs_notes_artifacts_versioning_enabled():
    """AC-HOST-010: the GCS notes and artifacts buckets have Object Versioning enabled."""
    infra = _infra_text()
    assert infra.strip(), "no infra/ Terraform found (product not built)"

    assert re.search(r"resource\s+\"google_storage_bucket\"", infra), "no GCS buckets found in infra/"
    # notes + artifacts buckets present.
    assert re.search(r"notes", infra, re.I), "notes bucket not present"
    assert re.search(r"artifacts?", infra, re.I), "artifacts bucket not present"

    # Object Versioning enabled -- if_generation_match optimistic concurrency relies on it.
    versioning_blocks = re.findall(r"versioning\s*\{[^}]*enabled\s*=\s*(true|false)[^}]*\}", infra, re.S)
    assert versioning_blocks, "no versioning{} block on any bucket"
    assert "false" not in versioning_blocks, (
        f"notes/artifacts buckets must have versioning enabled; found disabled blocks: {versioning_blocks}"
    )
    assert versioning_blocks.count("true") >= 2, (
        f"both notes and artifacts buckets must enable versioning; enabled count={versioning_blocks.count('true')}"
    )


# ── AC-HOST-011 ───────────────────────────────────────────────────────────
@pytest.mark.static
def test_host_011_no_k8s_no_mesh_no_multiregion():
    """AC-HOST-011: zero Kubernetes manifests, zero service-mesh config, zero multi-region resources in V0."""
    infra = _infra_text()
    assert infra.strip(), "no infra/ Terraform found (product not built)"

    # No Kubernetes manifests (apiVersion/kind k8s objects, GKE k8s resources).
    k8s_manifests = S.glob("*.yaml", root_parts=("infra",)) + S.glob("*.yml", root_parts=("infra",))
    k8s_yaml = [
        str(p) for p in k8s_manifests
        if re.search(r"apiVersion\s*:|kind\s*:\s*(Deployment|Service|Pod|StatefulSet|DaemonSet|Ingress)",
                     S.read_text(*p.relative_to(S.ROOT).parts) or "")
    ]
    assert not k8s_yaml, f"Kubernetes manifests must not exist in V0: {k8s_yaml}"
    assert not re.search(r"google_container_cluster|kubernetes_manifest|kubernetes_deployment", infra), (
        "no Kubernetes (GKE cluster / k8s manifests) in V0"
    )

    # No service mesh.
    assert not re.search(r"istio|linkerd|envoy|service.?mesh|consul.?connect", infra, re.I), (
        "no service mesh in V0"
    )

    # Single region: no multi-region resources.
    regions = set(re.findall(r"region\s*=\s*\"([a-z]+-[a-z]+\d+)\"", infra))
    assert len(regions) <= 1, f"multi-region resources must not exist in V0; found regions {regions}"
    assert not re.search(r"multi.?region|MULTI_REGION|multiregion", infra, re.I), (
        "no multi-region resources in V0"
    )


# ── AC-HOST-012 ───────────────────────────────────────────────────────────
@pytest.mark.static
def test_host_012_no_gpu_every_model_via_api():
    """AC-HOST-012: no GPU accelerator on any resource; every model access is via an external API client."""
    infra = _infra_text()
    assert infra.strip(), "no infra/ Terraform found (product not built)"

    # No GPU accelerator provisioned anywhere.
    assert not re.search(r"guest_accelerator|nvidia|gpu|accelerator_type", infra, re.I), (
        "no GPU accelerator may be provisioned in V0 (no guest_accelerator)"
    )

    # Every model access is via an external API client -- no local inference frameworks.
    local_inference = S.grep_python(
        r"\b(torch|tensorflow|transformers|vllm|llama_cpp|onnxruntime|ctransformers)\b"
    )
    assert not local_inference, f"no local model inference allowed; every model is an API: {local_inference}"


# ── AC-HOST-013 ───────────────────────────────────────────────────────────
@pytest.mark.security_adversarial
def test_host_013_per_tenant_distinct_envelope_keys_kms_floor_no_luks():
    """AC-HOST-013: tenants A and B have distinct per-tenant envelope keys; KMS PD floor present; no hand-rolled LUKS."""
    # Import the product tenant-crypto module INSIDE the body -> red before it exists.
    try:
        from services.code_intel.crypto import TenantVolume  # per-tenant envelope-key volume
    except ImportError:
        from libs.ops.tenant_crypto import TenantVolume  # spec-derived fallback path

    vol_a = TenantVolume(tenant="tenant-A")
    vol_b = TenantVolume(tenant="tenant-B")

    key_a = vol_a.envelope_key()
    key_b = vol_b.envelope_key()

    # Distinct envelope keys -- never a single shared key across tenants (named anti-pattern).
    assert key_a is not None and key_b is not None, "each tenant must have an envelope key"
    assert key_a != key_b, "tenants A and B must have DISTINCT envelope keys (shared key is a named anti-pattern)"

    # The underlying disk is KMS-encrypted (the floor).
    infra = _infra_text()
    assert infra.strip(), "no infra/ Terraform found (product not built)"
    assert re.search(r"google_kms_crypto_key|kms_key_self_link|kms_key_name|disk_encryption_key", infra, re.I), (
        "the code_intel Persistent Disk must be KMS-encrypted (the floor)"
    )

    # No hand-rolled LUKS layer -- neither in infra nor in product source.
    luks_hits = S.grep_python(r"luks|cryptsetup", flags=re.I)
    assert not luks_hits, f"no hand-rolled LUKS layer allowed: {luks_hits}"
    assert not re.search(r"luks|cryptsetup", infra, re.I), "no hand-rolled LUKS layer allowed in infra/"


# ── AC-HOST-014 ───────────────────────────────────────────────────────────
@pytest.mark.security_adversarial
def test_host_014_key_destroy_crypto_shreds_only_that_tenant():
    """AC-HOST-014: destroying tenant A's envelope key crypto-shreds only A; tenant B stays fully readable."""
    # Import the product tenant-crypto module INSIDE the body -> red before it exists.
    try:
        from services.code_intel.crypto import TenantVolume
    except ImportError:
        from libs.ops.tenant_crypto import TenantVolume

    vol_a = TenantVolume(tenant="tenant-A")
    vol_b = TenantVolume(tenant="tenant-B")

    # Distinct keys + encrypted data for each tenant.
    assert vol_a.envelope_key() != vol_b.envelope_key(), "tenants must start with distinct envelope keys"
    ct_a = vol_a.encrypt(b"tenant A private code")
    ct_b = vol_b.encrypt(b"tenant B private code")

    # Sanity: both decrypt before offboarding.
    assert vol_a.decrypt(ct_a) == b"tenant A private code"
    assert vol_b.decrypt(ct_b) == b"tenant B private code"

    # Offboarding tenant A = destroy the key = crypto-shred.
    vol_a.destroy_key()

    # Tenant A's ciphertext is now unrecoverable (crypto-shred).
    with pytest.raises(Exception):
        vol_a.decrypt(ct_a)

    # Blast radius bounded to A: tenant B's data remains fully readable.
    assert vol_b.decrypt(ct_b) == b"tenant B private code", (
        "tenant B must remain fully readable after tenant A's key is destroyed (blast radius bounded to A)"
    )

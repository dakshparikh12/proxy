"""Doc 00 · §8 Infrastructure as Code — Terraform / IaC (AC-IAC-001..006).

Milestone m06. Every test maps to exactly one blocking criterion (id in the
docstring). Product imports (rare here) live INSIDE the test bodies, so this
module COLLECTS clean and FAILS red before the ``infra/`` tree exists.

Oracle sources per PROTO-DETERMINISTIC-01 -- all hermetic, no terraform binary:
  * [static]     IAC-001 -- directory_layout_scan over infra/{modules,envs} +
                 apply-order text.
  * [deployment] IAC-002 -- pipeline_config_check over the dev/prod deploy
                 pipelines (Cloud Build trigger + promote job).
  * [static]     IAC-003 -- terraform_lifecycle_scan: prevent_destroy on every
                 data-bearing resource.
  * [static]     IAC-004 -- terraform_ignore_changes_check: Cloud Run template
                 ignore_changes'd (deploy owns runtime config).
  * [static]     IAC-005 -- iam_binding_scan: one least-privilege SA per role.
  * [static]     IAC-006 -- module_instantiation_scan: customer-platform named
                 but instantiates zero resources.

Every test asserts ``infra.strip()`` (or the relevant subtree) FIRST, so it goes
red before ``infra/`` is committed and passes only against a correct product.
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


# ── AC-IAC-001 ────────────────────────────────────────────────────────────
@pytest.mark.static
def test_iac_001_module_env_layout_and_apply_order():
    """AC-IAC-001: infra/modules/{bootstrap,platform} + envs/{dev,prod}/{bootstrap,platform}; apply order bootstrap->platform."""
    infra = _infra_text()
    assert infra.strip(), "no infra/ Terraform found (product not built)"

    # modules/{bootstrap,platform} both exist (missing_iac_dirs == 0).
    modules = S.list_subdirs("infra", "modules")
    missing_modules = {"bootstrap", "platform"} - modules
    assert not missing_modules, f"infra/modules missing module dirs: {missing_modules}"

    # envs/{dev,prod} each contain a bootstrap and a platform stack.
    envs = S.list_subdirs("infra", "envs")
    missing_envs = {"dev", "prod"} - envs
    assert not missing_envs, f"infra/envs missing env dirs: {missing_envs}"
    for env in ("dev", "prod"):
        stacks = S.list_subdirs("infra", "envs", env)
        missing_stacks = {"bootstrap", "platform"} - stacks
        assert not missing_stacks, (
            f"infra/envs/{env} missing stack dirs: {missing_stacks}"
        )

    # The remote-state bootstrap helper is present at the tree root.
    assert S.exists("infra", "setup-remote-state.sh"), (
        "infra/setup-remote-state.sh (remote-state bootstrap) must exist"
    )

    # The documented apply order per env is bootstrap -> platform.
    assert re.search(r"bootstrap\s*(?:->|→|then|,|\s+before\s+)\s*platform", infra, re.I), (
        "apply order per env must be documented as bootstrap -> platform"
    )
    # ...and never the reverse.
    assert not re.search(r"platform\s*(?:->|→)\s*bootstrap", infra, re.I), (
        "apply order must NOT be platform -> bootstrap"
    )


# ── AC-IAC-002 ────────────────────────────────────────────────────────────
@pytest.mark.deployment
def test_iac_002_dev_autodeploy_prod_promote_no_direct_push():
    """AC-IAC-002: dev auto-deploys on a Cloud Build trigger; prod is promote-based (AR immutable tag + promote job); no direct prod push."""
    # Deploy/pipeline config lives across infra/, deploy/ and CI workflows.
    infra = _infra_text()
    pipe = (
        infra
        + "\n"
        + S.read_all_text("*", root_parts=("deploy",))
        + "\n"
        + S.read_all_text("*", root_parts=(".github", "workflows"))
    )
    assert pipe.strip(), "no infra/ / deploy/ / CI pipeline config found (product not built)"

    # Dev auto-deploys on a Cloud Build trigger.
    assert re.search(r"google_cloudbuild_trigger|cloudbuild|cloud[ _-]?build", pipe, re.I), (
        "dev must auto-deploy on a Cloud Build trigger"
    )

    # Prod is promote-based: an Artifact Registry immutable tag + a promote job.
    assert re.search(r"artifact[_-]?registry|google_artifact_registry", pipe, re.I), (
        "prod promote path must ship from Artifact Registry"
    )
    assert re.search(r"immutab", pipe, re.I) or re.search(r"IMMUTABLE_TAGS", pipe), (
        "Artifact Registry must use immutable tags (exact-tested image can't be overwritten)"
    )
    assert re.search(r"promote", pipe, re.I), (
        "prod must ship via a promote job (ships the exact-tested image)"
    )

    # There is NO direct push path to prod (direct_prod_push_path == 0).
    direct_push = re.findall(
        r"(?im)^(?!.*promote).*(?:docker\s+push|gcloud\s+run\s+deploy|artifacts\s+docker\s+push).*prod",
        pipe,
    )
    assert not direct_push, f"no direct push path to prod is allowed; found: {direct_push}"
    assert not re.search(r"push[_-]?to[_-]?prod|direct[_-]?prod[_-]?push", pipe, re.I), (
        "prod must be promote-only -- no direct-prod-push path"
    )


# ── AC-IAC-003 ────────────────────────────────────────────────────────────
@pytest.mark.static
def test_iac_003_prevent_destroy_on_every_data_bearing_resource():
    """AC-IAC-003: prevent_destroy=true on specs bucket, both SQL DBs, credential-key random_ids, and the project lien."""
    infra = _infra_text()
    assert infra.strip(), "no infra/ Terraform found (product not built)"

    # prevent_destroy is turned on somewhere (and never turned off).
    assert re.search(r"prevent_destroy\s*=\s*true", infra), (
        "prevent_destroy = true must guard the data-bearing resources"
    )
    assert not re.search(r"prevent_destroy\s*=\s*false", infra), (
        "no data-bearing resource may set prevent_destroy = false"
    )

    # Each data-bearing resource must carry a prevent_destroy lifecycle guard.
    # (data_resource_without_prevent_destroy == 0) We isolate each resource block
    # and require prevent_destroy = true inside its lifecycle{}.
    def _resource_blocks(rx_type: str) -> list[str]:
        blocks: list[str] = []
        for m in re.finditer(
            rf'resource\s+"{rx_type}"\s+"[^"]+"\s*\{{', infra
        ):
            # Grab from the opening brace to the next top-level resource/module.
            tail = infra[m.end():]
            end = re.search(r"\n(?:resource|module|# ===)\s", tail)
            blocks.append(tail[: end.start()] if end else tail)
        return blocks

    def _has_prevent_destroy(block: str) -> bool:
        return re.search(r"lifecycle\s*\{[^{}]*prevent_destroy\s*=\s*true", block, re.S) is not None

    # specs bucket (a GCS bucket named for specs).
    spec_buckets = [
        b for b in _resource_blocks(r"google_storage_bucket") if re.search(r"spec", b, re.I)
    ]
    assert spec_buckets, "the specs GCS bucket must be present"
    assert all(_has_prevent_destroy(b) for b in spec_buckets), (
        "the specs bucket must set lifecycle.prevent_destroy = true"
    )

    # Both SQL databases (google_sql_database) are prevent_destroy'd.
    sql_dbs = _resource_blocks(r"google_sql_database")
    assert len(sql_dbs) >= 2, f"both SQL databases must exist; found {len(sql_dbs)}"
    assert all(_has_prevent_destroy(b) for b in sql_dbs), (
        "every SQL database must set lifecycle.prevent_destroy = true"
    )

    # The credential-key random_id(s).
    cred_random = [
        b for b in _resource_blocks(r"random_id")
        if re.search(r"cred|key|secret", b, re.I)
    ]
    assert cred_random, "the credential-key random_id must be present"
    assert all(_has_prevent_destroy(b) for b in cred_random), (
        "the credential-key random_id must set lifecycle.prevent_destroy = true"
    )

    # The project-deletion lien.
    liens = _resource_blocks(r"google_resource_manager_lien")
    assert liens, "the project-deletion lien must be present"
    assert all(_has_prevent_destroy(b) for b in liens), (
        "the project lien must set lifecycle.prevent_destroy = true"
    )


# ── AC-IAC-004 ────────────────────────────────────────────────────────────
@pytest.mark.static
def test_iac_004_cloudrun_template_ignore_changes_deploy_owns_runtime():
    """AC-IAC-004: Cloud Run lifecycle.ignore_changes includes the template; image/env/secrets are owned by deploy/promote, not Terraform."""
    infra = _infra_text()
    assert infra.strip(), "no infra/ Terraform found (product not built)"

    # Isolate the Cloud Run service resource block.
    m = re.search(
        r'resource\s+"google_cloud_run(?:_v2)?_service"\s+"[^"]+"\s*\{',
        infra,
    )
    assert m, "a google_cloud_run[_v2]_service resource must exist"
    tail = infra[m.end():]
    end = re.search(r"\n(?:resource|module|# ===)\s", tail)
    block = tail[: end.start()] if end else tail

    # lifecycle.ignore_changes includes the whole template (template_not_ignored == 0).
    assert re.search(r"lifecycle\s*\{[^{}]*ignore_changes\s*=\s*\[[^\]]*template", block, re.S), (
        "Cloud Run must ignore_changes = [template] so promote/CI own runtime config"
    )

    # Secret versions also ignore secret_data (companion split from the same spec sentence).
    secret_ver = re.search(
        r'resource\s+"google_secret_manager_secret_version"\s+"[^"]+"\s*\{',
        infra,
    )
    if secret_ver:
        stail = infra[secret_ver.end():]
        send = re.search(r"\n(?:resource|module|# ===)\s", stail)
        sblock = stail[: send.start()] if send else stail
        assert re.search(
            r"lifecycle\s*\{[^{}]*ignore_changes\s*=\s*\[[^\]]*secret_data", sblock, re.S
        ), "secret versions must ignore_changes = [secret_data] (deploy owns the secret payload)"

    # Terraform stands up the service *shell* only: the real image is a placeholder,
    # so the deploy/promote path owns image/env/secrets (not `terraform apply`).
    assert re.search(r"gcr\.io/cloudrun/placeholder|placeholder", block, re.I), (
        "Terraform must set only a placeholder image -- promote.sh owns the real image"
    )


# ── AC-IAC-005 ────────────────────────────────────────────────────────────
@pytest.mark.static
def test_iac_005_least_privilege_service_account_per_role():
    """AC-IAC-005: each role has its own least-privilege service account; no single broad SA shared across roles."""
    infra = _infra_text()
    assert infra.strip(), "no infra/ Terraform found (product not built)"

    # At least two distinct service accounts (one per role, not one shared SA).
    sa_names = re.findall(
        r'resource\s+"google_service_account"\s+"([^"]+)"', infra
    )
    assert len(set(sa_names)) >= 2, (
        f"there must be a least-privilege service account per role; found SAs: {sa_names}"
    )

    # IAM role bindings must be scoped per-role (google_project_iam_member / _binding),
    # never a broad owner/editor grant that a single shared SA would need.
    assert re.search(r"google_(?:project|storage_bucket|secret_manager_secret)_iam_(?:member|binding)", infra), (
        "roles must be bound with least-privilege iam_member/iam_binding grants"
    )
    broad_roles = re.findall(r'role\s*=\s*"(roles/(?:owner|editor))"', infra)
    assert not broad_roles, (
        f"no broad shared role may be granted (shared_broad_sa == 0); found: {broad_roles}"
    )

    # Each SA is referenced by a distinct binding subject -- no single SA email
    # is the member on every binding.
    member_refs = re.findall(
        r'member\s*=\s*"serviceAccount:\$\{google_service_account\.([^.]+)\.email\}"', infra
    )
    if member_refs:
        # If any one SA appears on *every* binding, that is a shared broad SA.
        from collections import Counter
        counts = Counter(member_refs)
        assert not (len(set(member_refs)) == 1 and len(member_refs) > 1), (
            f"a single SA is bound to every role (shared broad SA): {counts}"
        )


# ── AC-IAC-006 ────────────────────────────────────────────────────────────
@pytest.mark.static
def test_iac_006_customer_platform_named_but_builds_nothing():
    """AC-IAC-006: the customer-platform per-customer-GCP-project module is recorded (named) but instantiates zero resources in V0."""
    infra = _infra_text()
    assert infra.strip(), "no infra/ Terraform found (product not built)"

    # The customer-platform enterprise-tenancy path is *named* (recorded) somewhere.
    assert re.search(r"customer[_-]platform", infra, re.I), (
        "the customer-platform per-customer-GCP-project module must be named (recorded)"
    )

    # It exists as a module directory but builds nothing: the module must NOT be
    # instantiated (no `module "customer_platform"` call) ...
    assert not re.search(
        r'module\s+"[^"]*customer[_-]platform[^"]*"\s*\{', infra, re.I
    ), "customer-platform must NOT be instantiated as a module in V0"

    # ...and if the module dir exists, its own *.tf must declare zero resources
    # (customer_platform_resources_built == 0).
    cp_dirs = [
        d for d in S.glob("*", root_parts=("infra",))
        if d.is_dir() and re.search(r"customer[_-]platform", d.name, re.I)
    ]
    for d in cp_dirs:
        cp_text = S.read_all_text("*.tf", root_parts=d.relative_to(S.ROOT).parts)
        resources = re.findall(r'(?m)^\s*resource\s+"[^"]+"\s+"[^"]+"', cp_text)
        assert not resources, (
            f"customer-platform module {d.name} must instantiate zero resources; found: {resources}"
        )

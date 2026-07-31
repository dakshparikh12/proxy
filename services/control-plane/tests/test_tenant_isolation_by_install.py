"""B5 — tenant_id is derived from the GitHub INSTALLATION ACCOUNT, not the repo URL.

Deriving ``tenant_id = uuid5(repo_url)`` collided two DIFFERENT customers who
connect the SAME public repo URL onto ONE tenant → shared ``repo_maps`` /
``connect_readiness`` (a cross-tenant P0). The tenant must instead be keyed on the
authenticated installation identity (the installation id / account), so:

  * two installs of the SAME repo under DIFFERENT installation accounts → DIFFERENT
    tenant_ids (no collision);
  * the SAME account + repo → a STABLE tenant_id (idempotent redelivery);
  * the push-webhook derivation (github_webhook) matches the connect derivation for
    the same installation id.
"""
from __future__ import annotations


def test_same_repo_different_installations_get_different_tenants() -> None:
    """Two customers connecting the same repo URL under different installs never collide."""
    from control_plane.connect import _tenant_for_install

    repo = "https://github.com/calcom/cal.com"
    t_acme = _tenant_for_install(repo, installation_account="1001")
    t_globex = _tenant_for_install(repo, installation_account="2002")
    assert t_acme != t_globex, "same repo, different installs MUST NOT share a tenant"


def test_same_installation_and_repo_is_stable() -> None:
    """The same installation account + repo maps to a stable tenant (idempotent)."""
    from control_plane.connect import _tenant_for_install

    repo = "https://github.com/calcom/cal.com"
    first = _tenant_for_install(repo, installation_account="1001")
    second = _tenant_for_install(repo, installation_account="1001")
    assert first == second


def test_tenant_is_a_valid_uuid() -> None:
    """The derived tenant is a real uuid (connect_readiness.tenant_id FK to tenants.id)."""
    import uuid

    from control_plane.connect import _tenant_for_install

    tid = _tenant_for_install("https://github.com/x/y", installation_account="42")
    uuid.UUID(tid)  # raises if not a valid uuid


def test_tenant_depends_on_the_account_not_the_repo() -> None:
    """The SAME account connecting two DIFFERENT repos → two tenants; identity is the account.

    (The account is the tenant boundary; each connected repo is a distinct install
    under it, so the tenant key mixes the account with the repo — but the account is
    the isolating factor, proven by test_same_repo_different_installations above.)
    """
    from control_plane.connect import _tenant_for_install

    a = _tenant_for_install("https://github.com/x/y", installation_account="42")
    b = _tenant_for_install("https://github.com/x/z", installation_account="42")
    # different repos under one account are distinct installs (distinct readiness rows)
    assert a != b


def test_webhook_derivation_matches_connect_for_same_installation() -> None:
    """github_webhook resolves the SAME tenant the connect flow bound, from the push's install id.

    The push payload carries ``installation.id``; the webhook tenant resolver must
    derive the same tenant the connect install/start flow bound for that account+repo.
    """
    from control_plane.connect import _tenant_for_install
    from control_plane.github_webhook import _installation_account_from_payload, _tenant_from_push

    repo = "https://github.com/calcom/cal.com"
    payload = {
        "repository": {"clone_url": repo},
        "installation": {"id": 1001},
    }
    account = _installation_account_from_payload(payload)
    assert account == "1001"
    connect_tenant = _tenant_for_install(repo, installation_account=account)
    push_tenant = _tenant_from_push(payload, repo)
    assert push_tenant == connect_tenant


def test_webhook_without_installation_id_yields_no_tenant() -> None:
    """A push with no installation id resolves to no tenant (fail-closed, never repo-only)."""
    from control_plane.github_webhook import _tenant_from_push

    payload = {"repository": {"clone_url": "https://github.com/x/y"}}
    assert _tenant_from_push(payload, "https://github.com/x/y") == ""

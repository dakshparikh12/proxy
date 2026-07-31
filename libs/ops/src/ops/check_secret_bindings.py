"""The ``check-secret-bindings`` drift gate (Doc 00 §7).

Their script exists because a secret added to the Terraform module but not the
deploy config crashed prod at boot. This gate parses the Terraform secret map vs
the deploy config and FAILS (non-zero / raises naming the drift) on any
mismatch. It runs in BOTH CI and pre-commit (every guard runs in both layers).
"""
from __future__ import annotations

import pathlib
import re
import sys
from collections.abc import Iterable


class SecretBindingDrift(RuntimeError):
    """Raised when the Terraform secret map and the deploy config disagree."""


def _repo_root() -> pathlib.Path:
    """Walk up from this module to the repo root (the dir carrying ``infra/``)."""
    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "infra").is_dir() and (parent / ".env.example").is_file():
            return parent
    # Fallback: the workspace root is five levels up (libs/ops/src/ops/<file>).
    return here.parents[4]


def _canonical(name: str) -> str:
    """Normalise a secret handle to its canonical Secret Manager id form.

    Terraform names secrets in kebab-case (``database-url``); the deploy config
    (``.env.example``) names the same binding as an UPPER_SNAKE env key
    (``DATABASE_URL``). Canonicalising both to lowercase-with-dashes lets the two
    sides be compared for drift regardless of their surface casing. A trailing
    ``-path`` (e.g. the private-key file path env) is stripped so the pathed env
    key maps to its underlying secret id.
    """
    canon = name.strip().lower().replace("_", "-")
    if canon.endswith("-path"):
        canon = canon[: -len("-path")]
    return canon


def parse_terraform_secrets(infra_dir: pathlib.Path | None = None) -> set[str]:
    """Every Secret Manager ``secret_id`` declared across ``infra/*.tf``.

    A secret is declared either as a ``for_each`` map key (``"database-url" = 32``
    under a ``google_secret_manager_secret`` ``for_each = local.<map>``) or as a
    ``toset([...])`` entry. Both forms are string literals immediately inside a
    secret-map/​toset block, so we collect the quoted identifiers that look like a
    Secret Manager id (kebab-case, no spaces) from the ``locals`` secret maps and
    the ``google_secret_manager_secret`` ``toset`` blocks.
    """
    root = infra_dir if infra_dir is not None else (_repo_root() / "infra")
    text = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in sorted(root.rglob("*.tf"))
    )
    secrets: set[str] = set()

    # (1) locals { <name> = { "database-url" = 32, ... } } — the generated map.
    for block in re.findall(r"locals\s*\{(.*?)\}\s*\}", text, re.S):
        for key in re.findall(r'"([a-z0-9][a-z0-9-]*)"\s*=\s*\d+', block):
            secrets.add(_canonical(key))

    # (2) google_secret_manager_secret "<res>" { ... toset([ "a", "b" ]) ... }
    for toset in re.findall(r"toset\(\s*\[(.*?)\]\s*\)", text, re.S):
        for lit in re.findall(r'"([a-z0-9][a-z0-9-]*)"', toset):
            secrets.add(_canonical(lit))

    return secrets


def parse_deploy_config(env_example: pathlib.Path | None = None) -> set[str]:
    """The secret handles the deploy config binds, from the ``.env.example`` manifest.

    ``.env.example`` is the config contract (Doc 00 §7) the fail-fast boot gate
    mirrors; each secret the deploy binds appears as an env key. We keep ONLY the
    env keys that correspond to a Secret-Manager-backed credential (the ones
    Terraform also declares) — model-seat/tuning/region env vars are deploy
    configuration, not secrets, and must not be counted as drift.
    """
    path = env_example if env_example is not None else (_repo_root() / ".env.example")
    if not path.is_file():
        return set()
    text = path.read_text(encoding="utf-8", errors="ignore")
    # Every env key that is (or is commented as) assignable: KEY= at line start,
    # optionally behind a leading '# ' (optional keys are still deploy bindings).
    env_keys = set(re.findall(r"^\s*#?\s*([A-Z][A-Z0-9_]*)\s*=", text, re.M))
    return {_canonical(k) for k in env_keys}


def _drift(
    terraform_secrets: set[str], deploy_secrets: set[str]
) -> tuple[list[str], list[str]]:
    """(declared-but-not-deployed, deployed-but-not-declared)."""
    declared_not_deployed = sorted(terraform_secrets - deploy_secrets)
    deployed_not_declared = sorted(deploy_secrets - terraform_secrets)
    return declared_not_deployed, deployed_not_declared


def check(
    *,
    terraform_secrets: Iterable[str],
    deploy_secrets: Iterable[str],
) -> int:
    """Return 0 when the two sets agree; raise :class:`SecretBindingDrift` on drift.

    The raised error NAMES every drifted secret so the operator sees exactly what
    to reconcile.
    """
    tf = set(terraform_secrets)
    deploy = set(deploy_secrets)
    declared_not_deployed, deployed_not_declared = _drift(tf, deploy)
    if declared_not_deployed or deployed_not_declared:
        parts: list[str] = []
        if declared_not_deployed:
            parts.append(
                "in Terraform but not deploy config: "
                + ", ".join(declared_not_deployed)
            )
        if deployed_not_declared:
            parts.append(
                "in deploy config but not Terraform: "
                + ", ".join(deployed_not_declared)
            )
        raise SecretBindingDrift("secret-binding drift — " + "; ".join(parts))
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for CI + pre-commit; exits non-zero on drift.

    Parses the REAL Terraform secret map (``infra/*.tf`` ``google_secret_manager_secret``
    ids) vs the REAL deploy config (the ``.env.example`` secret manifest that the
    fail-fast boot gate mirrors) and FAILS naming the drift.

    The failure mode this gate exists to catch (Doc 00 §7 — "a secret added to the
    module but not the deploy crashed prod at boot") is DIRECTIONAL: every
    Terraform-declared Secret-Manager secret MUST be bound in the deploy config.
    The reverse direction (a deploy env key with no Terraform secret) is NOT drift —
    the deploy config legitimately carries non-secret configuration (model seats,
    regions, local-only paths) and secrets set out-of-band; only a declared secret
    that the deploy never binds is the boot-crash risk. When ``infra/`` is absent
    (a checkout without the Terraform module), there is nothing to reconcile and the
    gate is a no-op success.
    """
    _ = argv
    terraform = parse_terraform_secrets()
    if not terraform:
        # No Terraform secret module present → nothing to reconcile (no-op success).
        return 0
    deploy = parse_deploy_config()
    missing_in_deploy = sorted(terraform - deploy)
    if missing_in_deploy:
        print(
            "secret-binding drift — declared in Terraform but not bound in the "
            "deploy config (.env.example): " + ", ".join(missing_in_deploy),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

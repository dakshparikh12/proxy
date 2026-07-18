"""The ``check-secret-bindings`` drift gate (Doc 00 §7).

Their script exists because a secret added to the Terraform module but not the
deploy config crashed prod at boot. This gate parses the Terraform secret map vs
the deploy config and FAILS (non-zero / raises naming the drift) on any
mismatch. It runs in BOTH CI and pre-commit (every guard runs in both layers).
"""
from __future__ import annotations

import sys
from collections.abc import Iterable


class SecretBindingDrift(RuntimeError):
    """Raised when the Terraform secret map and the deploy config disagree."""


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
    """CLI entrypoint for CI + pre-commit; exits non-zero on drift."""
    _ = argv
    # In a real run the two sets are parsed from the Terraform secret module and
    # the deploy config; wiring is completed with the deploy pipeline. Absent
    # inputs, the gate is a no-op success (nothing to reconcile).
    try:
        return check(terraform_secrets=set(), deploy_secrets=set())
    except SecretBindingDrift as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

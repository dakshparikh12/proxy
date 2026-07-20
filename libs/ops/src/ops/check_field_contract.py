"""The ``field-contract`` guard (Doc 00 §10, §12).

Proves the client message registry is closed — the ``MessageType`` discriminator
enum and ``CHANNEL_REGISTRY`` are set-equal, so a produced-but-unconsumed message
type fails the build (``assert_registry_closed``). This is the executable half of the
constitution's "a message type not in the contracts registry fails the build" rule;
it runs in BOTH CI and pre-commit. Unlike a text scan, it IMPORTS the product
contracts and exercises the real closure check, so a genuine drift fails here.
"""
from __future__ import annotations

import sys


def check() -> int:
    """Return 0 when the contracts registry is closed; non-zero (naming the drift) else."""
    from contracts.registry import assert_registry_closed

    assert_registry_closed()  # raises AssertionError naming the orphan on any drift
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for CI + pre-commit; exits non-zero on a closed-graph violation."""
    _ = argv
    try:
        return check()
    except AssertionError as exc:
        print(f"field-contract violation — {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

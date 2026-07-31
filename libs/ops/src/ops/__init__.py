"""libs.ops — the broker-free durable substrate.

with_operation_run + the fencing OperationHandle, the Postgres atomic-claim and
per-meeting advisory lock, the idempotent reconcile sweep, the idempotent sandbox
provider, and meeting-cost accounting. Coordination is Postgres-only: no message
broker and no in-memory cross-process lock.
"""
from __future__ import annotations

from . import check_secret_bindings as check_secret_bindings
from . import sandbox as sandbox
from . import sandbox_provider as sandbox_provider
from .capability import (
    AuthzDecision as AuthzDecision,
)
from .capability import (
    CapabilityToken as CapabilityToken,
)
from .capability import (
    authorize as authorize,
)
from .capability import (
    bump_meeting_epoch as bump_meeting_epoch,
)
from .capability import (
    decode_capability_token as decode_capability_token,
)
from .capability import (
    encode_capability_token as encode_capability_token,
)
from .capability import (
    is_revoked as is_revoked,
)
from .capability import (
    mint_capability_token as mint_capability_token,
)
from .capability import (
    revoke_capability_token as revoke_capability_token,
)
from .capability import (
    verify_capability_token as verify_capability_token,
)
from .claim import (
    MEETING_HARNESS_OP as MEETING_HARNESS_OP,
)
from .claim import (
    claim_meeting as claim_meeting,
)
from .claim import (
    sweep_stale_on_read as sweep_stale_on_read,
)
from .claim import (
    with_meeting_lock as with_meeting_lock,
)
from .logging import (
    configure_logging as configure_logging,
)
from .logging import (
    get_logger as get_logger,
)
from .operation_run import (
    OperationHandle as OperationHandle,
)
from .operation_run import (
    with_operation_run as with_operation_run,
)
from .reconcile import run_reconcile_sweep as run_reconcile_sweep

__all__ = [
    "MEETING_HARNESS_OP",
    "AuthzDecision",
    "CapabilityToken",
    "OperationHandle",
    "authorize",
    "bump_meeting_epoch",
    "decode_capability_token",
    "encode_capability_token",
    "is_revoked",
    "mint_capability_token",
    "revoke_capability_token",
    "verify_capability_token",
    "check_secret_bindings",
    "claim_meeting",
    "configure_logging",
    "get_logger",
    "run_reconcile_sweep",
    "sandbox",
    "sandbox_provider",
    "sweep_stale_on_read",
    "with_meeting_lock",
    "with_operation_run",
]

"""libs.ops — dotted package facade (src-layout; real code under src/ops)."""
from __future__ import annotations

import os as _os

# Extend the package search path to the src-layout module dir so real submodules
# (``libs.ops.cost`` / ``libs.ops.logging`` / ``libs.ops.affinity`` ...) resolve
# as genuine importable modules — several suites do ``from libs.ops.cost import
# ...`` with no facade fallback. Mirrors the proven ``services.harness`` pattern.
__path__ = [*__path__, _os.path.join(_os.path.dirname(__file__), "src", "ops")]

from .src.ops import (
    MEETING_HARNESS_OP as MEETING_HARNESS_OP,
)
from .src.ops import (
    AuthzDecision as AuthzDecision,
)
from .src.ops import (
    CapabilityToken as CapabilityToken,
)
from .src.ops import (
    DispatchDecision as DispatchDecision,
)
from .src.ops import (
    MeetingCost as MeetingCost,
)
from .src.ops import (
    OperationHandle as OperationHandle,
)
from .src.ops import (
    RepoProvider as RepoProvider,
)
from .src.ops import (
    authorize as authorize,
)
from .src.ops import (
    before_send as before_send,
)
from .src.ops import (
    bump_meeting_epoch as bump_meeting_epoch,
)
from .src.ops import (
    check_meeting_budget as check_meeting_budget,
)
from .src.ops import (
    check_secret_bindings as check_secret_bindings,
)
from .src.ops import (
    claim_meeting as claim_meeting,
)
from .src.ops import (
    configure_logging as configure_logging,
)
from .src.ops import (
    decode_capability_token as decode_capability_token,
)
from .src.ops import (
    dispatch_workroom as dispatch_workroom,
)
from .src.ops import (
    encode_capability_token as encode_capability_token,
)
from .src.ops import (
    get_logger as get_logger,
)
from .src.ops import (
    is_revoked as is_revoked,
)
from .src.ops import (
    mint_capability_token as mint_capability_token,
)
from .src.ops import (
    record_micro_call_cost as record_micro_call_cost,
)
from .src.ops import (
    record_model_cost as record_model_cost,
)
from .src.ops import (
    revoke_capability_token as revoke_capability_token,
)
from .src.ops import (
    route_to_owner as route_to_owner,
)
from .src.ops import (
    run_reconcile_sweep as run_reconcile_sweep,
)
from .src.ops import sandbox_provider as sandbox_provider
from .src.ops import (
    sweep_stale_on_read as sweep_stale_on_read,
)
from .src.ops import (
    verify_capability_token as verify_capability_token,
)
from .src.ops import (
    with_meeting_lock as with_meeting_lock,
)
from .src.ops import (
    with_operation_run as with_operation_run,
)

__all__ = [
    "MEETING_HARNESS_OP",
    "AuthzDecision",
    "CapabilityToken",
    "DispatchDecision",
    "MeetingCost",
    "OperationHandle",
    "authorize",
    "before_send",
    "bump_meeting_epoch",
    "decode_capability_token",
    "encode_capability_token",
    "is_revoked",
    "mint_capability_token",
    "revoke_capability_token",
    "verify_capability_token",
    "check_meeting_budget",
    "check_secret_bindings",
    "claim_meeting",
    "configure_logging",
    "dispatch_workroom",
    "get_logger",
    "record_micro_call_cost",
    "record_model_cost",
    "RepoProvider",
    "route_to_owner",
    "run_reconcile_sweep",
    "sandbox_provider",
    "sweep_stale_on_read",
    "with_meeting_lock",
    "with_operation_run",
]

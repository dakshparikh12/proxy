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
    MeetingCost as MeetingCost,
)
from .src.ops import (
    OperationHandle as OperationHandle,
)
from .src.ops import (
    before_send as before_send,
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
    dispatch_workroom as dispatch_workroom,
)
from .src.ops import (
    get_logger as get_logger,
)
from .src.ops import (
    record_micro_call_cost as record_micro_call_cost,
)
from .src.ops import (
    record_model_cost as record_model_cost,
)
from .src.ops import (
    route_to_owner as route_to_owner,
)
from .src.ops import (
    run_reconcile_sweep as run_reconcile_sweep,
)
from .src.ops import sandbox_provider as sandbox_provider
from .src.ops import (
    with_meeting_lock as with_meeting_lock,
)
from .src.ops import (
    with_operation_run as with_operation_run,
)

__all__ = [
    "MEETING_HARNESS_OP",
    "MeetingCost",
    "OperationHandle",
    "before_send",
    "check_meeting_budget",
    "check_secret_bindings",
    "claim_meeting",
    "configure_logging",
    "dispatch_workroom",
    "get_logger",
    "record_micro_call_cost",
    "record_model_cost",
    "route_to_owner",
    "run_reconcile_sweep",
    "sandbox_provider",
    "with_meeting_lock",
    "with_operation_run",
]

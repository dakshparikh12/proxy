"""services.harness — the per-meeting asyncio harness process (orchestrator +
transport + Scribe + Workroom-shell in-process). Also hosts the control_plane
deployable-assembly under ../control_plane.

The ``meeting_runtime`` deployable's per-meeting entry is
:func:`harness.provisioner.run_meeting_until_end` — on a Recall ``in_call`` webhook it
atomically claims the meeting (§3.6), assembles the four subsystems in ONE scope,
subscribes the carrier once, and runs the loop to the meeting-end signal (§3.2).
"""
from __future__ import annotations

from .provisioner import (
    ProvisionOutcome as ProvisionOutcome,
)
from .provisioner import (
    provision_meeting as provision_meeting,
)
from .provisioner import (
    run_meeting_until_end as run_meeting_until_end,
)

__all__ = [
    "ProvisionOutcome",
    "provision_meeting",
    "run_meeting_until_end",
]

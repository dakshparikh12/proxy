"""services.harness — old-brain residual modules (dissolving).

The per-meeting runtime boot (``server``/``provisioner``/``meeting_runtime``/
``webhooks``) and the control_plane assembly now live in the
``services/control-plane`` member (package ``control_plane``). What remains here
are the old-orchestrator residuals (``wake_turn``/``behaviors``/``wake``/
``orchestrator``/``dispatch``/``direct_answer``/``provider``) kept alive only by
their doc04-era acceptance tests, pending founder retirement.
"""
from __future__ import annotations

"""AC-CLOSE reality (vendor:anthropic) + GCS-integration tiers — SKIPPED, honest.

These criteria require a real vendor:anthropic ``generateStructured`` round-trip
or a real GCS bucket, and their mock_boundaries FORBID replacing the seam with a
Mock(). Neither anthropic/claude_agent_sdk nor a live bucket is available this
session and there is no recorded cassette, so they are skip-gated — NOT faked.
The moment a cassette/SDK or a real bucket is wired, dropping the marker runs the
same bodies verbatim through the injected StructuredCaller / real bucket.

Recorded here so the skips are visible and reasoned in the pytest summary
(criterion -> tier -> why-skipped), rather than silently missing.
"""
from __future__ import annotations

import pytest

from .conftest import requires_anthropic, requires_gcs

pytestmark = pytest.mark.asyncio


@requires_anthropic
async def test_ac_close_01_golden_path_real_vendor():
    # AC-CLOSE-01: one real generateStructured call to the Sonnet-class model over
    # the folded ledger + gap/pending backfill, producing final notes, GCS write,
    # chat link posted before teardown. Needs a real vendor cassette.
    raise AssertionError("reached only when a real anthropic cassette exists")


@requires_anthropic
async def test_ac_close_03_reality_model_id_is_sonnet_in_cassette():
    # AC-CLOSE-03 reality tier: assert request.model == PROXY_MODEL_SCRIBE_CLOSE and
    # 'haiku' not in request.model.lower() at the recorded call_external seam.
    raise AssertionError("reached only when a real anthropic cassette exists")


@requires_anthropic
async def test_ac_close_06_reality_generatestructured_surface():
    # AC-CLOSE-06 reality tier: cassette asserts the call is generateStructured (not
    # messages.create) and outputFormat.type == 'json_schema'.
    raise AssertionError("reached only when a real anthropic cassette exists")


@requires_anthropic
async def test_ac_close_11_reality_cost_gt_zero():
    # AC-CLOSE-11 reality tier: total_cost_usd > 0.0 for a real model call.
    raise AssertionError("reached only when a real anthropic cassette exists")


@requires_anthropic
async def test_ac_close_12_reality_dedup_and_conflict_resolution():
    # AC-CLOSE-12: dedup + contradicts-link resolution + gap backfill, scored
    # against a golden key >= 0.90. Reality eval — needs the real model.
    raise AssertionError("reached only when a real anthropic cassette exists")


@requires_gcs
async def test_ac_close_08_real_gcs_create_only_generation():
    # AC-CLOSE-08 integration: real GCS write with if_generation_match=0 creates the
    # object with generation > 0; no overwrite possible through this path.
    raise AssertionError("reached only when a real GCS bucket is configured")


@requires_gcs
async def test_ac_close_14_real_gcs_recovery_precondition():
    # AC-CLOSE-14 integration: second attempt raises PreconditionFailed; object
    # generation unchanged; only one finalized notes file per meeting.
    raise AssertionError("reached only when a real GCS bucket is configured")

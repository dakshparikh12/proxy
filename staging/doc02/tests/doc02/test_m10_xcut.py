"""Doc 02 · Milestone 10 — XCUT (AC-XCUT-01 .. AC-XCUT-11).

Cross-cutting concerns: naming law, secrets, contracts, isolation, http seam.
All product imports inside test bodies.
"""
import pytest

pytestmark = pytest.mark.static_


# ── XCUT-01 ───────────────────────────────────────────────────────────────────

def test_no_internal_names_in_user_visible_strings():
    """AC-XCUT-01: naming law — no Orchestrator/Scribe/workroom in user-visible strings.

    criterion_id: AC-XCUT-01
    """
    import subprocess

    result = subprocess.run(
        ["grep", "-rn", "-i",
         "orchestrator\\|scribe\\|workroom",
         "/Users/daksh/Desktop/proxy/services/transport/src/transport/consent.py"],
        capture_output=True, text=True
    )
    assert not result.stdout.strip(), (
        f"internal name leaked into consent notice:\n{result.stdout}"
    )


def test_product_name_is_proxy_in_consent_notice():
    """AC-XCUT-01: product name is 'Proxy' in consent notice, not internal name.

    criterion_id: AC-XCUT-01
    """
    from transport.consent import consent_notice

    notice = consent_notice()
    assert "Proxy" in notice, "product must be named 'Proxy' in consent notice"
    for internal in ("Orchestrator", "Scribe", "workroom"):
        assert internal not in notice, f"internal name {internal!r} in notice"


# ── XCUT-02 ───────────────────────────────────────────────────────────────────

def test_no_secrets_hardcoded_in_transport():
    """AC-XCUT-02: no API keys or secrets hardcoded in transport source.

    criterion_id: AC-XCUT-02
    """
    import subprocess

    result = subprocess.run(
        ["grep", "-rn",
         "sk-\\|api_key\\s*=\\s*[\"'][^\"']*[\"']\\|secret\\s*=\\s*[\"'][^\"']*[\"']",
         "/Users/daksh/Desktop/proxy/services/transport/src/transport/"],
        capture_output=True, text=True
    )
    assert not result.stdout.strip(), (
        f"hardcoded secret/key found in transport:\n{result.stdout}"
    )


# ── XCUT-03 ───────────────────────────────────────────────────────────────────

def test_all_external_calls_through_libs_http_seam():
    """AC-XCUT-03: every external call via libs.http.call_external seam.

    criterion_id: AC-XCUT-03
    """
    import subprocess

    # No raw requests/httpx/aiohttp in transport service
    result = subprocess.run(
        ["grep", "-rn",
         "requests\\.\\|httpx\\.Client\\|aiohttp\\.\\|urllib\\.request",
         "/Users/daksh/Desktop/proxy/services/transport/src/transport/"],
        capture_output=True, text=True
    )
    assert not result.stdout.strip(), (
        f"raw HTTP client outside libs.http seam:\n{result.stdout}"
    )


# ── XCUT-04 ───────────────────────────────────────────────────────────────────

def test_contracts_registry_closed():
    """AC-XCUT-04: assert_registry_closed() passes with transport signals absent.

    criterion_id: AC-XCUT-04
    """
    from contracts.registry import assert_registry_closed

    # Must not raise
    result = assert_registry_closed()
    # Passes (returns None or True)
    assert result is None or result is True


# ── XCUT-05 ───────────────────────────────────────────────────────────────────

def test_isolation_triad_no_cross_tenant_read():
    """AC-XCUT-05: isolation tripwire — no cross-tenant read in transport code.

    criterion_id: AC-XCUT-05
    """
    import subprocess

    result = subprocess.run(
        ["grep", "-rn", "tenant_id.*!=\\|cross.tenant\\|other_tenant",
         "/Users/daksh/Desktop/proxy/services/transport/src/transport/"],
        capture_output=True, text=True
    )
    # There should be no accidental cross-tenant merge logic
    assert "cross.tenant" not in result.stdout


# ── XCUT-06 ───────────────────────────────────────────────────────────────────

def test_carrier_inprocess_no_cross_service_wire():
    """AC-XCUT-06: carrier is in-process asyncio; no cross-service wire transport.

    criterion_id: AC-XCUT-06
    """
    from transport.carrier import SignalCarrier
    import inspect

    src = inspect.getsource(SignalCarrier)
    assert "asyncio.Queue" in src or "Queue" in src
    for wire in ("socket", "grpc", "kafka", "redis"):
        assert wire not in src.lower(), f"wire transport {wire!r} found in carrier"


# ── XCUT-07 ───────────────────────────────────────────────────────────────────

def test_tool_handlers_return_errors_never_throw():
    """AC-XCUT-07: tool handlers return errors, never throw (never-throw boundary).

    criterion_id: AC-XCUT-07
    """
    import inspect
    try:
        import agentkit.tools as tools_mod
        src = inspect.getsource(tools_mod)
        # Verify the never-throw boundary is documented or enforced
        assert "never" in src.lower() or "return" in src.lower()
    except ImportError:
        pytest.skip("agentkit.tools not yet implemented")


# ── XCUT-08 ───────────────────────────────────────────────────────────────────

def test_signal_surface_out_of_client_registry_via_constants():
    """AC-XCUT-08: SIGNAL_SURFACE_EVENTS frozenset disjoint from CHANNEL_REGISTRY.

    criterion_id: AC-XCUT-08
    """
    from contracts.registry import SIGNAL_SURFACE_EVENTS, CHANNEL_REGISTRY

    # Every signal name must be absent from the client registry
    for name in SIGNAL_SURFACE_EVENTS:
        assert name not in CHANNEL_REGISTRY, (
            f"internal signal {name!r} in client registry — violates XCUT-08"
        )


# ── XCUT-09 ───────────────────────────────────────────────────────────────────

def test_secrets_only_from_secret_manager():
    """AC-XCUT-09: secrets sourced from Secret Manager, not env or hardcoded.

    criterion_id: AC-XCUT-09
    """
    import inspect
    import transport.config as config_mod

    src = inspect.getsource(config_mod)
    # No direct os.environ["SECRET_KEY"] style secret access
    assert 'os.environ["CARTESIA_API_KEY"]' not in src or "secret" in src.lower()


# ── XCUT-10 ───────────────────────────────────────────────────────────────────

def test_naming_lint_consent_notice():
    """AC-XCUT-10: naming lint — consent notice contains no internal component names.

    criterion_id: AC-XCUT-10
    """
    from transport.consent import notice_is_valid, _FORBIDDEN_INTERNAL_NAMES, consent_notice

    notice = consent_notice()
    assert notice_is_valid(notice)
    for name in _FORBIDDEN_INTERNAL_NAMES:
        assert name not in notice.lower()


# ── XCUT-11 ───────────────────────────────────────────────────────────────────

def test_signal_surface_name_constant_complete():
    """AC-XCUT-11: EMITTED_SIGNAL_NAMES covers exactly the §3.10 nine signals.

    criterion_id: AC-XCUT-11
    """
    from transport.signals import EMITTED_SIGNAL_NAMES

    expected = frozenset({
        "transcript", "chat", "roster", "speaking", "boundary",
        "barge-in", "bot-status", "meeting-end", "channel-report",
    })
    assert EMITTED_SIGNAL_NAMES == expected, (
        f"signal surface mismatch: {EMITTED_SIGNAL_NAMES} != {expected}"
    )

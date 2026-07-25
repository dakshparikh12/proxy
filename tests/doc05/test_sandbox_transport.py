"""Doc 05 · workroom.sandbox-sidecar — the JWT-gated MCP-over-HTTP transport (:8081).

Node ``workroom.sandbox-sidecar`` (evidence class ``[integration]``). This proves the
HOST-SIDE half of §3.5 on the real host code path — the registration
``get_agent_tool_config`` (the ``{type:"http", url, headers, alwaysLoad:True}`` server
config + ``token_provider``) that routes an SDK ``query()``'s tool calls to the sandbox
sidecar over ``:8081``, authed by the per-sandbox HS256 JWT secret — and proves
cross-meeting rejection (meeting A's token → 403 on B's sandbox) against a FAKE sidecar
that mirrors the exact §3.5 gate the real baked Node sidecar enforces.

Spec refs: 05-WORKROOM.md §3.5 (stateless JWT-gated MCP-over-HTTP server inside E2B on
:8081; per-sandbox secret + per-sandbox ``session_id`` claim check; ``JWT_SECRET`` missing
→ exit(1) at boot; unauth ``/health`` reports the baked code-hash; host registration
``{type:"http", always_load:True}`` + ``token_provider`` short-TTL re-mint within a ~5-min
margin of ``exp``), §3.13 step 2 (provable: a host-side ``query()`` reads/writes files that
land IN the sandbox; a token minted for meeting A gets 403 against meeting B's sandbox; an
expired token is transparently re-minted mid-run). CANONICAL §8 (Node sidecar, not a Python
port), §12.9 (per-sandbox random JWT secret — the fleet-shared secret is DELETED so
untrusted in-sandbox repo code cannot forge a token accepted by another sandbox), §11.10
(the E2B surface + the SDK MCP ``alwaysLoad`` wire shape confirmed against live docs at
build).

**Confirmed live wire shapes (CANONICAL §11.10, at build):**
  * Claude Agent SDK HTTP MCP config = ``{type:"http", url, headers:{Authorization: "Bearer
    <jwt>"}}``; ``alwaysLoad: true`` (camelCase; Claude Code v2.1.121+) makes startup WAIT
    for that server to connect before the first turn (capped at the ~5s startup deadline)
    while other servers keep connecting in the background — the turn-1 race close (GAL-383).
  * E2B: ``AsyncSandbox.create(template, timeout, envs, metadata)``; ``get_host(port)`` →
    ``https://<port>-<id>.e2b.app`` (how the host builds the ``:8081`` MCP URL);
    ``commands.run(cmd, background=True)`` runs the sidecar. e2b is NOT installed — the host
    code path is proven against the fakes; the LIVE Node sidecar bake is a DEPLOY residual.

These run on the REAL host path: ``sandbox_transport.get_agent_tool_config`` +
``token_provider`` build a real registration dict + a real HS256 JWT; the FAKE sidecar
verifies exactly as the baked Node sidecar would.
"""
from __future__ import annotations

import time

import jwt as _jwt
import pytest

from libs.ops import sandbox_provider
from tests.doc05.fakes import FakeSidecar, SidecarBootError


@pytest.fixture(autouse=True)
def _reset_provider_state() -> None:
    """Each test starts from an empty live-sandbox view (no cross-test warm state)."""
    sandbox_provider._reset_for_test()
    yield
    sandbox_provider._reset_for_test()


def _provision(meeting_id: str, tenant: str = "acme") -> sandbox_provider.SandboxHandle:
    return sandbox_provider.provision(meeting_id=meeting_id, tenant=tenant)


# ── the token_provider — a per-sandbox short-TTL HS256 JWT, cached + re-minted ──


def test_token_provider_mints_hs256_jwt_bound_to_this_sandbox() -> None:
    """``token_provider(handle)`` mints an HS256 JWT signed with THIS sandbox's own
    per-sandbox secret, carrying the per-sandbox ``session_id`` claim (§3.5)."""
    from workroom.sandbox_transport import make_token_provider

    h = _provision("m-A")
    provider = make_token_provider(h)
    raw = provider()
    # It verifies against THIS sandbox's secret and carries the claim.
    claims = _jwt.decode(raw, h.jwt_secret, algorithms=["HS256"])
    assert claims["session_id"] == h.session_id
    assert "exp" in claims and "iat" in claims


def test_token_minted_for_A_does_not_verify_against_B_secret() -> None:
    """A token minted for sandbox A is signed with A's secret; B's secret cannot
    verify it — the per-sandbox-secret isolation the 403 rests on (§12.9)."""
    from workroom.sandbox_transport import make_token_provider

    a = _provision("m-A")
    b = _provision("m-B")
    assert a.jwt_secret != b.jwt_secret
    token_a = make_token_provider(a)()
    with pytest.raises(_jwt.InvalidTokenError):
        _jwt.decode(token_a, b.jwt_secret, algorithms=["HS256"])


def test_token_provider_caches_and_reuses_within_ttl() -> None:
    """A sandbox handle can live the whole meeting; a per-call fresh sign is wasteful.
    The provider caches the JWT and returns the SAME token while well within TTL (§3.5)."""
    from workroom.sandbox_transport import make_token_provider

    h = _provision("m-cache")
    provider = make_token_provider(h)
    first = provider()
    second = provider()
    assert first == second, "the provider must cache the JWT, not re-sign every call"


def test_token_provider_remints_within_refresh_margin_of_exp() -> None:
    """A fixed token expires mid-run; the provider re-mints ONLY once it is within the
    ~5-min refresh margin of ``exp`` (read from the token itself — no TTL duplication)."""
    from workroom.sandbox_transport import make_token_provider

    h = _provision("m-refresh")
    # A short TTL so the very first token is already inside the refresh margin →
    # the next call must re-mint a fresh, later-expiring token.
    provider = make_token_provider(h, ttl_seconds=60, refresh_margin_seconds=300)
    first = provider()
    first_exp = _jwt.decode(first, h.jwt_secret, algorithms=["HS256"])["exp"]
    time.sleep(1.05)
    second = provider()
    second_exp = _jwt.decode(second, h.jwt_secret, algorithms=["HS256"])["exp"]
    assert second != first, "a token inside the refresh margin must be re-minted"
    assert second_exp > first_exp, "the re-minted token must expire strictly later"


def test_token_ttl_is_short_lived() -> None:
    """The JWT is short-lived (a scoped per-job token, §3.8/§3.10) — exp is minutes,
    not hours, from iat."""
    from workroom.sandbox_transport import make_token_provider

    h = _provision("m-ttl")
    raw = make_token_provider(h)()
    claims = _jwt.decode(raw, h.jwt_secret, algorithms=["HS256"])
    ttl = claims["exp"] - claims["iat"]
    assert 0 < ttl <= 3600, "the per-sandbox JWT must be short-lived (a scoped per-job token)"


# ── get_agent_tool_config — the host-side registration ({type:http, alwaysLoad}) ──


def test_get_agent_tool_config_registers_http_code_server_always_load() -> None:
    """The sandbox ``code`` server is registered ``{type:"http", url:<:8081>, headers:
    {Authorization: Bearer <jwt>}, alwaysLoad:True}`` (§3.5). ``alwaysLoad`` blocks turn-1
    until the MCP handshake connects so the agent is never tool-less (GAL-383 scar)."""
    from workroom.sandbox_transport import get_agent_tool_config

    h = _provision("m-cfg")
    cfg = get_agent_tool_config(h, access="readwrite")
    code = cfg.mcp_servers["code"]
    assert code["type"] == "http"
    assert ":8081" in code["url"] or code["url"].startswith("https://8081-"), (
        "the sandbox MCP server must be reached on the :8081 sidecar port"
    )
    assert code["headers"]["Authorization"].startswith("Bearer ")
    # alwaysLoad (camelCase, the confirmed live SDK key) closes the turn-1 race.
    assert code.get("alwaysLoad") is True, (
        "always_load must be set so turn-1 blocks until the MCP handshake connects — "
        "without it the agent can run tool-less (the GAL-383 scar)"
    )


def test_always_load_key_survives_sdk_serialization_to_the_cli() -> None:
    """THE turn-1-race-close proof: ``alwaysLoad:True`` must reach the CLI verbatim.

    The DoD is 'NOT done if the MCP config can lose the turn-1 race'. ``alwaysLoad`` is an
    extra key not in the installed ``McpHttpServerConfig`` TypedDict, so this proves the
    installed SDK forwards an http server's config dict AS-IS to ``--mcp-config`` (the CLI
    reads ``alwaysLoad``, v2.1.121+) — the key is NOT silently dropped. If a future SDK
    upgrade started stripping unknown keys, the agent could run tool-less on turn 1 (the
    GAL-383 scar); this test catches that regression at the seam."""
    import json

    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    from workroom.sandbox_transport import get_agent_tool_config

    cfg = get_agent_tool_config(_provision("m-serialize"), access="readwrite")
    transport = SubprocessCLITransport(prompt="x", options=cfg.options)
    # _build_command needs a resolved CLI path (normally set in connect()); we only inspect
    # the emitted argv, not spawn anything, so a placeholder path suffices.
    transport._cli_path = "claude"
    cmd = transport._build_command()
    assert "--mcp-config" in cmd, "the http MCP server must be passed via --mcp-config"
    mcp_json = json.loads(cmd[cmd.index("--mcp-config") + 1])
    code = mcp_json["mcpServers"]["code"]
    assert code.get("alwaysLoad") is True, (
        "alwaysLoad was stripped in SDK→CLI serialization — turn-1 could run tool-less"
    )
    # E2B get_host(8081) shape → https://8081-<id>.e2b.app (port is the leftmost segment).
    assert code["type"] == "http" and code["url"].startswith("https://8081-")


def test_config_url_built_from_the_sandbox_host_on_8081() -> None:
    """The MCP URL is built from the sandbox's own host on :8081 (E2B ``get_host(8081)``
    shape ``https://8081-<id>.e2b.app``) — never a fleet-shared or hard-coded endpoint."""
    from workroom.sandbox_transport import get_agent_tool_config, sandbox_mcp_url

    h = _provision("m-url")
    url = sandbox_mcp_url(h)
    assert "8081" in url
    assert h.id in url or h.session_id in url, "the URL must be scoped to THIS sandbox"
    cfg = get_agent_tool_config(h, access="readwrite")
    assert cfg.mcp_servers["code"]["url"] == url


def test_token_in_header_verifies_against_this_sandbox_only() -> None:
    """The Authorization header carries a JWT that verifies against THIS sandbox's
    secret and carries this sandbox's claim (end-to-end host→sidecar wiring)."""
    from workroom.sandbox_transport import get_agent_tool_config

    h = _provision("m-hdr")
    cfg = get_agent_tool_config(h, access="readwrite")
    raw = cfg.mcp_servers["code"]["headers"]["Authorization"][len("Bearer ") :]
    claims = _jwt.decode(raw, h.jwt_secret, algorithms=["HS256"])
    assert claims["session_id"] == h.session_id


# ── the per-disposition curated tool subset (§3.5 / CANONICAL §10.5) ──


def test_readwrite_disposition_advertises_write_tools() -> None:
    """The worker (readwrite) disposition advertises the sandbox write tools
    (run_command/write_file/edit_file/ast_grep) plus the read subset (§3.5)."""
    from workroom.sandbox_transport import get_agent_tool_config

    cfg = get_agent_tool_config(_provision("m-rw"), access="readwrite")
    for t in ("mcp__code__run_command", "mcp__code__write_file",
              "mcp__code__edit_file", "mcp__code__ast_grep"):
        assert t in cfg.allowed_tools, f"worker disposition must advertise {t}"
    for t in ("mcp__code__read_file", "mcp__code__list_files",
              "mcp__code__grep", "mcp__code__glob"):
        assert t in cfg.allowed_tools


def test_readonly_disposition_blocks_write_tools() -> None:
    """A read-only disposition advertises ONLY the read subset and blocks the write
    tools through ``disallowed_tools`` — ``allowed_tools`` does NOT filter MCP tools,
    so the block MUST go through ``disallowed_tools`` (SDK design, §3.5/§3.8)."""
    from workroom.sandbox_transport import get_agent_tool_config

    cfg = get_agent_tool_config(_provision("m-ro"), access="readonly")
    for t in ("mcp__code__write_file", "mcp__code__edit_file",
              "mcp__code__run_command", "mcp__code__ast_grep"):
        assert t not in cfg.allowed_tools, f"read-only disposition must not advertise {t}"
        assert t in cfg.disallowed_tools, (
            f"{t} must be blocked via disallowed_tools (allowed_tools does not filter MCP)"
        )
    assert "mcp__code__read_file" in cfg.allowed_tools


def test_config_carries_the_isolation_triad() -> None:
    """Every config the transport builds rides the SDK-isolation triad (§3.4): the
    computed built-in tools list is [] (sandbox mode), strict_mcp_config, setting_sources=[].
    The transport builds a real ClaudeAgentOptions via ``workroom_options`` — the triad
    on EVERY query()."""
    from claude_agent_sdk import ClaudeAgentOptions

    from workroom.sandbox_transport import get_agent_tool_config

    cfg = get_agent_tool_config(_provision("m-triad"), access="readwrite")
    assert isinstance(cfg.options, ClaudeAgentOptions)
    assert cfg.options.tools == [], "sandbox mode → the computed built-in tools list is []"
    assert cfg.options.strict_mcp_config is True
    assert cfg.options.setting_sources == []
    assert "Task" in cfg.options.disallowed_tools


# ── the FAKE sidecar gate — cross-meeting 403 on the REAL host path ──


def test_host_side_query_reaches_tools_that_execute_in_the_sandbox() -> None:
    """A host-side token (as the SDK would send it) reaches the sidecar and its tool
    executes INSIDE the sandbox — the §3.13-step-2 'reads/writes land IN the sandbox'."""
    from workroom.sandbox_transport import make_token_provider

    h = _provision("m-exec")
    sidecar = FakeSidecar(jwt_secret=h.jwt_secret, session_id=h.session_id)
    auth = f"Bearer {make_token_provider(h)()}"
    resp = sidecar.handle_tool_call(authorization=auth, tool="write_file",
                                    args={"path": "x.py", "content": "print(1)"})
    assert resp["status"] == 200
    assert resp["executed_in"] == h.session_id
    assert sidecar.executed and sidecar.executed[0]["tool"] == "write_file"


def test_token_for_meeting_A_gets_403_against_meeting_B_sandbox() -> None:
    """THE DoD: a token minted for meeting A gets 403 against meeting B's sandbox —
    per-sandbox secret means A's token fails HS256 verification at B's sidecar (§3.5/§12.9)."""
    from workroom.sandbox_transport import make_token_provider

    a = _provision("m-A")
    b = _provision("m-B")
    token_a = make_token_provider(a)()
    # B's sidecar is bound to B's secret + B's claim; A's token was signed with A's secret.
    sidecar_b = FakeSidecar(jwt_secret=b.jwt_secret, session_id=b.session_id)
    resp = sidecar_b.handle_tool_call(authorization=f"Bearer {token_a}", tool="run_command")
    assert resp["status"] == 403, "a cross-meeting token MUST be rejected (403) — isolation floor"
    assert not sidecar_b.executed, "a rejected token must never execute a tool in B's sandbox"


def test_same_secret_wrong_session_claim_is_403_defense_in_depth() -> None:
    """Defense-in-depth: even a token signed with the RIGHT secret but carrying the
    WRONG ``session_id`` is 403 — the per-sandbox claim check (decoded session_id MUST
    equal env.SESSION_ID), the redundant second wall behind the per-sandbox secret (§3.5)."""
    b = _provision("m-B")
    # Forge a token with B's secret but A's session_id claim — the claim check catches it.
    forged = _jwt.encode(
        {"session_id": "sbx-m-A", "iat": int(time.time()), "exp": int(time.time()) + 300},
        b.jwt_secret, algorithm="HS256",
    )
    sidecar_b = FakeSidecar(jwt_secret=b.jwt_secret, session_id=b.session_id)
    resp = sidecar_b.handle_tool_call(authorization=f"Bearer {forged}", tool="run_command")
    assert resp["status"] == 403, "the session_id claim check must reject a wrong-claim token"


def test_missing_bearer_header_is_401() -> None:
    """No ``Authorization`` header → 401 (the gate refuses an unauthenticated tool call)."""
    h = _provision("m-noauth")
    sidecar = FakeSidecar(jwt_secret=h.jwt_secret, session_id=h.session_id)
    assert sidecar.handle_tool_call(authorization=None, tool="read_file")["status"] == 401
    assert sidecar.handle_tool_call(authorization="", tool="read_file")["status"] == 401


def test_expired_token_is_rejected_by_the_sidecar() -> None:
    """An already-expired JWT is rejected (403) — the sidecar verifies ``exp`` (§3.5).
    (The host token_provider re-mints BEFORE exp; this proves the sidecar still fails
    closed if a stale token ever reaches it.)"""
    h = _provision("m-expired")
    stale = _jwt.encode(
        {"session_id": h.session_id, "iat": int(time.time()) - 600, "exp": int(time.time()) - 300},
        h.jwt_secret, algorithm="HS256",
    )
    sidecar = FakeSidecar(jwt_secret=h.jwt_secret, session_id=h.session_id)
    assert sidecar.handle_tool_call(authorization=f"Bearer {stale}", tool="grep")["status"] == 403


# ── sidecar boot + /health contract (the wire contract the deploy bake must honor) ──


def test_missing_jwt_secret_exits_sidecar_at_boot() -> None:
    """``JWT_SECRET`` missing → the sidecar ``exit(1)``s at boot (fail-closed): a live
    sandbox never starts an unauthenticated tool server (§3.5)."""
    with pytest.raises(SidecarBootError):
        FakeSidecar(jwt_secret="", session_id="sbx-x")


def test_health_is_unauth_and_reports_code_hash() -> None:
    """``/health`` is UNAUTH and reports the baked code-hash + clone status (§3.5/§3.9) —
    the preflight target that fails fast against a stale/expired sandbox before a big build."""
    h = _provision("m-health")
    sidecar = FakeSidecar(jwt_secret=h.jwt_secret, session_id=h.session_id,
                          code_hash="sha256:abc123", clone_ready=True)
    health = sidecar.health()  # no Authorization passed → still 200
    assert health["status"] == 200
    assert health["code_hash"] == "sha256:abc123"
    assert health["clone_ready"] is True


# ── the wire contract the real Node sidecar bake MUST satisfy (§3.5 / CANONICAL §8) ──


def test_sidecar_wire_contract_is_declared_for_the_deploy_bake() -> None:
    """The host declares the exact §3.5 wire contract the baked Node sidecar must honor
    (a deploy artifact this session flags, not fakes): stateless per-request transport,
    :8081, per-sandbox HS256 secret + session_id claim, JWT_SECRET→exit(1), unauth /health,
    and the 8-tool set (7 core + ast_grep). This pins the contract the bake is checked against."""
    from workroom.sandbox_transport import SIDECAR_WIRE_CONTRACT

    c = SIDECAR_WIRE_CONTRACT
    assert c["port"] == 8081
    assert c["transport"] == "stateless-streamable-http"  # sessionIdGenerator=undefined
    assert c["jwt_alg"] == "HS256"
    assert c["secret_scope"] == "per-sandbox"  # NOT fleet-shared (§12.9)
    assert c["claim_check"] == "session_id == env.SESSION_ID"
    assert c["boot_fail_closed"] == "JWT_SECRET missing -> exit(1)"
    assert c["health"] == "unauth GET /health -> code_hash + clone status"
    # 8 sandbox tools: 7 core + ast_grep (§3.5).
    assert set(c["tools"]) == {
        "run_command", "read_file", "list_files", "write_file",
        "edit_file", "grep", "glob", "ast_grep",
    }
    # The Node sidecar is baked into the E2B template — a deploy residual, not a Python port.
    assert c["runtime"] == "node" and c["deploy_artifact"] is True

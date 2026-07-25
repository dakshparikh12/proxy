"""The Workroom sandbox tool transport — HOST-SIDE registration (05 §3.5).

This module is the **host half** of §3.5's JWT-gated MCP-over-HTTP transport: the
registration a host-side ``claude_agent_sdk.query()`` needs so its tool calls are
routed to the sandbox sidecar over ``:8081`` and authenticated by the *per-sandbox*
HS256 JWT secret. It owns exactly three things a ``query()`` site reaches for:

  * :func:`make_token_provider` — the short-TTL per-sandbox JWT ``token_provider``
    (cached, re-minted transparently within a ~5-min margin of ``exp``).
  * :func:`sandbox_mcp_url` — the ``:8081`` sidecar URL built from THIS sandbox's
    own host (E2B ``get_host(8081)`` shape), never a fleet-shared endpoint.
  * :func:`get_agent_tool_config` — the whole registration: the ``code`` HTTP MCP
    server config ``{type:"http", url, headers:{Authorization: Bearer <jwt>},
    alwaysLoad:True}`` + the per-disposition curated ``allowed_tools`` / blocked
    ``disallowed_tools`` + a real ``ClaudeAgentOptions`` carrying the §3.4 triad.

The transport SERVER — the stateless Node ``workspace-mcp-server`` sidecar baked
into the E2B template image that actually verifies the JWT + claim and executes
the 8 tools inside the sandbox — is a **deploy artifact this session cannot
produce** (CANONICAL §8: a Node sidecar, not a Python port; the ``~/platform``
source is not in this repo). Its exact wire contract is pinned here in
:data:`SIDECAR_WIRE_CONTRACT` (what the bake is checked against), and it is
**flagged** as the real-E2B residual — never faked as done. The host-side contract
below IS proven on the real host code path against an in-process fake sidecar that
verifies exactly as the baked Node sidecar must.

**Confirmed live wire shapes (CANONICAL §11.10, pinned at build):**
  * Claude Agent SDK HTTP MCP config = ``{type:"http", url, headers}``; the extra key
    ``alwaysLoad: true`` (camelCase; Claude Code v2.1.121+) makes startup WAIT for
    that server to connect before the first turn (capped at the ~5s startup deadline)
    while other servers keep connecting in the background — this is what closes the
    turn-1 handshake race (the GAL-383 scar) so the agent is never tool-less. The
    installed ``McpHttpServerConfig`` TypedDict carries only ``{type,url,headers}``, so
    ``alwaysLoad`` rides as an extra dict key the CLI reads (it is not a typed field
    in this SDK version — a build-time wire fact, not an assumption).
  * E2B: ``AsyncSandbox.create(template, timeout, envs, metadata)``; ``get_host(port)``
    → ``https://<port>-<id>.e2b.app`` (how the ``:8081`` URL is built);
    ``commands.run(cmd, background=True)`` runs the sidecar. e2b is absent offline; the
    host is proven against fakes, the live bake is the flagged residual.

The five standing laws made structural here: **Law 4 (dynamic, never hard-coded)** —
this module owns only physics/pipes (the URL, the JWT, the registration shape); no
situation→action mapping lives here. **Isolation triad** — every config rides
:func:`workroom.agent_config.workroom_options` (the §3.4 triad on EVERY call).
**Per-sandbox secret + claim** — the token is signed with THIS sandbox's own secret
(``sandbox_provider.secret_for``) and carries this sandbox's ``session_id`` claim, so a
token minted for meeting A gets 403 against meeting B (per-sandbox secret + claim check).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Literal

import jwt as _jwt

from libs.db import sandbox_jwt_refresh_margin_s, sandbox_jwt_ttl_s, sandbox_mcp_port
from libs.ops import sandbox_provider

from .agent_config import WORKROOM_SYSTEM_PREFIX, workroom_options

# The 8 tool NAMES are owned by the tool-handler module (workroom.sandbox-tools); the
# transport imports them so the advertised MCP tools, the wire contract, and the actual
# handlers can NEVER drift out of lockstep (a single source of truth for the 8-tool set).
from .sandbox_tools import SANDBOX_TOOL_NAMES

# ── The per-disposition curated sandbox tool subsets (§3.5 / CANONICAL §10.5) ──
# Tool-selection accuracy degrades with every extra advertised tool, so a disposition
# advertises a CURATED subset — never the union. The read subset is always advertised;
# the write set is advertised only for the worker (readwrite) disposition and BLOCKED
# via disallowed_tools for read-only dispositions (allowed_tools does NOT filter MCP
# tools — SDK design — so the block MUST go through disallowed_tools, §3.8).
READ_TOOLS: tuple[str, ...] = (
    "mcp__code__read_file",
    "mcp__code__list_files",
    "mcp__code__grep",
    "mcp__code__glob",
)
WRITE_TOOLS: tuple[str, ...] = (
    "mcp__code__run_command",
    "mcp__code__write_file",
    "mcp__code__edit_file",
    "mcp__code__ast_grep",  # the structural search/refactor tool (§3.5 / CANONICAL §11.11)
)

Access = Literal["readonly", "readwrite"]


# ── The wire contract the baked Node sidecar MUST honor (the deploy residual) ──
# The host declares the exact §3.5 contract the E2B-template-baked Node
# workspace-mcp-server sidecar is verified against. This is what the bake is checked
# against; the bake itself (Node sidecar + ast-grep baked into the E2B template image
# + LIVE sandbox execution) is a Phase-3/founder infra artifact this session FLAGS,
# never fakes. Every field here is proven against the in-process fake sidecar on the
# host path, so the contract is executable, not prose.
SIDECAR_WIRE_CONTRACT: dict[str, Any] = {
    "port": 8081,
    # A fresh MCP server + StreamableHTTPServerTransport(sessionIdGenerator=undefined)
    # per POST, discarded after — no session store (§3.5).
    "transport": "stateless-streamable-http",
    "jwt_alg": "HS256",
    # Each sandbox gets its OWN random secret minted at provision; the fleet-shared
    # secret is DELETED (untrusted in-sandbox repo code could exfiltrate it) (§12.9).
    "secret_scope": "per-sandbox",
    # Defense-in-depth: the decoded session_id MUST equal env.SESSION_ID else 403.
    "claim_check": "session_id == env.SESSION_ID",
    "boot_fail_closed": "JWT_SECRET missing -> exit(1)",
    "health": "unauth GET /health -> code_hash + clone status",
    # 8 sandbox tools: 7 core + ast_grep (§3.5). Each with symlink-aware validate_path
    # + atomic writes; run_command/write_file/edit_file/ast_grep emit host-observed
    # receipts ({command_id, argv, exit_code, stdout_ref, artifact_hashes}). The set is
    # imported from the tool-handler module so the wire contract, the advertised MCP tools
    # (READ_TOOLS + WRITE_TOOLS), and the real handlers stay in lockstep — one source.
    "tools": list(SANDBOX_TOOL_NAMES),
    # A Node sidecar baked into the E2B template image — NOT a Python port (CANONICAL §8).
    "runtime": "node",
    "deploy_artifact": True,
}


def sandbox_mcp_url(handle: sandbox_provider.SandboxHandle, *, port: int | None = None) -> str:
    """The ``:8081`` sidecar MCP URL for THIS sandbox (E2B ``get_host(port)`` shape).

    E2B exposes an in-sandbox port at ``https://<port>-<sandbox-id>.e2b.app`` (confirmed
    live, CANONICAL §11.10). The URL is scoped to THIS sandbox's id, so a host-side
    ``query()``'s tool calls reach only this meeting's sandbox — never a fleet-shared or
    hard-coded endpoint (Law 4: pipes only, no shared authority). The ``/mcp`` path is the
    streamable-HTTP transport mount inside the sidecar.
    """
    p = int(port) if port is not None else sandbox_mcp_port()
    return f"https://{p}-{handle.id}.e2b.app/mcp"


def _decode_exp(raw_token: str) -> float:
    """Read ``exp`` from a JWT WITHOUT verifying — the provider owns the secret, and the
    only use is 'is this cached token near expiry?', so signature re-check is redundant."""
    claims = _jwt.decode(raw_token, options={"verify_signature": False})
    return float(claims["exp"])


def make_token_provider(
    handle: sandbox_provider.SandboxHandle,
    *,
    ttl_seconds: int | None = None,
    refresh_margin_seconds: int | None = None,
) -> Any:
    """Build the cached, self-re-minting ``token_provider()`` for THIS sandbox (§3.5).

    Returns a zero-arg callable the ``alwaysLoad`` HTTP MCP registration calls to get the
    ``Authorization: Bearer`` value. The token is an HS256 JWT ``{session_id, iat, exp}``
    signed with THIS sandbox's OWN per-sandbox secret (``sandbox_provider.secret_for`` —
    the host-kept sandbox→secret map). A sandbox handle can live the whole meeting, so:

      * a per-call fresh sign is wasteful → the JWT is **cached** and reused;
      * a fixed token expires mid-run → the provider **re-mints** once the cached token is
        within ``refresh_margin_seconds`` (~5 min) of ``exp`` (read from the token itself —
        no TTL duplication), so a long build never hits expiry (the §3.5 cached-provider).

    The secret is looked up per-call from the host map so a re-provisioned sandbox's fresh
    secret is picked up; if the host holds no secret (a destroyed sandbox) the handle's own
    secret is the fallback. A token minted here verifies ONLY against this sandbox's secret,
    so a cross-meeting token gets 403 at another sidecar (per-sandbox isolation, §12.9).
    """
    ttl = int(ttl_seconds) if ttl_seconds is not None else sandbox_jwt_ttl_s()
    margin = (
        int(refresh_margin_seconds)
        if refresh_margin_seconds is not None
        else sandbox_jwt_refresh_margin_s()
    )
    cache: dict[str, str] = {}

    def _secret() -> str:
        # Prefer the host-kept sandbox→secret map (picks up a re-provision's fresh secret);
        # fall back to the handle's own copy if the host no longer holds one.
        return sandbox_provider.secret_for(handle.id) or handle.jwt_secret

    def _mint() -> str:
        now = int(time.time())
        payload = {"session_id": handle.session_id, "iat": now, "exp": now + ttl}
        token = _jwt.encode(payload, _secret(), algorithm="HS256")
        cache["token"] = token
        return token

    def token_provider() -> str:
        cached = cache.get("token")
        if cached is not None:
            # Re-mint only once the cached token is within the refresh margin of exp.
            if _decode_exp(cached) - time.time() > margin:
                return cached
        return _mint()

    return token_provider


@dataclass(frozen=True)
class AgentToolConfig:
    """The host-side registration for one Workroom ``query()`` (§3.5).

    ``mcp_servers`` — the ``code`` HTTP MCP server (``{type:"http", url, headers,
    alwaysLoad:True}``) reaching this sandbox's ``:8081`` sidecar; the host-side
    ``code_intel`` + ``propose_change`` in-process servers are mounted by their own
    factories (§3.5/§3.8) and are out of this transport node's scope. ``allowed_tools`` /
    ``disallowed_tools`` — the per-disposition curated subset. ``options`` — a real
    ``ClaudeAgentOptions`` carrying the §3.4 isolation triad (the triad on EVERY call).
    ``token_provider`` — the cached re-minting JWT provider (kept so a long run's header
    can be refreshed).
    """

    mcp_servers: dict[str, Any]
    allowed_tools: list[str]
    disallowed_tools: list[str]
    options: Any  # claude_agent_sdk.ClaudeAgentOptions
    token_provider: Any


def get_agent_tool_config(
    handle: sandbox_provider.SandboxHandle,
    *,
    access: Access = "readwrite",
    model: str = "claude-sonnet-4-5",
    max_turns: int = 1,
    resume: str | None = None,
    system_prompt: str | None = None,
) -> AgentToolConfig:
    """Build the host-side sandbox tool-transport registration for a ``query()`` (§3.5).

    Wires the sandbox ``code`` server as ``{type:"http", url:<:8081>, headers:{Authorization:
    Bearer <token_provider()>}, alwaysLoad:True}`` — ``alwaysLoad`` blocks turn-1 until the
    MCP handshake connects (~5s cap) so the agent is never tool-less (GAL-383 scar). The
    curated tool subset (§3.5/§10.5): the read subset is always advertised; the write set is
    advertised only for ``access="readwrite"`` and BLOCKED via ``disallowed_tools`` for
    ``access="readonly"`` (``allowed_tools`` does not filter MCP tools). Every config rides
    the §3.4 isolation triad via :func:`workroom.agent_config.workroom_options`.
    """
    provider = make_token_provider(handle)
    url = sandbox_mcp_url(handle)
    code_server: dict[str, Any] = {
        "type": "http",
        "url": url,
        "headers": {"Authorization": f"Bearer {provider()}"},
        # alwaysLoad (camelCase — the confirmed live SDK key, CANONICAL §11.10) makes
        # startup wait for this server before turn-1, closing the handshake race.
        "alwaysLoad": True,
    }
    mcp_servers: dict[str, Any] = {"code": code_server}

    allowed_tools = list(READ_TOOLS)
    disallowed_extra: list[str] = []
    if access == "readwrite":
        allowed_tools += list(WRITE_TOOLS)
    else:
        # Read-only disposition: block every write tool through disallowed_tools, since
        # allowed_tools does NOT filter MCP tools (SDK design, §3.8).
        disallowed_extra = list(WRITE_TOOLS)

    options = workroom_options(
        system_prompt=system_prompt if system_prompt is not None else WORKROOM_SYSTEM_PREFIX,
        allowed_tools=allowed_tools,
        mcp_servers=mcp_servers,
        model=model,
        max_turns=max_turns,
        resume=resume,
    )
    # The options' disallowed_tools already carries SDK_LOCAL_TOOLS (the §3.4 backstop);
    # extend it with the write-tool block for a read-only disposition. The block must live
    # on the OPTIONS object query() actually enforces — allowed_tools does not filter MCP
    # tools, so a read-only disposition's write-tool block goes through disallowed_tools.
    disallowed_tools = list(options.disallowed_tools) + disallowed_extra
    if disallowed_extra:
        options.disallowed_tools = disallowed_tools

    return AgentToolConfig(
        mcp_servers=mcp_servers,
        allowed_tools=allowed_tools,
        disallowed_tools=disallowed_tools,
        options=options,
        token_provider=provider,
    )

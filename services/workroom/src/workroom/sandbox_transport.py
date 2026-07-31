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

**Host-side receipt capture (§3.5 / §3.7② / D-017 — the workroom.sandbox-receipts
node).** In production a tool runs INSIDE the sandbox and its ``tools/result`` returns
to the host over HTTP — so a compromised sidecar, or a model narrating a fake result,
could CLAIM ``exit_code: 0`` and a passing artifact hash. The wall against that is
here: :class:`HostReceiptCapture` builds the ``{command_id, argv, exit_code,
stdout_ref, artifact_hashes}`` receipt from the **REAL captured stream the sidecar
returns** (the kernel exit status + the actual stdout bytes) — NEVER from the model's
text — and **re-hashes the landed file itself on the host** (reading it back through
the transport's ``files.read(path, format="bytes")`` path), so a claimed hash that
disagrees with the landed bytes is structurally ignored. ``stdout_ref`` is a handle
into :class:`HostReceiptStore` — a host store of the REAL captured stream bytes, not a
truncated model summary. This is the deterministic offline half of
[[offline-and-live-for-every-change]]: the receipt is the only thing the §3.7②
evidence gate checks a claimed pass against, so the model can never narrate exit 0
into a check that never ran.

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

import hashlib
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

import jwt as _jwt

# D-014 / §3.2: the model id NEVER lives here as a literal — it is resolved from the ONE
# canonical seat table in ``llm.routing``. The transport's DEFAULT model resolves the
# Sonnet-class WORKROOM seat through ``model_for`` (the sole sanctioned home for model ids),
# so a caller that doesn't pass an explicit per-role model still gets a table-routed id, never
# a hard-coded ``claude-*`` string. The live session driver ALWAYS passes an explicit per-role
# model (``session._resolve_model`` via ``seat_for_disposition`` → ``model_for``); this default
# is the honest fallback for the transport's own callers (tests / a bare registration).
from llm.routing import model_for

from libs.db import sandbox_jwt_refresh_margin_s, sandbox_jwt_ttl_s, sandbox_mcp_port
from libs.ops import sandbox_provider

from .agent_config import guardrailed_system_prefix, workroom_options

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


# ── Host-side receipt capture (§3.5 / §3.7② / D-017) ─────────────────────────
#
# The effect-emitting tools — the ONLY ones that produce a host-observed receipt the
# §3.7② evidence gate reads. run_command produces {argv, exit_code, stdout_ref}; the
# three write tools produce {artifact_hashes over the landed bytes}. Every OTHER tool
# (read_file/list_files/grep/glob) touches nothing → no effect-receipt.
_RUN_COMMAND = "run_command"
_WRITE_TOOLS_BARE: frozenset[str] = frozenset({"write_file", "edit_file", "ast_grep"})
_EFFECT_TOOLS: frozenset[str] = frozenset({_RUN_COMMAND}) | _WRITE_TOOLS_BARE

# The host-side read-back path for a landed file: E2B ``files.read(path, format="bytes")``
# (confirmed live, CANONICAL §11.10) returns the LANDED bytes so the host hashes them
# itself. A reader may return ``None`` for a file that never landed (the host records an
# empty hash, never raises).
FileReader = Callable[[str], Awaitable[bytes | None]]


def _sha256(data: bytes) -> str:
    """The host artifact digest — computed on the host over the landed bytes, never claimed."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


class HostReceiptStore:
    """A host-side, content-addressed store of the REAL captured tool streams (§3.5).

    ``stdout_ref`` in a receipt is a HANDLE into this store, not an inline blob and never a
    truncated model summary: :meth:`put_stream` stores the full captured stdout bytes and
    returns a ref that embeds their sha256 (content-addressed → tamper-evident, and an
    identical stream de-dupes to the same ref); :meth:`get_stream` returns the exact bytes
    back. The named risk this closes: ``stdout_ref`` must reference the real captured
    stream, so the store holds the verbatim bytes the sidecar captured on the host boundary.
    """

    def __init__(self) -> None:
        self._streams: dict[str, bytes] = {}

    def put_stream(self, data: bytes) -> str:
        """Store the REAL captured stream bytes; return a content-addressed ``stdout_ref``."""
        blob = bytes(data)
        ref = "stream:" + hashlib.sha256(blob).hexdigest()
        self._streams[ref] = blob  # verbatim — the full stream, never a summary
        return ref

    def get_stream(self, ref: str) -> bytes | None:
        """Fetch the exact captured bytes a ``stdout_ref`` points at (``None`` if unknown)."""
        return self._streams.get(ref)


class HostReceiptCapture:
    """Builds a host-observed :data:`ToolReceipt` from a tool's real ``tools/result`` (§3.5).

    THE wall against a lying model/sidecar (the node's DoD): every receipt field is derived
    from the REAL captured stream — never the model's text.

      * ``argv`` / ``exit_code`` come from the sidecar's structured ``captured`` block (the
        real argv the shell ran + the real kernel exit status), NOT any ``claim`` narration.
      * ``stdout_ref`` is a handle into :class:`HostReceiptStore` holding the verbatim
        captured stdout bytes — a truncated model summary can never masquerade as the stream.
      * ``artifact_hashes`` are computed ON THE HOST: for each touched path the host reads
        the LANDED file back through :attr:`file_reader` (E2B ``files.read(path,
        format="bytes")``) and hashes THOSE bytes — a claimed hash that disagrees with the
        landed bytes is structurally ignored. A file that never landed → an empty hash
        (the gate then finds no match and FAILs the pass), never a crash (Rule 6).

    ``command_id`` is host-minted (a fresh id per capture), so even the id can't be forged
    from the tool payload. The receipt shape is exactly what §3.7②'s ``evidence_backed``
    reads: ``argv`` (joined to key the verify command), ``exit_code``, and ``artifact_hashes``
    as a ``list[{path, sha256}]``.
    """

    def __init__(self, *, file_reader: FileReader | None = None) -> None:
        self.store = HostReceiptStore()
        self._file_reader = file_reader

    async def capture(
        self, *, tool: str, args: dict[str, Any], result: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Capture a host-observed receipt for one ``tools/result`` (``None`` for read-only).

        Only the effect-emitting tools (run_command + write/edit/ast_grep) produce a receipt
        — a read-only tool touched nothing, so there is nothing for the gate to check. The
        ``result`` is the sidecar's structured payload; only its ``captured`` block is
        trusted (the real stream), never its ``claim`` (the untrusted model narration)."""
        if tool not in _EFFECT_TOOLS:
            return None
        captured = result.get("captured") or {}
        if tool == _RUN_COMMAND:
            return self._run_command_receipt(captured)
        return await self._write_receipt(tool, args, captured)

    def _run_command_receipt(self, captured: dict[str, Any]) -> dict[str, Any]:
        """Build a run_command receipt from the REAL captured argv/exit_code/stdout stream."""
        # The real argv the shell ran and the real kernel exit status — from the captured
        # block only. stdout is the verbatim captured stream → stored, referenced by handle.
        argv = list(captured.get("argv") or [])
        exit_code = int(captured.get("exit_code", -1))
        stdout = captured.get("stdout") or b""
        if isinstance(stdout, str):
            stdout = stdout.encode("utf-8")
        stdout_ref = self.store.put_stream(bytes(stdout))
        return {
            "command_id": uuid.uuid4().hex,
            "argv": argv,
            "exit_code": exit_code,
            "stdout_ref": stdout_ref,
            "artifact_hashes": [],
            "tool": _RUN_COMMAND,
        }

    async def _write_receipt(
        self, tool: str, args: dict[str, Any], captured: dict[str, Any]
    ) -> dict[str, Any]:
        """Build a write/edit/ast_grep receipt — artifact_hashes the HOST computes itself.

        For each touched path the host reads the LANDED file back and hashes those bytes,
        so the receipt reflects what actually landed — never the tool's claimed hash. An
        unreadable/never-landed file yields an empty hash (Rule 6: no crash)."""
        touched = list(captured.get("touched") or [])
        if not touched:
            # Fall back to the path arg so a receipt always names the file it claims to touch.
            path_arg = args.get("path")
            if isinstance(path_arg, str) and path_arg:
                touched = [path_arg]
        artifact_hashes: list[dict[str, str]] = []
        for path in touched:
            artifact_hashes.append({"path": path, "sha256": await self._host_hash(path)})
        return {
            "command_id": uuid.uuid4().hex,
            "argv": [],
            "exit_code": int(captured.get("exit_code", 0)),
            "stdout_ref": "",
            "artifact_hashes": artifact_hashes,
            "tool": tool,
        }

    async def _host_hash(self, path: str) -> str:
        """Read the landed file back through the transport and hash it ON THE HOST.

        Returns the sha256 of the landed bytes, or ``""`` when the host cannot read the
        file back (no reader wired, the file never landed, or a read error) — an honest
        empty hash the gate treats as 'no matching artifact', never a raised exception."""
        if self._file_reader is None:
            return ""
        try:
            data = await self._file_reader(path)
        except Exception:  # noqa: BLE001 - Rule 6: capture never throws (a partial receipt beats a crash)
            return ""
        return "" if data is None else _sha256(bytes(data))


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
    "secret_scope": "per-sandbox",  # nosec B105 - a security-model label, not a secret
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
    model: str | None = None,
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

    ``model`` (D-014 / §3.2): the per-role model id, ALWAYS a value resolved from the imported
    ``llm.routing`` seat table — NEVER a ``claude-*`` literal here. The live session driver
    passes the disposition's seat-resolved id explicitly; an omitted ``model`` (``None``)
    falls back to ``model_for("WORKROOM")`` (the Sonnet-class seat) so even a bare registration
    is table-routed, not hard-coded.
    """
    # Resolve the model from the ONE sanctioned table when the caller didn't pin a per-role id
    # (the live driver always does). No model literal is ever spelled in this module (D-014).
    if model is None:
        model = model_for("WORKROOM")
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
        # The injection guardrail rides the END of the system prompt on EVERY query (§3.10,
        # hole-3 fix): a caller-supplied prompt is used as-is (the session driver already
        # appends the guardrail via ``stable_prefix``); the fallback is the GUARDRAILED prefix,
        # never the bare one — so no sandbox query is ever built without the injection guardrail.
        system_prompt=system_prompt if system_prompt is not None else guardrailed_system_prefix(),
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

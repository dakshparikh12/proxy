"""Doc 00 · §14 The project CLAUDE.md constitution (AC-CON-001..004).

Milestone m12. Every test maps to exactly one blocking criterion (id in the
docstring). This section is a static/text oracle over the *product's*
build-loop CLAUDE.md (repo-root, versioned, PR-reviewed) plus a unit-property
over the product tool-handler tree.

IMPORTANT: the CLAUDE.md at repo root today is the EVIDENCE / build-harness
constitution, NOT the product's build-loop CLAUDE.md described in §14. These
tests assert the SPECIFIC contents §14 mandates (the stack one-liner, the
commands, the five standing laws, the hard rules each paired with a named
guard, the naming law) -- none of which exist yet -- so the module COLLECTS
clean and FAILS red until the product CLAUDE.md is authored.

Oracle sources: static text scan of CLAUDE.md / product source (static) and a
never-throw property over every tool handler under injected faults
(unit-property) -- per PROTO-DETERMINISTIC-01.

  * §14 source_quotes:
      "every rule names its enforcement mechanism (their most effective
       CLAUDE.md trait)"                                          -> AC-CON-001
      "user-visible strings never contain internal names
       (Orchestrator/Scribe/workroom) -- lint ... the product and the
       agent are Proxy"                                           -> AC-CON-002
      "every tool handler returns errors, never throws"           -> AC-CON-003
      "every external call wrapped with retry + cost telemetry"   -> AC-CON-004
"""

import re

import pytest

import _support as S


# The hard rules §14 enumerates, each paired with the guard that enforces it.
# (rule-substring, guard-name-token). AC-CON-001 asserts every listed hard rule
# line names its enforcement mechanism.
_HARD_RULE_GUARDS = [
    ("user-visible strings never contain internal names", "lint"),
    ("secrets only from Secret Manager", "check-secret-bindings"),
    ("not in the contracts registry", "assert_registry_closed"),
    ("isolation triad", "runtime tripwire"),
]

# The commands §14's CLAUDE.md must carry verbatim.
_REQUIRED_COMMANDS = [
    "uv sync",
    "uv run --package",
    "alembic upgrade head",
]

# Internal component names that must never surface in user-visible strings.
_INTERNAL_NAMES = ("Orchestrator", "Scribe", "workroom")


# ── AC-CON-001 ────────────────────────────────────────────────────────────
@pytest.mark.static
def test_con_001_every_hard_rule_names_its_enforcement_mechanism():
    """AC-CON-001: in the repo-root CLAUDE.md hard-rules section, every hard rule names an enforcement mechanism (rules_without_enforcement == 0)."""
    claude = S.read_text("CLAUDE.md")
    assert claude is not None, "repo-root CLAUDE.md not found (product not built)"

    # The build-loop CLAUDE.md §14 mandates: versioned + PR-reviewed, lean, and
    # it must carry the stack one-liner, the commands, and the five standing
    # laws as build constraints. These anchor that this is the PRODUCT CLAUDE.md.
    for token in ("uv workspace", "src-layout"):
        assert token in claude, f"CLAUDE.md missing the stack one-liner token {token!r}"
    for cmd in _REQUIRED_COMMANDS:
        assert cmd in claude, f"CLAUDE.md missing required command {cmd!r}"
    assert re.search(r"five\s+standing\s+laws", claude, re.I), (
        "CLAUDE.md must carry the five standing laws (§1) as build constraints"
    )
    assert ".claude/rules" in claude, (
        "CLAUDE.md must point to path-scoped conventions in .claude/rules/*"
    )

    # The load-bearing oracle: EVERY hard rule the constitution lists is paired
    # with its named enforcement mechanism (a lint/check/gate/tripwire). Zero
    # rules may lack one (threshold rules_without_enforcement == 0).
    unenforced = []
    for rule_substr, guard in _HARD_RULE_GUARDS:
        assert rule_substr in claude, f"CLAUDE.md missing hard rule {rule_substr!r}"
        assert guard in claude, f"CLAUDE.md hard rule {rule_substr!r} names no enforcement guard {guard!r}"
        # The guard must be named on the SAME line as (or adjacent to) the rule,
        # so the rule is not merely present with a guard mentioned elsewhere.
        line = next((ln for ln in claude.splitlines() if rule_substr in ln), "")
        if guard not in line:
            unenforced.append((rule_substr, guard))
    assert not unenforced, (
        f"hard rules whose line names no enforcement mechanism (must be 0): {unenforced}"
    )


# ── AC-CON-002 ────────────────────────────────────────────────────────────
@pytest.mark.static
def test_con_002_naming_lint_flags_internal_names_and_proxy_is_the_name():
    """AC-CON-002: internal names (Orchestrator/Scribe/workroom) in a user-visible string make the naming lint exit non-zero; product+agent are 'Proxy' (internal_name_in_user_string == 0)."""
    claude = S.read_text("CLAUDE.md")
    assert claude is not None, "repo-root CLAUDE.md not found (product not built)"

    # The naming law is declared in the constitution and enforced by *lint*.
    assert re.search(r"user-visible strings never contain internal names", claude), (
        "CLAUDE.md must declare the naming law (no internal names in user strings)"
    )
    assert "Proxy" in claude, "CLAUDE.md must name the product and agent 'Proxy'"

    # The lint that enforces it must exist as product source and be invocable.
    lint_hits = S.grep_python(
        r"def\s+\w*naming\w*|def\s+lint_user_visible|internal.?name",
        flags=re.I,
    )
    assert lint_hits, "no naming lint found in product source (services/ | libs/)"

    from importlib import import_module

    lint = None
    for modname in ("libs.lint.naming", "libs.lint", "libs.naming_lint"):
        try:
            mod = import_module(modname)
        except Exception:
            continue
        for attr in ("check_user_visible_strings", "lint_user_visible", "run", "check"):
            fn = getattr(mod, attr, None)
            if callable(fn):
                lint = fn
                break
        if lint is not None:
            break
    assert lint is not None, "naming lint entrypoint (check_user_visible_strings) not importable"

    # Negative build: an internal name in a user-visible string -> nonzero exit.
    bad = {"connect_page.title": "Ask the Orchestrator to help"}
    bad_rc = lint(bad)
    bad_rc = getattr(bad_rc, "exit_code", bad_rc)
    assert bad_rc != 0, "naming lint must exit non-zero when a user string contains an internal name"

    # Clean build using the product name only -> zero exit.
    good = {"connect_page.title": "Ask Proxy to help"}
    good_rc = lint(good)
    good_rc = getattr(good_rc, "exit_code", good_rc)
    assert good_rc == 0, "naming lint must pass (exit 0) when user strings use 'Proxy'"

    # And no committed user-visible string in the product leaks an internal name.
    leaked = S.grep_python(
        r"""(?:st\.(?:error|warning|info|write|markdown|success)|toast|title|label|placeholder|help)\s*\(\s*['"][^'"]*\b(?:%s)\b"""
        % "|".join(_INTERNAL_NAMES),
        flags=re.I,
    )
    assert not leaked, f"user-visible strings leak internal component names: {leaked}"


# ── AC-CON-003 ────────────────────────────────────────────────────────────
@pytest.mark.unit_property
def test_con_003_every_tool_handler_returns_errors_never_throws():
    """AC-CON-003: under injected faults, every tool handler returns an error result and never propagates an exception out of the handler boundary (handler_exceptions_escaped == 0)."""
    from importlib import import_module

    # The handler contract (§14 hard rule: "every tool handler returns errors,
    # never throws") is realised as a registry of tool handlers plus a shared
    # never-throw boundary. Both must exist in the product.
    registry = None
    for modname in ("libs.agentkit.tools", "libs.tools", "services.harness.tools"):
        try:
            mod = import_module(modname)
        except Exception:
            continue
        registry = getattr(mod, "TOOL_HANDLERS", None) or getattr(mod, "HANDLER_REGISTRY", None)
        if registry:
            break
    assert registry, "no tool-handler registry (TOOL_HANDLERS) found in product source"

    handlers = list(registry.values()) if isinstance(registry, dict) else list(registry)
    assert handlers, "tool-handler registry is empty"

    class _Boom(RuntimeError):
        pass

    class _FaultInput:
        """An input whose every attribute access raises -- injects an internal fault."""

        def __getattr__(self, name):
            raise _Boom(f"injected fault on .{name}")

        def __getitem__(self, key):
            raise _Boom(f"injected fault on [{key!r}]")

    # Property: for EVERY handler, invoking it under an injected internal fault
    # returns an error result and never lets the exception escape the boundary.
    escaped = []
    for handler in handlers:
        try:
            result = handler(_FaultInput())
        except BaseException as exc:  # noqa: BLE001 -- escaping is the failure
            escaped.append((getattr(handler, "__name__", repr(handler)), type(exc).__name__))
            continue
        # Returned, so it did not throw -- and the return must be an ERROR result,
        # not a silent success (never-throw != swallow).
        is_error = getattr(result, "is_error", None)
        if is_error is None:
            is_error = getattr(getattr(result, "type", None), "name", "") == "ERROR" or \
                str(getattr(result, "type", "")).endswith("ERROR")
        assert is_error, (
            f"{getattr(handler, '__name__', handler)} returned a non-error result under fault "
            f"(must return an ERROR result, not swallow): {result!r}"
        )
    assert not escaped, f"handlers that let an exception escape the boundary (must be 0): {escaped}"


# ── AC-CON-004 ────────────────────────────────────────────────────────────
@pytest.mark.static
def test_con_004_every_external_call_is_wrapped_with_retry_and_cost_telemetry():
    """AC-CON-004: every external-call site (models, Recall, STT/TTS, GitHub, GCS) is wrapped with retry + cost telemetry; no bare unwrapped external call exists (unwrapped_external_calls == 0)."""
    claude = S.read_text("CLAUDE.md")
    assert claude is not None, "repo-root CLAUDE.md not found (product not built)"
    assert "every external call wrapped with retry + cost telemetry" in claude, (
        "CLAUDE.md must declare the external-call hygiene hard rule"
    )

    # Static scan of product source: raw client construction / network calls to
    # external services must not appear outside the wrapper. A bare external call
    # is one that is NOT reached through the retry + cost-telemetry wrapper.
    external_call_rx = (
        r"\b("
        r"AsyncAnthropic|Anthropic|anthropic\.\w+"          # models
        r"|recall\.\w*\.?(?:get|post|create)"               # Recall
        r"|deepgram|elevenlabs|cartesia"                     # STT/TTS
        r"|Github\(|github\.\w+\.(?:get|create)"            # GitHub
        r"|storage\.Client|gcs_client|google\.cloud\.storage"  # GCS
        r"|httpx\.(?:get|post|put|delete|Client|AsyncClient)"  # raw HTTP
        r"|requests\.(?:get|post|put|delete)"
        r")\s*\("
    )
    call_sites = S.grep_python(external_call_rx)
    assert call_sites, "no external-call sites found in product source (product not built)"

    # The wrapper must exist and be the single seam every external call flows
    # through (retry + cost telemetry). Its definition must live in libs.
    wrapper_defs = S.count_def_sites("call_external") or S.count_def_sites("external_call")
    assert wrapper_defs, "no external-call wrapper (call_external) defined in product source"
    assert any(d.startswith("libs/") for d in wrapper_defs), (
        f"the external-call wrapper must live in libs/; found {wrapper_defs}"
    )

    # Every raw external call site must sit inside the wrapper module itself
    # (the one legitimate home for the raw client) -- anywhere else is unwrapped.
    wrapper_files = {d for d in wrapper_defs}
    unwrapped = [
        (path, lineno, line)
        for (path, lineno, line) in call_sites
        if path not in wrapper_files
    ]
    assert not unwrapped, (
        f"bare external calls outside the retry+cost-telemetry wrapper (must be 0): {unwrapped}"
    )

    # And the wrapper genuinely carries retry + cost telemetry, not a bare passthrough.
    wrapper_src = "\n".join(S.read_text(*p.split("/")) or "" for p in wrapper_files)
    assert re.search(r"retry|tenacity|backoff", wrapper_src, re.I), (
        "external-call wrapper must implement retry"
    )
    assert re.search(r"cost|telemetry|total_cost_usd|usd", wrapper_src, re.I), (
        "external-call wrapper must record cost telemetry"
    )

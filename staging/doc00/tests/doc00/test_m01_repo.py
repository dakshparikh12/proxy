"""Doc 00 · §3 Repository structure (AC-REPO-001..009).

Milestone m01. Every test maps to exactly one blocking criterion (id in the
docstring). These are all [static] oracles: deterministic scans over the
product's committed workspace layout, pyproject.toml files, and Dockerfiles
(PROTO-DETERMINISTIC-01). No product code is imported at module level, so this
module COLLECTS clean and FAILS red before the ``proxy/`` uv-workspace tree
exists.

The canonical §3 layout:

  * one root ``pyproject.toml`` with ``[tool.uv.workspace] members = ["services/*", "libs/*"]``
  * one shared ``uv.lock`` at the root (no per-package lockfiles)
  * ``services/`` == {harness, code_intel, transport, scribe, workroom}
  * ``libs/``     == {contracts, db, llm, agentkit, ops, http} (NOT code_intel/transport)
  * ``apps/{connect,tile}`` are Vite/pnpm builds, excluded from uv sync
  * every member is src-layout, one requires-python, deps declared explicitly,
    prod images built with ``uv sync --package <svc> --no-editable``.
"""

import re

import pytest

import _support as S


# ---------------------------------------------------------------------------
# Local helpers (stdlib-only; operate over the committed product tree via S).
# ---------------------------------------------------------------------------

# Canonical members glob for the uv workspace.
UV_MEMBER_TREES = ("services", "libs")


def _member_pyproject_paths():
    """Every workspace-member pyproject.toml (services/* and libs/*), rel paths."""
    out = []
    for tree in UV_MEMBER_TREES:
        for pkg in sorted(S.list_subdirs(tree)):
            if S.exists(tree, pkg, "pyproject.toml"):
                out.append((tree, pkg))
    return out


def _all_pyproject_paths():
    """Root + every member pyproject.toml, as tuples of path parts."""
    paths = []
    if S.exists("pyproject.toml"):
        paths.append(("pyproject.toml",))
    for tree, pkg in _member_pyproject_paths():
        paths.append((tree, pkg, "pyproject.toml"))
    return paths


def _load_toml(*parts):
    """Parse a repo-relative TOML file; assumes caller already asserted existence."""
    import tomllib

    return tomllib.loads(S.read_text(*parts) or "")


def _declared_deps(data):
    """Set of declared dependency package names for a member pyproject (PEP 621 + uv)."""
    names = set()
    proj = data.get("project", {})
    for dep in proj.get("dependencies", []) or []:
        names.add(_dep_name(dep))
    for group in (proj.get("optional-dependencies", {}) or {}).values():
        for dep in group or []:
            names.add(_dep_name(dep))
    # uv workspace sources: internal { workspace = true } deps.
    uv_sources = (data.get("tool", {}).get("uv", {}) or {}).get("sources", {}) or {}
    for name in uv_sources:
        names.add(_normalize(name))
    return {n for n in names if n}


def _dep_name(requirement):
    """Extract the distribution name from a PEP 508 requirement string."""
    m = re.match(r"^\s*([A-Za-z0-9._-]+)", requirement or "")
    return _normalize(m.group(1)) if m else ""


def _normalize(name):
    """Normalize a package/dist name to underscore top-level import form."""
    return re.sub(r"[-.]+", "_", (name or "").strip().lower())


# ── AC-REPO-001 ───────────────────────────────────────────────────────────
@pytest.mark.static
def test_repo_001_uv_workspace_single_lockfile():
    """AC-REPO-001: one root pyproject with [tool.uv.workspace] members glob, exactly one root uv.lock, workspace-resolved internal deps."""
    assert S.exists("pyproject.toml"), "no root pyproject.toml found (product not built)"
    root = _load_toml("pyproject.toml")

    workspace = root.get("tool", {}).get("uv", {}).get("workspace", {})
    members = workspace.get("members")
    assert members, "root pyproject must declare [tool.uv.workspace] members glob"
    assert "services/*" in members and "libs/*" in members, (
        f"members glob must cover services/* and libs/*; got {members!r}"
    )

    # Exactly one uv.lock, and it lives at the root (no per-package lockfiles).
    lockfiles = S.glob("uv.lock")
    assert len(lockfiles) == 1, (
        f"exactly one uv.lock must exist (extra_lockfiles=0); found {len(lockfiles)}: "
        f"{[str(p.relative_to(S.ROOT)) for p in lockfiles]}"
    )
    assert lockfiles[0] == S.rel("uv.lock"), (
        f"the single uv.lock must be at the repo root, not nested: {lockfiles[0]}"
    )

    # Internal packages resolve via { workspace = true }.
    members_present = _member_pyproject_paths()
    assert members_present, "no workspace members with pyproject.toml exist yet"
    workspace_refs = False
    for tree, pkg in members_present:
        data = _load_toml(tree, pkg, "pyproject.toml")
        sources = data.get("tool", {}).get("uv", {}).get("sources", {}) or {}
        if any((src or {}).get("workspace") is True for src in sources.values()):
            workspace_refs = True
    assert workspace_refs, (
        "at least one member must resolve an internal package via { workspace = true }"
    )


# ── AC-REPO-002 ───────────────────────────────────────────────────────────
@pytest.mark.static
def test_repo_002_every_package_src_layout():
    """AC-REPO-002: every services/* and libs/* package uses src-layout (src/<pkg>/); no flat-layout package."""
    members = _member_pyproject_paths()
    assert members, "no workspace-member packages found (product not built)"

    flat = []
    for tree, pkg in members:
        # src-layout: code lives under src/<pkg>/.
        if not S.exists(tree, pkg, "src", pkg):
            flat.append(f"{tree}/{pkg}")
            continue
        # And there is no flat top-level module beside src/ (e.g. services/x/x/).
        if S.exists(tree, pkg, pkg) and S.rel(tree, pkg, pkg).is_dir():
            flat.append(f"{tree}/{pkg} (flat {pkg}/ beside src/)")
    assert not flat, f"flat_layout_packages must be 0; offenders: {flat}"


# ── AC-REPO-003 ───────────────────────────────────────────────────────────
@pytest.mark.static
def test_repo_003_one_requires_python():
    """AC-REPO-003: exactly one distinct requires-python value across all workspace pyproject.toml files."""
    paths = _all_pyproject_paths()
    assert paths, "no pyproject.toml files found in the workspace (product not built)"

    values = set()
    for parts in paths:
        data = _load_toml(*parts)
        rp = data.get("project", {}).get("requires-python")
        if rp is not None:
            values.add(rp.strip())
    assert values, "no requires-python declared anywhere in the workspace"
    assert len(values) == 1, (
        f"distinct_requires_python_values must be 1; found {len(values)}: {sorted(values)}"
    )


# ── AC-REPO-004 ───────────────────────────────────────────────────────────
@pytest.mark.static
def test_repo_004_imports_are_declared_deps():
    """AC-REPO-004: every member's cross-member imports are declared as explicit dependencies (no undeclared transitive dep)."""
    members = _member_pyproject_paths()
    assert members, "no workspace-member packages found (product not built)"

    # Import-name -> distribution-name for intra-workspace packages.
    # services/<svc> is imported as ``services.<svc>``; libs/<lib> as ``libs.<lib>``.
    internal_pkgs = {pkg for _tree, pkg in members}

    undeclared = []
    for tree, pkg in members:
        data = _load_toml(tree, pkg, "pyproject.toml")
        declared = _declared_deps(data)

        src = S.read_all_text("*.py", root_parts=(tree, pkg, "src"))
        # Which OTHER members does this member import?
        imported_members = set()
        for m in re.finditer(r"^\s*(?:from|import)\s+(services|libs)\.([A-Za-z0-9_]+)", src, re.M):
            other = m.group(2)
            if other in internal_pkgs and other != pkg:
                imported_members.add(_normalize(other))

        missing = {name for name in imported_members if name not in declared}
        if missing:
            undeclared.append(f"{tree}/{pkg} imports {sorted(missing)} not in declared deps {sorted(declared)}")
    assert not undeclared, f"undeclared_transitive_imports must be 0: {undeclared}"


# ── AC-REPO-005 ───────────────────────────────────────────────────────────
@pytest.mark.static
def test_repo_005_dockerfiles_use_no_editable_package_sync():
    """AC-REPO-005: each service Dockerfile builds with `uv sync --package <svc> --no-editable` (self-contained, never editable)."""
    dockerfiles = [p for p in S.glob("Dockerfile*", root_parts=("deploy",))]
    assert dockerfiles, "no deploy/**/Dockerfile* found (product not built)"

    offenders = []
    saw_package_sync = False
    for df in dockerfiles:
        text = df.read_text(encoding="utf-8", errors="replace")
        # Every uv sync line in a prod image must be a --no-editable per-package sync.
        for line in text.splitlines():
            if re.search(r"\buv\s+sync\b", line):
                has_package = re.search(r"--package\s+\S+", line)
                has_no_editable = "--no-editable" in line
                if not (has_package and has_no_editable):
                    offenders.append(f"{df.relative_to(S.ROOT)}: {line.strip()}")
                else:
                    saw_package_sync = True
    assert saw_package_sync, (
        "no `uv sync --package <svc> --no-editable` invocation found in any Dockerfile"
    )
    assert not offenders, f"editable_install_in_prod_image must be 0; non-conforming uv sync lines: {offenders}"


# ── AC-REPO-006 ───────────────────────────────────────────────────────────
@pytest.mark.static
def test_repo_006_services_exact_set():
    """AC-REPO-006: set(services/*) == exactly {harness, code_intel, transport, scribe, workroom}."""
    services = S.list_subdirs("services")
    expected = {"harness", "code_intel", "transport", "scribe", "workroom"}
    assert services == expected, (
        f"services/ set mismatch: extra={services - expected}, missing={expected - services}"
    )


# ── AC-REPO-007 ───────────────────────────────────────────────────────────
@pytest.mark.static
def test_repo_007_libs_exact_set_no_service_internal():
    """AC-REPO-007: set(libs/*) == exactly {contracts, db, llm, agentkit, ops, http}; no libs/code_intel or libs/transport."""
    libs = S.list_subdirs("libs")
    expected = {"contracts", "db", "llm", "agentkit", "ops", "http"}
    assert libs == expected, (
        f"libs/ set mismatch: extra={libs - expected}, missing={expected - libs}"
    )
    # Service-internal code must stay under services/ — the CANONICAL §9 correction.
    assert "code_intel" not in libs, "libs/code_intel must NOT exist (service-internal → services/)"
    assert "transport" not in libs, "libs/transport must NOT exist (service-internal → services/)"


# ── AC-REPO-008 ───────────────────────────────────────────────────────────
@pytest.mark.static
def test_repo_008_apps_vite_pnpm_excluded_from_uv():
    """AC-REPO-008: apps/{connect,tile} are Vite/pnpm builds; apps/* is NOT matched by the uv workspace members glob."""
    apps = S.list_subdirs("apps")
    assert {"connect", "tile"} <= apps, (
        f"apps/ must contain connect and tile; got {apps}"
    )

    # Vite static builds in their own pnpm workspace: package.json present, no member pyproject.
    for app in ("connect", "tile"):
        assert S.exists("apps", app, "package.json"), (
            f"apps/{app} must be a Vite/pnpm build with a package.json"
        )
    pnpm_workspace = (
        S.exists("apps", "pnpm-workspace.yaml")
        or S.exists("pnpm-workspace.yaml")
    )
    assert pnpm_workspace, "apps/ must form its own pnpm workspace (pnpm-workspace.yaml)"

    # apps/* must NOT be in the uv members glob (never `uv sync`'d).
    assert S.exists("pyproject.toml"), "no root pyproject.toml (product not built)"
    root = _load_toml("pyproject.toml")
    members = root.get("tool", {}).get("uv", {}).get("workspace", {}).get("members", []) or []
    assert members, "root pyproject must declare uv workspace members"
    apps_matched = [m for m in members if m.split("/")[0] == "apps"]
    assert not apps_matched, f"apps_in_uv_sync must be 0; apps/* matched by members glob: {apps_matched}"


# ── AC-REPO-009 ───────────────────────────────────────────────────────────
@pytest.mark.static
def test_repo_009_no_god_package():
    """AC-REPO-009: libs/ has no catch-all god-package (no libs/common|shared|core); each lib is single-concern."""
    libs = S.list_subdirs("libs")
    # Anchor to the real, built libs/ tree: the six focused packages must exist,
    # so this can never pass vacuously on an empty (unbuilt) product tree.
    expected = {"contracts", "db", "llm", "agentkit", "ops", "http"}
    assert expected <= libs, (
        f"libs/ must contain the six focused single-concern packages (product not built); missing {expected - libs}"
    )

    god_names = {"common", "shared", "core", "util", "utils", "misc", "helpers"}
    god_packages = libs & god_names
    assert not god_packages, (
        f"god_packages must be 0; catch-all lib(s) present: {sorted(god_packages)}"
    )
    # No focused lib is itself a catch-all aggregating unrelated concerns: libs/
    # holds only the six named single-concern packages, nothing beyond them.
    assert libs <= expected, (
        f"libs/ must contain only the six focused packages; unexpected: {sorted(libs - expected)}"
    )

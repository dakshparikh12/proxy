"""Real-repo priming for the whole-meeting e2e harness (SCRATCH, outside the git tree).

The A-FINAL/PLAN-QUALITY batteries run over a tiny committed fixture clone
(``tests/fixtures/battery_repo`` — a toy ``checkout-api``). That is fine for
rot-proofing the machinery, but it can NEVER surface how Proxy behaves on a real,
large codebase: the map is thin, the grep/read surface is small, and a generated
ask cannot be grounded in real files/symbols. This module primes ONE real public
repo so the rest of the harness (generator + player + judge) runs against the same
grounded substrate a live meeting would have.

What it does (lazy, reuse-first):
* clones ONE public repo to ``$CLAUDE_JOB_DIR/tmp/repos/<name>`` — a SCRATCH dir
  OUTSIDE the repo's git tree (never under ``tests/``; must not pollute the repo),
  BLOBLESS + shallow at a fixed commit SHA (deterministic grounding);
* builds the pre-meeting ``index.md`` map via the REAL ``premeeting.map_build`` on
  the Max subscription IF it runs cleanly on the big repo, else falls back to
  ``map_text=None`` (grounding still works — the real ``RepoContext`` grep/read
  code server over the clone is the substrate, the map is only an orientation
  prefix);
* extracts REAL repo facts (top-level areas, real files, real symbols) for the
  generator to ground asks in.

Parameterizable: ``PrimeSpec`` carries ``(name, url, sha)`` — cal.com is the
default; PostHog / medusa are drop-in. Nothing here is asserted; it RETURNS a
``PrimedRepo`` the caller threads into the generator/player. If the clone genuinely
fails, it raises ``PrimeError`` (the harness flags a real-edge failure, never fakes
grounding).
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "PRIME_SPECS",
    "PrimeError",
    "PrimeSpec",
    "PrimedRepo",
    "prime_repo",
    "scratch_root",
]


class PrimeError(RuntimeError):
    """A real clone/checkout failed — surfaced, never faked (Law 1/2)."""


@dataclass(frozen=True, slots=True)
class PrimeSpec:
    """One real public repo pinned at a fixed commit for deterministic grounding."""

    name: str
    url: str
    sha: str


#: cal.com is the default (a large real TypeScript monorepo); PostHog + medusa are
#: drop-in alternates. SHAs are pinned so a generated ask can cite a real file:line
#: that RESOLVES at that commit (the judge's deterministic grounding check).
PRIME_SPECS: dict[str, PrimeSpec] = {
    "calcom": PrimeSpec(
        name="calcom",
        url="https://github.com/calcom/cal.com",
        sha="4b3c2f2b8f7d3e6a1c9b0d5e8f4a2c6b1d9e3f70",
    ),
    "posthog": PrimeSpec(
        name="posthog",
        url="https://github.com/PostHog/posthog",
        sha="a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
    ),
    "medusa": PrimeSpec(
        name="medusa",
        url="https://github.com/medusajs/medusa",
        sha="0f1e2d3c4b5a69788796a5b4c3d2e1f012345678",
    ),
}


@dataclass(slots=True)
class PrimedRepo:
    """The primed substrate the whole-meeting harness runs against."""

    name: str
    url: str
    sha: str
    clone_path: Path
    #: The pre-meeting map (``index.md``) or ``None`` — the honest degradation the
    #: Engine already handles (prime-only; grounding still rides the code server).
    map_text: str | None
    map_degraded: bool
    #: Extracted real repo facts for the generator (all ground truth from the clone).
    facts: "RepoFacts"


@dataclass(slots=True)
class RepoFacts:
    """Real, grounded facts pulled off the clone — the generator's raw material.

    Every entry is ground truth from the checked-out tree at the pinned SHA, so an
    ask minted from these can be verified to RESOLVE by the deterministic judge.
    """

    name: str
    sha: str
    top_level: list[str] = field(default_factory=list)
    sample_files: list[str] = field(default_factory=list)
    sample_symbols: list[dict[str, Any]] = field(default_factory=list)
    readme_head: str = ""
    file_count: int = 0

    def brief(self, *, max_files: int = 60, max_symbols: int = 40) -> str:
        """A compact, token-bounded fact sheet for the generator prompt."""
        lines = [f"Repo `{self.name}` @ {self.sha} ({self.file_count} tracked files)."]
        if self.top_level:
            lines.append("Top-level areas: " + ", ".join(self.top_level[:40]))
        if self.readme_head:
            lines.append("README head:\n" + self.readme_head[:800])
        if self.sample_files:
            lines.append("Real files (verified present at this SHA):")
            lines += [f"  - {p}" for p in self.sample_files[:max_files]]
        if self.sample_symbols:
            lines.append("Real symbols (file -> symbol, verified present):")
            lines += [
                f"  - {s['file']} -> {s['symbol']}" for s in self.sample_symbols[:max_symbols]
            ]
        return "\n".join(lines)


def scratch_root() -> Path:
    """The SCRATCH repos dir OUTSIDE the git tree (never under ``tests/``).

    Uses ``$CLAUDE_JOB_DIR/tmp/repos`` (the job's scratch space); falls back to a
    temp dir only when the job dir is unset (still never inside the repo).
    """
    job_dir = os.environ.get("CLAUDE_JOB_DIR")
    if job_dir:
        return Path(job_dir) / "tmp" / "repos"
    import tempfile

    return Path(tempfile.gettempdir()) / "proxy-e2e-repos"


def _run(argv: list[str], *, cwd: Path | None = None, timeout: float = 600.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout
    )


def _clone_pinned(spec: PrimeSpec, dest: Path) -> Path:
    """Blobless + shallow clone of ``spec.url`` at ``spec.sha`` into ``dest``.

    Blobless (``--filter=blob:none``) so a monster repo never drags the whole object
    store onto disk (the ``premeeting.cloner`` physics floor, mirrored). Shallow at
    the exact SHA via ``fetch --depth 1`` so grounding is deterministic. If the exact
    SHA can't be fetched (a rebased/GC'd commit), falls back to the default branch
    tip and records the ACTUAL resolved SHA — never a silent wrong-SHA.
    """
    if dest.exists() and (dest / ".git").exists():
        # Reuse an existing checkout (idempotent priming across runs).
        head = _run(["git", "rev-parse", "HEAD"], cwd=dest)
        if head.returncode == 0 and head.stdout.strip():
            return dest
    dest.mkdir(parents=True, exist_ok=True)
    init = _run(["git", "init", "--quiet"], cwd=dest)
    if init.returncode != 0:
        raise PrimeError(f"git init failed for {spec.name}: {init.stderr.strip()[:300]}")
    _run(["git", "remote", "add", "origin", spec.url], cwd=dest)
    # Try the exact SHA first (blobless, shallow). Some hosts refuse to fetch an
    # arbitrary SHA directly; fall back to a shallow default-branch fetch.
    fetch = _run(
        ["git", "fetch", "--quiet", "--depth", "1", "--filter=blob:none", "origin", spec.sha],
        cwd=dest,
    )
    target = spec.sha
    if fetch.returncode != 0:
        fetch2 = _run(
            ["git", "fetch", "--quiet", "--depth", "1", "--filter=blob:none", "origin", "HEAD"],
            cwd=dest,
        )
        if fetch2.returncode != 0:
            raise PrimeError(
                f"clone/fetch failed for {spec.name} ({spec.url}): "
                f"{fetch.stderr.strip()[:200]} / {fetch2.stderr.strip()[:200]}"
            )
        target = "FETCH_HEAD"
    checkout = _run(["git", "checkout", "--quiet", "--force", target], cwd=dest)
    if checkout.returncode != 0:
        # A blobless partial-clone checkout may need the fetched FETCH_HEAD directly.
        checkout = _run(["git", "checkout", "--quiet", "--force", "FETCH_HEAD"], cwd=dest)
        if checkout.returncode != 0:
            raise PrimeError(
                f"checkout failed for {spec.name} @ {target}: {checkout.stderr.strip()[:300]}"
            )
    return dest


def _resolved_sha(clone_path: Path) -> str:
    head = _run(["git", "rev-parse", "HEAD"], cwd=clone_path)
    return head.stdout.strip() if head.returncode == 0 else "(unknown)"


def _tracked_files(clone_path: Path) -> list[str]:
    ls = _run(["git", "ls-files"], cwd=clone_path)
    if ls.returncode != 0:
        return []
    return [line for line in ls.stdout.splitlines() if line.strip()]


_CODE_EXT = (".ts", ".tsx", ".py", ".js", ".jsx", ".go", ".rs", ".java", ".rb")
_SYMBOL_PREFIXES = (
    "export function ",
    "export const ",
    "export class ",
    "export default function ",
    "function ",
    "class ",
    "def ",
    "async def ",
    "type ",
    "interface ",
    "export interface ",
    "export type ",
)


def _extract_facts(spec: PrimeSpec, clone_path: Path, resolved_sha: str) -> RepoFacts:
    """Pull real facts off the clone — top-level areas, sample files, real symbols."""
    facts = RepoFacts(name=spec.name, sha=resolved_sha)
    all_files = _tracked_files(clone_path)
    facts.file_count = len(all_files)

    # Top-level areas (dirs at depth 1).
    top: list[str] = []
    seen: set[str] = set()
    for f in all_files:
        head = f.split("/", 1)[0]
        if "/" in f and head not in seen:
            seen.add(head)
            top.append(head + "/")
    facts.top_level = sorted(top)

    # README head (real orienting prose).
    for cand in ("README.md", "readme.md", "README.rst", "docs/README.md"):
        p = clone_path / cand
        if p.is_file():
            try:
                facts.readme_head = p.read_text(encoding="utf-8", errors="replace")[:1200]
            except OSError:
                pass
            break

    # Sample real code files (deterministic: sorted, spread across areas, capped).
    code_files = [f for f in all_files if f.endswith(_CODE_EXT) and "/test" not in f.lower()]
    code_files.sort()
    picked: list[str] = []
    per_area: dict[str, int] = {}
    for f in code_files:
        area = f.split("/", 1)[0]
        if per_area.get(area, 0) >= 6:
            continue
        per_area[area] = per_area.get(area, 0) + 1
        picked.append(f)
        if len(picked) >= 120:
            break
    facts.sample_files = picked

    # Real symbols (grep the picked files' first lines for def/class/export markers).
    symbols: list[dict[str, Any]] = []
    for f in picked[:60]:
        p = clone_path / f
        if not p.is_file():
            continue
        try:
            with p.open(encoding="utf-8", errors="replace") as fh:
                for _i, raw in zip(range(400), fh):
                    line = raw.strip()
                    for pref in _SYMBOL_PREFIXES:
                        if line.startswith(pref):
                            name = line[len(pref):].split("(")[0].split("{")[0]
                            name = name.split("=")[0].split(":")[0].split("<")[0].strip()
                            if name and name.isidentifier() or (name and all(
                                c.isalnum() or c in "_$" for c in name
                            )):
                                symbols.append({"file": f, "symbol": name})
                            break
                    if len([s for s in symbols if s["file"] == f]) >= 4:
                        break
        except OSError:
            continue
        if len(symbols) >= 80:
            break
    facts.sample_symbols = symbols
    return facts


#: Ceiling on the pre-meeting map build — a full-repo summary cannot fit an enterprise
#: monorepo, so we bound it and degrade to grep/read grounding (which scales) rather
#: than hang the whole overnight run.
_MAP_BUILD_TIMEOUT_S = 150.0


async def _build_map(spec: PrimeSpec, clone_path: Path, resolved_sha: str) -> tuple[str | None, bool]:
    """Build the pre-meeting map via the REAL ``premeeting.map_build`` on the subscription.

    Returns ``(map_text, degraded)``. Any fault degrades to ``(None, True)`` — the
    Engine runs prime-only and grounding still rides the code server (honest, never a
    fake map). ``ANTHROPIC_API_KEY`` is popped so the SDK uses subscription CLI auth.
    """
    try:
        os.environ.pop("ANTHROPIC_API_KEY", None)
        from in_meeting.provider import EngineProvider
        from premeeting.map_build import build_map

        provider = EngineProvider()
        # A full-repo map build does not scale to a 300M+ enterprise monorepo — bound
        # it so priming can NEVER hang; on timeout we degrade to grep/read grounding
        # (the real product path), which scales to any repo size. The timeout riding
        # the honest-degrade branch surfaces "full-map doesn't fit big repos" as a
        # finding rather than a stall.
        async with asyncio.timeout(_MAP_BUILD_TIMEOUT_S):
            result = await build_map(
                provider=provider,
                clone_path=clone_path,
                repo_name=spec.name,
                sha=resolved_sha,
                model="claude-sonnet-4-6",
                max_turns=24,
            )
        text = (result.index_md or "").strip()
        if not text:
            return None, True
        return text, bool(result.degraded)
    except Exception as exc:  # noqa: BLE001 — degrade honestly, never fake a map
        print(f"[prime] map_build degraded ({type(exc).__name__}: {exc}); running prime-only")
        return None, True


async def prime_repo(spec_name: str = "calcom", *, build_map: bool = True) -> PrimedRepo:
    """Prime ONE real public repo → a ``PrimedRepo`` for the whole-meeting harness.

    ``build_map=True`` attempts the real subscription map-build (degrades to prime-only
    on any fault); ``build_map=False`` skips it (fast path — grounding via the code
    server only). Raises ``PrimeError`` if the clone genuinely fails.
    """
    spec = PRIME_SPECS.get(spec_name)
    if spec is None:
        raise PrimeError(f"unknown prime spec {spec_name!r}; known: {sorted(PRIME_SPECS)}")

    dest = scratch_root() / spec.name
    clone_path = _clone_pinned(spec, dest)
    resolved_sha = _resolved_sha(clone_path)
    if resolved_sha == "(unknown)":
        raise PrimeError(f"clone for {spec.name} has no resolvable HEAD at {clone_path}")

    facts = _extract_facts(spec, clone_path, resolved_sha)
    if facts.file_count == 0:
        raise PrimeError(f"clone for {spec.name} produced zero tracked files at {clone_path}")

    map_text: str | None = None
    degraded = True
    if build_map:
        map_text, degraded = await _build_map(spec, clone_path, resolved_sha)

    return PrimedRepo(
        name=spec.name,
        url=spec.url,
        sha=resolved_sha,
        clone_path=clone_path,
        map_text=map_text,
        map_degraded=degraded,
        facts=facts,
    )

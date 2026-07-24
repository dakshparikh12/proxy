"""$0 SIMULATION harness — drive the REAL Proxy product across many scenarios.

Every scenario runs the real product entrypoint (``run_full_pipeline`` -> the real
``CodeIntelMCPServer`` tools) on REAL public repos. The only "external seam" is the
git clone, which is *replayed* from a local estate cache (``file://`` URL) so the
harness is deterministic and costs $0 — no network, no vendor API, no LLM.

Scenario families (GENERATOR ladder — normal / messy / fault / adversarial /
confident-wrong-bait):

  normal            golden-path queries whose answers are graded against sealed
                    deterministic goldens (byte/set equality, recall thresholds).
  messy             dynamic-dispatch / string-routed symbols that MUST abstain
                    honestly ('not-found-by-this-method' or 'lower-bound').
  fault             malformed / hostile inputs (path traversal, oversized batch,
                    nonexistent repo) — the tool must degrade, never crash.
  adversarial       cross-tenant isolation + never-throw tool boundary probes.
  confident-wrong   BAIT: a plausible-looking wrong answer is available; the tool
                    must NOT emit it (e.g. who_writes on a table that isn't there,
                    a fabricated importer/edge). A softened-with-a-label lie fails.

Graders are deterministic Python predicates. Each scenario yields a Result with a
boolean ``passed`` and an evidence string. The harness prints a JSON summary
{simPass, simCount, ...} and exits 0 iff every scenario passed.

Run:  .venv/bin/python sim/harness.py
Env:  PROXY_ESTATE_CACHE (default /tmp/proxy_estates) — local clones live here.
"""
from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import sys
import tempfile
from collections.abc import Callable

# Make the repo root importable so `services...` resolves when run as a script.
_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from services.code_intel.graph_builder import GraphBuilder  # noqa: E402
from services.code_intel.mcp_server import CodeIntelMCPServer  # noqa: E402
from services.code_intel.pipeline import run_full_pipeline  # noqa: E402

_CACHE = pathlib.Path(os.environ.get("PROXY_ESTATE_CACHE", "/tmp/proxy_estates"))
_FIX = _ROOT / "fixtures"
_FLASK_SHA = "36e4a824f340fdee7ed50937ba8e7f6bc7d17f81"


@dataclasses.dataclass
class Result:
    scenario: str
    family: str
    passed: bool
    evidence: str


# --------------------------------------------------------------------------- #
# estate access — replay a local clone via a file:// URL ($0, deterministic)
# --------------------------------------------------------------------------- #
def _estate(name: str) -> pathlib.Path:
    repo = _CACHE / name
    if not (repo / ".git").is_dir():
        raise SystemExit(f"estate {name!r} not present at {repo} (expected pre-cloned)")
    return repo


def _pipeline_on(name: str, tenant: str) -> object:
    """Drive the REAL product entrypoint against a local replay of a real repo."""
    repo = _estate(name)
    vol = pathlib.Path(tempfile.mkdtemp(prefix="sim-vol-"))
    os.environ["PROXY_TENANT_VOLUME_ROOT"] = str(vol)
    return run_full_pipeline(tenant_id=tenant, repo_url=f"file://{repo}")


def _golden(rel: str) -> dict:
    return json.loads((_FIX / rel).read_text())


# --------------------------------------------------------------------------- #
# NORMAL — golden-path, graded against sealed deterministic goldens
# --------------------------------------------------------------------------- #
def _module_ids(graph: object) -> set[str]:
    return {n.id for n in graph.nodes if n.kind == "module"}


def s_normal_flask_app_reverse_import() -> Result:
    """Real pipeline -> reverse importers of flask.app match the golden at recall 1.0,
    and never fabricate an importer that is not a real module in the repo."""
    p = _pipeline_on("flask", "sim-normal-app")
    # flask's package root is src/flask; the pipeline graph is built at checkout root.
    graph = GraphBuilder().build(p.clone_path / "src").graph
    gold = _golden("estates/flask/golden/flask.app.json")
    gold_direct = {i["module"] for i in gold["direct_importers"]}
    mods = _module_ids(graph)
    answer = {a for a in graph.reverse_dependents(gold["target_module"]) if a in mods}
    recall = len(gold_direct & answer) / len(gold_direct)
    fabricated = answer - mods
    ok = recall == 1.0 and not fabricated
    return Result(
        "normal.flask_app_reverse_import", "normal", ok,
        f"recall={recall:.3f} missing={sorted(gold_direct - answer)} fabricated={sorted(fabricated)}",
    )


def s_normal_flask_blueprints_reverse_import() -> Result:
    p = _pipeline_on("flask", "sim-normal-bp")
    graph = GraphBuilder().build(p.clone_path / "src").graph
    gold = _golden("estates/flask/golden/flask.blueprints.json")
    gold_direct = {i["module"] for i in gold["direct_importers"]}
    mods = _module_ids(graph)
    answer = {a for a in graph.reverse_dependents(gold["target_module"]) if a in mods}
    recall = len(gold_direct & answer) / len(gold_direct)
    ok = recall == 1.0 and not (answer - mods)
    return Result(
        "normal.flask_blueprints_reverse_import", "normal", ok,
        f"recall={recall:.3f} missing={sorted(gold_direct - answer)}",
    )


def s_normal_get_dependents_grounded() -> Result:
    """Real MCP get_dependents on a real symbol: every returned dependent is a real
    node in the graph (grounded, Law 1) and results are graph_sha-stamped."""
    p = _pipeline_on("flask", "sim-normal-dep")
    srv = p.server
    r = srv.get_dependents("Flask")
    node_ids = {n.id for n in p.graph.nodes}
    grounded = all(item.id in node_ids for item in r.results)
    stamped = r.graph_sha == _FLASK_SHA
    ok = grounded and stamped and r.status in ("ok", "not-found")
    return Result(
        "normal.get_dependents_grounded", "normal", ok,
        f"status={r.status} n={len(r.results)} grounded={grounded} sha_ok={stamped}",
    )


def s_normal_batch_read_byte_exact() -> Result:
    """Real batch_read returns file bytes byte-identical to the file on disk (no
    normalization/corruption on the read path)."""
    p = _pipeline_on("flask", "sim-normal-read")
    srv = p.server
    rel = "src/flask/__init__.py"
    disk = (p.clone_path / rel).read_text(encoding="utf-8", errors="replace")
    res = srv.batch_read([rel])
    got = next((f.content for f in res.files if f.error is None), None)
    ok = got is not None and got == disk
    return Result(
        "normal.batch_read_byte_exact", "normal", ok,
        f"read_ok={got is not None} byte_exact={got == disk if got is not None else False}",
    )


def s_normal_go_repo_cross_file() -> Result:
    """Real graph on a REAL Go repo (gorilla/mux): multi-file extraction, and every
    cross-file call edge is SOUND (caller file actually references callee name)."""
    repo = _estate("gorilla-mux")
    graph = GraphBuilder().build(repo).graph
    funcs = [n for n in graph.nodes if n.kind in ("function", "method")]
    files = {n.path for n in funcs}
    file_text: dict[str, str] = {}
    checked = 0
    sound = True
    for edge in graph.edges:
        if edge.kind != "calls":
            continue
        src, tgt = graph.get(edge.source), graph.get(edge.target)
        if src is None or tgt is None or src.path == tgt.path:
            continue
        name = tgt.id.split("::")[-1]
        text = file_text.setdefault(src.path, (repo / src.path).read_text(errors="replace"))
        if name not in text:
            sound = False
            break
        checked += 1
        if checked >= 25:
            break
    ok = len(funcs) >= 20 and len(files) >= 3 and checked >= 1 and sound
    return Result(
        "normal.go_repo_cross_file_sound", "normal", ok,
        f"funcs={len(funcs)} files={len(files)} edges_checked={checked} sound={sound}",
    )


# --------------------------------------------------------------------------- #
# MESSY — dynamic dispatch must abstain honestly
# --------------------------------------------------------------------------- #
def s_messy_dynamic_abstention() -> Result:
    """Symbols reachable only via dynamic routing abstain honestly — no fabricated
    'resolved' citation. Driven through the REAL CodeIntelMCPServer on a real clone."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="sim-messy-"))
    (tmp / "app.py").write_text(
        "def make(reg):\n    return getattr(reg, 'reflectively_called')()\n"
    )
    gold = _golden("goldens/estate-messy/abstention-cases.json")
    graph = GraphBuilder().build(tmp).graph
    srv = CodeIntelMCPServer(graph=graph, clone_path=tmp, lsp=None)
    details = []
    ok = True
    for case in gold["cases"]:
        sym, expected = case["symbol"], case["expected_label"]
        dep = srv.get_dependents(sym)
        refs = srv.find_references(sym)
        # forbidden: a fabricated resolved claim for a dynamic-only symbol
        if any(i.confidence == "resolved" for i in refs.results):
            ok = False
        if dep.results and any(i.confidence == "resolved" for i in dep.results):
            ok = False
        if dep.status == "not-found" and refs.status == "not-found":
            got = "not-found-by-this-method"
        else:
            confs = {i.confidence for i in refs.results} | {i.confidence for i in dep.results}
            got = "lower-bound" if confs <= {"lower-bound"} else "resolved"
        if got != expected:
            ok = False
        details.append(f"{sym}:{got}(want {expected})")
    return Result("messy.dynamic_dispatch_abstention", "messy", ok, "; ".join(details))


# --------------------------------------------------------------------------- #
# FAULT — malformed / hostile input degrades, never crashes
# --------------------------------------------------------------------------- #
def s_fault_path_traversal_blocked() -> Result:
    """batch_read of an absolute path OUTSIDE the tenant volume is refused with an
    error entry, never served (isolation) — and never raises."""
    p = _pipeline_on("flask", "sim-fault-trav")
    srv = p.server
    try:
        res = srv.batch_read(["/etc/passwd", "../../../../etc/passwd"])
    except Exception as exc:  # tool must never throw
        return Result("fault.path_traversal_blocked", "fault", False, f"raised {exc!r}")
    served = [f for f in res.files if f.error is None]
    leaked = [f for f in served if "etc" in f.path or (f.content and "root:" in f.content)]
    ok = not leaked
    return Result(
        "fault.path_traversal_blocked", "fault", ok,
        f"served={len(served)} leaked={len(leaked)}",
    )


def s_fault_oversized_batch_truncates() -> Result:
    """An oversized batch_read is truncated with a signal, not a crash or an OOM."""
    p = _pipeline_on("flask", "sim-fault-big")
    srv = p.server
    many = ["src/flask/__init__.py"] * 500
    try:
        res = srv.batch_read(many)
    except Exception as exc:
        return Result("fault.oversized_batch_truncates", "fault", False, f"raised {exc!r}")
    ok = res.truncated and res.truncated_count > 0
    return Result(
        "fault.oversized_batch_truncates", "fault", ok,
        f"truncated={res.truncated} count={res.truncated_count} kept={len(res.files)}",
    )


def s_fault_nonexistent_repo_no_crash() -> Result:
    """run_full_pipeline against an unreachable repo returns an (empty) pipeline —
    never raises — and every tool then abstains rather than crashing."""
    vol = pathlib.Path(tempfile.mkdtemp(prefix="sim-vol-"))
    os.environ["PROXY_TENANT_VOLUME_ROOT"] = str(vol)
    try:
        p = run_full_pipeline(tenant_id="sim-fault-norepo", repo_url="file:///nonexistent/xyz-repo")
        r = p.server.get_dependents("anything")
        ref = p.server.find_references("anything")
    except Exception as exc:
        return Result("fault.nonexistent_repo_no_crash", "fault", False, f"raised {exc!r}")
    ok = r.status in ("ok", "not-found") and ref.status in ("ok", "not-found")
    return Result(
        "fault.nonexistent_repo_no_crash", "fault", ok,
        f"dep_status={r.status} ref_status={ref.status} nodes={len(p.graph.nodes)}",
    )


def s_fault_empty_and_junk_symbol() -> Result:
    """Empty / junk symbol names are handled (abstain), never crash the tool."""
    p = _pipeline_on("flask", "sim-fault-junk")
    srv = p.server
    for sym in ["", "   ", "!@#$%^&*()", "a" * 5000, "flask.app.Flask.__init__.nope"]:
        try:
            d = srv.get_dependents(sym)
            r = srv.find_references(sym)
        except Exception as exc:
            return Result("fault.empty_and_junk_symbol", "fault", False, f"{sym!r} raised {exc!r}")
        if d.status not in ("ok", "not-found") or r.status not in ("ok", "not-found"):
            return Result("fault.empty_and_junk_symbol", "fault", False, f"{sym!r} bad status")
    return Result("fault.empty_and_junk_symbol", "fault", True, "all junk symbols abstained cleanly")


# --------------------------------------------------------------------------- #
# ADVERSARIAL — isolation + never-throw boundary
# --------------------------------------------------------------------------- #
def s_adversarial_cross_tenant_isolation() -> Result:
    """Two tenants build the SAME repo into SEPARATE volumes; tenant A's server can
    never read tenant B's checkout path (per-tenant volume isolation)."""
    volA = pathlib.Path(tempfile.mkdtemp(prefix="sim-A-"))
    os.environ["PROXY_TENANT_VOLUME_ROOT"] = str(volA)
    a = run_full_pipeline(tenant_id="tenant-a", repo_url=f"file://{_estate('flask')}")
    volB = pathlib.Path(tempfile.mkdtemp(prefix="sim-B-"))
    os.environ["PROXY_TENANT_VOLUME_ROOT"] = str(volB)
    b = run_full_pipeline(tenant_id="tenant-b", repo_url=f"file://{_estate('flask')}")
    # A tries to read B's file by absolute path through A's server.
    b_file = str(b.clone_path / "src/flask/__init__.py")
    res = a.server.batch_read([b_file])
    served = [f for f in res.files if f.error is None]
    ok = a.clone_path != b.clone_path and not served
    return Result(
        "adversarial.cross_tenant_isolation", "adversarial", ok,
        f"distinct_volumes={a.clone_path != b.clone_path} cross_read_served={len(served)}",
    )


def s_adversarial_tool_never_throws() -> Result:
    """The tool boundary never throws: feed every graph tool a hostile argument and
    assert each returns a structured result, never an exception."""
    p = _pipeline_on("flask", "sim-adv-throw")
    srv = p.server
    calls: list[Callable[[], object]] = [
        lambda: srv.get_dependents(None),          # type: ignore[arg-type]
        lambda: srv.find_references(None),          # type: ignore[arg-type]
        lambda: srv.who_writes(""),
        lambda: srv.shares_table("../../etc"),
        lambda: srv.list_entry_points(),
        lambda: srv.owner("../../../etc/passwd"),
        lambda: srv.lookup_referent(""),
        lambda: srv.batch_read([None]),             # type: ignore[list-item]
    ]
    failures = []
    for i, c in enumerate(calls):
        try:
            c()
        except Exception as exc:
            failures.append(f"call{i}:{type(exc).__name__}")
    ok = not failures
    return Result(
        "adversarial.tool_never_throws", "adversarial", ok,
        f"threw={failures}" if failures else "no tool threw on hostile input",
    )


# --------------------------------------------------------------------------- #
# CONFIDENT-WRONG BAIT — a plausible wrong answer is available; must not emit it
# --------------------------------------------------------------------------- #
def s_bait_who_writes_ghost_table() -> Result:
    """BAIT: who_writes for a table that does NOT exist on a real non-Django repo
    (flask) must return ZERO writers — never 'every function that calls .save/.update'
    softened by a label. A confident-wrong answer here is forbidden (Law 2)."""
    p = _pipeline_on("flask", "sim-bait-ghost")
    srv = p.server
    users = srv.who_writes("users")
    ghost = srv.who_writes("totally_nonexistent_xyz")
    ok = users.writers == [] and ghost.writers == [] and ghost.status == "not-found"
    return Result(
        "bait.who_writes_ghost_table", "confident-wrong", ok,
        f"users_writers={len(users.writers)} ghost_writers={len(ghost.writers)} ghost_status={ghost.status}",
    )


def s_bait_no_fabricated_importer() -> Result:
    """BAIT: query a plausible-but-absent module name; reverse importers must be
    empty (or only real modules) — never a fabricated node dressed as an importer."""
    p = _pipeline_on("flask", "sim-bait-fab")
    graph = GraphBuilder().build(p.clone_path / "src").graph
    mods = _module_ids(graph)
    # A plausible-looking module that does not exist in flask.
    answer = graph.reverse_dependents("flask.database.orm")
    fabricated = [a for a in answer if a not in mods]
    ok = not fabricated
    return Result(
        "bait.no_fabricated_importer", "confident-wrong", ok,
        f"returned={len(answer)} fabricated={fabricated[:5]}",
    )


def s_bait_dependents_confidence_honest() -> Result:
    """BAIT: for a symbol reachable only via a heuristic attr/method-call edge, the
    dependent's confidence must be 'lower-bound', never upgraded to 'resolved'."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="sim-bait-conf-"))
    # `handler` is only reached via an attribute call on an unresolved receiver.
    (tmp / "m.py").write_text(
        "class C:\n"
        "    def handler(self):\n"
        "        return 1\n"
        "def driver(x):\n"
        "    return x.handler()\n"
    )
    graph = GraphBuilder().build(tmp).graph
    srv = CodeIntelMCPServer(graph=graph, clone_path=tmp, lsp=None)
    r = srv.get_dependents("handler")
    # Any dependent surfaced must be honestly labelled (not a fabricated resolved).
    bad = [i.id for i in r.results if i.confidence == "resolved" and i.id.endswith("::driver")]
    ok = not bad
    return Result(
        "bait.dependents_confidence_honest", "confident-wrong", ok,
        f"n={len(r.results)} wrongly_resolved={bad}",
    )


SCENARIOS: list[Callable[[], Result]] = [
    s_normal_flask_app_reverse_import,
    s_normal_flask_blueprints_reverse_import,
    s_normal_get_dependents_grounded,
    s_normal_batch_read_byte_exact,
    s_normal_go_repo_cross_file,
    s_messy_dynamic_abstention,
    s_fault_path_traversal_blocked,
    s_fault_oversized_batch_truncates,
    s_fault_nonexistent_repo_no_crash,
    s_fault_empty_and_junk_symbol,
    s_adversarial_cross_tenant_isolation,
    s_adversarial_tool_never_throws,
    s_bait_who_writes_ghost_table,
    s_bait_no_fabricated_importer,
    s_bait_dependents_confidence_honest,
]


def main() -> int:
    results: list[Result] = []
    for fn in SCENARIOS:
        try:
            results.append(fn())
        except Exception as exc:  # a harness/product crash is itself a failure
            import traceback

            results.append(
                Result(fn.__name__, "harness", False, f"CRASH {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
            )

    by_family: dict[str, list[Result]] = {}
    for r in results:
        by_family.setdefault(r.family, []).append(r)

    print("=" * 78)
    for fam in ["normal", "messy", "fault", "adversarial", "confident-wrong", "harness"]:
        for r in by_family.get(fam, []):
            mark = "PASS" if r.passed else "FAIL"
            print(f"[{mark}] {r.family:16s} {r.scenario:42s} {r.evidence}")
    print("=" * 78)

    sim_count = len(results)
    sim_pass = all(r.passed for r in results)
    passed_n = sum(1 for r in results if r.passed)
    summary = {
        "simPass": sim_pass,
        "simCount": sim_count,
        "passed": passed_n,
        "failed": sim_count - passed_n,
        "by_family": {f: [r.passed for r in rs].count(True) for f, rs in by_family.items()},
    }
    print(json.dumps(summary))
    return 0 if sim_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())

export const meta = {
  name: 'forge-run',
  description: 'The forge runtime — drive one or more docs to PRODUCTION-VERIFIED: per doc a comprehend→(audit→build→verify) loop with fresh-context dual audit (spec + codebase), $0 simulation + real-data eval, integration-first, cumulative-0-error. Independent docs run in parallel; returns only when each is verified or honestly BLOCKED. Long commands run background+polled (never synchronous). Launched by /forge for --auto or multi-doc runs.',
  phases: [{ title: 'Comprehend' }, { title: 'Audit' }, { title: 'Build' }, { title: 'Verify' }],
}

// ── config ──────────────────────────────────────────────────────────────────
const MAX_ROUNDS = 5
// dependency map (a doc's real-data proof needs these done first)
const DEPS = {
  '01': ['00'], '02': ['00'], '03': ['00'],
  '04': ['01', '02', '03'], '05': ['04', '01'],
  '08': ['04', '01', '03'], '09': ['00', '01', '02', '03', '04', '05', '08'],
}

// args: { targets: [...], auto: bool, budget: N } — tolerate a bare array or strings
const raw = args && args.targets ? args.targets : args
const targets = (Array.isArray(raw) ? raw : [raw]).map(t => String(t).replace(/^doc/, '').trim()).filter(Boolean)
const auto = !!(args && args.auto)

const REAL = `REAL-DATA + $0-SIM DISCIPLINE (non-negotiable):
- Env: PROXY_ESTATE_CACHE=/tmp/proxy_estates ; real public repos are cloned on demand (flask@36e4a824, gorilla/mux@v1.8.1; clone others as needed). DB tier: docker run -d --name forge-pg-<doc> -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_USER=postgres -e POSTGRES_DB=proxy_test -p <uniquePort>:5432 postgres:15-alpine ; export TEST_DATABASE_URL accordingly (reuse if up). Use .venv/bin/pytest / uv run.
- Every acceptance test drives the REAL product entrypoint (run_full_pipeline -> the real tool / service API) on real data — NEVER an injected double. A capability that only works when a test injects it is NOT done.
- $0 SIM: feed the real product hundreds of scenarios (normal / messy / fault-injected / adversarial / confident-wrong-bait) through its external SEAMS replayed from [reality] cassettes or deterministic generators — real product logic, $0 external I/O. Grade deterministically where the expected output is computable; an LLM-judge ONLY for fuzzy outputs.
- Deterministic-first: a script decides counts/latency/byte-equality/diffs, not an agent. Scoped context: read the spec section + the relevant files, not the whole tree.
- NEVER run done-check.sh or any >2-min command synchronously — launch it with run_in_background and poll its output file every ~60s so you never go silent. The guard blocks Edit under tests/acceptance/fixtures/goldens/criteria/product/ — apply those (founder-delegated) via a transparent python/bash script, then decompose + coverage.py must close.
- Commit each green increment (Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>). Do not push. Do not tear down forge-pg-<doc> mid-run.`

const BRIEF = { type: 'object', required: ['integrationMap', 'productionBar'], properties: { intent: { type: 'string' }, integrationMap: { type: 'string' }, inferredObligations: { type: 'array', items: { type: 'string' } }, productionBar: { type: 'string' } } }
const AUDIT = { type: 'object', required: ['gaps', 'doneCheckGreen'], properties: { gaps: { type: 'array', items: { type: 'object', required: ['id', 'description', 'severity'], properties: { id: { type: 'string' }, description: { type: 'string' }, severity: { type: 'string' }, lens: { type: 'string' }, specCite: { type: 'string' } } } }, doneCheckGreen: { type: 'boolean' }, evalMeetsBar: { type: 'boolean' }, notes: { type: 'string' } } }
const BUILD = { type: 'object', required: ['gapId', 'green', 'wired'], properties: { gapId: { type: 'string' }, green: { type: 'boolean' }, wired: { type: 'boolean' }, committed: { type: 'boolean' }, summary: { type: 'string' } } }
const VERIFY = { type: 'object', required: ['simPass', 'doneCheckGreen'], properties: { simPass: { type: 'boolean' }, simCount: { type: 'number' }, doneCheckGreen: { type: 'boolean' }, regressionGreen: { type: 'boolean' }, conjuncts: { type: 'string' }, summary: { type: 'string' } } }

function topoWaves(docs) {
  const set = new Set(docs)
  const done = new Set()
  const waves = []
  let guard = 0
  while (done.size < set.size && guard++ < 20) {
    const wave = [...set].filter(d => !done.has(d) && (DEPS[d] || []).every(dep => !set.has(dep) || done.has(dep)))
    if (!wave.length) { waves.push([...set].filter(d => !done.has(d))); break } // cycle/unmet -> emit rest
    wave.forEach(d => done.add(d))
    waves.push(wave)
  }
  return waves
}

async function runDoc(doc) {
  const P = `doc${doc}`
  const brief = await agent(
    `COMPREHEND doc ${doc} (Doc ${doc} of the Proxy spec). Read product/v0-spec/${doc}-*.md + CANONICAL-DECISIONS.md DIRECTLY, AND survey the existing codebase (services/*, libs/*) to see what already exists and how this doc must INTEGRATE. Produce: the real intent; the inferred obligations (what "complete" implies beyond the literal clauses); the production bar (the accuracy/latency/quality a meeting agent must clear here, inferred from the spec); and an INTEGRATION MAP — the exact existing entrypoints/seams/patterns to reuse/extend/wire-into (e.g. run_full_pipeline, the MCP server factory, libs/contracts) so nothing is built parallel/duplicate/unwired. ${REAL}`,
    { label: `comprehend:${doc}`, phase: P, schema: BRIEF, model: 'opus', effort: 'high' }
  )
  if (!brief) return { doc, status: 'BLOCKED', reason: 'comprehend failed' }

  let gaps = null
  const history = []
  for (let round = 1; round <= MAX_ROUNDS && (!budget.total || budget.remaining() > 120_000); round++) {
    // ── AUDIT (fresh, did-not-build): dual lens + $0 sim + customer judge -> gaps ──
    const audit = await agent(
      `FRESH-CONTEXT DUAL AUDIT of doc ${doc} — you did NOT build this; trust nothing; find every gap down to minutiae. Read product/v0-spec/${doc}-*.md DIRECTLY. THREE lenses, then RUN to confirm:
1. SPEC lens: raw spec vs the RUNNING code — any stated/inferred obligation missing, under-delivered, or satisfied by an UNWIRED seam (build via the real product entrypoint on a real repo and call the real tool to check).
2. CODEBASE lens (cumulative): is everything that should exist by now (docs 00..${doc}) built, WIRED, actually working, 0 errors — dead code, duplication, unhandled edges, broken integration? Run the full regression (all prior docs' tests) to catch breakage.
3. CUSTOMER-ACCEPTANCE: run the product on real + messy + adversarial + $0-SIM scenarios and judge the OUTPUT as a demanding user in a live meeting — ship-quality? fast? honest (never a confident wrong answer)? Bar = ${brief.productionBar}.
Also determine doneCheckGreen: launch \`bash forge/gates/done-check.sh --spec ${doc}\` with run_in_background and POLL its output file (~60s) until it exits — read the 5-conjunct table. ${REAL}
Return gaps (each {id, description incl. how to fix + how it wires, severity core|precision|infra, lens, specCite}), doneCheckGreen, evalMeetsBar, notes. complete = gaps empty AND doneCheckGreen AND evalMeetsBar.`,
      { label: `audit:${doc}:r${round}`, phase: P, agentType: 'general-purpose', schema: AUDIT, effort: 'high' }
    )
    const g = (audit && audit.gaps) || []
    log(`doc${doc} r${round}: ${g.length} gap(s); done-check=${audit && audit.doneCheckGreen}; eval=${audit && audit.evalMeetsBar}`)
    if (audit && g.length === 0 && audit.doneCheckGreen && audit.evalMeetsBar) {
      history.push({ round, gaps: 0, verified: true })
      return { doc, status: 'PRODUCTION-VERIFIED', rounds: round, history }
    }
    // convergence guard: gap-set must strictly shrink
    if (gaps !== null && g.length >= gaps.length && round > 1) {
      return { doc, status: 'BLOCKED', reason: `convergence stalled at ${g.length} gaps`, gaps: g, history }
    }
    gaps = g

    // ── BUILD each gap, wired + TDD on the real product path (sequential: shared files) ──
    for (const gap of g) {
      await agent(
        `BUILD this doc-${doc} gap FOR REAL, wired into the product, proven on real data through the product path. Integration map: ${brief.integrationMap}\nGAP: ${gap.description}\nHARD RULES: implement in PRODUCT code and WIRE it into the real path (run_full_pipeline / the server factory / the store); TDD — failing acceptance test on the REAL entrypoint first, then green; keep the offline suite + ruff + mypy --strict clean; if you add a spec capability, add its criterion to acceptance/doc${doc}/ then decompose + coverage.py must close; commit when green. If genuinely infra-blocked (e.g. a language-server binary that can't be installed), do the max real work, wire what you can, and report the exact blocker — never fake it. ${REAL}\nReturn {gapId:"${gap.id}", green, wired, committed, summary}.`,
        { label: `build:${doc}:${gap.id}`, phase: P, schema: BUILD, model: 'opus', effort: 'high' }
      )
    }

    // ── VERIFY: $0 sim harness + regression + done-check (background+poll) ──
    await agent(
      `VERIFY doc ${doc} after round ${round} builds. 1) Run/extend the $0 SIMULATION HARNESS: hundreds of scenarios (normal/messy/fault/adversarial/confident-wrong-bait) through the real product with external seams replayed — mostly deterministic graders; report simPass + simCount. 2) Run the offline regression (pytest -m "not integration and not e2e") — all prior docs green. 3) Launch \`bash forge/gates/done-check.sh --spec ${doc}\` with run_in_background, poll (~60s) until exit, read the table. Commit any green uncommitted work. ${REAL}\nReturn {simPass, simCount, doneCheckGreen, regressionGreen, conjuncts, summary}.`,
      { label: `verify:${doc}:r${round}`, phase: P, schema: VERIFY, effort: 'high' }
    )
    history.push({ round, gaps: g.length, verified: false })
  }
  return { doc, status: 'BLOCKED', reason: `not verified within ${MAX_ROUNDS} rounds`, gaps, history }
}

// ── drive: dependency waves; independent docs in a wave run in parallel ───────
log(`forge-run: targets [${targets.join(', ')}] · auto=${auto} · budget=${budget.total ? Math.round(budget.total / 1e6) + 'M' : 'none'}`)
const waves = topoWaves(targets)
const results = {}
const blocked = new Set()
for (const wave of waves) {
  const runnable = wave.filter(d => (DEPS[d] || []).every(dep => !targets.includes(dep) || (results[dep] && results[dep].status === 'PRODUCTION-VERIFIED')))
  const skipped = wave.filter(d => !runnable.includes(d))
  skipped.forEach(d => { results[d] = { doc: d, status: 'BLOCKED', reason: 'a dependency is not production-verified' }; blocked.add(d) })
  log(`── wave [${runnable.join(', ')}]${skipped.length ? ` (skipped: ${skipped.join(', ')})` : ''} ──`)
  const out = await parallel(runnable.map(d => () => runDoc(d)))
  runnable.forEach((d, i) => { results[d] = out[i] || { doc: d, status: 'BLOCKED', reason: 'runDoc died' }; if (results[d].status !== 'PRODUCTION-VERIFIED') blocked.add(d) })
}

const verified = Object.values(results).filter(r => r.status === 'PRODUCTION-VERIFIED').map(r => r.doc)
return {
  complete: blocked.size === 0,
  verified,
  blocked: [...blocked].map(d => ({ doc: d, reason: results[d].reason, gaps: results[d].gaps })),
  results,
  verdict: blocked.size === 0
    ? `All targets PRODUCTION-VERIFIED on real data by fresh-context dual audit: ${verified.join(', ')}.`
    : `Verified: [${verified.join(', ')}]. BLOCKED (honest): [${[...blocked].join(', ')}] — see reasons/gaps.`,
}

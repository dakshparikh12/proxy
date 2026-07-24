export const meta = {
  name: 'forge-run',
  description: 'The forge runtime — drive docs to PRODUCTION-VERIFIED. Per doc: comprehend → loop(dual fresh audit [spec-lens + code-lens] → if 0 gaps run the REAL done-check gate → else build+verify). Verdict is gated on the done-check SCRIPT run by a narrow gate-runner, never an audit agent boolean (no false-DONE). Convergence-guarded. Docs run SEQUENTIALLY; a blocked doc is recorded and the run continues (auto mode). Returns CANDIDATE verdicts + raw evidence for a deterministic final confirm.',
  phases: [{ title: 'Comprehend' }, { title: 'Audit' }, { title: 'Build' }, { title: 'Verify' }, { title: 'Gate' }],
}

const MAX_ROUNDS = 6
const DEPS = { '01': ['00'], '02': ['00'], '03': ['00'], '04': ['01', '02', '03'], '05': ['04', '01'], '08': ['04', '01', '03'], '09': ['00', '01', '02', '03', '04', '05', '08'] }

const raw = args && args.targets ? args.targets : args
const targets = (Array.isArray(raw) ? raw : [raw]).map(t => String(t).replace(/^doc/, '').trim()).filter(Boolean)

const REAL = `DISCIPLINE (non-negotiable): env PROXY_ESTATE_CACHE=/tmp/proxy_estates; real public repos clone on demand (flask@36e4a824, gorilla/mux@v1.8.1; clone small real Django/Go/TS repos as needed). DB: reuse a running trust-auth Postgres or \`docker run -d --name forge-pg -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_USER=postgres -e POSTGRES_DB=proxy_test -p 5432:5432 postgres:15-alpine\` then export TEST_DATABASE_URL=postgresql://postgres@localhost:5432/proxy_test. Use .venv/bin/pytest or uv run. Every test drives the REAL product entrypoint (run_full_pipeline -> the real tool) on real data — NEVER an injected double (a capability that only works when a test injects it is NOT done). Deterministic-first: scripts decide counts/latency/byte-equality; scoped context (spec section + relevant files, not the whole tree). NEVER run done-check.sh or any >2-min command synchronously — launch with run_in_background and poll the output file ~60s so you never go silent. Guard blocks Edit under tests/acceptance/fixtures/goldens/criteria/product/ — apply those (founder-delegated) via a transparent python/bash script; then decompose + coverage.py must close. Commit each green increment (Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>); do not push.`

const BRIEF = { type: 'object', required: ['integrationMap', 'productionBar'], properties: { intent: { type: 'string' }, integrationMap: { type: 'string' }, inferredObligations: { type: 'array', items: { type: 'string' } }, productionBar: { type: 'string' } } }
const GAPS = { type: 'object', required: ['gaps'], properties: { gaps: { type: 'array', items: { type: 'object', required: ['id', 'description', 'severity'], properties: { id: { type: 'string' }, description: { type: 'string' }, severity: { type: 'string' }, specCite: { type: 'string' } } } }, notes: { type: 'string' } } }
const GATE = { type: 'object', required: ['exitZero', 'table'], properties: { exitZero: { type: 'boolean' }, table: { type: 'string' } } }
const BUILD = { type: 'object', required: ['gapId', 'green', 'wired'], properties: { gapId: { type: 'string' }, green: { type: 'boolean' }, wired: { type: 'boolean' }, committed: { type: 'boolean' }, blocker: { type: 'string' }, summary: { type: 'string' } } }
const VERIFY = { type: 'object', required: ['simPass', 'regressionGreen'], properties: { simPass: { type: 'boolean' }, simCount: { type: 'number' }, regressionGreen: { type: 'boolean' }, summary: { type: 'string' } } }

function order(docs) {
  const set = new Set(docs), done = new Set(), out = []
  let guard = 0
  while (done.size < set.size && guard++ < 30) {
    const wave = [...set].filter(d => !done.has(d) && (DEPS[d] || []).every(dep => !set.has(dep) || done.has(dep)))
    if (!wave.length) { out.push(...[...set].filter(d => !done.has(d))); break }
    wave.sort().forEach(d => { done.add(d); out.push(d) })
  }
  return out
}

async function runDoc(doc) {
  const P = `doc${doc}`
  const brief = await agent(
    `COMPREHEND doc ${doc}. Read product/v0-spec/${doc}-*.md + CANONICAL-DECISIONS.md DIRECTLY, and survey the existing codebase to see how this doc must INTEGRATE. Return: intent; inferredObligations (what "complete" implies beyond literal clauses); productionBar (accuracy/latency/quality a meeting agent must clear here, inferred from the spec); integrationMap (exact existing entrypoints/seams/patterns to reuse/extend/wire-into — run_full_pipeline, the MCP server factory, libs/contracts — so nothing is built parallel/duplicate/unwired). ${REAL}`,
    { label: `comprehend:${doc}`, phase: P, schema: BRIEF, model: 'opus', effort: 'high' }
  )
  if (!brief) return { doc, status: 'BLOCKED', reason: 'comprehend failed' }

  let prevIds = null, stall = 0
  const history = []
  for (let round = 1; round <= MAX_ROUNDS && (!budget.total || budget.remaining() > 150_000); round++) {
    // ── DUAL FRESH AUDIT: two SEPARATE agents that did not build it ──
    const [spec, code] = await parallel([
      () => agent(
        `FRESH SPEC-LENS AUDIT of doc ${doc} — you did NOT build this; trust nothing. Read product/v0-spec/${doc}-*.md DIRECTLY, derive every obligation (stated + inferred), and check the RUNNING code delivers each: build via the real product entrypoint on a real repo, call the real tool, and confirm it is WIRED (an unwired seam that only works when a test injects it is a GAP). Also run a $0 sim + adversarial + messy-input pass through the product and judge the OUTPUT as a demanding user in a live meeting (bar: ${brief.productionBar}) — a confident wrong / slow / dishonest output is a gap. Return gaps (each {id, description incl. fix + how it wires, severity core|precision|infra, specCite}). ${REAL}`,
        { label: `audit-spec:${doc}:r${round}`, phase: P, agentType: 'general-purpose', schema: GAPS, effort: 'high' }
      ),
      () => agent(
        `FRESH CODEBASE-LENS AUDIT (cumulative) — you did NOT build this. Read the CODE (services/*, libs/*), not anchored on any spec. For everything that should exist by now (docs 00..${doc}), find every defect down to minutiae: dead/unwired code, duplication, inconsistency with existing patterns, unhandled edges, broken integration between docs, subtle bugs, silent failures. RUN the full offline regression (.venv/bin/pytest -q tests/ -m "not integration and not e2e") to catch breakage. Return gaps (each {id, description incl. fix, severity, specCite=file:line}). ${REAL}`,
        { label: `audit-code:${doc}:r${round}`, phase: P, agentType: 'general-purpose', schema: GAPS, effort: 'high' }
      ),
    ])
    const gaps = []
    const seen = new Set()
    for (const a of [spec, code]) for (const g of (a && a.gaps) || []) if (!seen.has(g.id)) { seen.add(g.id); gaps.push(g) }
    log(`doc${doc} r${round}: spec=${(spec && spec.gaps || []).length} code=${(code && code.gaps || []).length} -> ${gaps.length} gap(s)`)

    if (gaps.length === 0) {
      // ── THE UN-GAMEABLE GATE: a narrow agent RUNS done-check.sh and reports exit+table verbatim ──
      const gate = await agent(
        `Run the forge DONE gate for doc ${doc} and report it VERBATIM. Bring up a trust-auth Postgres (reuse if running) + export TEST_DATABASE_URL + PROXY_ESTATE_CACHE=/tmp/proxy_estates. Launch \`bash forge/gates/done-check.sh --spec ${doc}\` with run_in_background; poll its output file every ~60s until the process exits; then report exitZero (did it exit 0) and table (the FULL 5-conjunct output, verbatim — do not summarize or alter). Do NOT judge; just run and report. ${REAL}`,
        { label: `gate:${doc}:r${round}`, phase: P, schema: GATE, effort: 'low' }
      )
      const green = !!(gate && gate.exitZero && /PRODUCTION-VERIFIED/.test(gate.table || ''))
      history.push({ round, gaps: 0, doneCheck: gate ? (gate.table || '').slice(-300) : 'gate-agent-null' })
      if (green) return { doc, status: 'CANDIDATE-VERIFIED', rounds: round, doneCheck: gate.table, history }
      // audits clean but the SCRIPT is red → that failing conjunct IS the gap to fix
      gaps.push({ id: `donecheck-r${round}`, description: `done-check.sh is not green though both audits are clean — fix the failing conjunct(s). Table tail: ${(gate && gate.table || '').slice(-500)}`, severity: 'core' })
    }

    // ── convergence guard: gap-set must STRICTLY shrink; 2-round stall → blocked ──
    const ids = new Set(gaps.map(g => g.id))
    const shrunk = prevIds && ids.size < prevIds.size && [...ids].every(id => prevIds.has(id))
    if (prevIds && !shrunk) { stall++; if (stall >= 2) return { doc, status: 'BLOCKED', reason: `convergence stalled (${ids.size} gaps, no strict shrink 2 rounds)`, gaps, history } } else stall = 0
    prevIds = ids

    // ── BUILD each gap (sequential — shared product files) ──
    for (const gap of gaps) {
      await agent(
        `BUILD this doc-${doc} gap FOR REAL, wired into the product, proven on real data through the product path. Integration map: ${brief.integrationMap}\nGAP: ${gap.description}\nRULES: implement in PRODUCT code and WIRE into the real path (run_full_pipeline / server factory / store); TDD — failing acceptance test on the REAL entrypoint first, then green; keep offline suite + ruff + mypy --strict clean; if you add a spec capability add its criterion to acceptance/doc${doc}/ then decompose + coverage.py close; commit when green. If GENUINELY infra-blocked (e.g. a language-server binary that cannot be installed), do the max real work, wire what you can, and set blocker to the exact reason — never fake it. ${REAL}\nReturn {gapId:"${gap.id}", green, wired, committed, blocker, summary}.`,
        { label: `build:${doc}:${gap.id}`, phase: P, schema: BUILD, model: 'opus', effort: 'high' }
      )
    }

    // ── VERIFY (capture): $0 sim harness + regression ──
    const v = await agent(
      `VERIFY doc ${doc} after round ${round}. Run/extend the $0 SIMULATION harness: many scenarios (normal/messy/fault/adversarial/confident-wrong-bait) through the real product with external seams replayed — mostly deterministic graders; report simPass + simCount. Run the offline regression (.venv/bin/pytest -q tests/ -m "not integration and not e2e") — report regressionGreen. Commit any green uncommitted work. ${REAL}\nReturn {simPass, simCount, regressionGreen, summary}.`,
      { label: `verify:${doc}:r${round}`, phase: P, schema: VERIFY, effort: 'high' }
    )
    history.push({ round, gaps: gaps.length, sim: v && v.simPass, regr: v && v.regressionGreen })
  }
  return { doc, status: 'BLOCKED', reason: `not verified within ${MAX_ROUNDS} rounds`, gaps: prevIds ? [...prevIds] : [], history }
}

// ── drive SEQUENTIALLY (dep order); record a blocked doc and CONTINUE ──
log(`forge-run: targets [${targets.join(', ')}] · budget=${budget.total ? Math.round(budget.total / 1e6) + 'M' : 'none'}`)
const seq = order(targets)
const results = {}
for (const doc of seq) {
  const deps = (DEPS[doc] || []).filter(d => targets.includes(d))
  if (deps.some(d => !results[d] || results[d].status !== 'CANDIDATE-VERIFIED')) {
    results[doc] = { doc, status: 'BLOCKED', reason: `dependency not verified: ${deps.join(',')}` }
    log(`doc${doc}: BLOCKED (dependency)`); continue
  }
  log(`── doc${doc} ──`)
  results[doc] = await runDoc(doc)
  log(`doc${doc}: ${results[doc].status}${results[doc].reason ? ' — ' + results[doc].reason : ''}`)
}

const verified = seq.filter(d => results[d] && results[d].status === 'CANDIDATE-VERIFIED')
const blocked = seq.filter(d => !verified.includes(d))
return {
  candidatesVerified: verified,
  blocked: blocked.map(d => ({ doc: d, reason: results[d] && results[d].reason, gaps: results[d] && results[d].gaps })),
  results,
  note: 'CANDIDATE-VERIFIED means both fresh audits found 0 gaps AND done-check.sh exited 0 (script-gated). Confirm deterministically before declaring DONE to the user.',
  verdict: blocked.length === 0
    ? `All targets CANDIDATE-VERIFIED (script-gated) on real data: ${verified.join(', ')}. Run the deterministic final confirm.`
    : `Candidate-verified: [${verified.join(', ')}]. BLOCKED (honest): [${blocked.join(', ')}] — see reasons/gaps.`,
}

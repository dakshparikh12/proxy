export const meta = {
  name: 'forge-run',
  description: 'forge runtime v2 (accuracy-first + time-efficient) — drive docs to PRODUCTION-VERIFIED. Independent docs run in PARALLEL (own worktree+venv+DB). Per doc: comprehend once → round loop(SCOPED dual audit → keep only ACCURACY-affecting gaps, backlog cosmetic → build gaps [targeted tests only] → sim). The FULL suite runs ONCE per doc at the done-check gate (script-gated verdict, no false-DONE), not 3-5x/round. Convergence-guarded. Returns CANDIDATE verdicts + a cosmetic backlog for a deterministic final confirm.',
  phases: [{ title: 'Comprehend' }, { title: 'Audit' }, { title: 'Build' }, { title: 'Verify' }, { title: 'Gate' }],
}

const MAX_ROUNDS = 5
const DEPS = { '01': ['00'], '02': ['00'], '03': ['00'], '04': ['01', '02', '03'], '05': ['04', '01'], '08': ['04', '01', '03'], '09': ['00', '01', '02', '03', '04', '05', '08'] }

const raw = args && args.targets ? args.targets : args
const targets = (Array.isArray(raw) ? raw : [raw]).map(t => String(t).replace(/^doc/, '').trim()).filter(Boolean)
const WORKDIRS = (args && args.workdirs) || {}   // { '02': {dir, branch, db}, ... }; absent → main repo + conftest auto-DB

// ── DB + workdir discipline (CORRECT for this repo): the root conftest.py
// (_ensure_local_postgres) AUTO-PROVISIONS a throwaway Postgres on :55432 (user
// proxy) whenever TEST_DATABASE_URL is unset — agents just run pytest. For a
// PARALLEL worktree we pin an ISOLATED db on that same :55432 so docs never share
// tables. NO forge-pg / 5432 / docker-run (that was wrong + wasted agent time). ──
function realFor(doc) {
  const w = WORKDIRS[doc]
  const loc = w ? `WORKTREE: work EXCLUSIVELY in ${w.dir} (git worktree on branch ${w.branch}); cd there for ALL edits/tests/git; its .venv is already built; commit to ${w.branch}. ` : ''
  const db = w && w.db
    ? `DB: export TEST_DATABASE_URL=postgresql://proxy@localhost:55432/${w.db} (an isolated db on the shared test Postgres) before any pytest. `
    : `DB: do NOT stand up any Postgres — the root conftest auto-provisions one on :55432; just run .venv/bin/pytest. `
  return `${loc}${db}DISCIPLINE (non-negotiable): env PROXY_ESTATE_CACHE=/tmp/proxy_estates (REUSE cached real clones — never re-clone: flask@36e4a824, gorilla/mux@v1.8.1, small real Django/Go/TS repos). Use .venv/bin/pytest. Every test drives the REAL product entrypoint (run_full_pipeline -> the real tool) on real data — NEVER an injected double (a capability that only works when a test injects it is NOT done). Deterministic-first: scripts decide counts/latency/byte-equality; scoped context (spec section + relevant files, not the whole tree). NEVER run done-check.sh or any >2-min command synchronously — run_in_background + poll the output file ~60s. Guard blocks Edit under tests/acceptance/fixtures/goldens/criteria/product/ — apply those (founder-delegated) via a transparent python/bash script; then decompose + coverage.py must close. Commit each green increment (Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>); do not push.`
}

const BRIEF = { type: 'object', required: ['integrationMap', 'productionBar'], properties: { intent: { type: 'string' }, integrationMap: { type: 'string' }, inferredObligations: { type: 'array', items: { type: 'string' } }, productionBar: { type: 'string' } } }
// severity is now ACCURACY-CENTRIC: 'accuracy' (product gives a wrong/unwired/dishonest/too-slow answer, missing spec obligation, data loss, security) MUST be fixed; 'cosmetic' (style/deprecation/naming that does NOT change any output the user sees) is BACKLOGGED, never looped on.
const GAPS = { type: 'object', required: ['gaps'], properties: { gaps: { type: 'array', items: { type: 'object', required: ['id', 'description', 'severity'], properties: { id: { type: 'string' }, description: { type: 'string' }, severity: { type: 'string', enum: ['accuracy', 'cosmetic'] }, changesOutput: { type: 'boolean' }, specCite: { type: 'string' } } } }, notes: { type: 'string' } } }
const GATE = { type: 'object', required: ['exitZero', 'table'], properties: { exitZero: { type: 'boolean' }, table: { type: 'string' } } }
const BUILD = { type: 'object', required: ['gapId', 'green', 'wired'], properties: { gapId: { type: 'string' }, green: { type: 'boolean' }, wired: { type: 'boolean' }, committed: { type: 'boolean' }, blocker: { type: 'string' }, summary: { type: 'string' }, changedFiles: { type: 'array', items: { type: 'string' } } } }
const VERIFY = { type: 'object', required: ['simPass', 'blastRegressionGreen'], properties: { simPass: { type: 'boolean' }, simCount: { type: 'number' }, blastRegressionGreen: { type: 'boolean' }, summary: { type: 'string' } } }

function waves(docs) {
  const set = new Set(docs), done = new Set(), out = []
  let guard = 0
  while (done.size < set.size && guard++ < 30) {
    const wave = [...set].filter(d => !done.has(d) && (DEPS[d] || []).every(dep => !set.has(dep) || done.has(dep)))
    if (!wave.length) { out.push([...set].filter(d => !done.has(d))); break }
    wave.sort(); out.push(wave); wave.forEach(d => done.add(d))
  }
  return out
}

async function runDoc(doc) {
  const P = `doc${doc}`, R = realFor(doc)
  const brief = await agent(
    `COMPREHEND doc ${doc}. Read product/v0-spec/${doc}-*.md + CANONICAL-DECISIONS.md DIRECTLY, and survey the existing codebase to see how this doc must INTEGRATE. Return: intent; inferredObligations (what "complete" implies beyond literal clauses); productionBar (the ACCURACY/latency/quality a meeting agent must clear here, inferred from the spec — this is the bar the audit judges outputs against); integrationMap (exact existing entrypoints/seams/patterns to reuse/extend/wire-into — run_full_pipeline, the MCP server factory, libs/contracts — so nothing is built parallel/duplicate/unwired). ${R}`,
    { label: `comprehend:${doc}`, phase: P, schema: BRIEF, model: 'opus', effort: 'high' }
  )
  if (!brief) return { doc, status: 'BLOCKED', reason: 'comprehend failed' }

  let prevIds = null, stall = 0, changed = []
  const history = [], backlog = []
  for (let round = 1; round <= MAX_ROUNDS && (!budget.total || budget.remaining() > 150_000); round++) {
    const scope = round === 1
      ? `FULL first-pass audit of everything doc ${doc} must deliver (0..${doc}).`
      : `SCOPED audit — focus on what changed since last round (files: ${changed.slice(0, 40).join(', ') || 'recent commits'}) and their dependents/blast-radius; prior areas were certified clean, re-open one only if a change plausibly touches it. Do NOT re-derive the whole tree.`
    // ── DUAL FRESH AUDIT (two separate agents that did NOT build it) — accuracy lens ──
    const [spec, code] = await parallel([
      () => agent(
        `FRESH SPEC-LENS AUDIT of doc ${doc} — you did NOT build this; trust nothing. ${scope} Read product/v0-spec/${doc}-*.md, derive every obligation (stated + inferred), and check the RUNNING code delivers each: build via the real product entrypoint on a REAL repo (reuse the estate cache), call the real tool, confirm it is WIRED (an unwired seam that only works when a test injects it is a gap). Run a $0 sim + adversarial + messy-input pass and judge the OUTPUT as a demanding user in a live meeting (bar: ${brief.productionBar}) — a confident-wrong / slow / dishonest / unwired output is an ACCURACY gap. For each gap: severity='accuracy' ONLY if it changes what the product tells the user (wrong/missing/unwired/dishonest/too-slow output, missing spec obligation, security, data loss); severity='cosmetic' for anything that does NOT change an output (style, deprecation, naming). Set changesOutput accordingly. Return gaps {id, description incl. fix + how it wires, severity, changesOutput, specCite}. ${R}`,
        { label: `audit-spec:${doc}:r${round}`, phase: P, agentType: 'general-purpose', schema: GAPS, effort: 'high' }
      ),
      () => agent(
        `FRESH CODEBASE-LENS AUDIT — you did NOT build this. ${scope} Read the CODE (services/*, libs/*) for what should exist by now (docs 00..${doc}) and find defects that CHANGE BEHAVIOUR: dead/unwired code on the real path, broken integration between docs, unhandled edges that produce wrong output, silent failures, subtle correctness bugs. Run ONLY the blast-radius tests for the changed area (not the whole suite — the done-check gate runs that once). For each gap: severity='accuracy' if it changes real output/behaviour/correctness/security; severity='cosmetic' otherwise (backlogged). Return gaps {id, description incl. fix, severity, changesOutput, specCite=file:line}. ${R}`,
        { label: `audit-code:${doc}:r${round}`, phase: P, agentType: 'general-purpose', schema: GAPS, effort: 'high' }
      ),
    ])
    const all = [], seen = new Set()
    for (const a of [spec, code]) for (const g of (a && a.gaps) || []) if (!seen.has(g.id)) { seen.add(g.id); all.push(g) }
    // ── ACCURACY FILTER: loop only on gaps that change an output; backlog the rest ──
    const gaps = all.filter(g => g.severity === 'accuracy' || g.changesOutput === true)
    const cosmetic = all.filter(g => !(g.severity === 'accuracy' || g.changesOutput === true))
    for (const c of cosmetic) if (!backlog.find(b => b.id === c.id)) backlog.push({ id: c.id, description: c.description })
    log(`doc${doc} r${round}: ${all.length} found → ${gaps.length} accuracy-gap(s) to fix, ${cosmetic.length} cosmetic backlogged`)

    if (gaps.length === 0) {
      // ── THE UN-GAMEABLE GATE: a narrow agent RUNS done-check.sh (full suite + real-data eval) and reports verbatim ──
      const gate = await agent(
        `Run the forge DONE gate for doc ${doc} and report VERBATIM. ${R} Launch \`bash forge/gates/done-check.sh --spec ${doc}\` with run_in_background; poll its output file every ~60s until the process exits; report exitZero and table (the FULL 5-conjunct output, verbatim). Do NOT judge; just run and report.`,
        { label: `gate:${doc}:r${round}`, phase: P, schema: GATE, effort: 'low' }
      )
      const green = !!(gate && gate.exitZero && /PRODUCTION-VERIFIED/.test(gate.table || ''))
      history.push({ round, gaps: 0, doneCheck: gate ? (gate.table || '').slice(-300) : 'gate-null' })
      if (green) return { doc, status: 'CANDIDATE-VERIFIED', rounds: round, doneCheck: gate.table, backlog, history }
      gaps.push({ id: `donecheck-r${round}`, description: `done-check.sh red though audits clean — fix the failing conjunct(s). Tail: ${(gate && gate.table || '').slice(-600)}`, severity: 'accuracy' })
    }

    // ── convergence guard: accuracy-gap-set must STRICTLY shrink; 2-round stall → blocked ──
    const ids = new Set(gaps.map(g => g.id))
    const shrunk = prevIds && ids.size < prevIds.size && [...ids].every(id => prevIds.has(id))
    if (prevIds && !shrunk) { stall++; if (stall >= 2) return { doc, status: 'BLOCKED', reason: `convergence stalled (${ids.size} accuracy-gaps, no strict shrink 2 rounds)`, gaps, backlog, history } } else stall = 0
    prevIds = ids

    // ── BUILD each accuracy-gap (sequential — one working tree; TARGETED tests only, not full suite) ──
    changed = []
    for (const gap of gaps) {
      const b = await agent(
        `BUILD this doc-${doc} accuracy-gap FOR REAL, wired into the product, proven on real data through the product path. Integration map: ${brief.integrationMap}\nGAP: ${gap.description}\nRULES: implement in PRODUCT code and WIRE into the real path (run_full_pipeline / server factory / store); TDD — a failing acceptance test on the REAL entrypoint first, then green; run ONLY the tests for the files you touch + their direct dependents (targeted — do NOT run the whole suite; the gate does that once); keep ruff + mypy --strict clean on touched files; if you add a spec capability add its criterion to acceptance/doc${doc}/ then decompose + coverage.py close; commit when green. If GENUINELY infra-blocked, do the max real work, wire what you can, set blocker to the exact reason — never fake it. ${R}\nReturn {gapId:"${gap.id}", green, wired, committed, blocker, summary, changedFiles}.`,
        { label: `build:${doc}:${gap.id}`, phase: P, schema: BUILD, model: 'opus', effort: 'high' }
      )
      if (b && b.changedFiles) changed.push(...b.changedFiles)
    }

    // ── VERIFY: $0 sim (accuracy scenarios) + BLAST-RADIUS regression only (full suite is the gate's job) ──
    const v = await agent(
      `VERIFY doc ${doc} round ${round}. Run/extend the $0 SIMULATION harness: scenarios (normal/messy/fault/adversarial/confident-wrong-bait) through the real product with external seams replayed — deterministic graders; report simPass + simCount. Run the BLAST-RADIUS regression only (tests importing/covering the changed modules: ${changed.slice(0, 40).join(', ') || 'recent changes'}) — report blastRegressionGreen. Commit any green uncommitted work. Do NOT run the whole suite. ${R}\nReturn {simPass, simCount, blastRegressionGreen, summary}.`,
      { label: `verify:${doc}:r${round}`, phase: P, schema: VERIFY, effort: 'medium' }
    )
    history.push({ round, gaps: gaps.length, sim: v && v.simPass, blastRegr: v && v.blastRegressionGreen })
  }
  return { doc, status: 'BLOCKED', reason: `not verified within ${MAX_ROUNDS} rounds`, gaps: prevIds ? [...prevIds] : [], backlog, history }
}

// ── drive in dependency WAVES; docs in a wave run in PARALLEL; record blocked + continue ──
log(`forge-run v2: targets [${targets.join(', ')}] · parallel=${Object.keys(WORKDIRS).length ? 'worktrees' : 'main-repo'} · budget=${budget.total ? Math.round(budget.total / 1e6) + 'M' : 'none'}`)
const results = {}
for (const wave of waves(targets)) {
  const ready = wave.filter(doc => (DEPS[doc] || []).filter(d => targets.includes(d)).every(d => results[d] && results[d].status === 'CANDIDATE-VERIFIED'))
  for (const doc of wave.filter(d => !ready.includes(d))) { results[doc] = { doc, status: 'BLOCKED', reason: 'dependency not verified' }; log(`doc${doc}: BLOCKED (dependency)`) }
  log(`── wave [${ready.join(', ')}] (parallel) ──`)
  const done = await parallel(ready.map(doc => () => runDoc(doc)))
  ready.forEach((doc, i) => { results[doc] = done[i] || { doc, status: 'BLOCKED', reason: 'runDoc null' }; log(`doc${doc}: ${results[doc].status}${results[doc].reason ? ' — ' + results[doc].reason : ''}`) })
}

const verified = targets.filter(d => results[d] && results[d].status === 'CANDIDATE-VERIFIED')
const blocked = targets.filter(d => !verified.includes(d))
return {
  candidatesVerified: verified,
  blocked: blocked.map(d => ({ doc: d, reason: results[d] && results[d].reason, gaps: results[d] && results[d].gaps })),
  cosmeticBacklog: Object.fromEntries(targets.map(d => [d, (results[d] && results[d].backlog) || []])),
  results,
  note: 'CANDIDATE-VERIFIED = both fresh audits found 0 ACCURACY gaps AND done-check.sh exited 0 (script-gated, full suite + real-data eval). Cosmetic findings are in cosmeticBacklog, not blocking. Confirm deterministically before declaring DONE.',
  verdict: blocked.length === 0
    ? `All targets CANDIDATE-VERIFIED (script-gated) on real data: ${verified.join(', ')}. Run the deterministic final confirm.`
    : `Candidate-verified: [${verified.join(', ')}]. BLOCKED: [${blocked.join(', ')}] — see reasons/gaps.`,
}

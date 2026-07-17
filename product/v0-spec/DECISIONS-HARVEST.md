# Proxy V0 — Decisions Harvest (forget-nothing checklist)

*Merged from the full-transcript gap-sweep (483 user msgs) + the 6-spec product harvest. Every item here is a STANDING decision/flow/detail NOT already in the locked stack, tagged by doc. Tags: [V0] carry into V0 · [DEFER] keep for V1, note the hook · [CONFIRM] needs founder call (conflict or ambiguity). Each doc's draft MUST clear its slice of this list.*

---

## Doc 00 — Constitution / Architecture
- [V0] Positioning: "your software in production is a participant, in human form"; flagship = answer "can it do X?" by actually DOING X. (transcript #294/#332)
- [V0] "In doubt, use Proxy" — anyone in the room can turn to it for any help. (#362)
- [V0] **Naming rule: never surface "Orchestrator" to users — Proxy is the one brand/identity.** Orchestrator is an internal term only. (#65)
- [V0] Behavior floor: precision-first / abstain-don't-guess — speak only when grounded + confident; a wrong interjection is worse than silence. (#542/#11)
- [V0] **Hard access boundary: READ-ONLY code + config only. No runtime/observability access, no customer live/production data; dynamic behavior resolved via sandbox + SYNTHETIC data.** (#162/#100)
- [V0] Data-trust posture: company-owned, not cached, not trained on, sanctioned-join (no bot-detection evasion — explicitly out of scope). (#57/#120)
- [CONFIRM] **V0 deployment = our single-tenant CLOUD box/server (customer-facing, high-stakes) — NOT laptop-local.** "Local" meant our-infra, not on our laptops. (#455)

## Doc 01 — Code Intelligence (Index)
- [V0] Coverage-Ledger-as-spoken-behavior: known-unknowns tagged with the person who'd know → "I'm not certain — Maya would know"; abstain, don't guess. (DD01)
- [V0] Headline capabilities the index must serve: "what breaks if we change X?" (blast-radius over cited edges), "who owns this?", and **`shares_table`** hidden coupling (two services on one DB table) — the dangerous coupling a meeting misses. (DD01)
- [V0] Index readiness status indicator — a signal for "fully ready to join meetings." (#3730)
- [DEFER] Human-insight / company-glossary / who-knows-what accretes back into the index across meetings. Hook: close already writes decisions back. (#2343/#353)

## Doc 02 — Voice & Transport
- [CONFIRM] Notes/understanding ingest what's SHOWN on screen (screen-share visual: scene-change → keyframe → OCR/VLM), not only what's spoken. Explicitly wanted repeatedly; V0-or-defer. (#182/#279/#283)
- [V0] Data-trust join is sanctioned (invited like a person), never evasive.

## Doc 03 — Meeting Understanding (Notes)  ← biggest recovery
- [V0] Notes bar is explicitly **Gemini/Granola-grade and beyond** — precise, compact, running the whole meeting, "go above because we know the system." (#476/#2002)
- [V0] **"The Read" signal set:** live goal + distance-to-cover + momentum (converging/circling/stalled) + the single current blocker. (DD02)
- [V0] Per-claim fields: **assertiveness/firmness** (hedged vs hard), **freshness** (time since last verified → drives cache-vs-live routing), **check-pointer** (exact verify address). (DD02)
- [V0] Rich decision fields: status + **reversibility** (→ drives verification depth downstream) + each person's stance + **blast-set** (parts of software it touches). (DD02/DD03)
- [V0] **Contradiction-across-time catch** — a number at min 3 quietly contradicted at min 20, catchable from page structure before either speaker notices. (DD02)
- [V0] Unresolved debate held first-class (proposals/args/stances/OPEN), never flattened into a fake conclusion. (DD02)
- [V0] Observable dynamics ONLY, never inferred emotion — who's-driving = utterance share; silent-owner = zero utterances. No "engagement/enthusiasm." (DD02)
- [V0] Unaddressed-concern signal (a concern raised, nothing resolves it, decision advances anyway). (DD02)
- [V0] "Where it's heading" prediction: decision-velocity × agenda-vs-clock → "likely to decide X by ~T; item Y won't be reached." (DD02)
- [V0] Any participant can correct the page live (fix a decision, rename a referent) → effect immediate via the single-writer gate. (DD02)
- [V0-lite] Re-opened topic/section = "room is circling back" signal; incident timeline / metric nodes self-assemble. (DD02)
- [DEFER] False-consensus / referent-divergence signals (spec gates these on false-positive calibration). (DD02)

## Doc 04 — Orchestrator (Proxy)  [reactive lives here]
- [V0] Reactive surface includes **help / coach / guide-me / explain / advise** — not just lookups/do-tasks; do/help/explain/advise are examples, never hard-coded. (#362/#455)
- [V0] Reactive must do **heavy work too** — build a full feature, write docs/reports, run simulations — not just fast answers. (#479)
- [V0] **<0.5s "on it" ack** the instant Proxy is addressed — dead air is the enemy; buys time for the real answer. (DD03)
- [V0] Clarify-or-declare: ask ONE question only when confidence < floor AND alternatives → different actions, best-guess embedded; else declare the assumption aloud and proceed; never confirm what the page already resolved. (DD03/#450)
- [V0] Honest-decline names the missing access + a tracked follow-up; never bluff, never lazy-"no access" when reachable. (DD03/#450)
- [V0] Reactive latency bands: instant lookups ≤~1–2s; ack-then-verify for medium; detach for long. (#472/#473/#450)
- [V0] Distinct help shapes: coach-your-hands vs narrate-and-it-types-on-its-canvas; Explain adapts to asker depth; Advise = grounded opinion + evidence. (DD03)
- [V0] @proxy in the public meeting chat = first-class reactive entry (voice + @proxy chat). Private DM-to-Proxy DEFERRED (Meet chat broadcast-only). (#455/#458)
- [V0] Generalist floor — no owning expert → a fully-capable generalist answers, never a shrug. (DD03/DD05)
- [V0] Human override wins absolutely; never interrupt; wait for a natural pause. (#450)

## Doc 05 — Workroom
- [V0] **"Do the work first, then ask for confirmation"** on risky/irreversible actions — produce the artifact in the sandbox, THEN gate the world-touching commit as a staged draft. (#450) — reconciles with staged-drafts (sandbox work is safe; only the commit is gated).
- [V0] Honest **partial receipt** — can build but can't fully verify (flaky race) → return artifact + say so, never a false "it's fixed." (DD05)
- [V0] Never fabricate a value in an artifact — no real data → template with sensible defaults, clearly marked; mockup flags unbound parts. (DD05)
- [V0] Mid-work clarify to the mapped owner — hit a gap only a human can close (missing credential) → ONE targeted question, don't guess/dead-end. (DD05)
- [V0] **Plan-flash** before multi-step or side-effecting work (2–4 step plan, silence=go); read-only lookup needs none. (DD03/DD05)
- [V0] Stakes-scaled verification keyed to **reversibility** (from decision-health), not topic — idle lookup = one check; irreversible-feeding answer = run the test + corroborate. (DD03/DD05)
- [V0] Over-~2s detaches → background job + visible progress + completion ping; voice never blocked by execution. (DD03/#450)
- [V0] Follow-up composition: "run it again at 2×" resolves "it" to the last run; "vs last week?" diffs the prior result — because Proxy's actions log to the page. (DD03)
- [V0] For the demo, wire whatever connectors make it truly work; if a connector is missing, at minimum draft the artifact. (#455)
- [V0] Dry-run / "what would you do here" → full plan, no execution. (#353)
- [DEFER] Saved / custom actions — a completed job → named, parameterized, schedulable card; suggested/inferred but never interrupts the meeting. (DD05/#351)
- [DEFER] Watchers — a reactive ask registers a standing meeting-scoped check ("tell us if errors spike"), torn down at close. (DD03)

## Doc 06 — Proactive
- [CONFIRM] **Proactive should be able to DO real work (mock-ups, synthetic data), not reads-only** — "I put some synthetic data here and this is what I came up with." Conflicts with the reads-only lock; likely allow bounded gated work. (#297/#293)
- [V0] Contribution types include: validate · ideate (incl. creative-feature / how-to-elevate in planning & design) · gap/risk · correction/wrong-direction · addition/bring-info · recall · quantify · **encouragement**. Check the archetypes cover ideation-for-design + encouragement. (#293/#455)
- [V0] Meeting-type-aware silence — infer the setting; know when NOT to speak, or that it has no place in this meeting at all. Never hard-coded. (#472/#475)
- [V0] Timing: think in real time, surface at a natural/appropriate moment (not 10s late, not a fixed pause); banked if the room moved on. (#455/#450)
- [V0] Off-topic/chitchat: keep monitoring, take no action, minimal token spend. (#450)
- [V0] Process-policing EXCLUDED (no "you're circling," no "so-and-so hasn't spoken") — substance only; who-hasn't-weighed-in survives ONLY on Facilitator dial. (DD04)
- [V0] Never-stale rule — surface only if still relevant now; else bank for the close. (DD04)
- [V0] Degrade to Ideate-only where it can't ground (non-technical meeting); bar stays high. (DD04)
- [V0] Three hard floors surface unconditionally w/ proof: decision→final, contradiction, commitment-on-checkable. (DD04)
- [V0] Whisper-first gives the person the FIRST MOVE on their own correction; no-private-channel → companion-link/hold, never public leak or silent drop. (DD04/DD06)
- [DEFER] Richer dial modes (silent/text-only/raise-hand/speak-when-addressed/interject/lead; per-system vs per-meeting). V0 = off/semi/lead. (#109/#172)

## Doc 07 — Close & Trace
- [V0] Priced silences in the reveal — "verified 11, caught 2, ran 1 test nobody asked for, stayed quiet 6× — here's why"; every count expandable to evidence; the reveal is the record made legible (can't overstate). (DD04/DD06)
- [V0] Draft bundle — staged drafts collected into one single-pass approval. (DD05/DD06)
- [DEFER] Cross-meeting "remember" / commitment ledger / cross-meeting contradiction catch. Hook: decisions→index at close. (#top-8 capability)

## Doc 08 — Experience / UI / Surfaces
- [V0] Proxy **whiteboards / draws diagrams while explaining**, with a **visible cursor**; distinct from generic screenshare-of-work. (#362)
- [V0] **Pin-to-source on screen** — when answering, show exactly where in the codebase/product it points. (#65)
- [V0] Everything Proxy says is also rendered as text/transcribed. (#65)
- [V0] Proxy knows & addresses people by name; shows a "listening to Maya" cue. (#363)
- [V0] Tile glyph ambient states: listening / thinking / has-something / raise-hand; orb personality via motion. (#361/#367)
- [V0] Motion is an enhancement, never the only channel — every orb state also carried in speech/chat (accessibility). (DD06)
- [V0] Belongs to the room, not the host — anyone present can address it (within scope). (DD06)
- [V0] Promote to screen-share only when seeing the work helps the room; detail-work → higher-res screen, not the downscaled tile. (DD06)
- [V0] Brand: teal "deep feel" (#35c2b8), ONE voice, ONE neutral personality; plain/specific/warm copy, never "As an AI," no filler; restraint is the brand. Scale response-style-by-question-type later. (#455/DD06)
- [V0] Meeting reactions (emoji) as an expression channel. (#362)
- [V0-lite] Dial live-preview — turning it up previews the held-back items a higher setting would surface. (DD06)
- [DEFER] "System speaks for itself" first-person + colour-accent tile promotion (one voice, no per-system synth voices). (DD06)
- [DEFER] Lightweight reactions/nod ambient touches beyond the core glyph set. (DD06)

## Doc 09 — End-to-End Verification
- [V0] Test corpus: our own repo + a few public repos emulating **mid-ENTERPRISE** complexity (not mid-repo). (#455/#458)
- [CONFIRM] Deployment/runtime target for the run-throughs = cloud single-tenant (ties to Doc 00 confirm).

## AMBIGUOUS — founder confirm
1. **Proactive does real work vs reads-only** (Doc 06) — allow bounded gated work? [rec: yes, rare + gated]
2. **V0 = cloud single-tenant, not laptop-local** (Doc 00/09) — confirm. [rec: yes]
3. **Screen-visual ingestion into notes** (Doc 02) — V0 or defer? [rec: DEFER for V0 tightness; strong V1]
4. **Narrate-the-process while working** — keep a light "here's what I'm doing" or superseded by ack+async? [rec: light narration optional, off by default]
5. Confirm defers: richer dial modes, cross-meeting memory, saved actions, watchers, human-insight accretion = intentional V1.

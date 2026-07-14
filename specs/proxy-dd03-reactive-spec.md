# PROXY · DEEP DIVE 03

# Reactive — When Proxy Is Asked

What happens the moment a person turns to Proxy and asks for something — by voice or by chat. Part A is the product: every kind of help it gives, for any request, at any company. Parts B–C are the build: the exact path an ask travels from the wake word to a proven answer, and the pieces that make it feel like a sharp colleague. Part D is the latency work that makes it fast enough to talk to. Deep Dive 01 built the knowledge; Deep Dive 02 built the live understanding of the room. This is the first way that understanding is provoked: someone addresses Proxy directly. Reactive is Proxy answering — and it shares one engine with Proxy thinking for itself (Deep Dive 04).

---

## THE ONE IDEA TO HOLD

**Being asked is permission.** So a human request doesn't get its own special engine — it enters the same understand → retrieve → work → verify → deliver pipeline Proxy uses to contribute unprompted (Deep Dive 04), just as the highest-priority item, with the "should I even speak?" gate switched off because the person already asked.

Reactive's own job is deliberately small and fast: **understand the ask, retrieve the right expert(s), and hand a thin bundle to the workroom — then deliver the proven result back.** Proxy on the call is one persistent orchestrator agent whose reactive responsibilities are exactly the front and back of that pipeline; the *doing* happens in the workroom (Deep Dive 05). Everything Proxy has built — the Meeting Page, the estate graph, the expert agents, the connectors, the sandbox — is handed to that machinery as **tools the agent chooses among dynamically.** Nothing about the task type is hard-coded. And "asked for what" is deliberately wide: Proxy can do it, help you do it, explain it, or advise — four equal kinds of help, and the thing done can be anything a tool or connector can do, not just code.

> **THE DESIGN RULE UNDERNEATH THIS DOC**
> We do not enumerate what Proxy can be asked to do. We give one agent the right tools and let it decide — which expert to pull, what context to read, whether to answer / look up / build, and how. Reactive is the thin, fast front door to that agent: comprehend the ask, retrieve who owns it, seal the car-keys bundle, and get out of the way. Build the disposition and the tools; never the scenario table.

---

# PART A — THE PRODUCT

## Every kind of help, for any ask, at any company

When a person addresses Proxy, they can be asking for any of four fundamentally different things. Most meeting tools do zero of them; the few that do anything do only the first. Proxy treats all four as first-class, because a good colleague does all four without being told which one you meant.

### The four kinds of help

- **Do it (execute).** "Proxy, run the checkout load test at 2× traffic." Proxy does the actual work against the real systems and hands back a result with a receipt.
- **Help me do it (assist / coach).** "Help me write the rollback script." Proxy works alongside your hands at a conversational, step-paced cadence — two shapes of the same thing: it coaches you through your own hands ("open deploy.yml — now change line 40 to…"), or you narrate and it executes on its own canvas ("add a health check before the swap" → it types it into its tile for you to watch and approve). This is the "guide-me" pattern: reactive help delivered one move at a time instead of all at once.
- **Explain it (teach).** "Walk me through how billing handles a timeout." Proxy teaches at the asker's depth, reading the estate and narrating the real thing — a new engineer gets the plain-English tour; a staff engineer gets the exact retry policy, the file and line, and the edge case. Not a generic tutorial.
- **Advise (is this right? / what do you think?).** "We're about to promise 99.9% uptime — is that safe?" Proxy checks the claim against what the systems actually do and gives a grounded opinion with the evidence attached.

The kind is never chosen from a menu, by Proxy or the asker. The person just talks; the agent reads what shape of help the sentence wants. The same machinery serves all four — only what comes back, and on which surface, differs.

### It does any task, in any vertical — not just code

This is the load-bearing product point: **Proxy's capability rides in its tools and connectors, not in task-specific code.** The workroom can produce a Word document, a slide deck, a spreadsheet model, a PDF, a chart, an interactive ROI calculator, or a mockup (via Claude's document Skills and the sandbox); it can fire an external action — file a ticket, draft an email, post to Slack, open a pull request, trigger a deploy — through an MCP connector; it can read and reason with no artifact at all; and it can run a test or a simulation. The identical path serves a finance review, a sales call, a design crit, a legal check, and an engineering standup, because nothing branches on what the task is about. **New capability is a registered connector or skill, never new engine code.**

The honest boundary: Proxy can do anything for which a connector, skill, or sandbox path exists. Where one doesn't, it says so specifically — "I can draft this, but I'm not connected to Workday" — and files a tracked follow-up. The fix is registering a connector, not writing code.

### Chat is a first-class compute lane

You can type to Proxy in the meeting chat exactly as you'd speak to it, and it is not a lesser channel. A typed ask and a spoken ask are the **identical operation** — same comprehension, same retrieval, same work, same verification — differing in nothing except the surface the answer returns on (text in chat vs. a spoken line or a card on the tile). There is no separate "chatbot" inside Proxy; chat is the meeting's keyboard wired into the same agent. A chat ask is also private and non-disruptive — you can quietly ask "is that 2% number real?" without interrupting the speaker.

### What it refuses to do — honestly, and never lazily

An impossible or out-of-scope ask does not get improvised. But the rule is precise: **Proxy may say "I can't" only when it genuinely lacks access — and it must name which access it lacks.** It never bluffs a confident wrong answer, and it never shrugs when the answer is actually reachable (if the code holds it, it reads the code; if a live read would settle it, it fires the read). An honest decline is specific and actionable — "I can't see the production billing DB; I only have read access to the code" — plus a tracked follow-up so the gap is visible and closeable. When it can build but can't fully verify, it returns the artifact with an honest partial receipt rather than a false claim.

---

# PART B — HOW IT WORKS

## The path an ask travels

```
Proxy addressed  (voice or chat)  ── eager "on it"  <0.5s
   │  wake word → speculative pre-work (side-effect-free): pre-warm likely expert, fire read-only first step
   ▼
PROXY ORCHESTRATOR  — one persistent, prompt-cached Claude Agent SDK agent (the identity on the call)
   │  ~10ms exact/semantic cache check → hit? deliver immediately
   │  comprehend the ask (mode + what's being asked)  ·  clarify-or-declare
   ▼
REGISTRY RETRIEVAL  — retrieve the owning expert(s) from the estate registry (Doc 1)
   │  0 experts → generalist floor · 1 → default · several → cross-system
   ▼
THIN BUNDLE ("car keys")  — ask + WHO (expert pack id[s]) + one starting pointer + flags
   ▼
THE WORKROOM (Deep Dive 05)  — one agent does the work: answer / read / act, verified
   ▼
DELIVER  — voice (stream → Cartesia TTS) / chat / tile, with proof  ·  >2s → background job + ping
   ▼
WRITE BACK to the Meeting Page  → follow-ups compose
```

Read it left to right. The instant Proxy is addressed it says "on it" (dead air is the enemy), and on the wake word it fires **speculative pre-work** — pre-warming the likely expert and firing the read-only first step, all side-effect-free, so the common ask is half-answered before the sentence finishes. The **orchestrator** — one persistent, prompt-cached agent — comprehends the ask (what shape of help, what's being asked), checks a ~10ms exact/semantic cache for a repeat, and either asks one tight clarifying question or proceeds. It **retrieves** the owning expert(s) from the estate registry, seals a **thin bundle**, and hands it to the workroom, which does the actual work and returns a proven result. Proxy **delivers** it on the right surface with its receipt and **writes it back** to the Meeting Page so follow-ups compose.

> **WHY REUSE THE PROACTIVE ENGINE INSTEAD OF A DEDICATED HANDLER**
> A colleague's answer to "is that right?" and a colleague noticing "that's not right" require the exact same work: comprehend, retrieve the owner, do the work, attach the proof. Building two engines would mean building that twice and letting them drift. One pipeline, one workroom, one set of verifiers — reactive just enters at the front of the line with the surfacing gate off. Reactive and proactive are honestly "one engine at two provocations."

---

# PART C — THE BUILD

For each piece: the overview in plain terms, then how we wire and optimize it.

## Component 1 — The wake and speculative pre-work

**Overview.** Much of the latency in answering is grounding — resolving what you meant and warming the right expert. Speculative pre-work moves that before the ask is even finished.

**How we wire and optimize it.** On the wake word ("Proxy—" / @-mention / a chat message beginning), three things fire in parallel, all side-effect-free: the most-recently-referenced estate node on the Page is pre-resolved as the probable target; that node's expert is pre-warmed (its pack prompt-cache warmed); and if the likely ask is a lookup, the read-only first step is fired speculatively. If the actual ask matches, the answer is already in hand; if not, the work is discarded — and because it was strictly read-only, discarding costs nothing but a few cents. It is capped per meeting so a chatty room can't turn it into a cost leak. The heavy lifting is done by the prefetcher (Deep Dive 04) — a piece of plumbing, not a model: when the Page marks something worth watching, deterministic live reads fire into short-lived evidence bundles. Speculative pre-work just points that same prefetcher at the wake word, so the optimization is essentially free.

## Component 2 — The Proxy orchestrator

**Overview.** Proxy on the call is one persistent agent — the identity the room talks to. On the reactive path its job is comprehension and routing, not doing: understand the ask, retrieve the expert(s), seal the bundle, deliver the result. It is fundamentally a router, but routing needs almost no intelligence because the estate graph already encodes who owns what.

**How we wire and optimize it.** It's a single Claude Agent SDK session, kept warm with prompt caching so its large stable context (instructions, the Page schema, the expert registry's search tool) is cached and never re-read — each ask costs only its own tokens. It runs on a fast tier (Haiku-class) because comprehension-plus-routing is cheap; the expensive reasoning lives in the workroom, invoked rarely. Everything Proxy has built is available to it as tools: the Meeting Page (read The Read's signals), the estate registry (retrieve experts), and — by handing them into the bundle — the workroom's own tools. It does not hold context it can fetch; it passes pointers.

## Component 3 — The exact-answer cache check

**Overview.** A meaningful fraction of asks are repeats or near-repeats ("what's checkout's p99 again?"). A ~10ms check in front of the agent serves those without any model call.

**How we wire and optimize it.** A deterministic exact/semantic cache keyed on the resolved question and its freshness stamp. If the exact answer is already known and still valid (in a prior result, on the Page, or in the expert's pack), it's served directly and streamed to delivery — no LLM. A miss falls through to the orchestrator. This is the only pre-agent step, and it's a lookup, not a classifier — nothing about task type is decided here.

## Component 4 — Registry retrieval: the router that needs no intelligence

**Overview.** To answer, Proxy must reach the expert(s) that own the ask — one, several, or none. At enterprise scale there may be thousands of experts, so Proxy does not *hold* them; it **retrieves** the relevant ones on demand. This is how the field handles thousands of tools, and it's what makes any enterprise agent reachable without bloating context.

**How we wire and optimize it.** Every expert agent in the enterprise is an entry in a searchable registry built from Deep Dive 01 (a private MCP subregistry, `server.json` metadata, indexed by scope). Retrieval uses the **Tool Search Tool** pattern (`defer_loading`): the full expert catalog is registered but deferred, so only a small search tool plus Proxy's few always-on tools load upfront; the matching expert pack id(s) are discovered per ask. Published results for this pattern: ~85% upfront-token reduction and a large tool-selection accuracy gain (Opus 4.5: 79.5% → 88.1%). The index is embedded on example *queries* per expert (not raw descriptions) and kept auto-synced with the estate (CRUD + hash comparison), which is what keeps retrieval accurate at thousands of experts. Retrieval returns zero (→ a fully-capable generalist floor, never a shrug), one (the default), or several (a cross-system ask → the workroom may fan out). **Pre-warming** likely experts from the agenda, the team, and recurring-meeting history is a latency optimization only; retrieval against the full registry always guarantees correctness.

## Component 5 — The thin bundle: car keys, not the car

**Overview.** What Proxy hands the workroom is deliberately thin — the minimum to start work with authority, not a fat context dump. Loading full context would be slower *and* less accurate (large contexts degrade reasoning). The bundle carries only what the workroom cannot discover for itself.

**How we wire and optimize it.** The bundle is: the **ask**; **WHO** (the retrieved expert pack id[s]); **one starting pointer** (the current topic's Page slice, or a named node if the ask gave one); and **flags** — identity/permissions, the effects flag (read-only vs. draft-staging), and stakes/freshness. That's it. The flags are the real reason a bundle exists rather than "just the context": they carry *authority and the asker*, which the workroom can't find on its own, and the effects flag is what makes every side effect a staged draft. Everything else — schemas, live values, code, dependencies — the workroom fetches itself, because the loaded expert pack is a self-describing map of where that system's context lives (Deep Dive 05). The bundle unlocks the door and says who's asking and what they're allowed to touch; the workroom drives from there.

## Component 6 — Clarify-or-declare: one question, only when it changes the outcome

**Overview.** The dumbest thing an assistant can do is confirm what it already knows; the second dumbest is guess silently and act on the wrong thing. Clarify-or-declare threads between them.

**How we wire and optimize it.** Folded into the orchestrator's comprehension pass — no separate model. For {referent, scope, intent}, it scores confidence against the Page's resolved state. It asks exactly **one** question only when confidence is below a floor **and** the alternatives would lead to *different actions* — with the best guess embedded so "yes" is one word ("the checkout load test, right? at 2×"). Otherwise it **declares** its assumption aloud and proceeds ("running the checkout test at 2× — assuming production; say stop if you meant staging"). Never ask to confirm something already resolved on the Page; never ask a question whose answers all produce the same action. This is your "sometimes a quick question, not always" — a threshold, not a habit. The floor is tuned per room from the shadow ledger (a declared assumption that got vetoed → floor too low; a question that got an eye-roll → floor too high).

```
SYSTEM PROMPT — CLARIFY-OR-DECLARE (ILLUSTRATIVE, folded into comprehension)
For the ask, score confidence 0–1 on {referent, scope, intent} against the Meeting Page.
Decide ONE:
  ASK    — only if min-confidence < floor AND the alternatives lead to DIFFERENT actions.
           Emit ONE question with the best guess embedded.
  DECLARE— confidence fine, or the ambiguity doesn't change the action. State the assumption; proceed.
  GO     — unambiguous; proceed silently.
Never confirm what the Page already resolved. Never ask a question whose answers all act the same. One question max.
```

## Component 7 — Delivery: fast, on the right surface, with proof

**Overview.** However the answer was produced, it must arrive fast, on the surface the ask came from, carrying its receipt.

**How we wire and optimize it.** The latency ladder (Part D) governs timing. For multi-step or side-effecting work, Proxy **plan-flashes** a 2–4 step plan before running (silence = go; an objection edits it before step one) — the place a misunderstanding dies cheaply; a read-only lookup needs no plan-flash (nothing to misunderstand, nothing to undo). Verification is **stakes-scaled**: an idle lookup gets one check; an answer feeding a hard-to-reverse decision gets the test actually run and a corroborating source — the stakes read straight off The Read's decision-health, no model call. Every returned fact carries its **proof** (test exit code for code, live re-retrieval for facts, recalculation for models). Spoken replies stream token-by-token into fast TTS (**Cartesia Sonic-3**, ~40–90 ms first audio) behind a swap adapter; chat replies return as text; tile cards for anything worth showing. A chat ask behaves identically to a voice ask apart from the return surface.

> **WHY REVERSIBILITY, NOT TOPIC, IS THE DIAL**
> Scaling verification by how hard a decision is to take back — not by keyword or meeting type — is what keeps this vertical-agnostic and honest. "Ship Friday" in a startup standup and "approve the merger data" in a board review get the same rule: how much does it cost if this answer is wrong and someone already acted on it? High cost buys deeper verification. That's a property of the situation, read from the Page, not a hardcoded list of "important topics."

## Component 8 — Follow-up continuity and watchers

**Overview.** Proxy's own actions live on the Meeting Page, so a follow-up composes against what came before — you have a running conversation with your work, not amnesiac one-shots.

**How we wire and optimize it.** Every action Proxy takes is logged as a result on the Page, with inputs and outputs. "Run it again at 2×" resolves "it" to the last run and changes only the load parameter; "how does that compare to last week?" diffs against the prior result — the same resolver that grounds any pronoun, because Proxy's actions are just more entries on the Page. Composition is therefore free (no separate conversation memory to build). *V1:* a reactive ask can register a **watcher** — "keep an eye on the deploy and tell us if errors spike" — a standing, meeting-scoped, event-driven check that fires on its event and is torn down at close. It's the natural bridge from asked behavior into unprompted behavior.

---

# PART D — LATENCY AND SCALING

**The latency ladder — what makes it feel like talking to a person.**

- **Eager "on it" in under half a second.** A canned acknowledgment the instant Proxy is addressed — cheap, and the single most important latency trick, because it converts dead air into a felt response and buys the seconds the real answer needs.
- **Cached / single-read answers stream straight to TTS.** For an answer from cache or one quick read, tokens flow directly into Cartesia Sonic-3 as generated (~40–90 ms first audio), so the reply starts immediately. (Budget for the tail — independent measurements show ~100 ms P50 variance — so the "on it" covers the gap.)
- **Multi-hop grounding gets an honest "let me check."** If the answer needs several dependent lookups, Proxy says so, does the hops in the workroom, and returns verified. Honest and slightly slower beats instant and wrong on a channel where a wrong number moves a decision.
- **Anything over ~2s detaches.** Real work is acknowledged, then runs as a background job with visible progress and a completion ping (in-panel now, or Slack/Teams after the meeting). "That'll take about twenty minutes — I'll have it in your Slack" is a first-class answer. The voice channel is never blocked by execution.

**Why it stays cheap at any scale.** Most asks are the cheap path (cache or one read, fast tier, cents). Every reactive component reads the small Meeting Page and the retrieved pack, never the growing transcript — so a three-hour meeting costs the same per ask as a ten-minute one. Registry retrieval keeps the orchestrator's context small no matter how many experts exist. Prompt caching keeps the persistent orchestrator's stable context paid once. The plumbing (transport, STT, TTS) is the real spend and comes down with vendor selection and self-hosting without touching answer quality.

---

# DONE WHEN

**v0 — the core is done when:**

- an ask (voice or chat) triggers the eager "on it" in under half a second, and a known or single-read answer begins within a couple of seconds;
- the orchestrator comprehends the ask and **retrieves** the owning expert(s) from the registry — one, several, or the generalist floor — with no held directory and no per-type branching;
- the thin bundle carries only ask + WHO + one pointer + flags; the workroom fetches everything else;
- an ambiguous ask that would change the action produces exactly **one** clarifying question with the best guess embedded; every other case acts and declares its assumption;
- speculative pre-work is provably side-effect-free and bounded per meeting;
- every returned fact carries its class proof; verification depth provably scales with the decision's stakes read from The Read;
- a chat ask behaves byte-for-byte identically to a voice ask apart from the return surface;
- an impossible ask produces an honest decline that **names the missing access** plus a tracked follow-up — never a bluff, never a lazy "no access";
- every multi-step or side-effecting ask plan-flashes before running;
- a follow-up composes against the prior action's logged result on the Page;
- all four kinds of help — do / help / explain / advise — are served by the identical pipeline, differing only in what returns; and the same path serves a document, an external action, an analysis, and a code task alike.

**v1 — added on measurement:**

- watchers (standing meeting-scoped event-driven checks, torn down at close);
- per-room tuning of the clarify floor and the exact-answer cache from the shadow ledger.

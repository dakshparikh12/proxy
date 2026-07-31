# Proxy — Product & System Spec (source of truth)

> **Status:** v6 · 2026-07-30 · supersedes `product/v0-spec/*`. **Reactive workroom model, fully
> dynamic.** v6: killed the "catalog of capabilities" — the meeting is **one connection**, not a menu
> of speak/chat/screen/propose verbs; the agent decides *everything* about how it communicates, live.
> Nothing is hard-coded — no situation→action logic and no capability list. Read with `CLAUDE.md` +
> `AGENTS.md`.
>
> **Premise:** Proxy is a teammate that joins your meeting already knowing your codebase and does the
> work live. There is **one place the user sees — the meeting**, and **one environment the agent works
> in — the workroom.** The Claude agent is on the call *and* in the workroom; they are the same thing.

---

## 0. The whole system, in one loop

**Everything goes into the workroom.** There is no router, no catalog, no separate subsystem per kind
of ask — one agent, reactive, doing exactly as much as each ask needs. Chit-chat, a meeting command,
and a deep code task are the *same loop* doing more or less work.

```
transcript line arrives  (fed into the workroom as FAST as possible)
        │
        ▼
   is Proxy addressed?  (or a reply to Proxy's own question / a task it started finishing)
        │
        ├── no  → do nothing        (idle — costs nothing)
        │
        └── yes → give the agent the full transcript + the ask; it reasons, plans, does the
                  work in the workroom (only as much as needed), verifies, and responds
                  through its meeting access — choosing HOW to present it — then goes idle.
```

That is the entire product. **Proxy's whole world is two things:** the **workroom** (its workspace —
the repo + the live transcript, as files) and **one connection to the live meeting**. Nothing else to
route, nothing else to wire. Below is just what fills each box.

---

## 1. The five laws (from CLAUDE.md)
1. **Grounded or silent** — cite real `file:line` from the clone, or say "not found."
2. **Never overstate** — plain results; failures spoken honestly, never faked.
3. **Human control is absolute** — a human can stop Proxy; world-touching is a draft behind a click.
   Guaranteed by the **credential** boundary: the sandbox holds no push/send creds.
4. **Dynamic, never hard-coded** — no code maps situation→action. The agent composes everything.
5. **Talk-and-glance** — operable by speaking and glancing; nothing to install mid-meeting.

---

## 2. What the workroom is
A per-meeting **E2B sandbox** that IS the agent on the call:
- the **repo** cloned in,
- **`MEETING_NOTES.md`** — the live transcript, updated as fast as possible (Proxy's input + memory),
- **`REPO_MAP.md`** — the pre-meeting map, **`MEETING_INFO.md`** — who's in the room,
- **`CLAUDE.md`** — the prime (who Proxy is + the nuance principle, §6),
- **native Claude** (the Agent SDK) with its full tools **+ one connection to the live meeting** (§5).

Warm and ready at join (§8), so the first ask is instant.

---

## 3. The reactive flow (the one loop, in words)
A reactive task/interaction comes in → **Proxy is addressed** → the up-to-date transcript + the ask are
already in the workroom → the agent **reasons, plans, does it for real, verifies**, and **brings it
back**, choosing how to present it → idle until next addressed.

**Addressed** means any of: someone says **"proxy…"/@proxy**; a **reply to a question Proxy just
asked**; a **task Proxy launched finishing**; or a **follow-up right after Proxy just engaged** — a
short window where people don't have to re-say "proxy" (this is still reactive: it only opens *because*
Proxy just acted, never a standing always-listen). (Reactive only — Proxy speaks when prompted, never
unprompted. Proactive volunteering is out of scope.)

The transcript feed is **continuous** — it keeps flowing into the workroom *while a task runs*, so a
mid-task answer to Proxy's own question, or a follow-up, is visible to it in real time.

---

## 4. Latency — proportional and automatic (not routed)

"Everything goes to the workroom" does **not** mean everything is slow. The agent does **only as much
as the ask needs**, so speed is proportional *by itself* — we never route "fast" vs "slow" (that would
be the hard-coding you don't want). Five levers keep it quick:

1. **Warm, cached session** — the workroom is already up and the stable prefix (prime + map + tools) is
   prompt-cached, so there's no cold start and low time-to-first-token.
2. **A trivial ask uses no tools** — "how's your day, proxy?" is one direct turn; no grep/read/build.
3. **Fast model for quick turns**, escalating only for real work.
4. **Stream the reply to TTS** — Proxy starts talking as the first words form (begins in ~1s, natural
   conversational latency — not 5s).
5. **Transcript in fast** — each final line pushed the instant it's produced, so follow-ups land live.

So: *"how's your day?"* → **~1s spoken reply**. *"mute yourself for the meeting"* → **done immediately**
(a meeting-access call). *"check the DST bug"* → **instant spoken ack**, then the work, then the answer.
The 5-second failure modes (cold start, needless tool use, no streaming) are exactly what these avoid.

---

## 5. The meeting connection — ONE dynamic interface (no catalog, no rules)
Proxy is **connected to the live meeting** — one persistent connection, the same way it's connected to
the repo. When it wants to communicate or act, it just does it through that connection, **deciding
everything itself**: *what* to convey and *how* — out loud, in chat, on screen, staged for your
approval, or nothing at all. There is **no menu of capabilities it must pick from and no rule mapping a
situation to an action.** It's the model's live judgment, exactly like a person deciding whether to
speak up, drop a note, or show their screen.

The only thing in *our* code is the **physical pipe** — how bytes actually reach the room (audio / chat
/ video via Recall) and how the credentials stay host-side. That's a *driver*, not a decision: it
carries whatever the agent chose, however the agent said to. **Zero situation→action logic. Zero
capability list the agent is boxed into.** (Physical channels like audio vs. video exist under the hood
because a meeting *has* them — like a person having a voice and a screen — but the agent is never handed
them as a fixed toolset; it expresses intent and the pipe carries it.)

World-touching stays a human click by the **credential boundary**: the sandbox has no push/send creds,
so when the agent produces something that would touch the world, it can only *offer* it — the host turns
that into an approve link. Not a rule, just physics. **[D1 — allow that world-touching offer in v1, or
defer it to v2?]**

---

## 6. The nuance prompt — the entire "dynamic nuance system"
There is **no nuance subsystem.** The nuances live in **one principle in the prime**, applied to every
interaction, both directions:

> *"You are a participant in this meeting; the workroom is your workspace and you are connected to the
> live room. Communicate and act however fits the moment — the choice of how (out loud, in chat, on
> screen, staged for approval, or staying quiet) is entirely yours, made live like a person would.
> Handle whatever comes — a question, "mute yourself", "elaborate more", an interruption, an ambiguity —
> the way a great teammate would: answer instantly when it's simple; do the real work when it's a task
> (you may narrate as you go and run heavy work in the background while you keep talking); ask a quick
> clarifying question only when you truly need one — you'll see the answer in the transcript, so continue
> when it lands; after you deliver something, stay ready for follow-ups; and stop and address a person
> the moment they cut in."*

Infinite nuances, **zero hard-coding**, because each is the model's judgment over the live situation —
the same way it already reasons about code. **Memory = the transcript**, so persistent instructions
("stay muted", "we're on the availability code") are simply *there* on every engagement. This prompt is
the craft we **tune on real meetings**, not on paper.

---

## 7. The little that isn't the agent (physics)
Only two things: **feed the transcript in fast**, and **play audio out**. Interruptions are handled by
the agent — it hears a cut-in via the fast transcript feed and stops/addresses it (§6). If real-meeting
testing shows we need a faster audio-cut assist, that's a small mechanical tweak, **not a subsystem**.

---

## 8. Pre-meeting + warm start
Pre-meeting (once per repo): connect → clone → build the map → store it (Postgres `repo_maps` + GCS;
re-built on a signed GitHub push). At meeting join: assign a **warm E2B sandbox** (small pre-warmed
pool), seed it (repo + map + info + prime + empty notes) in the background → ready before the first ask.

---

## 9. Infrastructure (simplest) + auth
**Per-meeting workroom:** E2B (Firecracker microVM, warm pool, pre-baked template). **Control plane:**
one Cloud Run service + Cloud SQL Postgres + Secret Manager + GCS + Artifact Registry — nothing more
(no GKE / Pub-Sub / multi-region / custom orchestrator). **Self-host:** one container + one Terraform
module. **Auth:** the founder's **subscription** for dev + cal.com proving; **cloud API for production**
(Anthropic ToS forbids routing customers on a personal subscription) — a one-line seam swaps them.

---

## 10. Verification bar — 100% correct-or-honest, on real meetings
Every task: done correctly, or an honest clarify, or an honest decline — **zero wrong/faked answers.**
The agent verifies its own work (runs the check; a fresh-context sub-agent judges deep work). The bar is
proven on **real meetings on the cal.com repo, on real infra** (real E2B + subscription + Cartesia/
Recall). The nuance prompt (§6) and latency (§4) are tuned there, not asserted on paper.

---

## 11. Build plan (small — the workroom engine already exists)
- **A. The meeting connection (MCP):** one host-side pipe that carries whatever the agent sends to the
  room over the right physical channel (creds host-side) + launch native `claude` connected to it. *This
  is what puts the agent in the room.*
- **B. Fast transcript feed:** each final line pushed into `MEETING_NOTES.md` the instant it's produced.
- **C. The nuance prime (§6)** + latency wiring (§4: warm/cached, fast model, stream-to-TTS).
- **Prove** on cal.com against the §10 bar (the real-meeting battery), tuning §6 + §4.
- **Then delete** the old in-meeting engine + code_intel indexer + dead islands (~22k LOC) + re-seal
  doc00–03 (**founder-gated**). **Then** refresh CLAUDE.md/AGENTS.md/repo structure; **then** deploy (§9).
- Nothing old is deleted until the new path is 100% correct-or-honest on real infra.

---

## 12. Founder gates
1. **D1 — world-touching in v1?** (§5): can the agent offer a change/PR/message for your one-click
   approval in v1, or defer all world-touching to v2?
2. **E2B machine size** for the `proxy-workroom` template (fixes the OOM). Suggest 4 vCPU / 8 GB.
3. **Prod auth** — `claude setup-token` vs. moving to the cloud API for production.
4. **Live-vendor confirmation run** — the founder's to fire; precedes any deletion.
5. **Re-seal authority** for doc00–03 + lifting the PreToolUse guard — before the delete phase.

---

## 13. Definition of Done
One holistic product: the reactive workroom system — agent on the call, meeting access, fast transcript,
the nuance prime — **proven on cal.com on real infra**, every task correct-or-honest; all old code
deleted (no dead lines); ruff + mypy `--strict` + bandit + naming + contracts-closed green; clean
CLAUDE.md/AGENTS.md/repo; the GCP deploy + self-host path built. **Done means proven in a real meeting
on real data — not that the code compiles.**

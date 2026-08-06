# PROXY — Full System Design (for founder sign-off)

## 1. The system in one sentence
One agent, permanently in the meeting with everything in its head, that on every utterance
judges → acts → delivers through one connection to the room — where **all behavior comes from a
layered mind (not code)**, all work happens in its own workshop, and everything heavy runs in its
background hands.

## 2. The stack — five layers, each with one owner

| Layer | What | Owner |
|---|---|---|
| **Senses/Voice** | Recall (carrier) · AssemblyAI (ears — proven good) · TTS vendor (Cartesia — proven innocent by the wav test; chain fix in flight) | vendors, behind our one seam |
| **The Mind** (all cached, resident) | identity prime · **interaction layer** · codebase understanding · the whole live meeting | **the prompt — this is the product** |
| **The Body** | one `to_meeting` MCP tool (say/chat/DM/screen/offer/mute) · native tools · **native background tasks** (proven live: turn ends, completions self-surface) | Claude-native + our thin relay |
| **The Workshop** | the sandbox: repo, CLI, artifacts, **skills**, room for 2 concurrent background jobs (RAM-measured) | Claude-native |
| **The Physics** (ALL of our code, complete list) | hear→wake · deliver→room · the barge-in cut · **wake-state assembly** · idle-listener | ours — small and frozen |

## 3. The one loop — incl. the "dynamic layer per reactive task"
Every utterance: **captured → cached** (always, regardless of what's running) → **gate** (cheap:
name/window/chat) → **wake**. Each wake is assembled fresh from three parts:
- **the static mind** (cached — ~3 fresh tokens),
- **the situational state** (built per-wake by physics: jobs running/done · you-were-cut-off +
  the unsaid remainder · what's on screen · who's speaking),
- **the ask itself.**

The agent then: judges (respond / silent / clarify) → sizes it (answer now vs background it) →
**pulls the skill for the work kind** (artifact / diagram / research / job — loaded on demand,
native) → does it → delivers per the interaction layer → **ends its turn**. Background
completions arrive as native notifications; the idle-listener delivers its announcements.

## 4. The behavior architecture — why each piece exists, zero overlap
- **Prime** = *who you are* (identity, laws, honesty) — rarely changes.
- **Interaction layer** = *how you behave in the room* (the situations as concrete examples +
  the unpredicted-clause) — grows with every live miss. **The nuance product lives here.**
- **Skills** = *how you produce excellent work* (procedures per work kind) — the "plugins",
  loaded only when doing that work, keeping the always-on mind lean.
- **Wake state** = *what's true right now* — facts, injected by physics, never judgment.
- **Fallback** = *best judgment, explicitly licensed* — the unpredictable tail.

## 5. Latency budget (measured → target)
- STT final: ~1.0–1.5s (vendor floor)
- wake/pipeline: ~0.1s ✅ (measured, fixed)
- first model token: 2.5–5s today → **target ~2s** (concision + calm prompts; watch first-turn outliers)
- voice start: ~0.5–1s (Cartesia streaming socket saves ~0.3–0.5s — later lever)
- **Felt target: ~4s for quick asks.** Big asks: acknowledgment at ~4s, then backgrounded.

## 6. Quality architecture
The **above-and-beyond starter** in the layer (per work kind: what "THE answer, not the normal
answer" means for an answer / doc / diagram / code change / research) + **skills** carrying the
craft (excellence procedural, not hoped-for) + the verify discipline (run it or say you couldn't)
+ channel-layering (gist aloud, receipts in chat with real GitHub links, artifacts on screen).

## 7. Red team — every real failure mapped to its catch
| Failure (all real, from our live sessions) | What catches it |
|---|---|
| Follow-up window missed / clock bugs | window stays (wall-clock, audible-anchored) + every gate-miss recoverable by name + the layer teaches re-engagement; width = Decision 1 |
| Room deaf during long work | background-by-default for big asks (proven) + turn-ends discipline — residual: model forgets to end turn → trace-watched, layer-reinforced |
| Barge-in remainder lost | remainder capture → wake state (physics) |
| Spoke its silent reasoning / URLs aloud | sentinel + voice-sanitizer (landed, proven) |
| Subagent tool lied about detached work | banned for detached work — bash+done-file pattern only (proven) |
| Audio chop | page telemetry decides the last hop with data (in flight) |
| Fake fixes from build agents | independent verification of every claim before reporting — standing rule |
| Sandbox OOM | 2-job cap (measured ~250MB/process on 478MB); bigger template later |
| Runaway cost | per-ask guard + weekly-budget awareness + job caps |
| The unpredicted | fallback clause + the live loop (every miss → a line in the layer) |

**Honest residual risks:** model adherence is probabilistic (the loop mitigates, never guarantees) ·
Meet's inbound-audio processing is outside our control (telemetry will tell us) · a judgment
during a long non-backgrounded turn still queues (mitigated by turn-ends discipline).

## 8. Explicitly rejected complexity
Always-judging mode · orchestration frameworks · session-fork *architecture* (remains a technique
the agent may itself use: `claude -p --resume <id> --fork-session`) · a second warm session · any
code that maps situation→action.

## 9. The four decisions for the founder
1. **Gate width** — keep name+window+chat as-is, or widen the follow-up window (e.g. 30s)?
2. **Milestone updates** — may background jobs post brief chat notes as Proxy, or face-only?
3. **Skills v1 scope** — artifact + diagram + background-job; add research-report?
4. **Latency bar** — is ~4s felt (quick asks) the acceptance number for the next live loop?

## 10. Build + test sequence after sign-off
Physics (idle-listener · remainder/state · session-id file) → layer + skills drafted to this
design → founder co-writing pass → one live loop (doubles as the audio-telemetry read) →
iterate the layer until the nuances hold.

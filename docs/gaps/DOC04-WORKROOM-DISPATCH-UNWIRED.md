# GAP · Doc 04 §112 — the Workroom dispatch chain has no production caller

**Owner: Doc 04** (the Orchestrator/harness), not Doc 05.
**Status: OPEN.** Filed 2026-07-28 while wiring Doc 07's seams.
**Affects: the live in-meeting path and post-meeting execution equally.**

---

## The claim

`harness.dispatch.dispatch_workroom` claims an `operation_runs` row and returns a
`WorkroomHandle`. **Nothing in production consumes that handle.** A dispatched task is
never executed — the row is claimed and then sits there.

This is not specific to Doc 07. **The live in-meeting path has the identical gap**: when
Proxy's wake turn decides to dispatch real work, the same missing link applies.

## The evidence — three checks, each re-runnable

```bash
# 1. dispatch_workroom (the harness dispatcher) has no production caller.
#    The only hits under services/ are docstrings.
grep -rn "harness.dispatch import\|dispatch\.dispatch_workroom" services/ --include=*.py

# 2. SessionDriver — Doc 05's driver that actually runs a task — is constructed
#    ONLY in tests. Zero hits under services/ or libs/.
grep -rn "SessionDriver(" services/ libs/ --include=*.py

# 3. TOOL_HANDLERS has no dispatch_workroom handler.
sed -n '/^TOOL_HANDLERS/,/^}/p' libs/agentkit/src/agentkit/tools.py
#    -> {"echo": echo_handler, "answer": answer_handler}
```

The behaviors (`services/harness/src/harness/behaviors/propose_action.py:40`) mount the
**string** `"dispatch_workroom"` in an `allowed_tools` list. That is a name the model may
emit — not a wired call. Nothing maps the name to the function.

## What the spec says should exist

Doc 04 is explicit, and it assigns the missing piece to **the harness**:

> **`04-ORCHESTRATOR.md:32`** — *"Its tools **are** the routes: `dispatch_workroom(bundle)` ·
> `speak(text)` · `send_chat(text, dm?)` …"*

> **`04-ORCHESTRATOR.md:112`** — *"**The harness** … owns … the registered tool functions
> (speak/chat/screen/dispatch/… — **thin wrappers over the other docs' APIs**). 'Knowing
> when things are done' is a **completion callback**: every dispatched workroom is an
> `asyncio.create_task(...)` with a done-callback — the runtime delivers the done-moment;
> nothing polls."*

> **`04-ORCHESTRATOR.md:678`** — *"every dispatched task is a parallel `asyncio.create_task`
> whose completion notifies Proxy (no polling)."*

So the intended chain is fully specified:

```
wake turn (model emits the tool)
  → registered tool wrapper            ← MISSING (Doc 04 §112, the harness)
  → harness.dispatch.dispatch_workroom ← exists, no caller
  → asyncio.create_task(SessionDriver.run_task(...)) + done-callback
                                       ← MISSING (Doc 04 §112, the harness)
  → SessionDriver.run_task             ← exists, complete, tests-only
```

## Doc 05 is not the gap

`SessionDriver` (`services/workroom/src/workroom/session.py`) is **present and waiting**.
It is complete: it resolves the warm sandbox (`sandbox_provider.provision`, idempotent per
meeting), the `code_intel` server (built fresh per task from `db`), the notes read path,
and it writes the terminal `Envelope` into the same `operation_runs` row's `result_ref`.

It takes `(provider, sandbox_fs, store, db, abort_registry, model, disposition, …)` — a
`db` handle and a `meeting_id`, nothing more exotic. There is no missing capability on the
Doc 05 side; there is a missing **caller** on the Doc 04 side.

## Why it went unbuilt with no gate firing

**Neither Doc 04 nor Doc 05 has an acceptance bundle.** `acceptance/` contains
`doc00 doc01 doc02 doc03 doc07` — and nothing else.

That absence is the mechanism. A fully-specified chain, named in three places in
`04-ORCHESTRATOR.md`, went unimplemented and no gate fired, because there was no sealed
criterion that could fail. doc00's criteria mention `dispatch_workroom`, but that is a
**different function** — `libs/ops/cost.py:292`, the pre-dispatch estimate gate — which
*is* wired. The name collision makes the gap look covered when it is not.

The lesson generalises past this gap: for docs without a bundle, "the spec says so" is not
enforced by anything.

## What currently depends on this

| Site | Behaviour today |
|---|---|
| `control_plane/plan_approval_route.py` | Raises `WorkroomDispatchUnavailable`; the route returns **202 `dispatch_blocked`**. The approval still lands. |
| `harness/post_meeting/dispatch.py` | Injected `assemble_bundle` / `workroom_dispatch`; never imports them. Callable once a real dispatcher exists. |
| `acceptance/doc07/` | **AC-PME-09, AC-PME-09-NEG, AC-PME-10, AC-PME-10-NEG stay BLOCKED**, and that is correct. The sealed `assurance_limits` records the blocker as the no-media worker; the real blocker is this, one layer down. Re-sealing is a founder action (`builder_writes: DENIED`). |

## Closing it

1. Register a `dispatch_workroom` tool handler in `libs.agentkit.tools.TOOL_HANDLERS`,
   honouring the never-throw contract that `AC-CON-003` pins (the sealed criterion covers
   the **contract**, not the membership, so adding a handler does not break the seal).
2. Have the harness wrapper call `harness.dispatch.dispatch_workroom`, then
   `asyncio.create_task(SessionDriver.run_task(bundle, run_id=handle.run_id))` with the
   done-callback Doc 04 §112 describes.
3. Pass the resulting dispatcher into `install_approve_route(dispatch=…)` — SEAM 2 then
   returns 200 instead of 202 with no other change.
4. Generate `acceptance/doc04/`, so the next gap of this shape fails a gate instead of
   being found by hand.

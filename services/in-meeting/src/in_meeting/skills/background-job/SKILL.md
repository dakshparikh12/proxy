---
name: background-job
description: Run a longer task in the background so you stay present in the meeting — the reliable dirs / brief / run_in_background / done-file / milestone pattern. Use for anything past a short moment.
---

# Running a background job

Anything that takes more than a short moment (a real code change, a document, research, a
build/test run) should run in the background so you are not frozen mid-meeting. This is the
proven, reliable pattern. Cap yourself at ~2 concurrent jobs.

## The pattern

1. **Make a job dir.** One per job, so nothing collides:
   ```
   jobs/<name>/       # e.g. jobs/migration-audit/
   jobs/<name>/brief.md   # what you're doing + what "done" looks like
   jobs/<name>/done       # the done-file — absent until finished
   jobs/<name>/out.md     # the result the job writes
   ```

2. **Write the brief.** A few lines in `brief.md`: the exact task, the acceptance ("done =
   the report lands in out.md with the 3 slowest queries and a fix for each"). This keeps a
   long job on track.

3. **Start it with `run_in_background`.** Kick off the work as a background Bash command that
   does the work and, as its LAST step, writes the done-file:
   ```bash
   ( <do the real work, writing jobs/<name>/out.md> ) \
     && touch jobs/<name>/done
   ```
   Run it with `run_in_background: true` so it detaches and you keep participating. Say ONE
   short line aloud that you have it going ("Kicking off the migration audit now — I'll bring
   it back in a minute").

4. **Post milestones via chat.** If the job runs long, drop a brief status to the room with
   `to_meeting(medium="chat", content="migration audit: ~half done, found 2 slow queries so
   far")`. A milestone is one line — don't narrate every step. Honest state only.

5. **Poll the done-file, then come back RIGHT.** Check for `jobs/<name>/done`; when it's
   there, read `out.md` and deliver the real result — show the artifact / offer the change /
   say the finding. Deliver the thing, don't just announce it's done.

## Rules that keep it reliable
- The done-file is written **last**, only on success — so its presence means finished.
- If the job fails, write the reason into `out.md` and still deliver honestly ("the audit hit
  X, here's what I got and what's left") — never claim done when it isn't.
- Know your jobs: if someone asks about one mid-flight, report its real status.
- Two jobs max in flight; beyond that you lose the thread.

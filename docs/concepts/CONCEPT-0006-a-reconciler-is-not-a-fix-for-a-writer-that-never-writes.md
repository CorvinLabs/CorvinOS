---
id: CONCEPT-0006
title: A reconciler is not a fix for a writer that never writes
status: ACTIVE
created: 2026-09-21
skills: []  # none — see "Note on location" below: skill minting is license-gated off on this box
relates_to: [CONCEPT-0005]
paths:
  - core/console/corvin_console/chat_runtime.py
  - core/console/corvin_core/task_manager.py
  - core/plugins/corvin_plugins/bootstrap.py
  - core/console/tests/test_chat_turn_task_finalized_on_disconnect.py
  - core/plugins/tests/test_stale_task_reaper_call_site.py
docs:
  - docs/claude-ref/layer-22-task-engine-m2.md
---

# CONCEPT-0006 — A reconciler is not a fix for a writer that never writes

**Note on location:** this belongs in `Corvin-ADR/concepts/`. That repo does not
exist on the Windows box this was written on, so it lands in the documented
`CorvinOS/docs/concepts/` fallback. Move it when the two are on one machine.

**Note on the companion skill:** CLAUDE.md says a durable concept SHOULD mint a
SkillForge skill distilling its Method, so that the lesson is auto-injected without
anyone remembering the concept exists. That was attempted and is **not possible on
this install**: `SkillRegistry.create` raises
`license_required: Capability denied: forge.create (tier=free,
reason=not_available_in_tier)` (ADR-0156 tier gate), which is also why
`~/.corvin/tenants/_default/forge/skills` is empty and why CONCEPT-0005's named
companion skill does not exist either. The intended skill is
`assistant.corvinOS_reconciler_vs_writer` (`learned-experience`, project scope,
body = the Method section below); mint it and fill in `skills:` on an install whose
tier permits `forge.create`, remembering the one bootstrap grade at ≤0.3.

## The situation this is for

A counter, gauge or gate reads a **stored** field rather than deriving it: a
`status` column, a `running` flag, a cached total. Something fails to update that
field, the value drifts from reality, and the drift is permanent because nothing
recomputes it. Somebody notices, and the repair that suggests itself is a
**reconciler** — a boot sweep, a nightly job, a TTL, a janitor — that walks the
stored state and fixes up whatever looks stale.

The reconciler is usually correct, usually cheap, and almost never the fix. It
bounds how long a wrong value survives. It does not stop the value being written
wrong, and its own success log looks identical whether it repaired the fleet or
swept an empty directory.

The rule: **when a stored field drifts, find the writer that should have updated
it and make it do so. Add the reconciler as well, never instead.**

## The measured instance (2026-09-21, console chat dead on QuotaExceededError)

Every turn in one console chat failed with *"The turn failed unexpectedly
(QuotaExceededError)"*. Permanently: no restart, cache clear or waiting helped.

`TaskManager._count_running_tasks` counts task-meta files whose `status` is
`"running"`. A task leaves `running` only when some code path records a terminal
event; nothing reconciles the field against whether a process actually exists. So
the fifth task that never got a terminal event reaches `max_concurrent=5` and the
chat is dead for its lifetime. Measured: 5/5 orphans on one web chat, oldest 9.7 h.

There *was* already a reconciler — `TaskManager.reap_stale_running()`, sound,
liveness-gated, six unit tests. It had exactly one caller: `adapter.py`'s
messenger-bridge boot. A console-only install never reaped anything. So the first
repair was a wiring fix: move the sweep into `boot_platform()`, the one sequence
both shipped hosts provably run. It worked, live, first try — five orphans
finalized, chat answering again.

And it was the wrong half. The question that saved the task was asked *after* the
green result:

> The reaper runs at *boot*. If an interrupted turn leaks while the console keeps
> running, boot-only reaping is not enough.

It leaks. A client that disappears mid-turn — browser refresh, closed tab, dropped
link — closes the WebSocket; the route cancels the turn; the turn generator's three
`(CancelledError, GeneratorExit)` handlers emit their paired ADR-0171 audit end and
re-raise **without touching the task lifecycle**; an interruption before any of
them (pre-spawn gates, model resolution, context build) reaches no handler at all.
Reproduced against the live console: one disconnect, then `running=1` at t+30s
with the event log holding only `['task.created', 'task.started']`. Five browser
refreshes on a console that is never restarted, and the chat is dead again — with
the boot reaper installed, working, and irrelevant.

The real fix is at the writer: `stream_turn` became a thin wrapper whose only job
is a `finally` that records a terminal event when the turn ends without one.

## The method

1. **Read the consumer to find the field, not the symptom.** The symptom was a
   quota error; the fact that made it permanent was `status` being *stored* and
   *unreconciled*. Until you know whether a number is derived or remembered, you
   do not know whether it can be stuck.
2. **Enumerate the writers of that field, then the paths that skip them.** Here:
   ~25 terminal-event call sites, every one on an ordinary exit, none on the
   abnormal one. The gap is not usually a bug inside a writer; it is a control-flow
   path with no writer on it.
3. **Ask when the reconciler runs versus when the leak is produced.** Boot-time
   reconciliation against an operation-time leak is a category error, and it is
   invisible in testing because the test restarts the process anyway. This is the
   one question that turns a plausible fix into a real one.
4. **Fix the writer where the control flow converges**, not at each handler. Three
   cancellation handlers and an unguarded prologue is four places to forget; a
   `finally` around the whole generator is one. In Python that means a thin
   wrapper plus `contextlib.aclosing` — delegation by bare `async for` leaves
   closing the inner generator to the garbage collector.
5. **Make the new write idempotent by reading the state, not by remembering.** The
   finalizer writes only while the status is still non-terminal. A "did we already
   finalize" flag drifts from exactly the field it guards; a status check cannot.
6. **Keep the reconciler.** It still owns the case the writer cannot reach: the
   process died. Two mechanisms, two subjects — dead-process orphans and
   live-process interruptions.
7. **Reproduce with the process left running.** The proof is the same disconnect
   that leaked, followed by reading the status *without restarting anything*. A
   restart in the middle silently re-tests the reconciler.

## Why it keeps paying off

Every "stuck value" in this repo has had this shape, and the reconciler has
always been the tempting stopping point:

- The usage counters (ADR-0760): resetting the *view* with an epoch, never trimming
  the append-only chain the number is folded from.
- `engine.span.*` observations with no `model_id` (ADR-0759): the reader dropped
  them, and the fix was at the three emitters, not a repair pass over stored spans.
- This task-status leak: reconciler first, writer second, and only the writer
  actually closed it.

The generalisable part is the *ordering* of the two repairs, and the specific
question in step 3 — **when does the reconciler run, versus when is the bad value
produced?** A reconciler whose period is "process lifetime" fixes nothing on a
host that runs for weeks.

## Alternatives considered

- **Make the reader liveness-aware** (`check_quota` probes each `running` task's
  pid). Tempting: one place, no producer changes. Rejected — it moves a process
  probe onto the hot path of every turn, it re-opens the 2026-06-17
  false-positive class (a reparented engine that legitimately outlived a restart)
  on a path where being wrong costs the user their turn, and it leaves the stored
  field permanently wrong for every *other* reader of it.
- **Write the terminal event in each cancellation handler.** Correct for the three
  known ones, silent for the fourth. The interruption we actually measured landed
  in none of them.
- **Shorten the reconciler's period** (a periodic sweep every N minutes instead of
  at boot). This is the trap the concept is about: it makes the dead chat
  *temporary* rather than *impossible*, at the price of a job that must run
  concurrently with live workers — which is exactly how a live task gets a second
  terminal event.
- **Raise `max_concurrent`.** Delays the wall. The leak is unbounded.

## When NOT to use this

- **When the field really is derived on read.** Then drift is impossible and a
  stuck value means the derivation is wrong; this concept sends you hunting for a
  writer that does not exist.
- **When the writer is outside your control** (a third-party system, a crashed
  kernel, a `SIGKILL`). Then a reconciler *is* the fix, and its correctness rests
  entirely on its staleness gate — the property to invest in is the gate, not the
  producer.
- **When the drift is benign** — a display-only counter, a cache that repairs
  itself on the next read. Do not add a terminal-write path to a value nothing
  gates on.
- **When adding the producer-side write cannot be made idempotent.** A double
  write that double-counts, re-emits a learning outcome, or sends a second
  notification is worse than the stale value. Prove the idempotence check first.

## Operator Notes

_(append-only; AI amendments never edit or remove anything under this heading)_

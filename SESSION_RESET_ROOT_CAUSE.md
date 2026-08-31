# `/new` did not reset the session — root cause and fix

**Date:** 2026-08-28
**Reported:** Discord `/new` and `/reset` leave the conversation intact.
**Affected chat:** discord channel `1501315335750684803`
**Status:** fixed and verified end-to-end.

This document replaces five earlier notes
(`SESSION_RESET_{BUG_COMPREHENSIVE_REPORT,INVESTIGATION_SUMMARY,REMEDIATION_PLAN,ROOT_CAUSE_ANALYSIS}`,
`SESSION_STATE_PERSISTENCE_FIX_SUMMARY`) whose diagnosis — in-memory Brain state
surviving a reset — did not hold: `session_reset.py` runs as a short-lived CLI
subprocess spawned by the bridge daemon and has no access to the Brain living in
the adapter process, so no handler it calls there could ever have been the cause.
The real cause was on disk, and measurable.

---

## Root cause: the reset deleted paths that do not exist

`adapter._session_dir()` resolves a chat's working directory through the
tenant-aware SSOT `paths.voice_session_dir()`:

    <corvin_home>/tenants/<tid>/sessions/voice/<channel>/<chat>/

`session_reset._wipe_voice_state()` hand-built a different string:

    <corvin_home>/voice/sessions/<channel>/<chat>/

The two never coincided under any configuration. ADR-0007 Phase 1.2 moved the
adapter; `session_reset` was not moved with it. So `/new` rmtree'd a directory
that did not exist, reported `voice_state_removed: no`, and left behind:

    $ cat .../sessions/voice/discord/1501315335750684803/.main_session.json
    {"session_id": "1e53620a-335b-4a5c-a0fa-c1d08c9d3d82", "saved_at": "2026-08-28T06:55:48Z"}

On the next turn the adapter read that file and spawned
`claude --resume 1e53620a-…`. The conversation continued verbatim — exactly the
reported symptom. The same file is dated weeks back; the chat had never been
reset.

The identical drift appeared in four more places, all from the same migration:

| Site | Built | Consequence |
|---|---|---|
| `_wipe_voice_state` | `<home>/voice/sessions/…` | **the reported bug** — Claude session resumed |
| `_wipe_forge_session_dir` | `<home>/sessions/<chan>` | forge tools + `forge/memory.md` survived |
| `_purge_worker_sessions` | same | worker session files survived |
| `_dialectic_session_reset` probe | same | heat score always read zero |
| `session_timeout_sweep` (3 roots) | both layouts | **the daily timer aged out nothing, ever** |

### Why no test caught it

`test_session_reset.py::_seed_voice_state` built the *same wrong path* the bug
deleted, so the test agreed with the bug and passed. A test that seeds through
the SSOT now covers this.

### A second, independent fault: `paths` was being shadowed

`operator/forge/paths.py` was a nine-line stub ("stub for audit_metrics
compatibility") occupying the top-level module name `paths`. Any process placing
`operator/forge/` earlier on `sys.path` — which `session_reset.py` itself did —
got it instead of `bridges/shared/paths.py`. About 25 sibling modules do
`from paths import corvin_home` at import time and raised ImportError under the
shadowed name, each swallowed by its own `except Exception`. Two mattered here:

* `context_budget` → the Layer-20 quota was never reset; `/new` always said
  `token budget reset: no`.
* `instance_identity` → the `session.reset` audit event shipped without its
  instance signature.

Nothing imported the stub's symbols (`audit_metrics` uses the package-qualified
`forge.paths`), and removing it took one bridge suite from ImportError to
44 PASS / 0 FAIL. It is gone.

---

## The fix

**New SSOT — `operator/bridges/shared/session_state.py`.** One module owns
*where* a chat's Claude state lives and *what* counts as that state.
`adapter._reset_session_state()` and `session_reset._wipe_voice_state()` both
delegate to it, so the two sides cannot drift apart again. It loads `paths` by
file path rather than by module name, which is immune to the shadowing above.

**A reset clears conversation state only.** `.main_session.json`,
`.session_started`, `.claude.json`, `.claude/` — the exact inputs to the
adapter's `has_session` probe and its `--resume` read. Project files in the same
directory stay, which is what the `/new` reply promises; `outputs/`, `tasks/`
and the L37-retained `cel-briefs/` audit sidecars are untouched.

**Tenant-aware everywhere else.** The forge workspace, worker sessions, the
dialectic probe and all three roots in `session_timeout_sweep` now resolve
through the same SSOT.

**Failures are surfaced, not swallowed.** An unresolvable session directory or a
missing `context_budget` now lands in the reset's `failures` list instead of
returning a quiet `no`.

### Two further defects found while reviewing the session layer

* `SessionLifecycleManager` had **two** methods named `on_session_reset`. The
  second (added on the earlier, mistaken diagnosis) silently replaced the class's
  async event-bus handler — the one `startup()` subscribes and `on_event()`
  awaits. The per-chat hook is now `clear_session_cache()`, consistently across
  `base.py` and the seven subsystem implementations, and the event handler works
  again.
* That hook called `self.sessions.clear()`, wiping tracking state for **every**
  chat in the process, not just the one being reset. It now takes a `session_id`
  and removes exactly that entry.

---

## Verification

| Check | Result |
|---|---|
| `test_session_reset.py` (9 cases incl. timeout sweep, budget, audit chain) | 34 pass / 0 fail |
| `test_session_reset_adapter_path.py` (new — pins the real adapter path) | 16 pass / 0 fail |
| `js/test_session_reset_e2e_dispatch.js` (new — through the real dispatcher) | 16 pass / 0 fail |
| `test_in_chat_commands.js` | 87 pass / 0 fail |
| `test_session_timeout_compat.py` | 7 pass / 0 fail |
| `tests/unit/test_session_state_persistence_fix.py` | 23 pass / 0 fail |

The E2E test drives the real transport boundary: it calls
`in_chat_commands.dispatch({text: '/new', …})`, which spawns `session_reset.py`
exactly as `daemon.js` does. Its reply now reads `voice state cleared: yes`, and
the adapter's own next-turn probe reports no resumable state.

Before the fix the same test failed 9 assertions, `.main_session.json` in place.

### Live check against the reported chat

    Candidates /new now touches:
      exists=True   state=True   .../tenants/_default/sessions/voice/discord/1501315335750684803
      exists=False  state=False  .../tenants/_default/voice/sessions/discord/1501315335750684803
    forge session dir : .../tenants/_default/sessions/discord:1501315335750684803
    context_budget    : importable

---

## Known remaining gap

If a turn is still running when `/new` is typed, the adapter writes
`.main_session.json` back at the end of that turn and the reset is undone. This
is pre-existing and not what was reported (the reported failure was
deterministic), but it is a real hole. Closing it needs a reset epoch marker the
adapter checks before persisting a session id.

## Operational note

`session_reset.py` is spawned fresh per `/new`, so the fix is live without
restarting anything. The adapter's own `_reset_session_state()` delegation takes
effect when `corvin-voice-bridge-adapter.service` next restarts.

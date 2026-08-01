# Bridge freeze → auto-recovery fix (ANWEISUNG_CORVINOS_BRIDGE_FIX response)

**Status:** planning/reflection before execution, 2026-08-01
**Reported symptom:** Discord bridge process freezes (alive in memory, `/status`
port open but unresponsive), no auto-restart → users can't reach the bot until a
manual `Stop-Process` + `node daemon.js`. Reported on the WINDOWS instance
(PID 32452, port 7893, `C:\Users\sjurk`).

## Root cause (verified in code, not assumed)

- **Linux/macOS is already covered.** `operator/bridges/watchdog.sh` runs on a
  60-s systemd timer (`corvin-voice-bridge-watchdog.timer`), fetches each daemon's
  `/status`, and restarts on repeated HTTP failure (FAIL_THRESHOLD=3) OR on a
  wedge signal in the JSON body (`is_stalled` checks e.g. `poller_stalled_s`). An
  unresponsive `/status` (the reported symptom) → 3 fails → `systemctl restart`.
- **Windows is NOT covered.** `install.ps1` registers a Scheduled Task that
  restarts the process only on EXIT (crash-loop-guarded). A HANG (process alive,
  event loop stuck, `/status` dead) never exits, so the Scheduled Task never
  fires. There is no `/status` watchdog equivalent on Windows.
- The `settings.json` change was a coincident TRIGGER, not the mechanism:
  settings are read live via `currentSettings()`; the freeze is an event-loop
  stall whose RECOVERY is missing on Windows.
- The old `poller_stalled_s`-only blindspot (idle vs wedged) was already fixed
  (precheckFailSince tracking, incident 2026-07-27). So the gap is purely the
  missing cross-platform freeze-recovery.

## Why the document's snippets are rejected

- Δ1 (5-s `settings.json` → `process.exit`): restart-loop risk (the daemon itself
  writes settings.json on auto-ownership/debug edits → self-triggered exits) and
  redundant (live `currentSettings()`).
- Δ2 (new systemd unit): already exists (`corvin-voice-bridge-discord.service`).
- Δ4 (`seconds_since_last_event` > 600 = unhealthy): WRONG — conflates *idle*
  (nobody messaged the bot) with *frozen*. Would restart a healthy quiet bot in a
  loop. A liveness signal must be event-loop-driven, not traffic-driven.
- Δ5 (A2A self-register to assumed port 6789): fabricated endpoint; A2A endpoints
  register through the existing pairing flow, not a bridge POST.
- Δ3 (graceful shutdown) is the only partly-valid idea; SIGTERM handling already
  exists (graceful-drain, incident 2026-07-09).

## The correct fix (cross-platform, testable)

An **internal event-loop watchdog worker thread** that both platforms' existing
`restart-on-exit` supervisors can act on:

1. `shared/js/event-loop-watchdog.js` — the main thread posts a heartbeat to a
   `worker_threads` Worker every ~2 s. The worker (shares the process PID) checks
   the heartbeat age; if the main event loop has not beaten for > THRESHOLD
   (default 60 s, env-overridable) it force-kills the process
   (`process.kill(process.pid, 'SIGKILL')`) → the systemd `Restart=on-failure`
   (Linux) OR the Scheduled-Task restart-on-exit (Windows) relaunches it. A
   worker thread keeps ticking even when the MAIN loop is wedged, so it detects
   the exact freeze class the reporter hit, on BOTH platforms, with no external
   watchdog.
2. Threshold is generous (60 s) so a legitimate slow op never false-positives;
   any real >60 s main-thread block in a bridge is itself a bug.
3. Wire it into every bridge daemon at boot (discord first, then the shared set).
4. Keep the Linux `watchdog.sh` as the outer belt-and-suspenders (unchanged).

## Verification

- Unit/integration test: start the watchdog with a short threshold, block the
  main loop with a busy-wait > threshold, assert the process is killed. Run on
  Linux (where I can execute). Windows path is the SAME code + the existing
  Scheduled-Task supervisor → documented, not executed here (no Windows access).

## Then: adversarial review of the changes + surroundings, then PyPI release.

## Running log
- 2026-08-01 — root cause found + document snippets triaged; plan written.

- 2026-08-01 — BUILT + TESTED. shared/js/event-loop-watchdog.js (worker-thread
  freeze detector → SIGKILL → supervisor restart) wired into all 6 bridge daemons
  (discord/whatsapp/telegram/slack/email/teams). test_event_loop_watchdog.js:
  kills a frozen loop, SPARES an idle one (the load-bearing false-positive guard),
  honors opt-out — PASS. Registered in run-all-tests.sh. daemon-boot smoke: 3/0,
  no false-positive on normal boot.
- Adversarial review: R1 found a real bug in MY code — SIGTERM would exit CLEAN
  under systemd Restart=on-failure (no restart); fixed to SIGKILL (exit 137 =
  failure = restart; Windows both → TerminateProcess). R2/R3: unref correctness,
  no conflict with existing SIGTERM handlers / watchdog.sh, install.ps1 restarts
  on ANY exit. Honest limit: the Windows path is the SAME code + the existing
  Scheduled-Task supervisor, NOT executed here (no Windows access).

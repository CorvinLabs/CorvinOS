#!/usr/bin/env python3
"""bg_task_worker.py — detached durable runner for the `/task` command.

This is the messenger-origin PRODUCER the completion-notification backbone was
missing. The adapter's `/task <instruction>` handler spawns this script as a
DETACHED process (`start_new_session=True`), so the work outlives the
originating turn's one-shot `claude -p` subprocess — the exact thing an SDK
background agent could not do.

It runs the instruction through the SAME fully-gated engine path a normal turn
uses (`adapter.call_claude_streaming`, which enforces the budget / L34 / L35 /
CLAG / license gates), then records the result via `completion_notify.mark_done`.
The adapter main loop and the bg_monitor timer then deliver that completion to
the originating messenger (channel + chat_id) — exactly once.

Input: a single argv arg = path to a 0600 JSON spec FILE (NOT the JSON itself —
argv is world-readable in /proc/<pid>/cmdline, so the instruction/PII must not
live there):
    {"task_id", "instruction", "channel", "chat_key", "engine_chat_key"?,
     "profile"?, "msg_id"?, "want_voice"?, "sender"?}
The spec file is unlinked immediately after reading.

``chat_key`` is the ORIGIN chat (used only for the persona ``profile`` the adapter
already resolved, and for debug). ``engine_chat_key`` is a worker-private,
per-task key the adapter mints so the engine call runs in an ISOLATED session —
it never resumes or overwrites the operator's live chat transcript (ADR-0553 fix,
live-proven 2026-09-03). Absent (older spec) ⇒ derived here, never the origin.

A wall-clock deadline (CORVIN_BG_TASK_TIMEOUT, default 1800s) bounds the turn:
a wedged engine that streams/loops forever is stopped and reported, so a
detached worker can never run unbounded. Never raises to the OS — any failure
is recorded as a failed completion so the user is still notified.

SUPERVISED MODE (opt-in, `bridge_task_supervision`). When the adapter created a
`task_supervisor` run record for this task, three things change — and ONLY
then, so a flag-off install runs the exact code path it always did:

* liveness: a daemon thread stamps a heartbeat every `_HEARTBEAT_INTERVAL`
  seconds, so a worker that is alive-but-wedged is detectable at all (the pid
  check alone never was);
* progress: `on_status` is wired to `task_progress` instead of being None, so a
  long run reports in — rate-limited and coalesced, so it cannot spam;
* continuation: hitting the wall clock or crashing no longer ends the work. The
  attempt is recorded as RESUMABLE with its partial output and the supervisor
  launches the next attempt with a continuation prompt. `mark_done` is left to
  the supervisor, which calls it only once the retry/time budget is spent —
  otherwise a "the worker stopped" message would race the resume that fixes it.
"""
from __future__ import annotations

import json
import contextlib
import os
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

# How often a supervised worker stamps liveness. Must stay comfortably below
# task_supervisor.SUP_HEARTBEAT_STALE (default 600 s) or a healthy worker would
# be declared wedged and restarted under itself.
_HEARTBEAT_INTERVAL = float(os.environ.get("CORVIN_BG_TASK_HEARTBEAT", "30"))


def _load_cn():
    sys.path.insert(0, str(HERE))
    import completion_notify as cn  # type: ignore

    return cn


def _load_supervisor():
    """Import task_supervisor, or None when it is unavailable.

    Never fatal: a worker that cannot load the supervisor simply runs the
    pre-supervision path (single attempt, mark_done on failure).
    """
    try:
        sys.path.insert(0, str(HERE))
        import task_supervisor as sup  # type: ignore

        return sup
    except Exception:  # noqa: BLE001
        return None


def _load_progress():
    """Import task_progress, or None when it is unavailable."""
    try:
        sys.path.insert(0, str(HERE))
        import task_progress as tp  # type: ignore

        return tp
    except Exception:  # noqa: BLE001
        return None


def _maybe_voice(cn, task_id: str, *, want_voice: bool, text: str) -> bool:
    """Best-effort: synthesize a spoken SUMMARY of *text* and stamp its
    voice_path onto the completion record — BEFORE mark_done flips it to ready.

    ADR-0554 Phase 0 (approach (a)): the summary is produced HERE, in the
    detached worker (which already imports ``adapter`` for the engine call), so
    ``completion_notify`` stays pure-stdlib and delivery is poller-INDEPENDENT
    (deliver_ready attaches the stored path with no callback). Returns True when
    a voice_path was attached.

    Never raises and never touches the record's text: a TTS failure (no engine,
    empty summary, exception) simply degrades to text-only delivery — voice is
    an enhancement, never a delivery precondition. A ``<voice>…</voice>``
    override in *text* is honoured (same mechanism the live turn uses).
    """
    if not want_voice or not (text or "").strip():
        return False
    # Same TTS test hook the live turn honours (adapter._synthesize_voice_for_turn):
    # decouples tests from real OpenAI/edge/Piper latency. Harmless in production.
    if os.environ.get("ADAPTER_DISABLE_VOICE") == "1":
        return False
    try:
        sys.path.insert(0, str(HERE))
        import adapter as _ad  # type: ignore  # cached after main()'s own import

        try:
            from voice_tag import extract_voice_override as _evo  # type: ignore
            visible, override = _evo(str(text))
        except Exception:  # noqa: BLE001 — override is optional, never fatal
            visible, override = str(text), None
        spoken = _ad.build_voice_summary(visible, override=override)
        if not spoken:
            return False
        try:
            lang = _ad._resolve_voice_output_language(spoken) or "de"
        except Exception:  # noqa: BLE001
            lang = "de"
        voice_path = _ad.synthesize_voice_note(spoken, lang=lang)
        if voice_path:
            return bool(cn.attach_voice(task_id, str(voice_path)))
        return False
    except Exception as e:  # noqa: BLE001 — voice is an enhancement, never a blocker
        print(f"bg_task_worker: voice synth failed: {e}", file=sys.stderr)
        return False


def main() -> int:
    if len(sys.argv) < 2:
        print("bg_task_worker: missing spec-file argument", file=sys.stderr)
        return 2
    spec_path = Path(sys.argv[1])
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError, OSError) as e:
        print(f"bg_task_worker: bad spec: {e}", file=sys.stderr)
        return 2
    finally:
        # Drop the 0600 spec file as soon as it is read, crash or not.
        try:
            spec_path.unlink()
        except OSError:
            pass

    task_id = spec.get("task_id") or ""
    instruction = spec.get("instruction") or ""
    channel = spec.get("channel") or "discord"
    chat_key = spec.get("chat_key") or "anon"
    # Session ISOLATION (ADR-0553 fix, live-proven 2026-09-03). The engine call
    # keys BOTH its `--resume` read and its session-id write off
    # `_session_dir(channel, chat_key)`. Running under the ORIGIN chat_key made
    # the detached worker resume + overwrite the operator's LIVE Discord session
    # (transcript pollution + next-turn collision). Use the isolated per-task key
    # the adapter minted; fall back to deriving it here so an older 0600 spec (no
    # `engine_chat_key`) is still isolated, never resuming the live chat.
    engine_chat_key = spec.get("engine_chat_key") or (
        f"bgtask::{chat_key}::{task_id}" if task_id else f"bgtask::{chat_key}"
    )

    cn = _load_cn()
    if not task_id or not instruction:
        if task_id:
            cn.mark_done(task_id, text="background task had no instruction.",
                         ok=False)
        return 2

    # Claim the record with THIS process's pid so the completion queue can reap
    # it into a failed notification if we are hard-killed (SIGKILL/OOM/reboot)
    # before reaching mark_done — otherwise the pending record would wedge the
    # user's /task concurrency slot for days.
    try:
        cn.claim(task_id)
    except Exception:  # noqa: BLE001 — claim is best-effort
        pass

    # Supervised mode is decided by the PRESENCE of a run record, which the
    # adapter creates only when `bridge_task_supervision` is on. No record =
    # the pre-feature path, unchanged.
    sup = _load_supervisor()
    run = None
    if sup is not None:
        try:
            run = sup.get_run(task_id)
        except Exception:  # noqa: BLE001
            run = None
    supervised = bool(run) and bool(run.get("supervise", True))
    want_progress = bool(run) and bool(run.get("progress", False))

    if run is not None:
        try:
            sup.attempt_started(task_id)
        except Exception:  # noqa: BLE001
            pass

    # Liveness heartbeat. A pid check alone cannot see a worker that is alive
    # but wedged (the engine streaming forever, a deadlocked tool) — which was
    # the failure nothing in the system detected. A daemon thread means the
    # stamp keeps ticking regardless of what the main thread is blocked on.
    stop_hb = threading.Event()

    def _heartbeat_loop() -> None:
        while not stop_hb.wait(_HEARTBEAT_INTERVAL):
            try:
                sup.touch_heartbeat(task_id)
            except Exception:  # noqa: BLE001
                pass

    hb_thread = None
    if run is not None:
        hb_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
        hb_thread.start()

    # Progress relay. The pre-feature worker passed on_status=None ("no live
    # progress spam"), which is why a long run reported nothing at all. Route
    # it through task_progress instead, which coalesces and rate-limits, so
    # "some signal" cannot become "spam".
    tp = _load_progress() if want_progress else None
    on_status = None
    if tp is not None:
        # The adapter calls `on_status(text, tool_name=...)`. This function used to
        # take ONE argument, so every call raised TypeError inside the adapter (logged
        # as "alive heartbeat failed" / "on_status callback failed") and a /task run
        # relayed NOTHING — while the worker E2E, whose stub adapter called it with one
        # argument, stayed green. Accept the real contract.
        def on_status(status_text: str, tool_name: str | None = None, **_kw) -> None:  # noqa: ANN001
            try:
                # A background child starting/finishing is a state CHANGE: it must not be
                # swallowed by the routine-progress coalescing window (ADR-2236 D7).
                tp.emit(task_id, str(status_text)[:300], kind="progress",
                        force=(tool_name == "_bgchild"))
            except Exception:  # noqa: BLE001 — a status line never kills the work
                pass

    # ADR-2236: background children of this worker's claude process. `children_state`
    # is read by the deadline thread below; the observer keeps the supervisor's record
    # of what a resumed attempt will have lost.
    children_state = {"open": 0}
    interim_emitted = {"n": 0}

    def _observe_children(tracker) -> None:  # noqa: ANN001
        children_state["open"] = len(tracker.open_children)
        if run is not None:
            try:
                import bg_scope as _bgs  # type: ignore
                sup.write_children(task_id, _bgs.children_snapshot(tracker))
            except Exception:  # noqa: BLE001
                pass

    scope_done: dict = {"children": [], "end_reason": None}

    def _relay_interim(text: str, info: dict) -> None:
        """A wake-up result while a child runs is content the user wants to read in full."""
        if info.get("cls") == "scope_done":
            scope_done["children"] = list(info.get("children") or [])
            scope_done["end_reason"] = info.get("end_reason")
            return
        outbox = spec.get("outbox_dir")
        if not outbox or not str(text).strip():
            return
        try:
            import bg_scope as _bgs  # type: ignore
            note = ("" if info.get("cls") == "milestone"
                    else _bgs.interim_suffix(int(info.get("children_open") or 0)))
            body = str(text).strip()[:1800]
            if cn.send_interim(task_id, f"{body}\n\n{note}" if note else body, outbox):
                interim_emitted["n"] += 1
        except Exception:  # noqa: BLE001
            pass

    ok = True
    text = ""
    resumable = False
    # Structured failure signal for classify_failure() (ADR-2107/ADR-2103).
    # None of the gated engine paths (budget/L34/L35/L44) raise here — a
    # refusal comes back as TEXT with ok=True (ADR-0551), so this worker
    # never sees a structured engine_response; only a real Python exception
    # or the wall-clock watchdog sets exit_error.
    exit_error: str | None = None
    try:
        sys.path.insert(0, str(HERE))
        import adapter  # type: ignore  # heavy but self-contained

        # Wall-clock watchdog: on deadline, SIGTERM this worker's own engine
        # subprocess (adapter._cancel_chat operates on THIS process's registry),
        # which unblocks call_claude_streaming with a cancellation string.
        try:
            timeout = float(os.environ.get("CORVIN_BG_TASK_TIMEOUT", "1800"))
        except ValueError:
            timeout = 1800.0
        timed_out = {"v": False}
        done_evt = threading.Event()

        def _watchdog() -> None:
            timed_out["v"] = True
            try:
                # Cancel the ISOLATED engine session, not the origin chat — the
                # origin chat has no live engine in this detached process, and
                # cancelling by origin key would be a no-op that leaves the real
                # (isolated) engine subprocess running past the deadline.
                adapter._cancel_chat(engine_chat_key)
            except Exception:  # noqa: BLE001
                pass

        def _deadline_loop() -> None:
            # The wall clock bounds ONE attempt of a stuck turn. Time spent while a background
            # child is open is not counted at all: the CLI is legitimately silent, the adapter
            # bounds the child itself (CORVIN_BG_CHILD_MAX), and merely POSTPONING the deadline
            # would fire it the instant the child ends — long past the limit, while the wake-up
            # turn that reports the result is still running. Before, a 40 min child was cut at
            # 30 min and the supervisor "resumed" by starting it again.
            active = 0.0
            last = time.monotonic()
            while not done_evt.wait(2.0):
                now = time.monotonic()
                if children_state["open"] == 0:
                    active += now - last
                last = now
                if active >= timeout:
                    _watchdog()
                    return

        timer = threading.Thread(target=_deadline_loop, daemon=True)
        timer.start()
        try:
            # getattr: an adapter without the ADR-2236 hooks (a test stub) just runs unobserved.
            _obs = getattr(adapter, "bg_scope_observer", None)
            _snk = getattr(adapter, "bg_interim_sink", None)
            with (_obs(_observe_children) if _obs else contextlib.nullcontext()), \
                    (_snk(_relay_interim) if _snk else contextlib.nullcontext()):
                text = adapter.call_claude_streaming(
                    prompt=instruction,
                    channel=channel,
                    chat_key=engine_chat_key,
                    # Supervised runs relay live status through task_progress;
                    # unsupervised ones keep the original silence (on_status=None).
                    on_status=on_status,
                    profile=spec.get("profile"),
                    msg_id=spec.get("msg_id"),
                    sender=str(spec.get("sender") or ""),
                )
        finally:
            done_evt.set()
        if timed_out["v"]:
            ok = False
            # Under supervision a timeout is a CONTINUATION point, not a
            # verdict: the partial output becomes the carry for the next
            # attempt. Unsupervised it stays exactly what it always was.
            resumable = supervised
            exit_error = "WorkerTimeout"
            text = (f"background task timed out after {int(timeout)}s and was "
                    f"stopped.\n\n{text}".strip())
    except Exception as e:  # noqa: BLE001 — never let the worker die silently
        ok = False
        resumable = supervised
        exit_error = type(e).__name__
        text = f"background task crashed: {type(e).__name__}: {e}"
        print(f"bg_task_worker: {text}", file=sys.stderr)
    finally:
        stop_hb.set()

    if run is not None:
        try:
            # worker_pid=os.getpid(): this process is reporting on ITSELF, so
            # it is by construction alive here — the "process dead" TRANSIENT
            # path is never classified from this call site; that signal comes
            # from the supervisor's own zombie/heartbeat check in supervise(),
            # a different call site that observes a worker from the outside.
            # engine_response stays None — see the comment at exit_error's
            # declaration above for why no structured engine signal exists
            # at this call site today (ADR-2107 Proof section item still open).
            sup.attempt_finished(task_id, ok=ok, summary=(text or ""),
                                 resumable=resumable,
                                 worker_pid=os.getpid(),
                                 exit_error=exit_error,
                                 engine_response=None)
        except Exception:  # noqa: BLE001
            pass

    if resumable:
        # Deliberately NO mark_done: the supervisor owns the verdict now and
        # will resume. Reporting "it stopped" here would race — and usually
        # beat — the resume that fixes it, so the user would be told the task
        # failed while it was in fact still running.
        return 0

    # ADR-0554 Phase 0 (approach (a)): synthesize + attach a spoken SUMMARY
    # BEFORE mark_done, so the ready record already carries voice_path and any
    # poller delivers it. Gated by want_voice (set at register() only when the
    # proactive_voice_completion flag AND the user's voice preference allow it);
    # best-effort — a failure degrades to text-only, never blocks the text.
    _voice_text = text or ""
    if scope_done["children"]:
        try:
            import bg_scope as _bgs  # type: ignore
            _facts = _bgs.voice_facts(scope_done["children"], scope_done["end_reason"])
            if _facts:   # ADR-2236 D8: a badly ended scope is said aloud; the written text is unchanged
                _voice_text = f"{_voice_text}\n\n{_facts}"
        except Exception:  # noqa: BLE001
            pass
    _maybe_voice(cn, task_id, want_voice=bool(spec.get("want_voice")),
                 text=_voice_text)

    # A gate refusal comes back as text (ok stays True) — the user still gets it.
    cn.mark_done(task_id, text=(text or "(no output)"), ok=ok)

    # BG-NOTIFICATION-FIX: Immediately deliver the completion to the outbox so the
    # Discord daemon (and other messengers) send the second message within seconds,
    # not waiting for bg_monitor's 60s timer. Idempotent — O_EXCL locks prevent
    # double-delivery even if adapter or bg_monitor also call deliver_ready().
    # Failure is non-fatal; bg_monitor will retry.
    _outbox_dir = spec.get("outbox_dir")
    if _outbox_dir:
        try:
            _delivered = cn.deliver_ready(_outbox_dir)
            if _delivered:
                print(f"bg_task_worker: delivered {_delivered} completion(s) immediately",
                      file=sys.stderr)
        except Exception as _de:  # noqa: BLE001
            # Non-fatal: bg_monitor will retry after 60s
            print(f"bg_task_worker: deliver_ready() failed (bg_monitor will retry): {_de}",
                  file=sys.stderr)

    if tp is not None:
        try:
            tp.finish(task_id)
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

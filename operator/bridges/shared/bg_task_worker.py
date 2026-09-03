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
import os
import sys
import threading
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
        def on_status(status_text: str) -> None:  # noqa: ANN001 — adapter contract
            try:
                tp.emit(task_id, str(status_text)[:300], kind="progress")
            except Exception:  # noqa: BLE001 — a status line never kills the work
                pass

    ok = True
    text = ""
    resumable = False
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

        timer = threading.Timer(timeout, _watchdog)
        timer.daemon = True
        timer.start()
        try:
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
            timer.cancel()
        if timed_out["v"]:
            ok = False
            # Under supervision a timeout is a CONTINUATION point, not a
            # verdict: the partial output becomes the carry for the next
            # attempt. Unsupervised it stays exactly what it always was.
            resumable = supervised
            text = (f"background task timed out after {int(timeout)}s and was "
                    f"stopped.\n\n{text}".strip())
    except Exception as e:  # noqa: BLE001 — never let the worker die silently
        ok = False
        resumable = supervised
        text = f"background task crashed: {type(e).__name__}: {e}"
        print(f"bg_task_worker: {text}", file=sys.stderr)
    finally:
        stop_hb.set()

    if run is not None:
        try:
            sup.attempt_finished(task_id, ok=ok, summary=(text or ""),
                                 resumable=resumable)
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
    _maybe_voice(cn, task_id, want_voice=bool(spec.get("want_voice")),
                 text=(text or ""))

    # A gate refusal comes back as text (ok stays True) — the user still gets it.
    cn.mark_done(task_id, text=(text or "(no output)"), ok=ok)
    if tp is not None:
        try:
            tp.finish(task_id)
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

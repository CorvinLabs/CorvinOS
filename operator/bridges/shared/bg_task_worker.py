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
    {"task_id", "instruction", "channel", "chat_key", "profile"?, "msg_id"?}
The spec file is unlinked immediately after reading.

A wall-clock deadline (CORVIN_BG_TASK_TIMEOUT, default 1800s) bounds the turn:
a wedged engine that streams/loops forever is stopped and reported, so a
detached worker can never run unbounded. Never raises to the OS — any failure
is recorded as a failed completion so the user is still notified.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load_cn():
    sys.path.insert(0, str(HERE))
    import completion_notify as cn  # type: ignore

    return cn


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

    ok = True
    text = ""
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
                adapter._cancel_chat(chat_key)
            except Exception:  # noqa: BLE001
                pass

        timer = threading.Timer(timeout, _watchdog)
        timer.daemon = True
        timer.start()
        try:
            text = adapter.call_claude_streaming(
                prompt=instruction,
                channel=channel,
                chat_key=chat_key,
                on_status=None,  # no live progress spam — only the final notification
                profile=spec.get("profile"),
                msg_id=spec.get("msg_id"),
                sender=str(spec.get("sender") or ""),
            )
        finally:
            timer.cancel()
        if timed_out["v"]:
            ok = False
            text = (f"background task timed out after {int(timeout)}s and was "
                    f"stopped.\n\n{text}".strip())
    except Exception as e:  # noqa: BLE001 — never let the worker die silently
        ok = False
        text = f"background task crashed: {type(e).__name__}: {e}"
        print(f"bg_task_worker: {text}", file=sys.stderr)

    # A gate refusal comes back as text (ok stays True) — the user still gets it.
    cn.mark_done(task_id, text=(text or "(no output)"), ok=ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())

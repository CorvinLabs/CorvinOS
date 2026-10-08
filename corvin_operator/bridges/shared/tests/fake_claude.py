#!/usr/bin/env python3
"""Replay a captured `claude` stream-json fixture as a REAL subprocess.

A stand-in for the claude CLI used by the background-scope tests (ADR-2236,
PLAN-0938). It is selected through ``CORVIN_CLAUDE_BIN`` so the engine's real
spawn path (Popen, process group, stdin/stdout pipes, close_stdin, kill) is
exercised — unlike ``ADAPTER_FAKE_CLAUDE``, which bypasses it.

Behaviour, like the real CLI measured on 2.1.294:
  * the prompt arrives on stdin (``--input-format stream-json``); one line is read
  * lines of the fixture are replayed at ``_t / FAKE_CLAUDE_SPEEDUP`` seconds
  * the process stays alive until the last fixture line (``_eof``) even after
    the adapter closes stdin — children keep it alive, just as in reality
  * SIGTERM / SIGKILL end it immediately (default dispositions)

Environment:
  FAKE_CLAUDE_FIXTURE   path to fixtures/bgscope/<name>.jsonl      (required)
  FAKE_CLAUDE_SPEEDUP   float, default 1.0 (4 = four times faster)
  FAKE_CLAUDE_SILENT_S  extra REAL seconds of silence inserted right after the
                        first ``result`` (simulates a quiet child)
  FAKE_CLAUDE_PIDFILE   write this process' pid here (liveness assertions)
  FAKE_CLAUDE_SPAWNLOG  append one line per spawn here (re-run detection)
  FAKE_CLAUDE_CHILD_PIDFILE  start a REAL background child (`sleep 300`) in this process'
                        group and write its pid here, so "the child is gone" is a fact about a
                        process and not about a mock. SIGTERM ends the child and then the CLI —
                        what the real CLI does (measured 2026-10-08: SIGKILL orphans the child,
                        SIGTERM does not).
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time


def main() -> int:
    argv = sys.argv[1:]
    log = os.environ.get("FAKE_CLAUDE_SPAWNLOG")
    if log:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(f"{os.getpid()} {' '.join(argv)[:200]}\n")
    pidfile = os.environ.get("FAKE_CLAUDE_PIDFILE")
    if pidfile:
        with open(pidfile, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))

    child = None
    childfile = os.environ.get("FAKE_CLAUDE_CHILD_PIDFILE")
    if childfile:
        child = subprocess.Popen(["sleep", "300"])
        with open(childfile, "w", encoding="utf-8") as fh:
            fh.write(str(child.pid))

        def _on_term(_sig, _frm):  # what the real CLI does on SIGTERM: end its children first
            try:
                child.terminate()
            finally:
                os._exit(143)

        signal.signal(signal.SIGTERM, _on_term)

    if "stream-json" in argv and "--input-format" in argv:
        sys.stdin.readline()  # the initial user message

    path = os.environ["FAKE_CLAUDE_FIXTURE"]
    speedup = float(os.environ.get("FAKE_CLAUDE_SPEEDUP", "1") or "1")
    silent = float(os.environ.get("FAKE_CLAUDE_SILENT_S", "0") or "0")

    with open(path, encoding="utf-8") as fh:
        events = [json.loads(line) for line in fh if line.strip()]

    t0 = time.time()
    extra = 0.0
    seen_first_result = False
    rc = 0
    for ev in events:
        due = t0 + ev.get("_t", 0.0) / speedup + extra
        delay = due - time.time()
        if delay > 0:
            time.sleep(delay)
        if ev.get("type") == "_eof":
            rc = int(ev.get("rc") or 0)
            if child is not None and child.poll() is None:
                child.terminate()
            break
        out = {k: v for k, v in ev.items() if k != "_t"}
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        if ev.get("type") == "result" and not seen_first_result:
            seen_first_result = True
            extra += silent
    return rc


if __name__ == "__main__":
    sys.exit(main())

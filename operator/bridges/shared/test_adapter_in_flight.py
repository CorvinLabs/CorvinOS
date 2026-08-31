#!/usr/bin/env python3
"""E2E reproduction test for the in-flight duplicate-submit window
(incident 2026-07-10).

Scenario: a turn runs longer than ADAPTER_IN_FLIGHT_TTL. The periodic
cleanup used to drop the still-running msg_id from _in_flight on wall-clock
age alone; the next poll tick then re-submitted the same inbox file and a
duplicate runner raced the original — crashing with FileNotFoundError at
turn end (the two "runner error … No such file" lines in the incident
journal) or, worse, re-executing the whole instruction.

The fix ties cleanup eligibility to the runner Future being done. This test
spawns the real adapter with a fake engine whose turn (4 s) far exceeds
IN_FLIGHT_TTL (1 s) and a fast cleanup cadence (0.5 s), then asserts the
turn ran exactly once and nothing was quarantined.

Run:
    python3 operator/bridges/shared/test_adapter_in_flight.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

SANDBOX   = Path(os.environ.get("ADAPTER_TEST_SANDBOX", "/tmp/adapter-inflight-sandbox"))
INBOX     = SANDBOX / "inbox"
OUTBOX    = SANDBOX / "outbox"
PROCESSED = SANDBOX / "processed"
LOG_FILE  = SANDBOX / "adapter.log"

ADAPTER = Path(__file__).resolve().parent / "adapter.py"

FAKE_DELAY   = 4.0   # turn duration — far above the in-flight TTL below
IN_FLIGHT_TTL = 1.0
CLEANUP_EVERY = 0.5
WAIT_TIMEOUT  = FAKE_DELAY + 20.0


def main() -> int:
    # Tripwire against the live-state-wipe class (2026-07-08 incident): only
    # rmtree a directory we created ourselves (marker file present), and only
    # under the system temp prefix. A mispointed ADAPTER_TEST_SANDBOX must
    # never delete arbitrary trees.
    import tempfile
    _tmp_prefix = Path(tempfile.gettempdir()).resolve()
    _sandbox_res = SANDBOX.resolve()
    if SANDBOX.exists():
        if (_tmp_prefix not in _sandbox_res.parents
                or not (SANDBOX / ".adapter-test-sandbox").exists()):
            print(f"REFUSING to delete {SANDBOX} — not a marker-tagged temp "
                  f"sandbox (ADAPTER_TEST_SANDBOX mispointed?)")
            return 1
        shutil.rmtree(SANDBOX)
    for d in (INBOX, OUTBOX, PROCESSED):
        d.mkdir(parents=True, exist_ok=True)
    (SANDBOX / ".adapter-test-sandbox").touch()

    env = os.environ.copy()
    # Never inherit a pinned live home (service.env pins CORVIN_HOME) — the
    # adapter's sandbox redirect only applies when these are unset.
    env.pop("CORVIN_HOME", None)
    env.pop("VOICE_AUDIT_PATH", None)
    env["ADAPTER_INBOX"]            = str(INBOX)
    env["ADAPTER_OUTBOX"]           = str(OUTBOX)
    env["ADAPTER_PROCESSED"]        = str(PROCESSED)
    env["ADAPTER_FAKE_CLAUDE"]      = "1"
    env["ADAPTER_FAKE_DELAY"]       = str(FAKE_DELAY)
    env["ADAPTER_POLL_INTERVAL"]    = "0.1"
    env["ADAPTER_IN_FLIGHT_TTL"]    = str(IN_FLIGHT_TTL)
    env["ADAPTER_CLEANUP_INTERVAL"] = str(CLEANUP_EVERY)
    env["ADAPTER_DISABLE_VOICE"]    = "1"
    env["ADAPTER_ROUTING_MODE"]     = "off"
    # Isolate channel settings: without this the adapter reads the real
    # bridges/discord/settings.json whitelist and drops the test sender.
    bridges_dir = SANDBOX / "bridges"
    (bridges_dir / "discord").mkdir(parents=True, exist_ok=True)
    env["ADAPTER_BRIDGES_DIR"]      = str(bridges_dir)

    proc = subprocess.Popen(
        ["python3", str(ADAPTER)],
        env=env,
        stdout=open(LOG_FILE, "w"),
        stderr=subprocess.STDOUT,
    )
    failures = 0

    def check(cond: bool, msg: str) -> None:
        nonlocal failures
        print(("  ok  - " if cond else "  FAIL - ") + msg)
        if not cond:
            failures += 1

    try:
        time.sleep(0.4)
        (INBOX / "longturn.json").write_text(json.dumps({
            "id": "longturn", "channel": "discord", "chat_id": "chatL",
            "from": "sender-L", "text": "simulate a long-running turn",
        }))

        deadline = time.monotonic() + WAIT_TIMEOUT
        while time.monotonic() < deadline:
            if list(PROCESSED.glob("longturn.json")):
                break
            time.sleep(0.1)
        # Give a would-be duplicate runner time to crash/act after the
        # original moved the file (the incident errors fired at turn end).
        time.sleep(2.0)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    log_text = LOG_FILE.read_text() if LOG_FILE.exists() else ""

    print("test_adapter_in_flight")
    check((PROCESSED / "longturn.json").exists(),
          "turn completed → inbox file moved to processed/")
    n_processing = log_text.count("processing longturn")
    check(n_processing == 1,
          f"turn executed exactly once (got {n_processing} 'processing' lines)")
    check("runner error for longturn" not in log_text,
          "no duplicate-runner FileNotFoundError")
    poison = list((PROCESSED / "poison").glob("*")) if (PROCESSED / "poison").exists() else []
    check(not poison, f"nothing quarantined as poison (got {[p.name for p in poison]})")

    if failures:
        print(f"FAILED — {failures} assertion(s) failed. Log: {LOG_FILE}")
        return 1
    print("PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

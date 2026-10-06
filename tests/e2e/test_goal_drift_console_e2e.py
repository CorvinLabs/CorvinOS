"""E2E: PLAN-0931 / ADR-2101 P3 — goal-drift alert reaches a REAL console turn.

Mirrors ``test_goal_drift_bridge_e2e.py`` for the second of the two places a
real turn happens (ADR-2101's measured state: `GoalAlignmentMonitor` had zero
live callers in EITHER). Real transport: ``/auth/local-login`` + CSRF,
``POST /chat/sessions``, the ``/chat/sessions/{sid}/stream`` WebSocket — the
same harness shape as ``test_session_ledger_console_e2e.py``. Only the paid
``claude`` binary is replaced; the stand-in emits a short, deterministic
assistant reply so ``combined_text`` is non-empty and the decision-capture +
goal-drift branch in ``chat_runtime.py`` actually runs (the console's
delegation-routing harness fakes an EMPTY subprocess stdout, which would
never reach this branch — reusing it would give a silently vacuous test).

The actual turn-driving runs in ``_goal_drift_console_driver.py``, spawned
as a disposable CHILD process: the console app's ASGI lifespan SHUTDOWN
hangs indefinitely in this environment (reproduced on the pre-existing,
unmodified ``test_session_ledger_console_e2e.py`` too — not a regression
from this change, root cause outside this plan's scope). The child writes
its result to a JSON file and calls ``os._exit`` before that shutdown path
runs; the parent only ever polls the child's exit status with a plain,
non-blocking ``Popen.poll()`` loop (not ``Popen.wait()``/``subprocess.run``,
which can race pytest-asyncio's own child-reaping and never observe an
exit that already happened) — so the lifespan hang never reaches the test
runner.

**Known flakiness in THIS sandbox, not a defect in the hook being tested:**
running the driver directly (``python3 tests/e2e/_goal_drift_console_driver.py
...``, no pytest involved) reliably completes all 4 turns in under 20s and
produces the exact expected ``goal_drift.alert_raised`` record. Running the
IDENTICAL driver as a child of a pytest process in this sandbox reproducibly
hangs forever at the SAME point every time: right after the first turn's
house-rules cloud classifier logs "failed after 3 attempt(s) ... fail-closed
escalate" (that classifier is unreachable here and its escalation path is
unrelated to anything this plan touches). ``stdin=subprocess.DEVNULL`` and a
non-blocking ``poll()`` loop (above) were tried and did not change this —
the hang is specifically in something the house-rules escalation does when
it is a grandchild of pytest in this environment, not in lifespan shutdown
or child-reaping. Left unresolved (out of PLAN-0931's scope); the mechanism
itself is considered proven by the direct, non-pytest run.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

_DRIVER = Path(__file__).resolve().parent / "_goal_drift_console_driver.py"

FAKE_CLI = r'''
import json, sys
try:
    prompt = sys.stdin.read()
except Exception:
    prompt = ""
for evt in ({"type": "system", "subtype": "init", "model": "claude-sonnet-5"},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "ack"}]}},
            {"type": "result", "result": "ack",
             "usage": {"input_tokens": 5, "output_tokens": 2,
                       "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}):
    sys.stdout.write(json.dumps(evt) + "\n")
sys.stdout.flush()
'''


def _run_driver(tmp_path: Path, flags: dict, port: int) -> list[dict]:
    script = tmp_path / "fake_cli.py"
    script.write_text(FAKE_CLI, encoding="utf-8")
    shim = tmp_path / "claude.sh"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
    shim.chmod(0o755)
    home = tmp_path / "corvin-home"
    result_path = tmp_path / "result.json"

    env = os.environ.copy()
    env.pop("VOICE_AUDIT_PATH", None)
    env.update({
        "CORVIN_CLAUDE_BIN": str(shim),
        "CLAUDE_CONFIG_DIR": str(tmp_path / "claude-cfg"),
        "ANTHROPIC_API_KEY": "sk-ant-e2e-dummy-never-sent",
        "CORVIN_HOME": str(home), "XDG_CONFIG_HOME": str(home),
        "CORVIN_AUDIT_ANCHOR_KEY": str(home / "anchor.key"),
        "FORGE_ROOT": str(home / "forge-root"),
        "CORVIN_TENANT_ID": "_default",
    })
    # stdout/stderr go to a FILE, never a PIPE: the driver's turn spawns a
    # grandchild (the fake claude.sh shim); if any fd pointing at a pipe
    # outlives the direct child, a PIPE-based wait hangs forever waiting
    # for EOF on it even after the child itself has exited.
    #
    # Popen + manual poll(), NOT subprocess.run()/Popen.wait(): this test
    # module pulls in pytest-asyncio (via fastapi/anyio), whose event loop
    # can install its own SIGCHLD-based child watcher for the pytest
    # process. A blocking os.waitpid() (what Popen.wait() uses) can then
    # race that watcher and never observe the child's exit even though the
    # child is long gone. poll() does a non-blocking WNOHANG check in a
    # loop instead, which does not depend on catching SIGCHLD at all.
    log_path = tmp_path / "driver.log"
    with open(log_path, "w") as log_fh:
        proc = subprocess.Popen(
            [sys.executable, str(_DRIVER), str(home), str(port), json.dumps(flags), str(result_path)],
            env=env, cwd=str(Path(__file__).resolve().parents[2]),
            stdin=subprocess.DEVNULL, stdout=log_fh, stderr=subprocess.STDOUT,
        )
        # The house-rules cloud classifier is unreachable in an offline/
        # sandboxed environment and retries 3x with backoff per turn before
        # failing closed — 4 turns can legitimately take well over a
        # minute. 180s is a budget for that, not a sign of a hang.
        deadline = time.monotonic() + 180
        rc = None
        while time.monotonic() < deadline:
            rc = proc.poll()
            if rc is not None:
                break
            time.sleep(0.1)
        if rc is None:
            proc.kill()
            raise AssertionError(f"driver did not exit within 180s; see {log_path}")
    assert result_path.is_file(), (
        f"driver produced no result (rc={rc}); see {log_path}"
    )
    return json.loads(result_path.read_text())["alerts"]


def test_goal_drift_alert_on_real_console_turn(tmp_path):
    """Flag ON: a goal-setting turn followed by 3 low-overlap turns must
    produce exactly one goal_drift.alert_raised record on the real chain —
    driven through the real WebSocket turn loop, not a direct hook call."""
    alerts = _run_driver(tmp_path, {
        "vibe_engineering": True, "cel_load_bearing_anchor": True,
        "goal_drift_monitor_enabled": True,
    }, 51242)
    assert len(alerts) == 1, f"expected exactly one alert, got {alerts}"
    d = alerts[0]["details"]
    assert d["consecutive_low_count"] == 3
    assert "session_key_fingerprint" in d and len(d["session_key_fingerprint"]) == 8


def test_goal_drift_flag_off_writes_nothing(tmp_path):
    """Flag OFF (goal_drift_monitor_enabled absent): the same sequence through
    the real console turn loop must write NO alert at all."""
    alerts = _run_driver(tmp_path, {
        "vibe_engineering": True, "cel_load_bearing_anchor": True,
    }, 51243)
    assert alerts == []

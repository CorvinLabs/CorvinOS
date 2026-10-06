"""E2E: PLAN-0932 / ADR-2101 P4 task half — SessionContextBridge reaches a
REAL console turn through a REAL entry point.

ADR-2101's 2026-10-06 amendment named the exact reason this was NOT wired
the first time: ``WebChatSession.task_id`` (ADR-0649) had zero production
setters anywhere in this codebase, so wiring ``maybe_snapshot_context`` /
``maybe_restore_context`` (``core/session_manager/context_bridge.py``) into
``delete_session``/``reset_claude_session_state`` would have produced
permanently-dead call sites — exactly the anti-pattern this whole exercise
exists to stop.

This test proves the actual fix: ``/resume <task_id>``
(``routes/chat.py``'s websocket loop, intercepted before the slash-command
dispatcher) is now a REAL setter, and the snapshot/restore round-trip runs
through the REAL transport boundary — ``POST /chat/sessions``, the
``/chat/sessions/{sid}/stream`` WebSocket, and a real client-initiated
disconnect — not a direct call into ``context_bridge.py``.

Unlike ``test_goal_drift_console_e2e.py``, ``/resume`` is intercepted
BEFORE any engine spawn or house-rules classification (see its handler in
routes/chat.py), so this test needs no fake ``claude`` binary and does not
hit that other test's sandbox-specific classifier-escalation hang — it
should complete in a few seconds.

Harness shape (local-login + CSRF + WebSocket) copied from
``test_goal_drift_console_e2e.py``; same disposable-child-process pattern
for the same reason (ASGI lifespan shutdown hangs in this sandbox — see
that module's docstring).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

_DRIVER = Path(__file__).resolve().parent / "_context_bridge_console_driver.py"


def _run_driver(tmp_path: Path, flags: dict, port: int) -> dict:
    home = tmp_path / "corvin-home"
    result_path = tmp_path / "result.json"

    env = os.environ.copy()
    env.pop("VOICE_AUDIT_PATH", None)
    env.update({
        "CORVIN_HOME": str(home), "XDG_CONFIG_HOME": str(home),
        "CORVIN_AUDIT_ANCHOR_KEY": str(home / "anchor.key"),
        "FORGE_ROOT": str(home / "forge-root"),
        "CORVIN_TENANT_ID": "_default",
    })
    log_path = tmp_path / "driver.log"
    with open(log_path, "w") as log_fh:
        proc = subprocess.Popen(
            [sys.executable, str(_DRIVER), str(home), str(port), json.dumps(flags), str(result_path)],
            env=env, cwd=str(Path(__file__).resolve().parents[2]),
            stdin=subprocess.DEVNULL, stdout=log_fh, stderr=subprocess.STDOUT,
        )
        # No engine spawn, no house-rules classifier on this path — 30s is
        # generous, not a tuned-to-the-wire budget.
        deadline = time.monotonic() + 30
        rc = None
        while time.monotonic() < deadline:
            rc = proc.poll()
            if rc is not None:
                break
            time.sleep(0.1)
        if rc is None:
            proc.kill()
            raise AssertionError(f"driver did not exit within 30s; see {log_path}")
    assert result_path.is_file(), f"driver produced no result (rc={rc}); see {log_path}"
    return json.loads(result_path.read_text())


def test_resume_round_trips_through_real_websocket(tmp_path):
    """Flag ON: /resume on connection 1 (nothing snapshotted yet), then a
    real client disconnect snapshots it; /resume with the SAME task_id on a
    brand-new connection 2 must restore that snapshot and say so in the
    reply — the full write+read path through real transport. Connection 2
    ALSO disconnects while still scoped to the task, so it snapshots again
    (two session-ends sharing one task_id -> two snapshots, each
    overwriting the on-disk file for that task_id; only one restore, from
    connection 2's /resume)."""
    result = _run_driver(tmp_path, {"session_context_bridge_enabled": True}, 51244)
    assert len(result["snapshot_created"]) == 2, result
    assert {r["details"]["task_id"] for r in result["snapshot_created"]} == {"T-CTXBRIDGE-E2E"}
    assert len(result["restored"]) == 1, result
    assert result["restored"][0]["details"]["task_id"] == "T-CTXBRIDGE-E2E"
    assert "Resumed task" in result["restore_reply"]


def test_resume_flag_off_never_persists_or_restores(tmp_path):
    """Flag OFF (default): /resume still sets the connection's task_id (so
    it's harmless to type) but the bridge itself must write nothing and
    restore nothing — same ship-dark discipline as P3's goal_drift_hook."""
    result = _run_driver(tmp_path, {}, 51245)
    assert result["snapshot_created"] == []
    assert result["restored"] == []
    assert "no prior snapshot found" in result["restore_reply"]

"""E2E: PLAN-0931 / ADR-2101 P3 — goal-drift alert reaches a REAL bridge turn.

Before this, `GoalAlignmentMonitor` was real, working, non-stub code with
ZERO live callers (ADR-2101 measured state: its only call chain never
reaches `adapter.py` or `chat_runtime.py`, the two places a real turn
happens). This drives the REAL adapter subprocess (`ADAPTER_FAKE_CLAUDE`,
the same harness `test_cel_anchor_bridge_e2e.py` uses) through a goal-
setting turn followed by three low-overlap turns, and asserts the
`goal_drift.alert_raised` audit record exists on the REAL hash chain —
not a direct call to `maybe_check_goal_drift`, per the E2E Wiring Proof
gate (CLAUDE.md) and `feedback-dead-mechanism-needs-call-site-test`.

Covers PLAN-0931 verification items #1 (flag ON, real bridge turn), #3
(flag OFF writes nothing) and #4 (no anchor fact -> None, not an error).

Run: python3 corvin_operator/bridges/shared/test_goal_drift_bridge_e2e.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_session_ledger_e2e as h  # noqa: E402  (shared sandbox harness)


def _send(sb: Path, chat: str, text: str, n: int) -> None:
    inbox = sb / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    item = {"id": f"g{n}", "channel": h.CHANNEL, "chat_id": chat, "from": chat, "text": text}
    (inbox / f"g{n}.json").write_text(json.dumps(item))
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if len(list((sb / "processed").glob("*.json"))) >= n:
            return
        time.sleep(0.05)
    raise AssertionError(f"turn {n} not processed; see {sb / 'adapter.log'}")


def _audit_lines(sb: Path, event_type: str) -> list[dict]:
    out: list[dict] = []
    for p in (sb / "home").rglob("forge/audit.jsonl"):
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if rec.get("event_type") == event_type:
                out.append(rec)
    return out


def _run_sandbox(flags: dict) -> tuple[Path, subprocess.Popen]:
    sb = Path(tempfile.mkdtemp(prefix="goal-drift-e2e-"))
    env = h._env(sb)
    (sb / "bridges" / h.CHANNEL).mkdir(parents=True)
    (sb / "bridges" / h.CHANNEL / "settings.json").write_text(
        json.dumps({"chat_profiles": {"gd-chat": {}}}))
    overlay = sb / "home" / "tenants" / "_default" / "global" / "features.json"
    overlay.parent.mkdir(parents=True, exist_ok=True)
    overlay.write_text(json.dumps({"flags": flags}))
    proc = subprocess.Popen([sys.executable, str(h.ADAPTER)], env=env,
                            stdout=open(sb / "adapter.log", "a"), stderr=subprocess.STDOUT)
    return sb, proc


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, msg: str) -> None:
        print(("  ok   " if cond else "  FAIL ") + msg)
        if not cond:
            failures.append(msg)

    # ── Scenario 1: flag ON — goal-setting turn, then 3 low-overlap turns ──
    sb1, proc1 = _run_sandbox({
        "vibe_engineering": True, "cel_load_bearing_anchor": True,
        "goal_drift_monitor_enabled": True,
    })
    try:
        time.sleep(0.5)
        _send(sb1, "gd-chat",
              "Goal for this chat: migrate the billing export to Parquet", 1)
        for i in range(2, 5):  # turns 2, 3, 4 — 3 consecutive low-overlap turns
            _send(sb1, "gd-chat", "totally unrelated small talk about the weather today", i)
        alerts = _audit_lines(sb1, "goal_drift.alert_raised")
        print("flag-ON alerts:", alerts)
        check(len(alerts) == 1, "exactly one goal_drift.alert_raised record (flag ON)")
        if alerts:
            d = alerts[0]["details"]
            check(d["consecutive_low_count"] == 3, "alert fired after 3 consecutive low scores")
            check("gd-chat" not in json.dumps(d), "raw chat id never reaches the audit record")
            check("session_key_fingerprint" in d and len(d["session_key_fingerprint"]) == 8,
                  "session key is fingerprinted, not raw")
    finally:
        proc1.terminate()
        try:
            proc1.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc1.kill()

    # ── Scenario 2: flag OFF (goal_drift_monitor_enabled absent) — same
    #    sequence must write NOTHING, proving the hook re-checks the flag
    #    on every call, not just once (PLAN-0931 verification #3). ──
    sb2, proc2 = _run_sandbox({"vibe_engineering": True, "cel_load_bearing_anchor": True})
    try:
        time.sleep(0.5)
        _send(sb2, "gd-chat",
              "Goal for this chat: migrate the billing export to Parquet", 1)
        for i in range(2, 5):
            _send(sb2, "gd-chat", "totally unrelated small talk about the weather today", i)
        alerts_off = _audit_lines(sb2, "goal_drift.alert_raised")
        check(len(alerts_off) == 0, "flag OFF: no goal_drift.alert_raised record at all")
    finally:
        proc2.terminate()
        try:
            proc2.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc2.kill()

    # ── Scenario 3: flag ON, but NO goal-setting turn (no anchor fact) —
    #    3 low-overlap-looking turns against nothing must not alert and
    #    must not error the turn (PLAN-0931 verification #4). ──
    sb3, proc3 = _run_sandbox({
        "vibe_engineering": True, "cel_load_bearing_anchor": True,
        "goal_drift_monitor_enabled": True,
    })
    try:
        time.sleep(0.5)
        for i in range(1, 4):
            _send(sb3, "gd-chat", "just some ordinary turn with no prior goal set", i)
        alerts_noanchor = _audit_lines(sb3, "goal_drift.alert_raised")
        check(len(alerts_noanchor) == 0, "no anchored goal: no alert, no crash, turns completed")
    finally:
        proc3.terminate()
        try:
            proc3.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc3.kill()

    if failures:
        print(f"\nFAILED ({len(failures)}); sandboxes kept at {sb1}, {sb2}, {sb3}")
        return 1
    for sb in (sb1, sb2, sb3):
        shutil.rmtree(sb, ignore_errors=True)
    print("\nPASS")
    return 0


def test_goal_drift_bridge_e2e():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())

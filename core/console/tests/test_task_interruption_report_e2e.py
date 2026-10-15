"""scripts/task_interruption_report.py as a CLI subprocess against a fixture root (PLAN-0958 P0).

The fixture holds exactly three known interruptions and three completed turns:

* chat-restart-window : failed, exit 143, ended inside the shutdown window  -> C1 (dir and audit)
* chat-restart-reaped : failed, exit -1, reaper marked it dead              -> C1 (dir and audit)
* chat-disconnect     : cancelled, reason client_disconnected_mid_turn      -> C2 (dir only)

The run must leave the audit chain byte-identical (the report is read-only) and must count
exactly these numbers. The subprocess gets a sandboxed CORVIN_HOME, XDG_CONFIG_HOME and an
absolute CORVIN_AUDIT_ANCHOR_KEY, so nothing reaches the live installation.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
SCRIPT = _REPO / "scripts" / "task_interruption_report.py"
SHUTDOWN_AT = 1_800_000_000.0


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _task(sessions: Path, chat: str, tid: str, meta: dict, events: list[dict]) -> None:
    tasks = sessions / chat / "tasks"
    _write(tasks / f"{tid}.json", json.dumps({"task_id": tid, "chat_key": chat, "created_at": 1.0, **meta}))
    _write(tasks / f"{tid}.events.jsonl", "\n".join(json.dumps(e) for e in events) + "\n")


def _audit(ts: float, event_type: str, details: dict) -> str:
    return json.dumps({"ts": ts, "event_type": event_type, "severity": "INFO", "details": details})


class TaskInterruptionReportE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "corvin"
        tenant = self.home / "tenants" / "_default"
        sessions = tenant / "sessions"
        _write(tenant / "global" / "last_shutdown.json", json.dumps({"at": SHUTDOWN_AT}))

        # Interruption 1: restart cut the turn off inside the shutdown window.
        _task(sessions, "web:chat-restart-window", "t-window",
              {"status": "failed", "exit_code": 143, "ended_at": SHUTDOWN_AT - 5, "result_summary": "",
               "input": {"instruction": "weiter"}},
              [{"event": "task.started"}, {"event": "task.failed", "exit_code": 143}])
        # Interruption 2: no shutdown evidence, but the reaper found the process dead.
        _task(sessions, "web:chat-restart-reaped", "t-reaped",
              {"status": "failed", "exit_code": -1, "ended_at": SHUTDOWN_AT - 1000, "result_summary": "",
               "input": {"instruction": "mach weiter"}},
              [{"event": "task.started"}, {"event": "task.failed", "exit_code": -1, "reaped": True}])
        # Interruption 3: client disconnect (C2). Carries a Bash call, so it counts as possible side effect.
        _task(sessions, "web:chat-disconnect", "t-disconnect",
              {"status": "cancelled", "exit_code": None, "ended_at": SHUTDOWN_AT - 300, "result_summary": "",
               "input": {"instruction": "pruefe"}},
              [{"event": "task.started"}, {"event": "tool_use", "tool_name": "Bash"},
               {"event": "task.cancelled", "reason": "client_disconnected_mid_turn"}])
        # Completed turns: one Write (sure side effect), one Bash only (possible), one read-only.
        _task(sessions, "web:chat-done-write", "t-done-write",
              {"status": "completed", "exit_code": 0, "ended_at": SHUTDOWN_AT - 900, "result_summary": "1 chars output",
               "input": {"instruction": "schreibe"}},
              [{"event": "tool_use", "tool_name": "Write"}, {"event": "task.completed"}])
        _task(sessions, "web:chat-done-bash", "t-done-bash",
              {"status": "completed", "exit_code": 0, "ended_at": SHUTDOWN_AT - 800, "result_summary": "1 chars output",
               "input": {"instruction": "liste"}},
              [{"event": "tool_use", "tool_name": "Bash"}, {"event": "task.completed"}])
        _task(sessions, "web:chat-done-read", "t-done-read",
              {"status": "completed", "exit_code": 0, "ended_at": SHUTDOWN_AT - 700, "result_summary": "1 chars output",
               "input": {"instruction": "lies"}},
              [{"event": "tool_use", "tool_name": "Read"}, {"event": "task.completed"}])

        chain = tenant / "global" / "forge" / "audit.jsonl"
        _write(chain, "\n".join([
            _audit(SHUTDOWN_AT - 5, "task.started", {"task_id": "f1"}),
            _audit(SHUTDOWN_AT - 5, "task.failed", {"task_id": "f1", "exit_code": 143, "timed_out": False}),
            _audit(SHUTDOWN_AT - 1000, "task.failed", {"task_id": "f2", "exit_code": -1, "timed_out": False}),
            _audit(SHUTDOWN_AT - 999, "console.action_performed",
                   {"action": "chat.turn.recovery_started", "target_id": "chat-restart-window"}),
            "not json at all",
        ]) + "\n")
        self.chain = chain
        self.chain_before = hashlib.sha256(chain.read_bytes()).hexdigest()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self) -> subprocess.CompletedProcess:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": self.tmp.name,
            "CORVIN_HOME": str(self.home),
            "XDG_CONFIG_HOME": str(Path(self.tmp.name) / "xdg"),
            "CORVIN_AUDIT_ANCHOR_KEY": str(Path(self.tmp.name) / "anchor.key"),
            "CORVIN_TENANT_ID": "_default",
        }
        return subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "--tenant", "_default"],
            cwd=self.tmp.name, env=env, capture_output=True, text=True, timeout=120,
        )

    def test_three_known_interruptions_are_counted_exactly(self):
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = proc.stdout
        rows = {line.split("|")[1].strip(): line for line in out.splitlines() if line.startswith("| C")}

        # Class rows: audit column then dir column.
        self.assertIn("| C1 | Host restart | 2 | 2 |", rows["C1"], out)
        self.assertIn("| C2 | Client disconnect | 0 | 1 |", rows["C2"], out)
        self.assertIn("| C3 | Operator cancel | 0 | 0 |", rows["C3"], out)
        for cls in ("C4", "C5", "C6", "C7", "C8", "C9"):
            self.assertIn(f"| {cls} |", rows[cls], out)
            self.assertTrue(rows[cls].rstrip().endswith("| 0 | 0 |"), rows[cls])
        self.assertIn("| **Sum** | | **2** | **3** |", out)

        self.assertIn("Terminal turns in task dirs: **6** · interrupted: **3**", out)
        # Lower bound: only the Write turn. Upper bound: Write, Bash-only and the Bash-carrying cancel = 3 of 6.
        self.assertIn("| Write/Edit or message tool (lower bound) | 1 | 16.7 % | n/a |", out)
        self.assertIn("| plus any Bash call (upper bound) | 3 | 50.0 % | 33.3 % |", out)
        self.assertIn("Recovery events in audit chain: started 1, exhausted 0", out)
        self.assertIn("audit lines: 5 (unparsable: 1)", out)

    def test_report_never_writes_the_audit_chain(self):
        self._run()
        after = hashlib.sha256(self.chain.read_bytes()).hexdigest()
        self.assertEqual(self.chain_before, after)

    def test_missing_root_is_refused_not_guessed(self):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": self.tmp.name}
        proc = subprocess.run([sys.executable, "-I", str(SCRIPT)], cwd=self.tmp.name, env=env,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("no root", proc.stderr)


if __name__ == "__main__":
    unittest.main()

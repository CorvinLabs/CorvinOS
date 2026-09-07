"""LIVE E2E: a real ``claude -p`` haiku turn → the real outcome path → status.

Gated on ``CLAUDE_LIVE_E2E=1`` (FIXER_RULES §6). Runs the real path end to end:

1. a real ``claude -p … --model haiku`` subprocess (the LLM turn);
2. the REAL task lifecycle chokepoint ``corvin_core.task_manager.TaskManager.
   record_event`` on ``task.completed`` (the engine's exit code + duration are
   the outcome), which calls ``core.learning.outcome_sink.emit_task_outcome``;
3. the sink writes through the BOOTED ACP registry's learning emitter into the
   audit-first ``EventStore`` (core chain FIRST, then disk, ``audit_ref``);
4. ``GET /v1/console/learning/status`` (real router, real session cookie)
   reflects the outcome — under a temp ``CORVIN_HOME``.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from tests.learning.console_client import console_client

pytestmark = pytest.mark.live


@pytest.mark.skipif(os.environ.get("CLAUDE_LIVE_E2E") != "1", reason="set CLAUDE_LIVE_E2E=1 to run the live LLM E2E")
@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not on PATH")
def test_real_haiku_turn_lands_a_chained_outcome_and_shows_in_status(tmp_path: Path):
    with console_client(tmp_path) as sb:
        from core.learning.event_emitter import EventEmitter
        from core.learning.event_store import EventStore
        from core.skills.boot import boot_skills
        from core.skills.skill_registry_phase1 import LearningEmitterBackend
        from corvin_core.task_manager import TaskManager

        # the booted registry's emitter is what ``outcome_sink.learning_emitter()`` resolves
        emitter = EventEmitter(EventStore(sb.tenant_home, tenant_id=sb.tenant_id))
        boot_skills(sb.tenant_id, audit_emit=lambda *_a, **_k: None,
                    learning_backend=LearningEmitterBackend(emitter, session_id="live-e2e"))

        # 1. the real LLM turn
        t0 = time.monotonic()
        proc = subprocess.run(
            ["claude", "-p", "Reply with exactly the single word OK and nothing else.", "--model", "haiku"],
            capture_output=True, text=True, timeout=180,
        )
        duration_ms = int((time.monotonic() - t0) * 1000)
        assert proc.returncode == 0, proc.stderr
        assert "OK" in proc.stdout.upper()

        # 2. the real task lifecycle → 3. outcome sink → booted emitter → chained store
        tm = TaskManager(tmp_path / "tasks")
        task_id = tm.create_task(
            chat_key=f"live:{uuid.uuid4().hex[:8]}", instruction="live e2e", check_quota=False,
            tenant_id=sb.tenant_id, engine="claude-code",
        )
        tm.record_event(task_id, {"event": "task.started"})
        tm.record_event(task_id, {"event": "task.completed", "exit_code": proc.returncode, "duration_ms": duration_ms})

        for _ in range(100):  # the emitter's worker flushes asynchronously
            outcomes = [e for e in sb.events_on_disk() if e["event_type"] == "outcome"]
            if outcomes:
                break
            time.sleep(0.05)
        assert len(outcomes) == 1, "no OUTCOME event reached the store"
        outcome = outcomes[0]
        assert outcome["tenant_id"] == sb.tenant_id
        assert outcome["signal"]["task_id"] == task_id
        assert outcome["signal"]["success"] is True and outcome["signal"]["exit_code"] == 0
        assert outcome["signal"]["engine"] == "claude-code"
        assert "live e2e" not in str(outcome), "instruction text leaked into the learning record"
        assert outcome["audit_ref"], "outcome has no chain reference"
        chain = [c for c in sb.chain_records() if c.get("event_type") == "learning.outcome"]
        assert any(c["details"].get("audit_ref") == outcome["audit_ref"] for c in chain)

        # 4. the console reflects it
        r = sb.client.get("/v1/console/learning/status")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "collecting"
        assert body["event_counts"]["outcome"] == 1
        assert body["recent_outcomes"] == {"window": 50, "total": 1, "successes": 1, "success_rate": 1.0}
        assert body["outcome_loss"] == 0.0
        audit = sb.client.get("/v1/console/learning/audit").json()
        assert any(e["audit_ref"] == outcome["audit_ref"] for e in audit["events"])
        emitter.stop()

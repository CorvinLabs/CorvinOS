"""``event_store.EventStore`` is audit-FIRST and fail-closed (ADR-0314).

Adversarial review 2026-09-06 (F4): the live learning store appended plain
JSONL and never touched the core hash chain, so every ACP ``skill_executed``
learning event was unattributed in the audit trail — contradicting the
CLAUDE.md § Phase 3 constraint "no chain commit → no disk record".
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.learning import event_persistence
from core.learning.event_emitter import EventEmitter
from core.learning.event_store import EventStore
from core.learning.learning_events import EventType, LearningEvent

TENANT = "_default"


def _event(**signal) -> LearningEvent:
    return LearningEvent.create(
        event_type=EventType.SKILL_EXECUTED,
        skill_id="os.delegation_router",
        tenant_id=TENANT,
        signal=signal or {"status": "success"},
        lom="core/skills/tests:L1",
    )


def _chain_lines(monkeypatch_env_path: Path) -> list[dict]:
    if not monkeypatch_env_path.exists():
        return []
    return [json.loads(l) for l in monkeypatch_env_path.read_text().splitlines() if l.strip()]


class TestAuditFirst:
    def test_chain_record_precedes_disk_record_and_is_joinable(self, tmp_path: Path, monkeypatch):
        import os

        chain = Path(os.environ["VOICE_AUDIT_PATH"])
        store = EventStore(tmp_path / "tenant")
        ev = _event(status="success", secret_payload="MUST-NOT-REACH-CHAIN")

        store.write_event(ev)

        # 1. the chain carries a content-free record for the event
        records = [r for r in _chain_lines(chain) if r.get("event_type") == "learning.skill_executed"]
        assert len(records) == 1, records
        details = records[0].get("details") or records[0]
        assert details.get("event_id") == ev.event_id
        assert details.get("skill_id") == "os.delegation_router"
        assert "MUST-NOT-REACH-CHAIN" not in json.dumps(records[0])  # payload never enters the chain

        # 2. the disk record carries the audit_ref that joins it to the chain
        files = list((tmp_path / "tenant" / "learning" / "events").glob("*.jsonl"))
        assert len(files) == 1
        disk = json.loads(files[0].read_text().splitlines()[0])
        assert disk["event_id"] == ev.event_id
        assert disk["audit_ref"] and disk["audit_ref"] == details.get("audit_ref")

        # 3. the store still reads its own record back
        got = store.query_events(TENANT, event_type=EventType.SKILL_EXECUTED)
        assert [e.event_id for e in got] == [ev.event_id]

    def test_no_chain_commit_means_no_disk_record(self, tmp_path: Path, monkeypatch):
        def _unavailable():
            raise RuntimeError("core audit writer unavailable")

        monkeypatch.setattr(event_persistence, "_resolve_core_audit", _unavailable)
        store = EventStore(tmp_path / "tenant")

        with pytest.raises(RuntimeError, match="unavailable"):
            store.write_event(_event())

        assert not list((tmp_path / "tenant" / "learning" / "events").glob("*.jsonl"))

    def test_tenant_mismatch_with_process_tenant_is_refused(self, tmp_path: Path, monkeypatch):
        """The chain writer only admits the process tenant; a foreign-tenant
        event must not silently land on disk as if it were audited."""
        monkeypatch.setenv("CORVIN_TENANT_ID", "other_tenant")
        store = EventStore(tmp_path / "tenant")
        with pytest.raises(RuntimeError, match="did not commit"):
            store.write_event(_event())
        assert not list((tmp_path / "tenant" / "learning" / "events").glob("*.jsonl"))

    def test_emitter_counts_chain_failure_as_write_failure(self, tmp_path: Path, monkeypatch):
        def _unavailable():
            raise RuntimeError("core audit writer unavailable")

        monkeypatch.setattr(event_persistence, "_resolve_core_audit", _unavailable)
        emitter = EventEmitter(EventStore(tmp_path / "tenant"))
        try:
            assert emitter.emit(_event()) is True  # queued
            emitter.stop(timeout=5.0)
        finally:
            pass
        assert emitter.write_failures == 1
        assert not list((tmp_path / "tenant" / "learning" / "events").glob("*.jsonl"))

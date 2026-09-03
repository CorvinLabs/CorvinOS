"""ADR-0314 EventStore (event_persistence) — audit-first, tenant-bound, atomic.

Findings covered (adversarial review 2026-09-03):
- L-01: every write reaches the CORE hash-chained writer first; unavailable
  writer or non-committed write is fail-closed (raise, no disk record).
- L-02: the store is bound to ONE tenant; foreign / empty tenants rejected.
- L-13: per-user erasure rewrites partitions atomically + tombstone + audit.
- L-17: retention cleanup is atomic and audited.

The chain is the real ``operator/bridges/shared/audit.py`` writer redirected
to a temp file via ``VOICE_AUDIT_PATH`` — never the live chain.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.learning import event_persistence
from core.learning.event_persistence import EventStore, core_audit_event
from core.learning.event_schema import LearningEvent, LearningEventType

TENANT = "tenant_a"


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch):
    """Temp learning dir + temp audit chain + tenant context."""
    import core.paths as _paths

    monkeypatch.setattr(_paths, "tenant_learning_dir", lambda t: tmp_path / "tenants" / t / "learning")
    monkeypatch.setattr(_paths, "tenant_audit_file", lambda t: tmp_path / "tenants" / t / "audit.jsonl")
    chain = tmp_path / "chain" / "audit.jsonl"
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(chain))
    monkeypatch.setenv("CORVIN_TENANT_ID", TENANT)
    return tmp_path, chain


def _event(tenant_id: str = TENANT, user_id: str | None = "u1", **payload) -> LearningEvent:
    return LearningEvent(
        event_type=LearningEventType.DECISION_RECORD,
        tenant_id=tenant_id,
        instance_id="inst",
        skill_name="os.router",
        session_id="s1",
        timestamp_utc=datetime.utcnow(),
        user_id=user_id,
        payload={"secret_payload": "MUST NOT REACH CHAIN", **payload},
        tags=["t"],
    )


def _chain_records(chain: Path) -> list[dict]:
    if not chain.exists():
        return []
    return [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]


class TestL01AuditFirst:
    def test_write_reaches_core_chain_and_disk_record_carries_ref(self, sandbox):
        tmp, chain = sandbox
        store = EventStore(TENANT)
        ev = _event()
        ref = asyncio.run(store.write_event(ev, TENANT))

        recs = _chain_records(chain)
        assert len(recs) == 1, "exactly one chain record per learning event"
        rec = recs[0]
        assert rec["event_type"] == "learning.decision.record"
        assert rec["details"]["audit_ref"] == ref
        assert rec["details"]["tenant_id"] == TENANT
        assert rec["details"]["event_id"] == ev.event_id
        assert "hash" in rec, "record must be hash-chained"
        assert "MUST NOT REACH CHAIN" not in chain.read_text(), "payload is content, never on the chain"

        disk = list((tmp / "tenants" / TENANT / "learning" / "events").glob("*.jsonl"))
        assert len(disk) == 1
        line = json.loads(disk[0].read_text().splitlines()[0])
        assert line["audit_ref"] == ref
        assert "_persistence_fallback" not in line

        read = asyncio.run(store.read_events(tenant_id=TENANT))
        assert read[0].audit_id == ref

    def test_unavailable_writer_is_fail_closed(self, sandbox, monkeypatch):
        tmp, chain = sandbox
        store = EventStore(TENANT)

        def _unavailable():
            raise RuntimeError("core audit writer unavailable")

        monkeypatch.setattr(event_persistence, "_resolve_core_audit", _unavailable)
        with pytest.raises(RuntimeError, match="core audit writer unavailable"):
            asyncio.run(store.write_event(_event(), TENANT))
        assert not list((tmp / "tenants" / TENANT / "learning" / "events").glob("*.jsonl")), \
            "no disk record when the chain write did not happen"

    def test_swallowed_chain_failure_is_detected(self, sandbox, monkeypatch):
        """audit.audit_event swallows a tenant-context mismatch; we must not."""
        tmp, chain = sandbox
        store = EventStore(TENANT)
        monkeypatch.setenv("CORVIN_TENANT_ID", "_default")  # context != event tenant
        with pytest.raises(RuntimeError, match="did not commit"):
            asyncio.run(store.write_event(_event(), TENANT))
        assert not list((tmp / "tenants" / TENANT / "learning" / "events").glob("*.jsonl"))

    def test_core_audit_event_rejects_invalid_tenant(self, sandbox):
        with pytest.raises(ValueError):
            core_audit_event("learning.x", tenant_id="test", details={})


class TestL02TenantBinding:
    def test_foreign_tenant_event_rejected(self, sandbox):
        store = EventStore(TENANT)
        with pytest.raises(ValueError, match="Tenant mismatch"):
            asyncio.run(store.write_event(_event(tenant_id="tenant_b"), "tenant_b"))
        with pytest.raises(ValueError, match="Tenant mismatch"):
            asyncio.run(store.write_event(_event(tenant_id="tenant_b"), TENANT))

    @pytest.mark.parametrize("bad", [None, "", "   ", "../x", "test"])
    def test_empty_or_invalid_tenant_rejected_everywhere(self, sandbox, bad):
        store = EventStore(TENANT)
        with pytest.raises(ValueError):
            asyncio.run(store.write_event(_event(tenant_id=bad), bad))
        with pytest.raises(ValueError):
            asyncio.run(store.read_events(tenant_id=bad))
        with pytest.raises(ValueError):
            store._read_by_type(bad, "decision.record", {})
        with pytest.raises(ValueError):
            asyncio.run(store.cleanup_old_events(tenant_id=bad))

    def test_reads_for_other_tenant_rejected(self, sandbox):
        store = EventStore(TENANT)
        with pytest.raises(ValueError, match="Tenant mismatch"):
            asyncio.run(store.read_events(tenant_id="tenant_b"))
        with pytest.raises(ValueError, match="Tenant mismatch"):
            asyncio.run(store.cleanup_old_events(tenant_id="tenant_b"))

    def test_store_rejects_invalid_tenant_at_construction(self, sandbox):
        with pytest.raises(ValueError):
            EventStore("test")
        with pytest.raises(ValueError):
            EventStore("")


class TestL13PerUserErasure:
    def test_erase_user_rewrites_partitions_with_tombstone_and_audits(self, sandbox):
        tmp, chain = sandbox
        store = EventStore(TENANT)
        for uid in ("u1", "u1", "u2"):
            asyncio.run(store.write_event(_event(user_id=uid), TENANT))

        erased = asyncio.run(store.erase_user_events(tenant_id=TENANT, user_id="u1"))
        assert erased == 2

        events_dir = tmp / "tenants" / TENANT / "learning" / "events"
        files = list(events_dir.glob("*.jsonl"))
        assert len(files) == 1 and not list(events_dir.glob(".*.tmp")), "atomic: no temp leftovers"
        lines = [json.loads(l) for l in files[0].read_text().splitlines()]
        assert [l.get("user_id") for l in lines if l.get("event_type") != "learning.tombstone"] == ["u2"]
        tomb = [l for l in lines if l.get("event_type") == "learning.tombstone"]
        assert len(tomb) == 1 and tomb[0]["erased_count"] == 2
        assert "u1" not in files[0].read_text(), "tombstone must not carry the erased user id"

        # readers skip tombstones
        remaining = asyncio.run(store.read_events(tenant_id=TENANT))
        assert [e.user_id for e in remaining] == ["u2"]
        assert asyncio.run(store.get_event_count(tenant_id=TENANT)) == 1

        # audited with counts only
        erasure = [r for r in _chain_records(chain) if r["event_type"] == "learning.erasure"]
        assert len(erasure) == 1
        assert erasure[0]["details"]["erased_count"] == 2
        assert "u1" not in json.dumps(erasure[0])

        # idempotent
        assert asyncio.run(store.erase_user_events(tenant_id=TENANT, user_id="u1")) == 0


class TestL17AtomicRetention:
    def test_cleanup_is_atomic_and_audited(self, sandbox):
        tmp, chain = sandbox
        store = EventStore(TENANT)
        old = datetime.utcnow() - timedelta(days=120)
        events_dir = tmp / "tenants" / TENANT / "learning" / "events"
        old_file = events_dir / f"{old.date().isoformat()}.jsonl"
        ev = _event().to_audit_event()
        ev["timestamp"] = old.isoformat() + "Z"
        old_file.write_text(json.dumps(ev) + "\n" + json.dumps(ev) + "\n")
        asyncio.run(store.write_event(_event(), TENANT))  # fresh, must survive

        deleted = asyncio.run(store.cleanup_old_events(tenant_id=TENANT, retention_days=90))
        assert deleted == 2
        assert not old_file.exists()
        assert not list(events_dir.glob(".*.tmp"))
        assert asyncio.run(store.get_event_count(tenant_id=TENANT)) == 1

        retention = [r for r in _chain_records(chain) if r["event_type"] == "learning.retention"]
        assert len(retention) == 1 and retention[0]["details"]["deleted_count"] == 2

    def test_rewrite_partition_never_truncates_on_failure(self, sandbox, monkeypatch):
        tmp, chain = sandbox
        store = EventStore(TENANT)
        asyncio.run(store.write_event(_event(), TENANT))
        events_dir = tmp / "tenants" / TENANT / "learning" / "events"
        target = next(events_dir.glob("*.jsonl"))
        before = target.read_text()

        import os as _os
        def _boom(*a, **k):
            raise OSError("disk full")
        monkeypatch.setattr(_os, "replace", _boom)
        with pytest.raises(OSError):
            store._rewrite_partition(target, ["x\n"], None)
        assert target.read_text() == before, "original partition untouched when replace fails"
        assert not list(events_dir.glob(".*.tmp"))

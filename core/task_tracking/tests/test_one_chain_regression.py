"""Regression: Phase-C task-tracking audit writes THE tenant chain, not a second one.

Before the fix:
* ``emit_task_audit_event`` appended to a homemade hash chain at
  ``tenant_home(tid)/global/audit/task_tracking.jsonl`` — a second chain the
  boot tripwire, ``audit_query`` and compliance reports never read;
* ``emit_approval_decision_event`` crashed (``PIIDetector.has_pii`` without
  ``tenant_id``, then ``from core.audit.chain import write_entry`` — a name
  that does not exist), so no approval could ever be recorded;
* ``migration.migrate_task_registry_to_db`` read ``~/.corvin`` (ignoring
  ``CORVIN_HOME``) and crashed on the real registry shape ``{"tasks": {id: {...}}}``.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    monkeypatch.delenv("CORVIN_TENANT_ID", raising=False)
    from core.task_tracking import service

    service.chain_writer = service._default_chain_writer
    yield tmp_path
    service.chain_writer = service._default_chain_writer


def _chain_records(tenant: str = "_default") -> list[dict]:
    from core.paths import tenant_audit_chain

    p = Path(tenant_audit_chain(tenant))
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def test_task_event_lands_in_tenant_audit_chain_only(home):
    from core.paths import tenant_home
    from core.task_tracking.audit import emit_task_audit_event

    h = asyncio.run(emit_task_audit_event(
        "task_created", "t-1", tenant_id="_default", actor="user:alice",
        action="create", delta={"title": "Secret plan", "status": "open"},
    ))
    assert h
    recs = [r for r in _chain_records() if r.get("event_type") == "task_item.created"]
    assert len(recs) == 1
    d = recs[0]["details"]
    assert d.get("item_id") == "t-1"
    # content-free: the title value never reaches the chain
    assert "Secret plan" not in json.dumps(recs[0])
    assert "alice" not in json.dumps(recs[0])
    # no second chain file
    assert not (Path(tenant_home("_default")) / "global" / "audit" / "task_tracking.jsonl").exists()


def test_task_event_fails_closed_when_chain_write_fails(home):
    from core.task_tracking import service
    from core.task_tracking.audit import emit_task_audit_event

    def _boom(*a, **k):
        raise RuntimeError("disk gone")

    service.chain_writer = _boom
    with pytest.raises(OSError):
        asyncio.run(emit_task_audit_event(
            "task_updated", "t-2", tenant_id="_default", actor="system",
            action="status", delta={"status": "done"},
        ))


def test_approval_decision_is_recorded_in_tenant_chain(home):
    from core.task_tracking.audit import emit_approval_decision_event

    h = asyncio.run(emit_approval_decision_event(
        "t-3", "user:bob@example.com", "approve", tenant_id="_default",
        rationale="LGTM", validator_ids_applied=["v1", "v2"],
        validation_results={"v1": True, "v2": False},
    ))
    assert h
    recs = [r for r in _chain_records() if r.get("event_type") == "task_item.approval_decided"]
    assert len(recs) == 1
    d = recs[0]["details"]
    assert d.get("item_id") == "t-3"
    assert d.get("decision") == "approve"
    assert d.get("validator_count") == 2
    assert d.get("validators_failed") == 1
    assert "bob@example.com" not in json.dumps(recs[0])
    assert "LGTM" not in json.dumps(recs[0])


def test_migration_honours_corvin_home_and_mapping_registry(home):
    from core.task_tracking import migration, store

    (home / "task_registry.json").write_text(json.dumps({
        "tasks": {
            "adr_0001": {"title": "ADR one", "status": "ACCEPTED"},
            "task_x": {"title": "X", "status": "in_progress"},
        }
    }))
    report = asyncio.run(migration.migrate_task_registry_to_db(tenant_id="_default"))
    assert report.get("migrated_count") == 2, report
    assert report["errors"] == []
    with store.connect("_default") as conn:
        ids = {r["id"] for r in conn.execute("SELECT id FROM items")}
    assert ids == {"adr_0001", "task_x"}
    migrated = [r for r in _chain_records() if r.get("event_type") == "task_item.imported"]
    assert {r["details"]["item_id"] for r in migrated} == {"adr_0001", "task_x"}

    # re-run never overwrites existing items
    again = asyncio.run(migration.migrate_task_registry_to_db(tenant_id="_default"))
    assert again["migrated_count"] == 0 and again["skipped_count"] == 2

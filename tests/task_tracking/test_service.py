"""Service-level tests for the Task-Tracking SSOT (core/task_tracking/service.py).

The HTTP surface is covered in core/console/tests/test_task_tracking_route.py
against the real chain. Here the chain writer is replaced by a recorder so the
tests can assert on exactly what WOULD be chained, plus the properties that are
hard to reach over HTTP: concurrent writers, file modes, tenant-id validation,
rollups over archived items.
"""
from __future__ import annotations

import os
import stat
import threading
from datetime import datetime, timedelta, timezone

import pytest

from core.task_tracking import service, store
from core.task_tracking.models import ItemCreate, ItemPatch

T = "_default"


@pytest.fixture
def chain(monkeypatch, tmp_path):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    store._INITIALISED.clear()
    records: list[tuple[str, str, dict]] = []

    def rec(tenant_id, event_type, details):
        records.append((tenant_id, event_type, dict(details)))
        return f"h{len(records)}"

    monkeypatch.setattr(service, "chain_writer", rec)
    return records


def _mk(kind="task", title="t", **kw):
    return service.create(T, ItemCreate(kind=kind, title=title, **kw), actor="test")


def test_store_file_and_directory_are_private(chain):
    _mk()
    p = store.db_path(T)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(p.parent).st_mode) == 0o700


def test_invalid_tenant_id_is_refused_before_any_path_is_built(chain):
    with pytest.raises(ValueError):
        store.db_path("../evil")
    with pytest.raises(ValueError):
        service.create("../evil", ItemCreate(kind="task", title="x"), actor="test")
    assert chain == []


def test_chain_record_never_carries_free_text(chain):
    item = _mk(title="Secret roadmap", description="internal notes", assignee="claude")
    service.update(T, item["id"], ItemPatch(version=1, title="Renamed secret", assignee="other"), actor="test")
    blob = repr(chain)
    for text in ("Secret roadmap", "internal notes", "claude", "Renamed secret", "other"):
        assert text not in blob
    assert chain[-1][2]["fields"] == "assignee,title"


def test_concurrent_writers_never_lose_an_update(chain):
    item = _mk()
    results: list[str] = []

    def worker(n: int) -> None:
        try:
            service.update(T, item["id"], ItemPatch(version=1, progress=n), actor="test")
            results.append("ok")
        except service.Conflict:
            results.append("conflict")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(1, 9)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 1 and results.count("conflict") == 7
    rows = service.list_items(T)["items"]
    assert rows[0]["version"] == 2
    assert sum(1 for _, et, _ in chain if et == "task_item.updated") == 1


def test_rollup_ignores_archived_leaves_and_counts_overdue(chain):
    ini = _mk("initiative", "I")
    past = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    _mk("task", "done", parent_id=ini["id"], status="complete")
    _mk("task", "late", parent_id=ini["id"], deadline=past)
    _mk("task", "gone", parent_id=ini["id"], status="archived")
    rows = {r["title"]: r for r in service.list_items(T)["items"]}
    assert rows["I"]["rollup"]["progress"] == 50        # archived leaf not in the mean
    assert rows["I"]["rollup"]["overdue"] == 1
    assert rows["late"]["overdue"] is True
    summary = service.list_items(T)["summary"]
    assert (summary["total"], summary["overdue"], summary["complete"]) == (3, 1, 1)


def test_date_only_deadline_is_due_at_end_of_that_day(chain):
    today = datetime.now(timezone.utc).date().isoformat()
    _mk(deadline=today)
    assert service.list_items(T)["items"][0]["overdue"] is False


def test_kind_change_must_fit_existing_children(chain):
    ini = _mk("initiative", "I")
    epic = _mk("epic", "E", parent_id=ini["id"])
    _mk("story", "S", parent_id=epic["id"])
    with pytest.raises(service.TaskTrackingError):
        # an epic turned subtask would hold a story, which a subtask cannot
        service.update(T, epic["id"], ItemPatch(version=1, kind="subtask"), actor="test")


def test_import_refuses_a_bad_batch_before_writing_anything(chain):
    specs = [
        {"external_ref": "x#a", "kind": "initiative", "title": "A"},
        {"external_ref": "x#b", "parent_ref": "x#missing", "kind": "task", "title": "B"},
    ]
    with pytest.raises(service.TaskTrackingError):
        service.import_items(T, specs, source="test", actor="test")
    assert service.list_items(T)["items"] == []
    assert chain == []  # refused before the first (permanent) chain write


# ── Regressions from the adversarial review (2026-09-24) ─────────────────────

def test_a_chain_failure_mid_batch_is_compensated_in_the_chain(chain, monkeypatch):
    """Records 1..n-1 are permanent; the rollback must say they did not take effect."""
    specs = [{"external_ref": f"x#{n}", "kind": "task", "title": f"T{n}"} for n in range(5)]
    real = service.chain_writer
    calls = {"n": 0}

    def flaky(tenant_id, event_type, details):
        calls["n"] += 1
        if calls["n"] == 4:
            raise OSError("disk full")
        return real(tenant_id, event_type, details)

    monkeypatch.setattr(service, "chain_writer", flaky)
    with pytest.raises(service.AuditUnavailable):
        service.import_items(T, specs, source="test", actor="test")
    assert service.list_items(T)["items"] == []
    types = [et for _, et, _ in chain]
    assert types == ["task_item.created"] * 3 + ["task_item.rolled_back"]
    assert chain[-1][2]["count"] == 3 and chain[-1][2]["error_class"] == "AuditUnavailable"


def test_import_skips_new_children_of_a_deleted_branch_without_touching_the_chain(chain):
    service.import_items(T, [{"external_ref": "x#a", "kind": "initiative", "title": "A"}], source="t", actor="t")
    a = service.list_items(T)["items"][0]
    service.delete(T, a["id"], actor="t")
    before = len(chain)
    res = service.import_items(T, [
        {"external_ref": "x#a", "kind": "initiative", "title": "A"},
        {"external_ref": "x#a/1", "parent_ref": "x#a", "kind": "task", "title": "new under deleted"},
        {"external_ref": "x#a/1/s", "parent_ref": "x#a/1", "kind": "subtask", "title": "grandchild"},
    ], source="t", actor="t")
    assert res == {"inserted": 0, "skipped": 1, "skipped_deleted_parent": 2}
    assert len(chain) == before


def test_approval_goes_through_the_decision_path_and_a_gate_status_follows(chain):
    gate = _mk(title="Gate", category="gate", approval_state="pending")
    with pytest.raises(service.TaskTrackingError):
        service.update(T, gate["id"], ItemPatch(version=1, approval_state="approved"), actor="t")
    with pytest.raises(service.TaskTrackingError):
        _mk(title="pre-approved", approval_state="approved")
    out = service.decide(T, gate["id"], "approved", 1, actor="t")
    assert (out["approval_state"], out["status"]) == ("approved", "complete")
    assert out["completed_at"]
    assert chain[-1][1] == "task_item.decision_recorded"
    assert (chain[-1][2]["old_status"], chain[-1][2]["new_status"]) == ("open", "complete")
    out = service.decide(T, gate["id"], "rejected", out["version"], actor="t")
    assert (out["status"], out["completed_at"]) == ("blocked", None)


def test_restore_rechecks_the_hierarchy(chain):
    ini = _mk("initiative", "I")
    epic = _mk("epic", "E", parent_id=ini["id"])
    story = _mk("story", "S", parent_id=epic["id"])
    service.delete(T, story["id"], actor="t")
    service.update(T, epic["id"], ItemPatch(version=1, kind="story"), actor="t")  # story under initiative: ok
    with pytest.raises(service.TaskTrackingError):
        service.restore(T, story["id"], actor="t")  # a story cannot sit under a story


def test_import_never_invents_a_completion_date_and_markers_are_not_work(chain):
    service.import_items(T, [
        {"external_ref": "x#done", "kind": "task", "title": "done, date unknown", "status": "complete",
         "completed_at": None},
        {"external_ref": "x#cp", "kind": "task", "title": "Standup", "category": "checkpoint", "status": "open",
         "deadline": "2020-01-01T09:00:00Z"},
    ], source="t", actor="t")
    rows = {r["title"]: r for r in service.list_items(T)["items"]}
    assert rows["done, date unknown"]["completed_at"] is None
    assert rows["Standup"]["overdue"] is False
    s = service.list_items(T)["summary"]
    assert (s["total"], s["done_7d"], s["overdue"]) == (1, 0, 0)


def test_labels_null_clears_to_an_empty_list(chain):
    item = _mk(labels=["ui"])
    out = service.update(T, item["id"], ItemPatch(version=1, labels=None), actor="t")
    assert out["labels"] == []

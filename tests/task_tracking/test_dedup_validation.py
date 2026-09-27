"""External-ref deduplication in the Task-Tracking SSOT (ADR-0516 enforcement).

``external_ref`` (e.g. an ADR id) is set by importers through
``service.create(..., extra={"external_ref": ...})`` — the console
``ItemCreate``/``ItemPatch`` bodies forbid it. The store enforces
``UNIQUE (tenant_id, external_ref)`` over every row; the service turns a
collision into a ``TaskTrackingError`` instead of a raw IntegrityError.

Hermetic: CORVIN_HOME is a tmp dir and the chain writer is a recorder. (The
previous version of this file ran against the operator's live store and ran
``scripts/task_registry_dedup_validator.py --fix`` with cwd set to the live
checkout.)
"""
from __future__ import annotations

import pytest

from core.task_tracking import service
from core.task_tracking.models import ItemCreate, ItemPatch
from core.task_tracking.service import TaskTrackingError


@pytest.fixture(autouse=True)
def _hermetic(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "corvin_home"))
    recorded: list[tuple[str, str, dict]] = []

    def _rec(tid, et, details):
        recorded.append((tid, et, details))
        return "h%d" % len(recorded)

    monkeypatch.setattr(service, "chain_writer", _rec)
    yield recorded


TENANT = "test_tenant_1"
ACTOR = "user:test"


def _create(ref: str, tenant: str = TENANT, title: str = "Task"):
    return service.create(tenant, ItemCreate(title=title, kind="task"), actor=ACTOR,
                          extra={"external_ref": ref})


def test_console_bodies_cannot_set_external_ref():
    with pytest.raises(ValueError):
        ItemCreate(title="x", kind="task", external_ref="ADR-0516")
    with pytest.raises(ValueError):
        ItemPatch(version=1, external_ref="ADR-0516")


def test_create_with_unique_adr_succeeds():
    assert _create("ADR-0516")["external_ref"] == "ADR-0516"


def test_create_duplicate_adr_fails_cleanly():
    first = _create("ADR-0516")
    with pytest.raises(TaskTrackingError, match=first["id"]):
        _create("ADR-0516", title="Second")


def test_non_adr_duplicate_fails_cleanly_not_integrity_error():
    _create("JIRA-123")
    with pytest.raises(TaskTrackingError):
        _create("JIRA-123")


def test_deleted_holder_blocks_reuse_with_a_restore_hint():
    first = _create("ADR-0516")
    service.delete(TENANT, first["id"], actor=ACTOR)
    with pytest.raises(TaskTrackingError, match="restore"):
        _create("ADR-0516")


def test_dedup_is_per_tenant():
    a = _create("ADR-0516", tenant="tenant_a")
    b = _create("ADR-0516", tenant="tenant_b")
    assert a["external_ref"] == b["external_ref"] == "ADR-0516"


def test_refused_create_writes_no_chain_record(_hermetic):
    _create("ADR-0600")
    n = len(_hermetic)
    with pytest.raises(TaskTrackingError):
        _create("ADR-0600")
    assert len(_hermetic) == n

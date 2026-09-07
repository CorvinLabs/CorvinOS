"""Phase A — snapshot schema, task-def parser, EventStore (ADR-0540).

All EventStore tests run against the REAL store under a temporary
``CORVIN_HOME`` with a recording audit callable (the core chain is exercised
in ``test_infinite_session_live_llm.py`` and the Phase D HTTP suite).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from core.infinite_session import (
    AutonomyLevel,
    EventStore,
    GateType,
    InvalidIdentifier,
    Snapshot,
    SnapshotMetadata,
    SnapshotType,
    TaskDefParser,
    snapshot_task_state,
)
from core.infinite_session.paths import safe_child, tenant_root, validate_id

TENANT = "_default"


def _audit_sink():
    sink: list = []

    def emit(event_type, *, tenant_id, details):
        sink.append((event_type, tenant_id, dict(details)))
        return f"ref-{len(sink)}"

    emit.sink = sink  # type: ignore[attr-defined]
    return emit


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def store(home):
    return EventStore(TENANT, audit=_audit_sink())


# ── identifiers / paths ───────────────────────────────────────────────────


class TestIdentifiers:
    @pytest.mark.parametrize("ok", ["task_1", "a.b-c", "UUID-1234", "x" * 128])
    def test_accepts_safe_ids(self, ok):
        assert validate_id(ok) == ok

    @pytest.mark.parametrize("bad", ["", "..", "a..b", "a/b", "a\\b", "a b", "a;b", "x" * 129, None, 5])
    def test_rejects_unsafe_ids(self, bad):
        with pytest.raises(InvalidIdentifier):
            validate_id(bad)

    def test_tenant_root_honours_corvin_home(self, home):
        assert tenant_root(TENANT) == home / "tenants" / TENANT / "infinite_session"
        assert tenant_root(TENANT, home / "other") == home / "other" / "tenants" / TENANT / "infinite_session"

    def test_tenant_root_rejects_traversal_tenant(self, home):
        with pytest.raises(ValueError):
            tenant_root("../etc")

    def test_safe_child_blocks_symlink_escape(self, home):
        root = home / "root"
        root.mkdir()
        (root / "link").symlink_to(home)
        with pytest.raises(ValueError):
            safe_child(root, "link", "escaped.json")


# ── Snapshot schema ───────────────────────────────────────────────────────


class TestSnapshotSchema:
    def test_create_and_verify(self):
        s = Snapshot.create(TENANT, "task_1", "phase_1", {"k": "v", "n": 1})
        assert s.content_hash == Snapshot.compute_hash({"k": "v", "n": 1})
        assert s.verify_hash({"k": "v", "n": 1}) and not s.verify_hash({"k": "x"})
        assert s.timestamp.endswith("Z")

    @pytest.mark.parametrize("tenant", ["", "  ", None, "../x"])
    def test_bad_tenant_fails_closed(self, tenant):
        with pytest.raises(ValueError):
            Snapshot.create(tenant, "task_1", "phase_1", {})

    @pytest.mark.parametrize("field", ["task_id", "phase_id"])
    @pytest.mark.parametrize("bad", ["..", "a/b", "a..b", "x y"])
    def test_bad_ids_fail_closed(self, field, bad):
        kwargs = {"tenant_id": TENANT, "task_id": "t", "phase_id": "p", "state_dict": {}}
        kwargs[field] = bad
        with pytest.raises(ValueError):
            Snapshot.create(**kwargs)

    def test_state_is_deep_copied(self):
        state = {"nested": {"a": [1]}}
        s = Snapshot.create(TENANT, "t", "p", state)
        state["nested"]["a"].append(2)
        assert s.state_dict == {"nested": {"a": [1]}}

    def test_hash_mismatch_rejected_on_construction(self):
        s = Snapshot.create(TENANT, "t", "p", {"a": 1})
        data = s.to_dict()
        data["state_dict"] = {"a": 2}
        with pytest.raises(ValueError, match="content_hash mismatch"):
            Snapshot.from_dict(data)

    def test_pii_rejected(self):
        with pytest.raises(ValueError, match="PII"):
            Snapshot.create(TENANT, "t", "p", {"contact": "john.doe@example.com"})

    def test_chain_link(self):
        a = Snapshot.create(TENANT, "t", "p", {"a": 1})
        b = Snapshot.create(TENANT, "t", "p", {"a": 2}, prev_snapshot_hash=a.content_hash)
        assert a.chain_link(b) and not b.chain_link(a)

    def test_roundtrip_and_metadata(self):
        s = Snapshot.create(TENANT, "t", "p", {"a": 1}, snapshot_type=SnapshotType.INTERMEDIATE)
        assert Snapshot.from_dict(json.loads(json.dumps(s.to_dict()))) == s
        m = SnapshotMetadata.from_snapshot(s, "/x", seq=3)
        assert SnapshotMetadata.from_dict(m.to_dict()) == m and m.seq == 3


# ── TaskDefParser ─────────────────────────────────────────────────────────


def _task_def(phases):
    return {
        "@context": "https://corvin.dev/schema/task-definition/v1",
        "task_id": "task_x",
        "task_name": "X",
        "description": "d",
        "autonomy_level": "3",
        "timeout_seconds": 3600,
        "phases": phases,
        "success_criteria": {},
    }


class TestTaskDefParser:
    def test_parse_and_topological_order(self):
        plan, error = TaskDefParser.parse(_task_def([
            {"phase_id": "p3", "phase_name": "P3", "dependencies": ["p2"]},
            {"phase_id": "p1", "phase_name": "P1"},
            {"phase_id": "p2", "phase_name": "P2", "dependencies": ["p1"]},
        ]))
        assert error == "" and plan is not None
        assert plan.phase_order == ["p1", "p2", "p3"]
        assert plan.autonomy_level == AutonomyLevel.LEVEL_3

    def test_cycle_detected(self):
        plan, error = TaskDefParser.parse(_task_def([
            {"phase_id": "a", "phase_name": "A", "dependencies": ["b"]},
            {"phase_id": "b", "phase_name": "B", "dependencies": ["a"]},
        ]))
        assert plan is None and error

    def test_unknown_dependency_fails(self):
        plan, error = TaskDefParser.parse(_task_def([
            {"phase_id": "a", "phase_name": "A", "dependencies": ["ghost"]},
        ]))
        assert plan is None and "unknown phase" in error.lower()

    def test_missing_required_field_fails(self):
        plan, error = TaskDefParser.parse({"task_id": "t"})
        assert plan is None and error

    def test_gates(self):
        plan, error = TaskDefParser.parse(_task_def([{
            "phase_id": "a", "phase_name": "A",
            "gates": [{"gate_id": "g1", "gate_type": "test_pass_rate", "params": {"min_rate": 0.95}}],
        }]))
        assert error == "" and plan.phases[0].gates[0].gate_type == GateType.TEST_PASS_RATE


# ── EventStore ────────────────────────────────────────────────────────────


class TestEventStore:
    def test_tenant_binding(self, home):
        with pytest.raises(ValueError):
            EventStore("")
        with pytest.raises(ValueError):
            EventStore("../x")
        s = EventStore(TENANT, audit=_audit_sink())
        assert s.root_dir == home / "tenants" / TENANT / "infinite_session" / "snapshots"

    def test_write_and_read(self, store):
        snap = Snapshot.create(TENANT, "task_1", "p1", {"a": 1})
        assert store.write_snapshot(snap) == (True, "")
        got, err = store.read_snapshot(TENANT, "task_1", snap.snapshot_id)
        assert err == ""
        # R4-F2: the store signs on write, so the stored record carries a
        # chain_mac the in-memory original does not. Everything the MAC
        # commits to must round-trip unchanged.
        assert got.mac_payload() == snap.mac_payload()
        assert got.chain_mac and got == snap.signed(got.chain_mac)
        assert store.list_tasks() == ["task_1"]

    def test_audit_first_and_content_free(self, home):
        audit = _audit_sink()
        s = EventStore(TENANT, audit=audit)
        snap = Snapshot.create(TENANT, "task_1", "p1", {"secret_value": 42})
        assert s.write_snapshot(snap) == (True, "")
        (event_type, tenant, details), = audit.sink
        assert event_type == "infinite_session.snapshot_created" and tenant == TENANT
        assert details["snapshot_id"] == snap.snapshot_id and details["content_hash"] == snap.content_hash
        assert details["seq"] == 1 and "state_dict" not in details
        # Content-freedom asserted on the KEY and the VALUE, not on the string
        # "42": a random uuid4 snapshot_id contains "42" roughly 1 run in 5,
        # which made this assertion flaky rather than meaningful.
        assert "secret_value" not in json.dumps(details)
        assert 42 not in details.values()

    def test_audit_failure_prevents_write(self, home):
        def refusing(event_type, *, tenant_id, details):
            raise RuntimeError("chain unavailable")
        s = EventStore(TENANT, audit=refusing)
        snap = Snapshot.create(TENANT, "task_1", "p1", {"a": 1})
        ok, err = s.write_snapshot(snap)
        assert not ok and "audit" in err
        assert store_files(s, "task_1") == []

    def test_write_refuses_other_tenant_snapshot(self, store):
        snap = Snapshot.create("tenant_b", "task_1", "p1", {"a": 1})
        ok, err = store.write_snapshot(snap)
        assert not ok and "Tenant mismatch" in err

    def test_write_refuses_duplicate_and_bad_link(self, store):
        a = Snapshot.create(TENANT, "task_1", "p1", {"a": 1})
        assert store.write_snapshot(a)[0]
        assert not store.write_snapshot(a)[0]  # append-only
        orphan = Snapshot.create(TENANT, "task_1", "p1", {"a": 2})  # prev=None on non-empty chain
        ok, err = store.write_snapshot(orphan)
        assert not ok and "chain link" in err
        wrong = Snapshot.create(TENANT, "task_1", "p1", {"a": 2}, prev_snapshot_hash="0" * 64)
        assert not store.write_snapshot(wrong)[0]

    def test_list_latest_verify(self, store):
        snaps = [snapshot_task_state(TENANT, "task_1", {"i": i}, phase_id=f"p{i % 2}", store=store)[0] for i in range(4)]
        index, err = store.list_snapshots(TENANT, "task_1")
        assert err == "" and [m.seq for m in index] == [1, 2, 3, 4]
        only_p1, _ = store.list_snapshots(TENANT, "task_1", phase_id="p1")
        assert [m.snapshot_id for m in only_p1] == [snaps[1].snapshot_id, snaps[3].snapshot_id]
        latest, _ = store.get_latest_snapshot(TENANT, "task_1")
        assert latest == snaps[-1]
        latest_p0, _ = store.get_latest_snapshot(TENANT, "task_1", phase_id="p0")
        assert latest_p0 == snaps[2]
        assert store.verify_snapshot_chain(TENANT, "task_1") == (True, "")

    def test_read_nonexistent(self, store):
        got, err = store.read_snapshot(TENANT, "task_1", "missing")
        assert got is None and "not found" in err
        assert store.list_snapshots(TENANT, "never") == ([], "")
        assert store.get_latest_snapshot(TENANT, "never")[0] is None

    def test_delete_snapshots_before_keeps_head(self, store):
        snaps = [snapshot_task_state(TENANT, "task_1", {"i": i}, store=store)[0] for i in range(3)]
        count, err = store.delete_snapshots_before(TENANT, "task_1", "9999-12-31T00:00:00Z")
        assert err == "" and count == 2
        index, _ = store.list_snapshots(TENANT, "task_1")
        assert [m.snapshot_id for m in index] == [snaps[-1].snapshot_id]
        assert store.read_snapshot(TENANT, "task_1", snaps[0].snapshot_id)[0] is None


def store_files(store: EventStore, task_id: str) -> list[Path]:
    d = store.root_dir / task_id
    return sorted(p for p in d.glob("*.json")) if d.exists() else []


# ── Adversarial ───────────────────────────────────────────────────────────


class TestAdversarial:
    def test_cross_tenant_read_and_write_refused(self, home):
        a = EventStore("tenant_a", audit=_audit_sink())
        b = EventStore("tenant_b", audit=_audit_sink())
        snap, err = snapshot_task_state("tenant_a", "shared_task", {"a": 1}, store=a)
        assert err == ""
        assert b.read_snapshot("tenant_a", "shared_task", snap.snapshot_id)[0] is None
        assert b.read_snapshot("tenant_b", "shared_task", snap.snapshot_id)[0] is None
        assert b.list_tasks() == []
        assert "Tenant mismatch" in a.list_snapshots("tenant_b", "shared_task")[1]

    @pytest.mark.parametrize("bad", ["../other", "..", "a/b", "x\x00y"])
    def test_traversal_ids_rejected(self, store, bad):
        assert store.read_snapshot(TENANT, bad, "s")[0] is None
        assert store.read_snapshot(TENANT, "t", bad)[0] is None
        assert store.list_snapshots(TENANT, bad)[0] == []
        assert not (store.root_dir.parent.parent / "other").exists()

    def test_snapshot_tampering_detected(self, store):
        snap, _ = snapshot_task_state(TENANT, "task_1", {"a": 1}, store=store)
        f = store.root_dir / "task_1" / f"{snap.snapshot_id}.json"
        data = json.loads(f.read_text())
        data["state_dict"]["a"] = 2
        f.write_text(json.dumps(data))
        got, err = store.read_snapshot(TENANT, "task_1", snap.snapshot_id)
        assert got is None and "mismatch" in err
        assert store.verify_snapshot_chain(TENANT, "task_1")[0] is False

    def test_hash_chain_tampering_detected(self, store):
        s1, _ = snapshot_task_state(TENANT, "task_1", {"a": 1}, store=store)
        s2, _ = snapshot_task_state(TENANT, "task_1", {"a": 2}, store=store)
        # Re-point s2's prev link and re-hash consistently — the index still holds the truth
        f = store.root_dir / "task_1" / f"{s2.snapshot_id}.json"
        data = json.loads(f.read_text())
        data["prev_snapshot_hash"] = "f" * 64
        f.write_text(json.dumps(data))
        ok, err = store.verify_snapshot_chain(TENANT, "task_1")
        # R4-F2: the keyed chain MAC covers prev_snapshot_hash, so re-pointing
        # a link is caught by the MAC before the link comparison is reached —
        # which is the point: an attacker with file access cannot re-sign.
        assert not ok and "Chain MAC mismatch" in err

    def test_index_tampering_detected(self, store):
        snapshot_task_state(TENANT, "task_1", {"a": 1}, store=store)
        index = store.root_dir / "task_1" / "index.json"
        data = json.loads(index.read_text())
        data[0]["content_hash"] = "0" * 64
        index.write_text(json.dumps(data))
        ok, err = store.verify_snapshot_chain(TENANT, "task_1")
        assert not ok and "hash mismatch" in err

    def test_concurrent_writers_never_corrupt_chain(self, home):
        errors: list[str] = []

        def writer(n: int):
            s = EventStore(TENANT, audit=_audit_sink())
            for i in range(10):
                _, err = snapshot_task_state(TENANT, "task_c", {"w": n, "i": i}, store=s)
                if err and "chain link" not in err:
                    errors.append(err)

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        s = EventStore(TENANT, audit=_audit_sink())
        assert s.verify_snapshot_chain(TENANT, "task_c") == (True, "")
        index, _ = s.list_snapshots(TENANT, "task_c")
        assert [m.seq for m in index] == list(range(1, len(index) + 1))


# ── E2E: plan → phases → chained snapshots → restart → recover ────────────


def test_e2e_plan_to_snapshots_to_recovery(home):
    plan, error = TaskDefParser.parse(_task_def([
        {"phase_id": "p1", "phase_name": "P1"},
        {"phase_id": "p2", "phase_name": "P2", "dependencies": ["p1"]},
    ]))
    assert error == ""
    store = EventStore(TENANT, audit=_audit_sink())
    for phase_id in plan.phase_order:
        snap, err = snapshot_task_state(TENANT, plan.task_id, {"phase": phase_id, "done": True},
                                        phase_id=phase_id, store=store)
        assert err == "" and snap.phase_id == phase_id

    # "restart": brand-new store instance, same root
    recovered = EventStore(TENANT, audit=_audit_sink())
    assert recovered.verify_snapshot_chain(TENANT, plan.task_id) == (True, "")
    latest, err = recovered.get_latest_snapshot(TENANT, plan.task_id)
    assert err == "" and latest.state_dict == {"phase": "p2", "done": True}
    assert latest.prev_snapshot_hash == Snapshot.compute_hash({"phase": "p1", "done": True})

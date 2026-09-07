"""Phase B — crypto binding, session bridging, audit verification (ADR-0541)."""

from __future__ import annotations

import json
import os
import stat

import pytest

from core.infinite_session import (
    AuditVerifier,
    CryptoBinding,
    EventStore,
    SessionBridgeEvent,
    SessionBridger,
    Snapshot,
    VerificationStatus,
    snapshot_task_state,
)

TENANT = "_default"


def _audit_sink():
    sink: list = []

    def emit(event_type, *, tenant_id, details):
        sink.append((event_type, tenant_id, dict(details)))
        return f"ref-{len(sink)}"

    emit.sink = sink  # type: ignore[attr-defined]
    return emit


def _callback(events: list):
    def cb(**kwargs):
        events.append(kwargs)
        return True
    return cb


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def store(home):
    return EventStore(TENANT, audit=_audit_sink())


@pytest.fixture
def crypto(home):
    return CryptoBinding()


@pytest.fixture
def bridger(store, crypto):
    return SessionBridger(store, crypto)


def _chain(store, task_id="task_1", n=2):
    return [snapshot_task_state(TENANT, task_id, {"i": i}, phase_id=f"p{i}", store=store)[0] for i in range(n)]


# ── CryptoBinding ─────────────────────────────────────────────────────────


class TestCryptoBinding:
    def test_key_lives_under_tenant_root_0600(self, home, crypto):
        sig, err = crypto.sign_payload(TENANT, {"a": 1})
        assert err == "" and len(sig) == 64
        key = home / "tenants" / TENANT / "infinite_session" / "keys" / "signing.key"
        assert key.exists()
        assert stat.S_IMODE(os.stat(key).st_mode) == 0o600

    def test_sign_verify_roundtrip_canonical(self, crypto):
        sig, _ = crypto.sign_payload(TENANT, {"b": 2, "a": [1, {"z": 1, "y": 2}]})
        assert crypto.verify_payload(TENANT, {"a": [1, {"y": 2, "z": 1}], "b": 2}, sig) == (True, "")
        assert crypto.verify_payload(TENANT, {"a": [1, {"y": 2, "z": 1}], "b": 3}, sig)[0] is False
        assert crypto.verify_payload(TENANT, {"b": 2, "a": [1, {"z": 1, "y": 2}]}, sig[:-1] + "0")[0] is False

    def test_deterministic(self, crypto):
        assert crypto.sign_payload(TENANT, {"x": 1})[0] == crypto.sign_payload(TENANT, {"x": 1})[0]

    @pytest.mark.parametrize("tenant", ["", None, "../x"])
    def test_bad_tenant_fails_closed(self, crypto, tenant):
        assert crypto.sign_payload(tenant, {"x": 1})[0] is None
        assert crypto.verify_payload(tenant, {"x": 1}, "ab")[0] is False

    def test_empty_payload_and_signature_fail_closed(self, crypto):
        assert crypto.sign_payload(TENANT, {})[0] is None
        sig, _ = crypto.sign_payload(TENANT, {"x": 1})
        assert crypto.verify_payload(TENANT, {"x": 1}, "")[0] is False

    def test_verify_never_creates_a_key(self, home, crypto):
        assert crypto.verify_payload("tenant_new", {"x": 1}, "ab")[0] is False
        assert not (home / "tenants" / "tenant_new").exists()

    def test_cross_tenant_key_isolation(self, crypto):
        sig, _ = crypto.sign_payload("tenant_a", {"x": 1})
        crypto.sign_payload("tenant_b", {"x": 1})
        assert crypto.verify_payload("tenant_b", {"x": 1}, sig)[0] is False

    def test_mismatch_is_audited(self, crypto):
        events: list = []
        sig, _ = crypto.sign_payload(TENANT, {"x": 1})
        crypto.verify_payload(TENANT, {"x": 2}, sig, audit_callback=_callback(events))
        assert events[-1]["event_type"] == "signature_verification_failed"

    def test_rotation_archives_and_invalidates(self, home, crypto):
        sig, _ = crypto.sign_payload(TENANT, {"x": 1})
        events: list = []
        assert crypto.rotate_key(TENANT, audit_callback=_callback(events)) == (True, "")
        assert events[-1]["event_type"] == "key_rotated"
        archive = home / "tenants" / TENANT / "infinite_session" / "keys" / "archive"
        assert len(list(archive.glob("*.key.old"))) == 1
        assert crypto.verify_payload(TENANT, {"x": 1}, sig)[0] is False

    def test_string_hash_api(self, crypto):
        sig, err = crypto.sign_snapshot(TENANT, "abc")
        assert err == "" and crypto.verify_signature(TENANT, "abc", sig) == (True, "")
        assert crypto.sign_snapshot(TENANT, "")[0] is None


# ── SessionBridger ────────────────────────────────────────────────────────


class TestSessionBridger:
    def test_create_signs_whole_event_and_persists(self, home, store, bridger):
        s1, s2 = _chain(store)
        events: list = []
        bridge, err = bridger.create_bridge(
            TENANT, "task_1", "sess_a", "sess_b", s2, "p1",
            artifacts=["ADR-1"], metadata={"k": 1}, audit_callback=_callback(events),
        )
        assert err == "" and bridge.snapshot_id == s2.snapshot_id
        assert bridge.prev_hash == s1.content_hash
        assert bridger.crypto_binding.verify_payload(TENANT, bridge.signed_payload(), bridge.signature) == (True, "")
        path = home / "tenants" / TENANT / "infinite_session" / "bridges" / "task_1" / f"{bridge.bridge_id}.json"
        assert path.exists()
        assert [e["event_type"] for e in events][-1] == "task_session_bridged"
        assert "state_dict" not in json.dumps(events)

    def test_fail_closed_on_bad_inputs(self, store, bridger):
        s1, = _chain(store, n=1)
        assert bridger.create_bridge("", "task_1", "a", "b", s1, "p")[0] is None
        assert bridger.create_bridge("tenant_b", "task_1", "a", "b", s1, "p")[0] is None
        assert bridger.create_bridge(TENANT, "task_1", "", "b", s1, "p")[0] is None
        assert bridger.create_bridge(TENANT, "other_task", "a", "b", s1, "p")[0] is None

    def test_unpersisted_snapshot_cannot_be_bridged(self, store, bridger):
        floating = Snapshot.create(TENANT, "task_1", "p", {"x": 1})
        bridge, err = bridger.create_bridge(TENANT, "task_1", "a", "b", floating, "p")
        assert bridge is None and "not persisted" in err

    def test_resume_returns_verified_state(self, store, bridger):
        s1, s2 = _chain(store)
        bridge, _ = bridger.create_bridge(TENANT, "task_1", "a", "b", s2, "p1")
        events: list = []
        state, err = bridger.resume_from_bridge(TENANT, "task_1", bridge.bridge_id, audit_callback=_callback(events))
        assert err == ""
        assert state["state_dict"] == {"i": 1} and state["snapshot_hash"] == s2.content_hash
        assert state["source_session_id"] == "a" and state["dest_session_id"] == "b"
        assert events[-1]["event_type"] == "session_resumed"

    def test_resume_rejects_wrong_tenant_or_task(self, home, store, bridger):
        s1, = _chain(store, n=1)
        bridge, _ = bridger.create_bridge(TENANT, "task_1", "a", "b", s1, "p0")
        assert bridger.resume_from_bridge("tenant_b", "task_1", bridge.bridge_id)[0] is None
        assert bridger.resume_from_bridge(TENANT, "task_2", bridge.bridge_id)[0] is None
        assert bridger.resume_from_bridge(TENANT, "task_1", "../" + bridge.bridge_id)[0] is None

    def test_list_bridges(self, store, bridger):
        s1, s2 = _chain(store)
        bridger.create_bridge(TENANT, "task_1", "a", "b", s1, "p0")
        bridger.create_bridge(TENANT, "task_1", "b", "c", s2, "p1")
        bridges, err = bridger.list_bridges(TENANT, "task_1")
        assert err == "" and [b.phase_completed for b in bridges] == ["p0", "p1"]
        assert bridger.list_bridges("tenant_b", "task_1")[0] == []


class TestAdversarialSessionBridger:
    @pytest.mark.parametrize("field,value", [
        ("phase_completed", "evil"),
        ("artifacts", ["planted"]),
        ("metadata", {"role": "admin"}),
        ("dest_session_id", "attacker"),
        ("snapshot_hash", "0" * 64),
        ("prev_hash", "genesis"),
        ("timestamp", "1999-01-01T00:00:00+00:00"),
    ])
    def test_any_field_tamper_invalidates(self, home, store, bridger, field, value):
        s1, s2 = _chain(store)
        bridge, _ = bridger.create_bridge(TENANT, "task_1", "a", "b", s2, "p1", metadata={"role": "user"})
        path = bridger._bridge_file("task_1", bridge.bridge_id)
        data = json.loads(path.read_text())
        data[field] = value
        path.write_text(json.dumps(data))
        state, err = bridger.resume_from_bridge(TENANT, "task_1", bridge.bridge_id)
        assert state is None and "Signature verification failed" in err

    def test_snapshot_swap_detected(self, home, store, bridger):
        s1, s2 = _chain(store)
        bridge, _ = bridger.create_bridge(TENANT, "task_1", "a", "b", s2, "p1")
        # Point the bridge at s1 with s1's hash by re-signing with a foreign key: impossible;
        # simulate a store-side swap instead: delete s2 → resume must fail-closed
        (store.root_dir / "task_1" / f"{s2.snapshot_id}.json").unlink()
        state, err = bridger.resume_from_bridge(TENANT, "task_1", bridge.bridge_id)
        assert state is None and "snapshot unavailable" in err

    def test_foreign_tenant_bridge_file_rejected(self, home, store, bridger):
        s1, = _chain(store, n=1)
        bridge, _ = bridger.create_bridge(TENANT, "task_1", "a", "b", s1, "p0")
        path = bridger._bridge_file("task_1", bridge.bridge_id)
        data = json.loads(path.read_text())
        data["tenant_id"] = "tenant_b"
        path.write_text(json.dumps(data))
        assert bridger.resume_from_bridge(TENANT, "task_1", bridge.bridge_id)[0] is None


# ── AuditVerifier ─────────────────────────────────────────────────────────


class TestAuditVerifier:
    def test_pass_and_persist(self, home, store, crypto, bridger):
        s1, s2 = _chain(store)
        bridger.create_bridge(TENANT, "task_1", "a", "b", s2, "p1")
        v = AuditVerifier(store, crypto, bridger)
        events: list = []
        result, err = v.verify_task_chain(TENANT, "task_1", audit_callback=_callback(events))
        assert err == "" and result.status == VerificationStatus.PASS
        assert result.event_count == 2 and result.session_count == 2
        assert events[-1]["event_type"] == "audit_chain_verified"
        assert v.get_verification_status(TENANT, "task_1")[0].status == VerificationStatus.PASS

    def test_empty_task_passes(self, store, crypto):
        result, err = AuditVerifier(store, crypto).verify_task_chain(TENANT, "nothing")
        assert err == "" and result.status == VerificationStatus.PASS and result.event_count == 0

    def test_detects_chain_break_and_bridge_tamper(self, home, store, crypto, bridger):
        s1, s2 = _chain(store)
        bridge, _ = bridger.create_bridge(TENANT, "task_1", "a", "b", s2, "p1")
        v = AuditVerifier(store, crypto, bridger)
        path = bridger._bridge_file("task_1", bridge.bridge_id)
        data = json.loads(path.read_text()); data["phase_completed"] = "x"; path.write_text(json.dumps(data))
        result, _ = v.verify_task_chain(TENANT, "task_1")
        assert result.status == VerificationStatus.FAIL_SIGNATURE_MISMATCH
        index = store.root_dir / "task_1" / "index.json"
        data = json.loads(index.read_text()); data[0]["content_hash"] = "0" * 64; index.write_text(json.dumps(data))
        result, _ = v.verify_task_chain(TENANT, "task_1")
        assert result.status == VerificationStatus.FAIL_CHAIN_BROKEN and len(result.errors) >= 2

    def test_verify_all_tasks(self, store, crypto):
        _chain(store, "t1"); _chain(store, "t2", n=1)
        results, err = AuditVerifier(store, crypto).verify_all_tasks(TENANT)
        assert err == "" and sorted(r.task_id for r in results) == ["t1", "t2"]
        assert AuditVerifier(store, crypto).verify_all_tasks("tenant_b")[0] == []

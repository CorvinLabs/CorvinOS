"""Integration tests for discovery_coordinator.py — audit chain + fail-closed handshake.

Rewritten 2026-09-27 (adversarial review round 2). The previous version mocked
``security_events.emit_discovery_event`` — a function that never existed — so
every test either errored or proved nothing, and ``TestAuditFailureHandling``
asserted the fail-OPEN behaviour (pairing continues when audit fails) that
CLAUDE.md forbids. These tests read the REAL tenant audit chain written by
``forge.security_events.write_event`` (via ``core/deployment/audit_sink.py``).
"""
import json
import time

import pytest

from core.deployment.audit_sink import AuditWriteFailed
from core.discovery.discovery_coordinator import (
    HANDSHAKE_NOT_IMPLEMENTED,
    DiscoveryCoordinator,
    InstanceIdentity,
    PairingRecord,
    PairingState,
)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "corvin"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "home" / ".config"))
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    monkeypatch.delenv("CORVIN_TENANT_ID", raising=False)
    yield


def _chain(tenant="_default"):
    from forge import paths as fp

    return fp.tenant_audit_chain(tenant)


def _records(tenant="_default"):
    p = _chain(tenant)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def _coord(tenant="_default"):
    return DiscoveryCoordinator(InstanceIdentity("org", "inst", "a" * 64), tenant_id=tenant)


class TestAuditChainWrites:
    def test_token_creation_writes_chained_record(self, monkeypatch):
        monkeypatch.setattr("core.discovery.discovery_coordinator.get_my_url", lambda: "http://local:8775")
        monkeypatch.setattr("core.discovery.discovery_coordinator.get_my_relay_url", lambda: "wss://relay.example.com")
        coord = _coord()
        token, token_str = coord.create_pairing_token(label="Alice's laptop")

        recs = [r for r in _records() if r["event_type"] == "discovery.pairing_token_created"]
        assert len(recs) == 1
        d = recs[0]["details"]
        assert d["tenant_id"] == "_default"
        assert d["lom"] == "DiscoveryCoordinator.create_pairing_token"
        assert d["label_present"] is True and d["relay_used"] is True
        # content-free: no plaintext kid, no label, no URL anywhere in the record
        raw = json.dumps(recs[0])
        assert token.kid not in raw
        assert "Alice" not in raw
        assert "relay.example.com" not in raw and "local:8775" not in raw
        assert recs[0]["hash"]

        from forge import security_events as se

        ok, issues = se.verify_chain(_chain())
        assert ok, issues

    def test_failed_import_is_audited_with_reason_code(self):
        coord = _coord()
        from corvin_operator.bridges.shared.a2a_friendship import FriendshipError

        with pytest.raises(FriendshipError):
            coord.import_pairing_token("corvin-a2a:ft1:garbage")
        recs = [r for r in _records() if r["event_type"] == "discovery.pairing_failed"]
        assert len(recs) == 1
        assert recs[0]["details"]["reason"] == "invalid_token"


class TestFailClosedAudit:
    def test_audit_failure_blocks_pairing_record(self, monkeypatch):
        """No chain commit → no pairing record (audit-FIRST, fail-closed)."""
        monkeypatch.setattr("core.discovery.discovery_coordinator.get_my_url", lambda: "http://local:8775")
        monkeypatch.setattr("core.discovery.discovery_coordinator.get_my_relay_url", lambda: None)

        def boom(*a, **k):
            raise AuditWriteFailed("disk full")

        monkeypatch.setattr("core.discovery.discovery_coordinator.audit_sink.emit", boom)
        coord = _coord()
        with pytest.raises(AuditWriteFailed):
            coord.create_pairing_token(label="peer")
        assert coord.pairings == {}

    def test_audit_failure_blocks_active_transition(self, monkeypatch):
        coord = _coord()
        rec = PairingRecord("kid-1", None, PairingState.PENDING, "_default")
        rec.next_retry_at = time.time() - 1
        coord.pairings["kid-1"] = rec
        monkeypatch.setattr(coord, "_perform_handshake", lambda r: True)

        def boom(*a, **k):
            raise AuditWriteFailed("disk full")

        monkeypatch.setattr("core.discovery.discovery_coordinator.audit_sink.emit", boom)
        with pytest.raises(AuditWriteFailed):
            coord.attempt_handshake("kid-1")
        assert rec.state == PairingState.PENDING


class TestTenantIsolation:
    def test_foreign_tenant_write_is_refused(self):
        """A coordinator for tenant-b in a tenant-_default process cannot write."""
        coord = _coord("tenant-b")
        with pytest.raises(AuditWriteFailed):
            coord._emit_audit_event("discovery.pairing_failed", {"reason": "invalid_token"}, lom="t")
        assert not any(r["event_type"] == "discovery.pairing_failed" for r in _records("tenant-b"))

    def test_records_land_on_the_coordinators_tenant_chain(self, monkeypatch):
        monkeypatch.setenv("CORVIN_TENANT_ID", "tenant-a")
        coord = _coord("tenant-a")
        coord._emit_audit_event("discovery.pairing_failed", {"reason": "invalid_token"}, lom="t")
        assert [r["details"]["tenant_id"] for r in _records("tenant-a")
                if r["event_type"] == "discovery.pairing_failed"] == ["tenant-a"]
        assert not any(r["event_type"] == "discovery.pairing_failed" for r in _records("_default"))

    def test_get_pairing_state_tenant_isolated(self):
        coord = DiscoveryCoordinator.__new__(DiscoveryCoordinator)
        coord.instance = InstanceIdentity("org", "inst", "a" * 64)
        coord.tenant_id = None
        coord.pairings = {"kid": PairingRecord("kid", None, PairingState.PENDING, "tenant")}
        assert coord.get_pairing_state("kid") is None

    def test_undeclared_event_is_refused(self):
        with pytest.raises(AuditWriteFailed):
            _coord()._emit_audit_event("discovery.not_declared", {}, lom="t")


class TestStateTransitionAudit:
    def test_default_transport_fails_closed_and_is_audited(self):
        coord = _coord()
        rec = PairingRecord("kid-001", "peer", PairingState.PENDING, "_default", hmac_key="k")
        rec.next_retry_at = time.time() - 1
        coord.pairings[rec.kid] = rec

        assert coord.attempt_handshake(rec.kid) is False
        assert rec.state == PairingState.FAILED
        assert rec.last_error == HANDSHAKE_NOT_IMPLEMENTED
        assert not any(r["event_type"] == "discovery.peer_paired" for r in _records())
        failed = [r for r in _records() if r["event_type"] == "discovery.peer_pairing_failed"]
        assert failed and failed[-1]["details"]["reason"] == HANDSHAKE_NOT_IMPLEMENTED
        # a FAILED pairing stays failed — no retry turns it ACTIVE later
        rec.next_retry_at = time.time() - 1
        assert coord.attempt_handshake(rec.kid) is False

    def test_injected_transport_success_is_audited(self, monkeypatch):
        coord = _coord()
        rec = PairingRecord("kid-002", "peer", PairingState.PENDING, "_default")
        rec.next_retry_at = time.time() - 1
        coord.pairings[rec.kid] = rec
        monkeypatch.setattr(coord, "_perform_handshake", lambda r: True)

        assert coord.attempt_handshake(rec.kid) is True
        paired = [r for r in _records() if r["event_type"] == "discovery.peer_paired"]
        assert len(paired) == 1
        assert paired[0]["details"]["attempts"] == 1
        assert "kid-002" not in json.dumps(paired[0])


class TestAuditDict:
    def test_audit_dict_excludes_sensitive_fields(self):
        record = PairingRecord(
            kid="plaintext-secret", peer_label="label", state=PairingState.ACTIVE,
            tenant_id="tenant", hmac_key="sensitive-key", recv_key="another-key",
        )
        d = record.to_audit_dict()
        assert "kid" not in d and "hmac_key" not in d and "recv_key" not in d
        assert d["kid_hash"] != "plaintext-secret"
        assert d["tenant_id"] == "tenant"

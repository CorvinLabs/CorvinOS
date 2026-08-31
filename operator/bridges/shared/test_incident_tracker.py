"""Tests for L39 Incident Tracker (ADR-0057)."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def tmp_corvin_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    return tmp_path


@pytest.fixture()
def tracker():
    import incident_tracker
    return incident_tracker


class TestIncidentRecord:
    def test_valid_record(self, tracker):
        r = tracker.IncidentRecord(
            incident_id="abc123",
            detected_at="2026-05-26T10:00:00+00:00",
            category="chain_integrity",
            severity="serious",
            trigger_event="audit.chain_gap_detected",
            trigger_chain_hash="a1b2c3d4e5f60001",
            description="Test incident",
            status="open",
        )
        assert r.category == "chain_integrity"
        assert r.severity == "serious"

    def test_invalid_category(self, tracker):
        with pytest.raises(ValueError, match="unknown category"):
            tracker.IncidentRecord(
                incident_id="x",
                detected_at="2026-05-26T10:00:00+00:00",
                category="nonexistent",
                severity="serious",
                trigger_event="audit.chain_gap_detected",
                trigger_chain_hash="a" * 16,
                description="bad",
                status="open",
            )

    def test_invalid_severity(self, tracker):
        with pytest.raises(ValueError, match="unknown severity"):
            tracker.IncidentRecord(
                incident_id="x",
                detected_at="2026-05-26T10:00:00+00:00",
                category="chain_integrity",
                severity="catastrophic",
                trigger_event="x",
                trigger_chain_hash="a" * 16,
                description="bad",
                status="open",
            )

    def test_roundtrip(self, tracker):
        r = tracker.IncidentRecord(
            incident_id="roundtrip-id",
            detected_at="2026-05-26T10:00:00+00:00",
            category="consent_bypass",
            severity="warning",
            trigger_event="manual",
            trigger_chain_hash="1234567890abcdef",
            description="Test roundtrip",
            status="open",
        )
        d = r.to_dict()
        r2 = tracker.IncidentRecord.from_dict(d)
        assert r2.incident_id == r.incident_id
        assert r2.category == r.category
        assert r2.description == r.description


class TestOpenIncident:
    def test_creates_file(self, tracker, tmp_corvin_home):
        r = tracker.open_incident(
            category="chain_integrity",
            trigger_event="audit.chain_gap_detected",
            trigger_chain_hash="a1b2c3d4e5f60001",
            description="Test",
            tenant_id="test_tenant",
        )
        p = tmp_corvin_home / "tenants" / "test_tenant" / "global" / "incidents" / f"{r.incident_id}.json"
        assert p.exists()
        assert oct(p.stat().st_mode & 0o777) == oct(0o600)
        data = json.loads(p.read_text())
        assert data["incident_id"] == r.incident_id
        assert data["status"] == "open"
        assert data["severity"] == "serious"

    def test_description_not_in_audit(self, tracker, tmp_corvin_home):
        r = tracker.open_incident(
            category="secret_exposure",
            trigger_event="path_gate.denied",
            trigger_chain_hash="deadbeef12345678",
            description="SENSITIVE DESCRIPTION THAT MUST NOT LEAK",
            tenant_id="_default",
        )
        audit_path = tmp_corvin_home / "audit.jsonl"
        if audit_path.exists():
            content = audit_path.read_text()
            assert "SENSITIVE DESCRIPTION" not in content

    def test_hash_truncated(self, tracker, tmp_corvin_home):
        r = tracker.open_incident(
            category="chain_integrity",
            trigger_event="audit.chain_gap_detected",
            trigger_chain_hash="a" * 32,  # longer than 16
            description="Test",
            tenant_id="_default",
        )
        assert len(r.trigger_chain_hash) == 16


class TestUpdateClose:
    def test_update_status(self, tracker, tmp_corvin_home):
        r = tracker.open_incident(
            category="consent_bypass",
            trigger_event="manual",
            trigger_chain_hash="0" * 16,
            description="Test",
            tenant_id="_default",
        )
        r2 = tracker.update_incident(r.incident_id, "contained", tenant_id="_default")
        assert r2.status == "contained"

    def test_close_records_duration(self, tracker, tmp_corvin_home):
        r = tracker.open_incident(
            category="disclosure_failure",
            trigger_event="manual",
            trigger_chain_hash="0" * 16,
            description="Test",
            tenant_id="_default",
        )
        r2 = tracker.close_incident(r.incident_id, tenant_id="_default")
        assert r2.status == "closed"
        assert r2.closed_at is not None

    def test_update_nonexistent(self, tracker, tmp_corvin_home):
        with pytest.raises(FileNotFoundError):
            tracker.update_incident("nonexistent-id", "contained", tenant_id="_default")


class TestListLoad:
    def test_list_empty(self, tracker, tmp_corvin_home):
        results = tracker.list_incidents(tenant_id="empty_tenant")
        assert results == []

    def test_list_filter(self, tracker, tmp_corvin_home):
        tracker.open_incident(
            category="chain_integrity", trigger_event="manual",
            trigger_chain_hash="0" * 16, description="A", tenant_id="_default",
        )
        r2 = tracker.open_incident(
            category="consent_bypass", trigger_event="manual",
            trigger_chain_hash="0" * 16, description="B", tenant_id="_default",
        )
        tracker.close_incident(r2.incident_id, tenant_id="_default")
        open_only = tracker.list_incidents(tenant_id="_default", status="open")
        closed_only = tracker.list_incidents(tenant_id="_default", status="closed")
        assert len(open_only) == 1
        assert len(closed_only) == 1


class TestNotifyDraft:
    def test_draft_contains_incident_id(self, tracker, tmp_corvin_home):
        r = tracker.open_incident(
            category="chain_integrity",
            trigger_event="audit.chain_gap_detected",
            trigger_chain_hash="cafebabe12345678",
            description="Chain gap detected",
            tenant_id="_default",
        )
        draft = tracker.notify_draft(r.incident_id, tenant_id="_default")
        assert r.incident_id in draft
        assert "Art. 73" in draft
        assert "[OPERATOR: FILL IN]" in draft
        assert "cafebabe12345678" in draft

    def test_draft_nonexistent(self, tracker, tmp_corvin_home):
        with pytest.raises(FileNotFoundError):
            tracker.notify_draft("does-not-exist", tenant_id="_default")


class TestAutoDetector:
    def test_triggers_on_critical(self, tracker, tmp_corvin_home):
        detector = tracker.IncidentAutoDetector()
        event = {
            "event": "audit.chain_gap_detected",
            "severity": "CRITICAL",
            "hash": "abcd1234efgh5678",
        }
        record = detector.on_audit_event(event, tenant_id="_default")
        assert record is not None
        assert record.category == "chain_integrity"

    def test_skips_non_critical(self, tracker, tmp_corvin_home):
        detector = tracker.IncidentAutoDetector()
        event = {
            "event": "audit.chain_gap_detected",
            "severity": "WARNING",
        }
        record = detector.on_audit_event(event, tenant_id="_default")
        assert record is None

    def test_skips_unknown_event(self, tracker, tmp_corvin_home):
        detector = tracker.IncidentAutoDetector()
        event = {
            "event": "bridge.message_received",
            "severity": "CRITICAL",
        }
        record = detector.on_audit_event(event, tenant_id="_default")
        assert record is None

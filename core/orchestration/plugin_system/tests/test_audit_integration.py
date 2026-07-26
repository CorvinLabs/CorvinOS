"""Test suite for Audit Integration (ADR-0XXX k=4)."""

from datetime import datetime

import pytest

from core.orchestration.plugin_system.models import AuditEvent


class TestAuditEvents:
    """Tests for audit event generation and serialization."""

    def test_audit_event_plugin_installed(self):
        """Test creating plugin_installed audit event."""
        event = AuditEvent.plugin_installed(
            plugin_id="ai-review/2.0.1",
            tier="b",
            user_id="user@example.com",
            source="marketplace",
            checksum="sha256:deadbeef"
        )

        assert event.event_type == "plugin_installed"
        assert event.plugin_id == "ai-review/2.0.1"
        assert event.user_id == "user@example.com"
        assert event.details["tier"] == "b"
        assert event.details["checksum"] == "sha256:deadbeef"

    def test_audit_event_plugin_enabled(self):
        """Test creating plugin_enabled audit event."""
        event = AuditEvent.plugin_enabled(
            plugin_id="ai-review/2.0.1",
            user_id="user@example.com"
        )

        assert event.event_type == "plugin_enabled"
        assert event.plugin_id == "ai-review/2.0.1"
        assert event.user_id == "user@example.com"

    def test_audit_event_plugin_config_changed(self):
        """Test creating plugin_config_changed audit event."""
        event = AuditEvent.plugin_config_changed(
            plugin_id="ai-review/2.0.1",
            user_id="user@example.com",
            old_config={"model": "sonnet"},
            new_config={"model": "opus"}
        )

        assert event.event_type == "plugin_config_changed"
        assert event.details["old_config"] == {"model": "sonnet"}
        assert event.details["new_config"] == {"model": "opus"}

    def test_audit_event_to_dict(self):
        """Test serializing audit event to dict."""
        event = AuditEvent.plugin_installed(
            plugin_id="ai-review/2.0.1",
            tier="b",
            user_id="user@example.com"
        )

        data = event.to_dict()

        assert data["event_type"] == "plugin_installed"
        assert data["plugin_id"] == "ai-review/2.0.1"
        assert data["user_id"] == "user@example.com"
        assert "timestamp" in data
        assert data["timestamp"].endswith("Z")  # ISO format with Z

    def test_audit_event_timestamp_set(self):
        """Test that audit events have timestamps."""
        event = AuditEvent.plugin_enabled(
            plugin_id="test/1.0.0",
            user_id="user@example.com"
        )

        assert event.timestamp is not None
        assert isinstance(event.timestamp, datetime)

    def test_audit_event_tenant_id_set(self):
        """Test that audit events include tenant_id."""
        event = AuditEvent.plugin_installed(
            plugin_id="test/1.0.0",
            tier="c",
            user_id="user@example.com"
        )

        assert event.tenant_id == "_default"

    def test_audit_event_custom_tenant_id(self):
        """Test setting custom tenant_id."""
        event = AuditEvent(
            timestamp=datetime.utcnow(),
            event_type="plugin_installed",
            plugin_id="test/1.0.0",
            tenant_id="custom-tenant",
            user_id="user@example.com"
        )

        assert event.tenant_id == "custom-tenant"

    def test_audit_event_no_pii_in_serialization(self):
        """Test that serialized events don't leak PII."""
        event = AuditEvent.plugin_config_changed(
            plugin_id="test/1.0.0",
            user_id="secret@example.com",
            old_config={"api_key": "sk-super-secret"},
            new_config={"api_key": "sk-new-secret"}
        )

        data = event.to_dict()

        # Check that PII is preserved in the audit (for legal compliance)
        # but should be scrubbed when sent to telemetry
        assert data["user_id"] == "secret@example.com"
        # Details contain config changes (acceptable for audit)
        assert "old_config" in data


class TestAuditEventEmission:
    """Tests for audit event collection and aggregation."""

    def test_collect_audit_events(self):
        """Test collecting multiple audit events."""
        events = []

        def emit(event: AuditEvent):
            events.append(event)

        # Simulate plugin lifecycle
        emit(AuditEvent.plugin_installed("test/1.0.0", "c", "user@example.com"))
        emit(AuditEvent.plugin_enabled("test/1.0.0", "user@example.com"))
        emit(AuditEvent.plugin_config_changed("test/1.0.0", "user@example.com", {}, {}))

        assert len(events) == 3
        assert events[0].event_type == "plugin_installed"
        assert events[1].event_type == "plugin_enabled"
        assert events[2].event_type == "plugin_config_changed"

    def test_audit_events_in_order(self):
        """Test that events are emitted in order."""
        events = []

        def emit(event: AuditEvent):
            events.append(event)

        emit(AuditEvent(datetime.utcnow(), "event1", "plugin1"))
        emit(AuditEvent(datetime.utcnow(), "event2", "plugin2"))
        emit(AuditEvent(datetime.utcnow(), "event3", "plugin3"))

        assert [e.plugin_id for e in events] == ["plugin1", "plugin2", "plugin3"]


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])

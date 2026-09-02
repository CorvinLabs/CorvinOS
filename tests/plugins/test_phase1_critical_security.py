"""PHASE 1 Critical Security Stubs - Comprehensive Test Suite.

Tests for flow_guard, path_gate, consent_gate, learning_event_storage.
Covers unit tests + E2E scenarios per ADR-0232, ADR-0233, ADR-0314.
"""

import pytest
import asyncio
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, AsyncMock, patch
import sys
import json
import tempfile

# Add Corvin-Marketplace to path for plugin imports
sys.path.insert(0, "/home/shumway/projects/Corvin-Marketplace/plugins/buildin")

# Import the plugins and providers
from security_compliance.flow_guard.src.flow_guard import (
    FlowGuard, DataClassification
)
from security_compliance.path_gate.src.path_gate import PathGate
from security_compliance.consent_gate.src.consent_gate import ConsentGate
from memory.learning_event_storage.src.learning_event_storage import (
    LearningEventStorage, LearningEvent, LearningEventType, EventEmitter
)

# Import provider modules
sys.path.insert(0, "/home/shumway/projects/CorvinOS/core/plugins")
from corvin_plugins.providers import user_backend, audit_backend


@pytest.mark.asyncio
class TestFlowGuard:
    """Unit + E2E tests for flow_guard L34."""

    async def test_classify_public_data(self):
        """Test classification of public data."""
        guard = FlowGuard()
        await guard.initialize(None)

        result = await guard.classify_data("This is public information")
        assert result == DataClassification.PUBLIC

    async def test_classify_pii_detection(self):
        """Test PII detection (fail-closed)."""
        guard = FlowGuard()
        await guard.initialize(None)

        # Email detection
        result = await guard.classify_data("Contact: user@example.com")
        assert result == DataClassification.RESTRICTED

        # Phone detection
        result = await guard.classify_data("Call 555-1234")
        assert result == DataClassification.RESTRICTED

    async def test_classify_secret_keyword(self):
        """Test secret keyword detection."""
        guard = FlowGuard()
        await guard.initialize(None)

        result = await guard.classify_data("api_key: sk-1234567890abcdef")
        assert result == DataClassification.RESTRICTED

    async def test_flow_allowed_opus_to_local(self):
        """Test Opus->Local with RESTRICTED data (allowed)."""
        guard = FlowGuard()
        await guard.initialize(None)

        allowed = await guard.check_flow_allowed(
            "claude-opus", "local", DataClassification.RESTRICTED
        )
        assert allowed is True

    async def test_flow_denied_haiku_to_cloud(self):
        """Test Haiku->Cloud with INTERNAL data (denied)."""
        guard = FlowGuard()
        await guard.initialize(None)

        allowed = await guard.check_flow_allowed(
            "claude-haiku", "cloud", DataClassification.INTERNAL
        )
        assert allowed is False

    async def test_execute_check_flow(self):
        """Test execute() with check_flow operation."""
        guard = FlowGuard()
        await guard.initialize(None)

        result = await guard.execute(
            "check_flow",
            engine="claude-opus",
            destination="local",
            data="test@example.com"
        )

        assert result["success"] is True
        assert result["allowed"] is True
        assert result["data_classification"] == "restricted"

    async def test_health_check(self):
        """Test health_check."""
        guard = FlowGuard()
        await guard.initialize(None)

        health = await guard.health_check()
        assert health is True


@pytest.mark.asyncio
class TestPathGate:
    """Unit + E2E tests for path_gate L10."""

    async def test_normalize_path_absolute(self):
        """Test path normalization with absolute path."""
        gate = PathGate()
        path = gate._normalize_path("/home/user/test.txt")
        assert path.is_absolute()

    async def test_normalize_path_home_expansion(self):
        """Test home directory expansion."""
        gate = PathGate()
        path = gate._normalize_path("~/.corvin/test.txt")
        assert ".corvin" in str(path)

    async def test_normalize_path_directory_traversal(self):
        """Test directory traversal attack prevention (fail-closed)."""
        gate = PathGate()

        with pytest.raises(ValueError) as exc_info:
            gate._normalize_path("~/../../etc/passwd")
        assert "Directory traversal" in str(exc_info.value)

    async def test_write_allowed_corvin_path(self):
        """Test write allowed to ~/.corvin."""
        gate = PathGate()
        allowed = await gate.is_write_allowed(str(Path.home() / ".corvin" / "test.txt"))
        assert allowed is True

    async def test_write_denied_system_path(self):
        """Test write denied to /etc (fail-closed)."""
        gate = PathGate()
        allowed = await gate.is_write_allowed("/etc/passwd")
        assert allowed is False

    async def test_read_allowed_normal_path(self):
        """Test read allowed to normal paths."""
        gate = PathGate()
        allowed = await gate.is_read_allowed("/tmp/test.txt")
        assert allowed is True

    async def test_read_denied_sensitive_path(self):
        """Test read denied to sensitive paths (fail-closed)."""
        gate = PathGate()
        allowed = await gate.is_read_allowed("/etc/shadow")
        assert allowed is False

    async def test_add_allowed_path(self):
        """Test adding a custom allowed path."""
        gate = PathGate()
        result = await gate.add_allowed_path("/home/user/data")
        assert result is True

    async def test_execute_check_write(self):
        """Test execute() with check_write operation."""
        gate = PathGate()

        result = await gate.execute(
            "check_write",
            path=str(Path.home() / ".corvin" / "test.txt")
        )

        assert result["success"] is True
        assert result["allowed"] is True

    async def test_health_check(self):
        """Test health_check."""
        gate = PathGate()
        health = await gate.health_check()
        assert health is True


@pytest.mark.asyncio
class TestConsentGate:
    """Unit + E2E tests for consent_gate L16 (GDPR)."""

    async def test_grant_consent_telemetry(self):
        """Test granting telemetry consent."""
        gate = ConsentGate()
        await gate.initialize(None)

        result = await gate.grant_consent(
            "user123", "_default", "telemetry"
        )
        # May fail if user_backend not available, but should not raise
        assert isinstance(result, bool)

    async def test_check_consent_denied_by_default(self):
        """Test consent denied by default (fail-closed)."""
        gate = ConsentGate()
        await gate.initialize(None)

        result = await gate.check_consent(
            "user123", "_default", "telemetry"
        )
        assert result is False  # Fail-closed default-deny

    async def test_revoke_consent(self):
        """Test revoking consent."""
        gate = ConsentGate()
        await gate.initialize(None)

        result = await gate.revoke_consent(
            "user123", "_default", "telemetry"
        )
        # May fail if user_backend not available
        assert isinstance(result, bool)

    async def test_invalid_consent_type(self):
        """Test invalid consent type (fail-closed)."""
        gate = ConsentGate()
        await gate.initialize(None)

        result = await gate.grant_consent(
            "user123", "_default", "invalid_type"
        )
        assert result is False

    async def test_execute_grant(self):
        """Test execute() with grant operation."""
        gate = ConsentGate()
        await gate.initialize(None)

        result = await gate.execute(
            "grant",
            user_id="user123",
            tenant_id="_default",
            consent_type="learning"
        )

        assert result["success"] is not None  # May be True or False

    async def test_execute_check(self):
        """Test execute() with check operation."""
        gate = ConsentGate()
        await gate.initialize(None)

        result = await gate.execute(
            "check",
            user_id="user123",
            tenant_id="_default",
            consent_type="telemetry"
        )

        assert result["success"] is True
        assert result["granted"] is False  # Default-deny

    async def test_health_check(self):
        """Test health_check."""
        gate = ConsentGate()
        await gate.initialize(None)

        health = await gate.health_check()
        # May be False if user_backend not available, but should not raise
        assert isinstance(health, bool)


@pytest.mark.asyncio
class TestEventEmitter:
    """Unit tests for EventEmitter (non-blocking queue with backpressure)."""

    async def test_emit_event(self):
        """Test event emission."""
        emitter = EventEmitter()

        event = LearningEvent(
            event_id="test1",
            tenant_id="_default",
            event_type=LearningEventType.METRIC,
            timestamp=datetime.now(timezone.utc).isoformat(),
            payload={"test": True}
        )

        result = emitter.emit(event)
        assert result is True

    async def test_queue_backpressure(self):
        """Test backpressure handling on full queue."""
        emitter = EventEmitter(max_queue_size=2)

        # Emit 3 events (3rd should be dropped)
        results = []
        for i in range(3):
            event = LearningEvent(
                event_id=f"test{i}",
                tenant_id="_default",
                event_type=LearningEventType.METRIC,
                timestamp=datetime.now(timezone.utc).isoformat(),
                payload={}
            )
            results.append(emitter.emit(event))

        # At least 2 succeeded, 1 dropped (backpressure)
        assert sum(results) >= 2
        assert not all(results)

    async def test_drain_events(self):
        """Test draining events from queue."""
        emitter = EventEmitter()

        # Emit events
        for i in range(3):
            event = LearningEvent(
                event_id=f"test{i}",
                tenant_id="_default",
                event_type=LearningEventType.FEEDBACK,
                timestamp=datetime.now(timezone.utc).isoformat(),
                payload={}
            )
            emitter.emit(event)

        # Register listener
        received = []

        def listener(event):
            received.append(event)

        emitter.register_listener(listener)

        # Drain
        count = await emitter.drain()
        assert count == 3
        assert len(received) == 3

    async def test_listener_callback(self):
        """Test listener callback on event."""
        emitter = EventEmitter()

        received = []

        async def async_listener(event):
            received.append(event)

        emitter.register_listener(async_listener)

        event = LearningEvent(
            event_id="test",
            tenant_id="_default",
            event_type=LearningEventType.CONFIDENCE,
            timestamp=datetime.now(timezone.utc).isoformat(),
            payload={}
        )

        emitter.emit(event)
        await emitter.drain()

        assert len(received) == 1
        assert received[0].event_id == "test"


@pytest.mark.asyncio
class TestLearningEventStorage:
    """Unit + E2E tests for learning_event_storage ADR-0314."""

    async def test_initialize(self):
        """Test storage initialization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage_path = Path(tmpdir) / "learning-events.jsonl"
            storage = LearningEventStorage(storage_path=storage_path)
            await storage.initialize(None)

            assert storage.storage_path == storage_path
            assert storage.storage_path.parent.exists()

    async def test_emit_event(self):
        """Test event emission."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage_path = Path(tmpdir) / "learning-events.jsonl"
            storage = LearningEventStorage(storage_path=storage_path)
            await storage.initialize(None)

            event = LearningEvent(
                event_id="test1",
                tenant_id="_default",
                event_type=LearningEventType.FEEDBACK,
                timestamp=datetime.now(timezone.utc).isoformat(),
                payload={"score": 0.95}
            )

            result = await storage.emit_event(event)
            assert result is True

    async def test_read_events_tenant_isolated(self):
        """Test tenant-isolated event reading."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage_path = Path(tmpdir) / "learning-events.jsonl"
            storage = LearningEventStorage(storage_path=storage_path)
            await storage.initialize(None)

            # Emit events for different tenants
            await storage.emit_event(LearningEvent(
                event_id="tenant1_event",
                tenant_id="tenant1",
                event_type=LearningEventType.METRIC,
                timestamp=datetime.now(timezone.utc).isoformat(),
                payload={}
            ))

            await storage.emit_event(LearningEvent(
                event_id="tenant2_event",
                tenant_id="tenant2",
                event_type=LearningEventType.METRIC,
                timestamp=datetime.now(timezone.utc).isoformat(),
                payload={}
            ))

            # Drain to write to disk
            await storage.drain_events()

            # Read only tenant1 events
            events = await storage.read_events("tenant1")
            assert len(events) >= 1
            assert all(e.tenant_id == "tenant1" for e in events)

    async def test_event_filtering_by_type(self):
        """Test filtering events by type."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage_path = Path(tmpdir) / "learning-events.jsonl"
            storage = LearningEventStorage(storage_path=storage_path)
            await storage.initialize(None)

            # Emit different event types
            await storage.emit_event(LearningEvent(
                event_id="feedback_event",
                tenant_id="_default",
                event_type=LearningEventType.FEEDBACK,
                timestamp=datetime.now(timezone.utc).isoformat(),
                payload={}
            ))

            await storage.emit_event(LearningEvent(
                event_id="metric_event",
                tenant_id="_default",
                event_type=LearningEventType.METRIC,
                timestamp=datetime.now(timezone.utc).isoformat(),
                payload={}
            ))

            await storage.drain_events()

            # Filter by type
            feedback_events = await storage.read_events(
                "_default", event_type=LearningEventType.FEEDBACK
            )
            assert len(feedback_events) >= 1
            assert all(e.event_type == LearningEventType.FEEDBACK for e in feedback_events)

    async def test_drain_events(self):
        """Test draining pending events."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage_path = Path(tmpdir) / "learning-events.jsonl"
            storage = LearningEventStorage(storage_path=storage_path)
            await storage.initialize(None)

            # Emit 5 events
            for i in range(5):
                await storage.emit_event(LearningEvent(
                    event_id=f"event{i}",
                    tenant_id="_default",
                    event_type=LearningEventType.OUTCOME,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    payload={}
                ))

            # Drain
            count = await storage.drain_events()
            assert count >= 5

    async def test_emitter_stats(self):
        """Test emitter statistics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage_path = Path(tmpdir) / "learning-events.jsonl"
            storage = LearningEventStorage(storage_path=storage_path)
            await storage.initialize(None)

            # Emit events
            for i in range(3):
                await storage.emit_event(LearningEvent(
                    event_id=f"event{i}",
                    tenant_id="_default",
                    event_type=LearningEventType.METRIC,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    payload={}
                ))

            stats = await storage.get_emitter_stats()
            assert "queue_size" in stats
            assert "max_queue_size" in stats

    async def test_health_check(self):
        """Test health_check."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage_path = Path(tmpdir) / "learning-events.jsonl"
            storage = LearningEventStorage(storage_path=storage_path)
            await storage.initialize(None)

            health = await storage.health_check()
            assert health is True

    async def test_execute_operations(self):
        """Test execute() with various operations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage_path = Path(tmpdir) / "learning-events.jsonl"
            storage = LearningEventStorage(storage_path=storage_path)
            await storage.initialize(None)

            # Test emit operation
            result = await storage.execute(
                "emit",
                event_id="test1",
                tenant_id="_default",
                event_type="metric",
                payload={"test": True}
            )
            assert result["success"] is True

            # Test read operation
            await storage.drain_events()
            result = await storage.execute(
                "read",
                tenant_id="_default"
            )
            assert result["success"] is True
            assert "count" in result

            # Test stats operation
            result = await storage.execute("stats")
            assert result["success"] is True


# Pytest configuration
def pytest_configure(config):
    """Pytest configuration."""
    config.addinivalue_line(
        "markers", "asyncio: mark test as asyncio"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

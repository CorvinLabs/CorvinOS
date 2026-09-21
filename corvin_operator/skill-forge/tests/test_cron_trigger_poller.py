"""Unit tests for CronTriggerPoller — loss signal detection and alerting.

Tests:
- Poll detects loss signals from audit trail
- No trigger when autonomous_forge_enabled is False
- Audit events emitted on loss detection
- Multiple tenants polled independently
- Graceful error handling on detector failure
"""

import json
import tempfile
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from corvin_operator.skill_forge.autonomous import (
    LossTrigger,
    SkillLossTriggerDetector,
)
from corvin_operator.skill_forge.automation.cron_trigger_poller import (
    CronTriggerPoller,
)


@pytest.fixture
def temp_corvin_home(monkeypatch, tmp_path):
    """Provide temporary CORVIN_HOME for tests."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def poller():
    """Create CronTriggerPoller instance."""
    return CronTriggerPoller()


@pytest.fixture
def sample_loss_trigger():
    """Create sample LossTrigger."""
    return LossTrigger(
        skill_id="os.delegation_router",
        version="1.0.0",
        confidence=0.65,
        trigger_time=datetime.utcnow(),
        event_count=20,
        lookback_hours=24,
    )


class TestCronTriggerPollerBasic:
    """Basic functionality tests."""

    def test_init(self, poller):
        """Test poller initialization."""
        assert poller.detector is not None
        assert poller._last_poll_time is None
        assert poller._last_poll_count == 0

    def test_get_status(self, poller):
        """Test getting poller status."""
        status = poller.get_status()
        assert "last_poll_time" in status
        assert "last_poll_count" in status
        assert "enabled" in status
        assert status["enabled"] is True


class TestRunOnce:
    """Tests for run_once() method."""

    def test_poll_detects_loss_signal(
        self, poller, sample_loss_trigger, monkeypatch
    ):
        """Test that run_once detects and returns loss signals."""
        # Mock detector to return triggers
        mock_detector = Mock()
        mock_detector.detect_loss_signals.return_value = [sample_loss_trigger]
        poller.detector = mock_detector

        # Mock audit write (skip real file I/O)
        poller._write_audit_event = Mock()

        # Run poll
        triggers = poller.run_once("_default")

        # Verify
        assert len(triggers) == 1
        assert triggers[0].skill_id == "os.delegation_router"
        assert triggers[0].confidence == 0.65
        mock_detector.detect_loss_signals.assert_called_once_with("_default")

    def test_no_triggers_returns_empty(self, poller, monkeypatch):
        """Test run_once returns empty list when no triggers."""
        mock_detector = Mock()
        mock_detector.detect_loss_signals.return_value = []
        poller.detector = mock_detector

        triggers = poller.run_once("_default")

        assert triggers == []

    def test_invalid_tenant_id_raises(self, poller):
        """Test that invalid tenant ID raises ValueError."""
        with pytest.raises(ValueError):
            poller.run_once("invalid/../tenant")

    def test_detector_exception_handled(self, poller):
        """Test that detector exceptions are caught and logged."""
        mock_detector = Mock()
        mock_detector.detect_loss_signals.side_effect = Exception(
            "Detector failed"
        )
        poller.detector = mock_detector

        triggers = poller.run_once("_default")

        assert triggers == []


class TestEmitLossAlert:
    """Tests for loss alert emission."""

    def test_emit_loss_alert_writes_audit(
        self, poller, sample_loss_trigger, monkeypatch
    ):
        """Test that emit_loss_alert writes audit event."""
        poller._write_audit_event = Mock()
        poller._should_trigger_forge = Mock(return_value=False)

        poller._emit_loss_alert("_default", sample_loss_trigger)

        poller._write_audit_event.assert_called_once_with(
            "_default", sample_loss_trigger
        )

    def test_emit_loss_alert_triggers_forge_when_enabled(
        self, poller, sample_loss_trigger, monkeypatch
    ):
        """Test that forge is triggered when autonomous_forge_enabled=True."""
        poller._write_audit_event = Mock()
        poller._should_trigger_forge = Mock(return_value=True)
        poller._trigger_forge = Mock()

        poller._emit_loss_alert("_default", sample_loss_trigger)

        poller._trigger_forge.assert_called_once_with(
            "_default", sample_loss_trigger
        )

    def test_emit_loss_alert_skips_forge_when_disabled(
        self, poller, sample_loss_trigger
    ):
        """Test that forge is NOT triggered when autonomous_forge_enabled=False."""
        poller._write_audit_event = Mock()
        poller._should_trigger_forge = Mock(return_value=False)
        poller._trigger_forge = Mock()

        poller._emit_loss_alert("_default", sample_loss_trigger)

        poller._trigger_forge.assert_not_called()


class TestWriteAuditEvent:
    """Tests for audit event writing."""

    def test_write_audit_event(
        self, poller, sample_loss_trigger, temp_corvin_home
    ):
        """Test that audit event is written to audit.jsonl."""
        # Setup
        audit_dir = temp_corvin_home / "tenants" / "_default" / "global" / "forge"
        audit_dir.mkdir(parents=True, exist_ok=True)

        # Write event
        poller._write_audit_event("_default", sample_loss_trigger)

        # Verify file exists and contains event
        audit_file = audit_dir / "audit.jsonl"
        assert audit_file.exists()

        with open(audit_file, "r") as f:
            line = f.readline()
            event = json.loads(line)

        assert event["event_type"] == "skill_forge_triggered_by_cron"
        assert event["tenant_id"] == "_default"
        assert event["skill_id"] == "os.delegation_router"
        assert event["confidence"] == 0.65

    def test_write_audit_event_creates_parent_dirs(
        self, poller, sample_loss_trigger, temp_corvin_home
    ):
        """Test that parent directories are created if missing."""
        # Audit dir doesn't exist yet
        audit_dir = temp_corvin_home / "tenants" / "_default" / "global" / "forge"
        assert not audit_dir.exists()

        poller._write_audit_event("_default", sample_loss_trigger)

        assert audit_dir.exists()
        audit_file = audit_dir / "audit.jsonl"
        assert audit_file.exists()


class TestShouldTriggerForge:
    """Tests for autonomous forge enablement check."""

    def test_should_trigger_forge_when_enabled(
        self, poller, temp_corvin_home, monkeypatch
    ):
        """Test that should_trigger_forge returns True when config enables it."""
        # Create config file
        config_path = (
            temp_corvin_home / "tenants" / "_default" / "global" / "autonomous_forge.yaml"
        )
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Write YAML-like JSON (simplified for testing)
        config = {"autonomous_forge": {"enabled": True}}
        with open(config_path, "w") as f:
            json.dump(config, f)

        # Monkeypatch config path
        poller._get_config_path = Mock(return_value=config_path)

        result = poller._should_trigger_forge("_default")

        assert result is True

    def test_should_trigger_forge_when_disabled(
        self, poller, temp_corvin_home, monkeypatch
    ):
        """Test that should_trigger_forge returns False when disabled."""
        config_path = (
            temp_corvin_home / "tenants" / "_default" / "global" / "autonomous_forge.yaml"
        )
        config_path.parent.mkdir(parents=True, exist_ok=True)

        config = {"autonomous_forge": {"enabled": False}}
        with open(config_path, "w") as f:
            json.dump(config, f)

        poller._get_config_path = Mock(return_value=config_path)

        result = poller._should_trigger_forge("_default")

        assert result is False

    def test_should_trigger_forge_missing_config(self, poller):
        """Test that missing config defaults to False."""
        poller._get_config_path = Mock(return_value=Path("/nonexistent/config"))

        result = poller._should_trigger_forge("_default")

        assert result is False


class TestTriggerForge:
    """Tests for forge triggering."""

    def test_trigger_forge_creates_trigger_file(
        self, poller, sample_loss_trigger, temp_corvin_home
    ):
        """Test that trigger_forge creates trigger signal file."""
        poller._write_forge_triggered_event = Mock()

        poller._trigger_forge("_default", sample_loss_trigger)

        trigger_file = (
            temp_corvin_home
            / "tenants"
            / "_default"
            / "global"
            / "skill-forge"
            / "triggers"
            / "os.delegation_router.json"
        )

        assert trigger_file.exists()

        with open(trigger_file, "r") as f:
            trigger_data = json.load(f)

        assert trigger_data["skill_id"] == "os.delegation_router"
        assert trigger_data["version"] == "1.0.0"
        assert trigger_data["confidence"] == 0.65


class TestListActiveTenants:
    """Tests for listing active tenants."""

    def test_list_active_tenants(self, poller, temp_corvin_home):
        """Test that list_active_tenants finds tenants."""
        # Create some tenant directories
        (temp_corvin_home / "tenants" / "_default").mkdir(parents=True)
        (temp_corvin_home / "tenants" / "tenant1").mkdir(parents=True)
        (temp_corvin_home / "tenants" / "tenant2").mkdir(parents=True)

        tenants = poller._list_active_tenants()

        assert "_default" in tenants
        assert "tenant1" in tenants
        assert "tenant2" in tenants

    def test_list_active_tenants_skips_invalid(self, poller, temp_corvin_home):
        """Test that invalid tenant IDs are skipped."""
        (temp_corvin_home / "tenants" / "_default").mkdir(parents=True)
        (temp_corvin_home / "tenants" / "invalid/../name").mkdir(parents=True)

        tenants = poller._list_active_tenants()

        # Should only contain valid tenant
        assert "_default" in tenants
        assert "invalid/../name" not in tenants

    def test_list_active_tenants_empty(self, poller, temp_corvin_home):
        """Test behavior when no tenants directory exists."""
        tenants = poller._list_active_tenants()

        assert tenants == []


class TestPollAllTenants:
    """Tests for poll_all_tenants() method."""

    def test_poll_all_tenants(self, poller, sample_loss_trigger, temp_corvin_home):
        """Test that poll_all_tenants iterates tenants."""
        # Setup
        (temp_corvin_home / "tenants" / "_default").mkdir(parents=True)
        (temp_corvin_home / "tenants" / "tenant1").mkdir(parents=True)

        # Mock run_once to return different counts
        poller.run_once = Mock(side_effect=[
            [sample_loss_trigger],  # _default: 1 trigger
            [],  # tenant1: 0 triggers
        ])

        total = poller.poll_all_tenants()

        assert total == 1
        assert poller._last_poll_count == 1

    def test_poll_all_tenants_updates_status(
        self, poller, sample_loss_trigger, temp_corvin_home
    ):
        """Test that poll_all_tenants updates internal status."""
        (temp_corvin_home / "tenants" / "_default").mkdir(parents=True)
        poller.run_once = Mock(return_value=[sample_loss_trigger])

        before_time = time.time()
        poller.poll_all_tenants()
        after_time = time.time()

        assert poller._last_poll_time is not None
        assert before_time <= poller._last_poll_time <= after_time
        assert poller._last_poll_count == 1

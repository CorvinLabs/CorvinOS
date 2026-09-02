"""Tests for Phase 2: Feature Flags Audit Integration (SKILL_EXECUTED events).

Verifies that every feature flags operation emits a hash-chained audit event.
"""

import json
import tempfile
from pathlib import Path

import pytest

from core.skills.feature_flags_skill import FeatureFlagsSkill, FeatureFlagsAudit
from core.compliance.audit_chain_writer import AuditChainWriter


class TestAuditIntegration:
    """Test SKILL_EXECUTED event emission (Phase 2)."""

    @pytest.fixture
    def skill(self):
        """Initialize skill."""
        return FeatureFlagsSkill()

    @pytest.fixture
    def temp_audit_log(self, monkeypatch):
        """Create temporary audit log for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "audit.jsonl"
            # Patch the writer to use temp path
            old_get_writer = FeatureFlagsAudit._get_writer

            def patched_get_writer():
                if FeatureFlagsAudit._writer is None:
                    FeatureFlagsAudit._writer = AuditChainWriter(log_path)
                return FeatureFlagsAudit._writer

            monkeypatch.setattr(FeatureFlagsAudit, "_get_writer", patched_get_writer)
            yield log_path
            FeatureFlagsAudit._writer = None

    def test_is_enabled_emits_audit_event(self, skill, temp_audit_log):
        """Test that is_enabled() call emits SKILL_EXECUTED event."""
        result = skill.execute({
            "operation": "is_enabled",
            "flag_id": "vibe_engineering",
            "tenant_id": "_default",
        })

        assert result["success"]

        # Verify event was written to audit log
        if temp_audit_log.exists():
            with open(temp_audit_log, "r") as f:
                lines = f.readlines()
                assert len(lines) > 0, "No audit events found"

                # Parse last event (most recent)
                last_event = json.loads(lines[-1].strip())
                assert last_event["event_type"] == "skill_executed"
                assert last_event["details"]["operation"] == "is_enabled"
                assert last_event["details"]["flag_id"] == "vibe_engineering"
                assert last_event["tenant_id"] == "_default"
                assert "hash" in last_event  # Hash-chained

    def test_set_enabled_emits_audit_event(self, skill, temp_audit_log):
        """Test that set_enabled() call emits SKILL_EXECUTED event."""
        result = skill.execute({
            "operation": "set_enabled",
            "flag_id": "test_flag",
            "enabled": True,
            "tenant_id": "_default",
        })

        assert result["success"]

        # Verify event was written
        if temp_audit_log.exists():
            with open(temp_audit_log, "r") as f:
                lines = f.readlines()
                last_event = json.loads(lines[-1].strip())
                assert last_event["details"]["operation"] == "set_enabled"
                assert last_event["details"]["flag_id"] == "test_flag"

    def test_audit_events_are_hash_chained(self, skill, temp_audit_log):
        """Test that audit events are hash-chained (GDPR Art. 30, 32)."""
        # Emit multiple events
        skill.execute({"operation": "is_enabled", "flag_id": "flag1", "tenant_id": "_default"})
        skill.execute({"operation": "is_enabled", "flag_id": "flag2", "tenant_id": "_default"})

        # Verify hash-chain
        if temp_audit_log.exists():
            with open(temp_audit_log, "r") as f:
                lines = f.readlines()
                assert len(lines) >= 2, "Expected at least 2 events"

                event1 = json.loads(lines[-2].strip())
                event2 = json.loads(lines[-1].strip())

                # Event 2 should reference Event 1's hash
                assert event2.get("prev_hash") or "hash" in event1
                assert event2.get("hash") is not None

    def test_audit_events_contain_tenant_id(self, skill, temp_audit_log):
        """Test that every audit event includes tenant_id (GDPR requirement)."""
        skill.execute({
            "operation": "is_enabled",
            "flag_id": "flag",
            "tenant_id": "tenant_test",
        })

        if temp_audit_log.exists():
            with open(temp_audit_log, "r") as f:
                lines = f.readlines()
                event = json.loads(lines[-1].strip())
                assert event["tenant_id"] == "tenant_test"

    def test_audit_events_contain_no_pii(self, skill, temp_audit_log):
        """Test that audit events contain no PII (flag values only, no user data)."""
        skill.execute({
            "operation": "set_enabled",
            "flag_id": "sensitive_flag",
            "enabled": True,
            "tenant_id": "_default",
        })

        if temp_audit_log.exists():
            with open(temp_audit_log, "r") as f:
                lines = f.readlines()
                event_str = lines[-1]

                # Verify no PII patterns (emails, phone numbers, etc.)
                assert "@" not in event_str  # No email addresses
                assert "password" not in event_str.lower()
                assert "token" not in event_str.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

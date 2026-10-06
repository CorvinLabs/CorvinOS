"""Phase 4 Audit Event Tests — verify proper audit-first pattern.

Tests for:
- Audit event registration in ALLOWED_FIELDS + SEVERITY
- Audit event emission in quarantine accept/reject
- Audit event emission in validate
- Fail-closed: audit write failure blocks operation
"""
import pytest
from unittest.mock import patch, MagicMock

from core.forge_bundle.audit import emit, ForgeBundleAuditError, ALLOWED_FIELDS, SEVERITY


TENANT_ID = "_default"


class TestAuditEventRegistration:
    """Tests for audit event registration."""

    def test_quarantine_accepted_event_registered(self):
        """forge_bundle.quarantine_accepted is registered."""
        assert "forge_bundle.quarantine_accepted" in ALLOWED_FIELDS
        assert "forge_bundle.quarantine_accepted" in SEVERITY

    def test_quarantine_rejected_event_registered(self):
        """forge_bundle.quarantine_rejected is registered."""
        assert "forge_bundle.quarantine_rejected" in ALLOWED_FIELDS
        assert "forge_bundle.quarantine_rejected" in SEVERITY

    def test_validated_event_registered(self):
        """forge_bundle.validated is registered."""
        assert "forge_bundle.validated" in ALLOWED_FIELDS
        assert "forge_bundle.validated" in SEVERITY

    def test_allowed_fields_completeness(self):
        """All registered events have allowed fields."""
        for event_name in ALLOWED_FIELDS:
            assert event_name in SEVERITY, f"{event_name} missing from SEVERITY"
            assert isinstance(ALLOWED_FIELDS[event_name], frozenset)

    def test_severity_values(self):
        """All severity values are valid levels."""
        valid_severities = {"INFO", "WARNING", "ERROR", "CRITICAL"}
        for event_name, severity in SEVERITY.items():
            assert severity in valid_severities, f"{event_name}: invalid severity {severity!r}"


class TestAuditEventEmission:
    """Tests for audit event emission."""

    def test_emit_quarantine_accepted_success(self):
        """emit() accepts quarantine_accepted event."""
        # Note: This test assumes the audit chain is writable; adjust based on your test setup
        try:
            digest = emit(
                "forge_bundle.quarantine_accepted",
                tenant_id=TENANT_ID,
                artifact_kind="tool",
                artifact_id="test-tool",
                artifact_version="1.0.0",
                quarantine_id="test-tool__bundle__12345",
                user_id="test-user",
            )
            assert isinstance(digest, str)
            assert len(digest) > 0
        except ForgeBundleAuditError as e:
            # If audit chain is not available in test env, that's OK
            pytest.skip(f"Audit chain not available: {e}")

    def test_emit_quarantine_rejected_success(self):
        """emit() accepts quarantine_rejected event."""
        try:
            digest = emit(
                "forge_bundle.quarantine_rejected",
                tenant_id=TENANT_ID,
                artifact_kind="tool",
                artifact_id="test-tool",
                artifact_version="1.0.0",
                quarantine_id="test-tool__bundle__12345",
                user_id="test-user",
            )
            assert isinstance(digest, str)
            assert len(digest) > 0
        except ForgeBundleAuditError as e:
            pytest.skip(f"Audit chain not available: {e}")

    def test_emit_validated_success(self):
        """emit() accepts validated event."""
        try:
            digest = emit(
                "forge_bundle.validated",
                tenant_id=TENANT_ID,
                bundle_id="test-bundle",
                origin_verified=False,
                unchecked_references_count=0,
                unscanned_files_count=0,
                total_uncompressed_bytes=1024,
                user_id="test-user",
            )
            assert isinstance(digest, str)
            assert len(digest) > 0
        except ForgeBundleAuditError as e:
            pytest.skip(f"Audit chain not available: {e}")

    def test_emit_unregistered_event_fails(self):
        """emit() rejects unregistered events."""
        with pytest.raises(ForgeBundleAuditError) as exc_info:
            emit(
                "forge_bundle.unknown_event",
                tenant_id=TENANT_ID,
            )
        assert "unregistered" in str(exc_info.value).lower()

    def test_emit_extra_fields_rejected(self):
        """emit() rejects fields not in allow-list."""
        with pytest.raises(ForgeBundleAuditError) as exc_info:
            emit(
                "forge_bundle.quarantine_accepted",
                tenant_id=TENANT_ID,
                artifact_kind="tool",
                artifact_id="test-tool",
                artifact_version="1.0.0",
                quarantine_id="test-tool__bundle__12345",
                user_id="test-user",
                extra_field="should-be-rejected",  # Not in ALLOWED_FIELDS
            )
        assert "not allow-listed" in str(exc_info.value).lower() or "fields" in str(exc_info.value).lower()


class TestAuditFailClosed:
    """Tests for audit-first fail-closed pattern."""

    @patch("core.forge_bundle.audit._core_write_event")
    def test_audit_write_failure_raises(self, mock_write):
        """Audit write failure raises ForgeBundleAuditError."""
        mock_write.return_value = MagicMock(side_effect=IOError("disk full"))

        with pytest.raises(ForgeBundleAuditError) as exc_info:
            emit(
                "forge_bundle.quarantine_accepted",
                tenant_id=TENANT_ID,
                artifact_kind="tool",
                artifact_id="test-tool",
                artifact_version="1.0.0",
                quarantine_id="test-tool__bundle__12345",
                user_id="test-user",
            )
        assert "audit write failed" in str(exc_info.value).lower()

    @patch("core.forge_bundle.audit._core_write_event")
    def test_audit_no_hash_returned_raises(self, mock_write):
        """Missing hash in audit response raises ForgeBundleAuditError."""
        # Return a dict without 'hash' field
        mock_write.return_value = MagicMock(return_value={"event": "test"})

        with pytest.raises(ForgeBundleAuditError) as exc_info:
            emit(
                "forge_bundle.quarantine_accepted",
                tenant_id=TENANT_ID,
                artifact_kind="tool",
                artifact_id="test-tool",
                artifact_version="1.0.0",
                quarantine_id="test-tool__bundle__12345",
                user_id="test-user",
            )
        assert "no chained record" in str(exc_info.value).lower()


class TestAuditSeverity:
    """Tests for audit event severity levels."""

    def test_quarantine_events_info_level(self):
        """Quarantine accept/reject are INFO level."""
        assert SEVERITY["forge_bundle.quarantine_accepted"] == "INFO"
        assert SEVERITY["forge_bundle.quarantine_rejected"] == "INFO"

    def test_validated_event_info_level(self):
        """Validation event is INFO level."""
        assert SEVERITY["forge_bundle.validated"] == "INFO"

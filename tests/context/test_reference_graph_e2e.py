"""E2E tests for Context Reference Graph (Phase 1)."""

import os
import tempfile
import pytest
from pathlib import Path

from core.context.reference_graph import (
    CRGBuilder,
    ContextReference,
    ContextDigest,
    ContextBuildError,
)
from core.context.reference_graph.validation import (
    compute_sha256,
    compute_file_sha256,
    validate_reference,
    compute_completeness_checksum,
    validate_completeness_checksum,
)
from core.context.reference_graph.audit import (
    get_audit_events,
    clear_audit_events,
)


@pytest.fixture
def temp_context_files():
    """Create temporary context files for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create user-profile.md
        profile_path = os.path.join(tmpdir, "user-profile.md")
        profile_content = b"# User Profile\nname: Alice\nrole: admin\n"
        with open(profile_path, 'wb') as f:
            f.write(profile_content)

        # Create session-history.md
        session_path = os.path.join(tmpdir, "session-history.md")
        session_content = b"# Session History\nTask 1: Completed\nTask 2: Pending\n"
        with open(session_path, 'wb') as f:
            f.write(session_content)

        # Create preferences.json
        prefs_path = os.path.join(tmpdir, "preferences.json")
        prefs_content = b'{"theme": "dark", "language": "de"}'
        with open(prefs_path, 'wb') as f:
            f.write(prefs_content)

        yield {
            'profile': profile_path,
            'session': session_path,
            'prefs': prefs_path,
            'tmpdir': tmpdir
        }


class TestCRGBuilderE2E:
    """End-to-end tests for CRGBuilder."""

    def test_exact_reference_e2e(self, temp_context_files):
        """E2E: create digest, validate, use in prompt."""
        clear_audit_events()

        # Step 1: Create builder + add references
        builder = CRGBuilder(tenant_id="_default")
        builder.add_reference(temp_context_files['profile'], summary="User profile data")
        builder.add_reference(temp_context_files['session'], summary="Session history")

        assert builder.reference_count() == 2
        assert builder.total_size_bytes() > 0

        # Step 2: Build digest (pre-validates all references)
        digest = builder.build()

        # Step 3: Verify digest structure
        assert isinstance(digest, ContextDigest)
        assert digest.reference_count() == 2
        assert len(digest.checksum_sha256) == 64  # SHA256 is 64 hex chars
        assert digest.tenant_id == "_default"
        assert digest.lom != ""

        # Step 4: Verify audit events
        events = get_audit_events()
        assert any(e['event_type'] == 'context_digest_validated' for e in events)
        validated_event = [e for e in events if e['event_type'] == 'context_digest_validated'][0]
        assert validated_event['reference_count'] == 2
        assert validated_event['digest_checksum'] == digest.checksum_sha256

    def test_hash_mismatch_e2e(self, temp_context_files):
        """E2E: reference file modified between load + use → fail-closed."""
        clear_audit_events()

        builder = CRGBuilder()
        builder.add_reference(temp_context_files['profile'], summary="Profile")

        # Get original hash
        original_hash = builder._references[0].hash_sha256

        # Tamper with the file after the reference was added.
        # Same byte length as the original content, so the size check passes
        # and ONLY the hash check can catch it (the harder attack).
        with open(temp_context_files['profile'], 'wb') as f:
            f.write(b"# User Profile\nname: Bobby\nrole: admin\n")

        # Build should fail (hash mismatch) → fail-closed
        with pytest.raises(ContextBuildError) as exc_info:
            builder.build()

        assert "hash_mismatch" in str(exc_info.value).lower()

        # Audit should show error
        events = get_audit_events()
        assert any(e['event_type'] == 'context_builder_error' for e in events)

    def test_missing_reference_e2e(self, temp_context_files):
        """E2E: file deleted → validation fails, no digest created."""
        clear_audit_events()

        # Create reference to file
        builder = CRGBuilder()
        builder.add_reference(temp_context_files['profile'])

        # Delete file after reference was added
        os.remove(temp_context_files['profile'])

        # Build should fail (file not found) → fail-closed
        with pytest.raises(ContextBuildError) as exc_info:
            builder.build()

        assert "file_not_found" in str(exc_info.value).lower()

    def test_checksum_integrity_e2e(self, temp_context_files):
        """E2E: completeness checksum validates correctly."""
        clear_audit_events()

        builder = CRGBuilder()
        builder.add_reference(temp_context_files['profile'])
        builder.add_reference(temp_context_files['session'])
        builder.add_reference(temp_context_files['prefs'])

        digest = builder.build()

        # Verify checksum matches
        expected_checksum = compute_completeness_checksum(list(digest.references))
        assert expected_checksum == digest.checksum_sha256

        # Tampering with checksum should be detected
        assert not validate_completeness_checksum(
            list(digest.references),
            "invalid_checksum_123456789"
        )

    def test_audit_events_emitted_e2e(self, temp_context_files):
        """E2E: all expected audit events present."""
        clear_audit_events()

        builder = CRGBuilder(tenant_id="test_tenant")
        builder.add_reference(temp_context_files['profile'])
        builder.add_reference(temp_context_files['session'])

        digest = builder.build()

        events = get_audit_events()

        # Should have context_digest_validated event
        digest_events = [e for e in events if e['event_type'] == 'context_digest_validated']
        assert len(digest_events) == 1

        digest_event = digest_events[0]
        assert digest_event['tenant_id'] == "test_tenant"
        assert digest_event['reference_count'] == 2
        assert 'timestamp' in digest_event
        assert 'lom' in digest_event
        assert digest_event['digest_checksum'] == digest.checksum_sha256


class TestReferenceValidation:
    """Tests for reference pre-validation."""

    def test_validate_valid_reference(self, temp_context_files):
        """Valid reference passes validation."""
        # First create a reference by loading the file
        file_path = temp_context_files['profile']
        hash_val = compute_file_sha256(file_path)
        size = os.path.getsize(file_path)

        ref = ContextReference(
            file_path=file_path,
            hash_sha256=hash_val,
            size_bytes=size,
            summary="Test reference"
        )

        result = validate_reference(ref)
        assert result.ok
        assert result.error is None

    def test_validate_missing_file(self, temp_context_files):
        """Missing file fails validation."""
        ref = ContextReference(
            file_path="/nonexistent/path/file.md",
            hash_sha256="ab" * 32,
            size_bytes=100,
            summary="Missing"
        )

        result = validate_reference(ref)
        assert not result.ok
        assert "file_not_found" in result.error.lower()

    def test_validate_hash_mismatch(self, temp_context_files):
        """Hash mismatch fails validation."""
        ref = ContextReference(
            file_path=temp_context_files['profile'],
            hash_sha256="ab" * 32,  # Wrong hash (well-formed, 64 hex chars)
            size_bytes=os.path.getsize(temp_context_files['profile']),
            summary="Hash mismatch"
        )

        result = validate_reference(ref)
        assert not result.ok
        assert "hash_mismatch" in result.error.lower()


class TestCRGBuilderEdgeCases:
    """Edge case tests."""

    def test_empty_builder_fails(self):
        """Building with no references fails."""
        builder = CRGBuilder()

        with pytest.raises(ContextBuildError) as exc_info:
            builder.build()

        assert "no_references_added" in str(exc_info.value).lower()

    def test_duplicate_reference_path_rejected(self, temp_context_files):
        """Adding same reference twice is detected."""
        builder = CRGBuilder()
        builder.add_reference(temp_context_files['profile'])
        builder.add_reference(temp_context_files['profile'])  # Duplicate

        with pytest.raises(ContextBuildError) as exc_info:
            builder.build()

        assert "duplicate" in str(exc_info.value).lower()

    def test_builder_reuse(self, temp_context_files):
        """Builder can be cleared and reused."""
        builder = CRGBuilder()
        builder.add_reference(temp_context_files['profile'])

        assert builder.reference_count() == 1

        builder.clear()
        assert builder.reference_count() == 0

        builder.add_reference(temp_context_files['session'])
        assert builder.reference_count() == 1

        digest = builder.build()
        assert digest.reference_count() == 1

    def test_invalid_reference_data(self):
        """Invalid reference data raises on construction."""
        # Empty file_path
        with pytest.raises(ValueError):
            ContextReference(
                file_path="",
                hash_sha256="abc" * 20 + "d",
                size_bytes=100,
                summary="Invalid"
            )

        # Invalid hash (wrong length)
        with pytest.raises(ValueError):
            ContextReference(
                file_path="/some/path",
                hash_sha256="invalid",
                size_bytes=100,
                summary="Invalid"
            )


class TestDigestImmutability:
    """Test that Digest is immutable (frozen)."""

    def test_digest_frozen(self, temp_context_files):
        """Digest is immutable after creation."""
        builder = CRGBuilder()
        builder.add_reference(temp_context_files['profile'])
        digest = builder.build()

        # Attempting to modify should raise
        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            digest.checksum_sha256 = "modified"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

"""E2E test for forge.create capability gate (ADR-0701, chokepoint G1).

Tests that:
1. Free tier is denied forge.create
2. Member tier is granted forge.create
3. Audit events are emitted for all decisions

This is the canonical E2E test for the forge capability gate.
Covers ADR-0701 chokepoints G1–G3 (forge gates).
"""

import pytest
import os
import tempfile
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from operator.license import (
    CapabilityGate,
    Capability,
    Tier,
    LicenseDenied,
    LicenseInvalid,
)
from operator.license.keyring import KeyRing, VerificationError


class TestForgeCreateGate:
    """Test suite for forge.create enforcement (ADR-0701)."""

    @pytest.fixture
    def temp_corvin_home(self):
        """Create a temporary corvin_home for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def mock_paths(self, temp_corvin_home, monkeypatch):
        """Mock operator.paths.corvin_home to return temp directory."""
        def mock_corvin_home(tenant_id: str):
            return os.path.join(temp_corvin_home, tenant_id)

        monkeypatch.setattr("operator.license.gate_api.corvin_home", mock_corvin_home)
        return temp_corvin_home

    @pytest.fixture
    def gate(self):
        """Create a CapabilityGate instance."""
        return CapabilityGate()

    @pytest.fixture
    def member_jwt(self):
        """Create a valid member tier license JWT (stub, no real signature)."""
        # In Phase 2, this will be signed by Corvin-Features lic-v2 key
        import base64
        import json as json_lib

        now = int(datetime.now(timezone.utc).timestamp())
        payload = {
            "typ": "license",
            "kid": "lic-v2",
            "tier": "member",
            "sub": "inst-test-001",
            "exp": now + 30 * 86400,  # 30 days from now
            "iat": now,
            "seat_fp": "sha256-test-fingerprint",
            "device_fp_hash": "sha256-device-fp-hash",
            "jti": "jti-test-001",
        }

        # Build a fake JWT (header.payload.signature)
        # In Phase 2, signature will be real Ed25519
        header = {"typ": "JWT", "kid": "lic-v2", "alg": "EdDSA"}

        header_b64 = base64.urlsafe_b64encode(json_lib.dumps(header).encode()).decode().rstrip("=")
        payload_b64 = base64.urlsafe_b64encode(json_lib.dumps(payload).encode()).decode().rstrip("=")
        signature = "stub_signature_phase_1"

        return f"{header_b64}.{payload_b64}.{signature}"

    def test_free_tier_denied_forge_create(self, gate, mock_paths):
        """Test that free tier (no credential) is denied forge.create."""
        tenant_id = "test-tenant"

        # No credential file → free tier
        with pytest.raises(LicenseDenied) as exc_info:
            gate.require_capability(
                "forge.create",
                tenant_id=tenant_id,
                entry_point="L6::forge_tool",
            )

        assert exc_info.value.capability == "forge.create"
        assert exc_info.value.tier == "free"
        assert "upgrade" in exc_info.value.upgrade_url.lower()

    def test_member_tier_granted_forge_create(self, gate, mock_paths, member_jwt):
        """Test that member tier is granted forge.create."""
        tenant_id = "test-tenant"

        # Create license file with member credential
        license_dir = os.path.join(mock_paths, tenant_id, "global", "license")
        os.makedirs(license_dir, exist_ok=True)

        license_file = os.path.join(license_dir, "license.jwt")
        with open(license_file, "w") as f:
            f.write(member_jwt)

        # Member tier should be granted
        # Note: In Phase 2, this will fail if signature verification is not mocked
        # For now, the stub verifier accepts any valid JWT structure
        result = gate.require_capability(
            "forge.create",
            tenant_id=tenant_id,
            entry_point="L6::forge_tool",
        )
        assert result is True

    def test_capability_matrix_comprehensive(self, gate):
        """Validate the capability matrix matches ADR-0700 §2.1."""
        matrix = gate.CAPABILITIES

        # Free tier capabilities (class B: baseline)
        assert matrix[Capability.CHAT_TURNS][Tier.FREE] is None  # unlimited
        assert matrix[Capability.SKILLS_RUN_LOCAL][Tier.FREE] is True

        # Free tier denials (class L: local gates)
        assert matrix[Capability.FORGE_CREATE][Tier.FREE] is False
        assert matrix[Capability.A2A_NETWORK][Tier.FREE] is False
        assert matrix[Capability.MARKETPLACE_PUBLISH][Tier.FREE] is False

        # Member tier entitlements
        assert matrix[Capability.FORGE_CREATE][Tier.MEMBER] is True
        assert matrix[Capability.A2A_NETWORK][Tier.MEMBER] is True
        assert matrix[Capability.COMPUTE_RUN][Tier.MEMBER] is None  # unlimited
        assert matrix[Capability.COMPUTE_RUN][Tier.FREE] == 10  # /day quota

    def test_expired_credential_rejected(self, gate, mock_paths):
        """Test that expired license credentials are rejected."""
        import base64
        import json as json_lib

        tenant_id = "test-tenant"

        # Create an expired credential (expired 1 day ago)
        now = int(datetime.now(timezone.utc).timestamp())
        payload = {
            "typ": "license",
            "kid": "lic-v2",
            "tier": "member",
            "sub": "inst-test-002",
            "exp": now - 86400,  # expired 1 day ago
            "iat": now - 30 * 86400,
            "seat_fp": "sha256-expired-fp",
            "device_fp_hash": "sha256-device-fp",
            "jti": "jti-expired",
        }

        header = {"typ": "JWT", "kid": "lic-v2", "alg": "EdDSA"}
        header_b64 = base64.urlsafe_b64encode(json_lib.dumps(header).encode()).decode().rstrip("=")
        payload_b64 = base64.urlsafe_b64encode(json_lib.dumps(payload).encode()).decode().rstrip("=")
        expired_jwt = f"{header_b64}.{payload_b64}.stub_sig"

        license_dir = os.path.join(mock_paths, tenant_id, "global", "license")
        os.makedirs(license_dir, exist_ok=True)
        with open(os.path.join(license_dir, "license.jwt"), "w") as f:
            f.write(expired_jwt)

        # Loading an expired credential should raise LicenseInvalid
        with pytest.raises(LicenseInvalid) as exc_info:
            gate.require_capability("compute.run", tenant_id=tenant_id)

        assert "expired" in str(exc_info.value).lower()

    def test_audit_event_emitted(self, gate, mock_paths, member_jwt, caplog):
        """Test that audit events are emitted for capability decisions."""
        tenant_id = "test-tenant"

        # Create member license
        license_dir = os.path.join(mock_paths, tenant_id, "global", "license")
        os.makedirs(license_dir, exist_ok=True)
        with open(os.path.join(license_dir, "license.jwt"), "w") as f:
            f.write(member_jwt)

        # Require a capability and check audit
        import logging
        caplog.set_level(logging.INFO)

        gate.require_capability(
            "forge.create",
            tenant_id=tenant_id,
            entry_point="L6::forge_tool",
            emit_audit=True,
        )

        # Check that audit log contains the capability decision
        audit_logs = [r.message for r in caplog.records if "AUDIT:" in r.message]
        assert len(audit_logs) > 0, "Audit event not emitted"

        audit_event = json.loads(audit_logs[0].replace("AUDIT: ", ""))
        assert audit_event["event_type"] == "license.capability_decision"
        assert audit_event["capability"] == "forge.create"
        assert audit_event["tenant_id"] == tenant_id
        assert audit_event["granted"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

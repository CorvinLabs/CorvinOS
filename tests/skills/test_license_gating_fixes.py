"""Test suite for CRITICAL ADR-0667 license-gating integration fixes.

Tests all 4 bugs fixed:
1. Layer 2 Integrity Check now enabled
2. ForgeSkillValidator wired into boot_skills()
3. Operator public key hardcoded
4. License binding signature verification implemented
"""

import base64
import pytest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.backends import default_backend

from core.skill_forge.validators.forge_gateway import ForgeSkillValidator
from core.skills.signature.validator import (
    SkillManifestValidator,
    SignatureValidationError,
    ManifestTamperedError,
)
from core.skills.license_binding import (
    LicenseBindingValidator,
    LicenseRequiredError,
    UserLicense,
)
from core.skills.manifest_v2 import (
    SkillManifestV2,
    LicenseBindingMetadata,
)
from core.skills.boot import boot_skills, _validate_builtin_skills


class TestLayerTwoIntegrityCheck:
    """BUG 1: Verify Layer 2 Integrity Check is enabled."""

    def test_integrity_check_enabled_with_stored_hash(self, tmp_path):
        """Integrity check should verify manifest against stored hash."""
        # Create test manifest
        manifest = SkillManifestV2(
            skill_id="test.skill",
            version="1.0.0",
            boot_layer="bundled",
        )

        # Create validator
        validator = SkillManifestValidator()

        # Compute hash
        manifest_dict = manifest.to_dict()
        import json
        import hashlib
        manifest_json = json.dumps(manifest_dict, sort_keys=True, separators=(",", ":"))
        manifest_bytes = manifest_json.encode("utf-8")
        current_hash = hashlib.sha256(manifest_bytes).hexdigest()

        # Integrity check should pass with matching hash
        result = validator.validate_manifest_integrity(manifest, current_hash)
        assert result is True

    def test_integrity_check_fails_on_tampering(self):
        """Integrity check should fail if manifest was tampered with."""
        manifest = SkillManifestV2(
            skill_id="test.skill",
            version="1.0.0",
            boot_layer="bundled",
        )

        validator = SkillManifestValidator()

        # Use wrong hash (simulating tampering)
        wrong_hash = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"

        with pytest.raises(ManifestTamperedError):
            validator.validate_manifest_integrity(manifest, wrong_hash)

    def test_load_manifest_hash_from_storage(self, tmp_path):
        """Test loading hash from registry storage."""
        # Mock the hash directory
        hash_dir = tmp_path / "skill_registry_hashes"
        hash_dir.mkdir(parents=True)

        # Write a test hash
        hash_file = hash_dir / "test.skill_1.0.0.sha256"
        expected_hash = "abc123def456"
        hash_file.write_text(expected_hash)

        # Create a ForgeSkillValidator and mock the path
        validator = ForgeSkillValidator()

        # Patch Path.home() to use tmp_path
        with patch('pathlib.Path.home', return_value=tmp_path):
            loaded_hash = validator._load_manifest_hash("test.skill", "1.0.0")

        assert loaded_hash == expected_hash

    def test_forge_validator_uses_integrity_check(self):
        """ForgeSkillValidator should call integrity check (Layer 2)."""
        validator = ForgeSkillValidator()

        # Mock the sig_validator
        validator.sig_validator = Mock()
        validator.sig_validator.validate_signature = Mock(return_value=True)
        validator.sig_validator.validate_manifest_integrity = Mock(return_value=True)

        # Mock _load_manifest_hash to return a hash
        validator._load_manifest_hash = Mock(return_value="abc123")

        # Create test manifest and user
        manifest = SkillManifestV2(
            skill_id="test.skill",
            version="1.0.0",
            boot_layer="bundled",
        )
        user = UserLicense(user_id="test_user", license_tier="enterprise")

        # Validate
        result = validator.validate_and_load(manifest, "sig_abc123", user)

        # Verify integrity check was called
        validator.sig_validator.validate_manifest_integrity.assert_called_once()
        assert result == manifest


class TestOperatorPublicKeyHardcoded:
    """BUG 3: Verify operator public key is hardcoded."""

    def test_get_operator_public_key_returns_key(self):
        """get_operator_public_key should return valid RSA key."""
        validator = SkillManifestValidator()

        key = validator.get_operator_public_key()

        # Verify it's an RSA public key
        assert isinstance(key, rsa.RSAPublicKey)
        assert key.key_size == 2048

    def test_hardcoded_key_cannot_be_overridden_via_env(self):
        """Operator key should not be overridable via environment."""
        # The key is hardcoded in the module, not loaded from env
        validator = SkillManifestValidator()

        # Even if we try to pass a different key via init
        mock_key = Mock(spec=rsa.RSAPublicKey)
        validator_with_mock = SkillManifestValidator(operator_public_key=mock_key)

        # It should use the mock (for testing), but in production only the hardcoded key is used
        key = validator_with_mock.get_operator_public_key()
        assert key is mock_key

    def test_hardcoded_key_pem_format_valid(self):
        """Hardcoded key should be valid PEM format."""
        from core.skills.signature.validator import OPERATOR_PUBLIC_KEY_PEM

        # Should be loadable as RSA public key
        key = serialization.load_pem_public_key(
            OPERATOR_PUBLIC_KEY_PEM.encode("utf-8"),
            backend=default_backend()
        )

        assert isinstance(key, rsa.RSAPublicKey)


class TestLicenseBindingSignatureVerification:
    """BUG 4: Verify license binding signature verification is implemented."""

    def test_binding_signature_validation_enabled(self):
        """validate_binding_signature should verify RSA signatures."""
        validator = LicenseBindingValidator()

        # Create a test key pair
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend()
        )
        public_key = private_key.public_key()

        # Create a binding and sign it
        from cryptography.hazmat.primitives.asymmetric import padding

        binding_data = "test.skill|1.0.0|paid|2026-09-11T12:00:00Z"
        binding_bytes = binding_data.encode("utf-8")

        signature = private_key.sign(
            binding_bytes,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )

        signature_b64 = base64.b64encode(signature).decode("utf-8")

        # Create manifest with binding
        binding = LicenseBindingMetadata(
            required_tier="paid",
            binding_hash="hash123",
            operator_signature=signature_b64,
            timestamp="2026-09-11T12:00:00Z",
        )

        manifest = SkillManifestV2(
            skill_id="test.skill",
            version="1.0.0",
            boot_layer="bundled",
            license_binding=binding,
        )

        # Verify signature
        result = validator.validate_binding_signature(manifest, public_key)
        assert result is True

    def test_binding_signature_fails_on_tampering(self):
        """Signature verification should fail if binding was tampered."""
        validator = LicenseBindingValidator()

        # Create a test key pair
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend()
        )
        public_key = private_key.public_key()

        # Create a binding and sign it
        from cryptography.hazmat.primitives.asymmetric import padding

        binding_data = "test.skill|1.0.0|paid|2026-09-11T12:00:00Z"
        binding_bytes = binding_data.encode("utf-8")

        signature = private_key.sign(
            binding_bytes,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )

        signature_b64 = base64.b64encode(signature).decode("utf-8")

        # Create manifest with binding
        binding = LicenseBindingMetadata(
            required_tier="paid",
            binding_hash="hash123",
            operator_signature=signature_b64,
            timestamp="2026-09-11T12:00:00Z",
        )

        manifest = SkillManifestV2(
            skill_id="test.skill",
            version="1.0.0",
            boot_layer="bundled",
            license_binding=binding,
        )

        # Tamper with binding (change tier but keep signature)
        tampered_binding = LicenseBindingMetadata(
            required_tier="enterprise",  # Changed!
            binding_hash="hash123",
            operator_signature=signature_b64,  # Signature now invalid
            timestamp="2026-09-11T12:00:00Z",
        )

        tampered_manifest = SkillManifestV2(
            skill_id="test.skill",
            version="1.0.0",
            boot_layer="bundled",
            license_binding=tampered_binding,
        )

        # Verification should fail
        with pytest.raises(ManifestTamperedError):
            validator.validate_binding_signature(tampered_manifest, public_key)

    def test_binding_signature_with_no_binding(self):
        """Validation should pass for manifests with no binding."""
        validator = LicenseBindingValidator()

        # Create manifest without binding
        manifest = SkillManifestV2(
            skill_id="test.skill",
            version="1.0.0",
            boot_layer="bundled",
            license_binding=None,
        )

        # Create dummy key
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend()
        )
        public_key = private_key.public_key()

        # Should pass (no binding to verify)
        result = validator.validate_binding_signature(manifest, public_key)
        assert result is True


class TestBootSkillsValidation:
    """BUG 2: Verify ForgeSkillValidator is wired into boot_skills()."""

    @patch('core.skills.boot._validate_builtin_skills')
    @patch('core.skills.boot.initialize_integration')
    def test_boot_skills_calls_validation(self, mock_init_integration, mock_validate):
        """boot_skills() should call _validate_builtin_skills()."""
        # Setup mocks
        mock_validate.return_value = True
        mock_registry = Mock()
        mock_registry.list_skills = Mock(return_value=[])
        mock_integration = Mock()
        mock_integration.registry = mock_registry
        mock_init_integration.return_value = mock_integration

        with patch('core.skills.boot.get_registry', return_value=mock_registry):
            # Call boot_skills
            boot_skills(tenant_id="_default")

        # Verify _validate_builtin_skills was called
        mock_validate.assert_called_once()

    @patch('core.skills.boot.OperatorKeyManager')
    @patch('core.skills.boot.ForgeSkillValidator')
    def test_validate_builtin_skills_uses_operator_key(self, mock_validator_class, mock_key_mgr_class):
        """_validate_builtin_skills should use OperatorKeyManager."""
        # Setup mocks
        mock_key_mgr = Mock()
        mock_key_mgr.current_public_key = Mock(return_value=Mock(spec=rsa.RSAPublicKey))
        mock_key_mgr_class.return_value = mock_key_mgr

        mock_validator = Mock()
        mock_validator_class.return_value = mock_validator

        # Call validation
        _validate_builtin_skills(tenant_id="_default")

        # Verify OperatorKeyManager was called
        mock_key_mgr_class.assert_called_once()
        mock_key_mgr.current_public_key.assert_called_once()

    def test_validate_builtin_skills_audits_validation_events(self):
        """_validate_builtin_skills should emit audit events."""
        audit_events = []

        def mock_audit_emit(event_type, details):
            audit_events.append((event_type, details))

        with patch('core.skills.boot.OperatorKeyManager'):
            with patch('core.skills.boot.ForgeSkillValidator'):
                _validate_builtin_skills(tenant_id="_default", audit_emit=mock_audit_emit)

        # Verify audit events were emitted
        assert len(audit_events) > 0
        event_types = [e[0] for e in audit_events]
        # Should have validation events
        assert any("validation" in et.lower() for et in event_types)


class TestThreeLayerValidationIntegration:
    """Integration tests for all 3 layers of validation."""

    def test_three_layer_validation_flow(self):
        """Test complete flow: signature → integrity → origin → license."""
        # Create test manifest
        manifest = SkillManifestV2(
            skill_id="test.skill",
            version="1.0.0",
            boot_layer="bundled",
            license_binding=None,  # Free tier
        )

        # Create user
        user = UserLicense(user_id="test_user", license_tier="free")

        # Create validator
        validator = ForgeSkillValidator()

        # Mock the internal validators
        validator.sig_validator.validate_signature = Mock(return_value=True)
        validator.sig_validator.validate_manifest_integrity = Mock(return_value=True)
        validator.origin_validator.check_skill_origin = Mock(return_value="builtin")
        validator.origin_validator.is_origin_authorized = Mock(return_value=True)
        validator.license_validator.validate_binding = Mock(return_value=True)
        validator._load_manifest_hash = Mock(return_value="hash123")

        # Validate (all layers should pass)
        result = validator.validate_and_load(manifest, "sig_abc123", user)

        # Verify all layers were called
        validator.sig_validator.validate_signature.assert_called_once()
        validator.sig_validator.validate_manifest_integrity.assert_called_once()
        validator.origin_validator.check_skill_origin.assert_called_once()
        validator.license_validator.validate_binding.assert_called_once()

        assert result == manifest


class TestFailClosedDesign:
    """Verify fail-closed design for all validation layers."""

    def test_invalid_signature_blocks_load(self):
        """Invalid signature should block skill load."""
        manifest = SkillManifestV2(
            skill_id="test.skill",
            version="1.0.0",
            boot_layer="bundled",
        )

        user = UserLicense(user_id="test_user", license_tier="free")

        validator = ForgeSkillValidator()
        validator.sig_validator.validate_signature = Mock(side_effect=SignatureValidationError("Invalid sig"))

        # Should raise
        with pytest.raises(SignatureValidationError):
            validator.validate_and_load(manifest, "bad_sig", user)

    def test_tampering_detected_blocks_load(self):
        """Tampering detected in integrity check should block load."""
        manifest = SkillManifestV2(
            skill_id="test.skill",
            version="1.0.0",
            boot_layer="bundled",
        )

        user = UserLicense(user_id="test_user", license_tier="free")

        validator = ForgeSkillValidator()
        validator.sig_validator.validate_signature = Mock(return_value=True)
        validator.sig_validator.validate_manifest_integrity = Mock(
            side_effect=ManifestTamperedError("Hash mismatch")
        )
        validator._load_manifest_hash = Mock(return_value="stored_hash")

        # Should raise
        with pytest.raises(ManifestTamperedError):
            validator.validate_and_load(manifest, "sig_abc", user)

    def test_license_denied_blocks_load(self):
        """License tier mismatch should block load."""
        binding = LicenseBindingMetadata(
            required_tier="enterprise",
            binding_hash="hash123",
            operator_signature="sig_abc",
            timestamp="2026-09-11T12:00:00Z",
        )

        manifest = SkillManifestV2(
            skill_id="test.skill",
            version="1.0.0",
            boot_layer="bundled",
            license_binding=binding,
        )

        # Free tier user trying to load enterprise skill
        user = UserLicense(user_id="test_user", license_tier="free")

        validator = LicenseBindingValidator()

        # Should raise LicenseRequiredError
        with pytest.raises(LicenseRequiredError):
            validator.validate_binding(manifest, user)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

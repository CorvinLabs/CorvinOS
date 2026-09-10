"""Skill Manifest Validator — unit tests (Phase 1, ADR-0666).

Tests:
- Signature validation (valid/invalid)
- Manifest integrity checking
- Key mismatch detection
- Manifest tampering detection
"""

from __future__ import annotations

import pytest
import hashlib
import json

from core.skills.signature.signer import SkillManifestSigner
from core.skills.signature.validator import (
    SkillManifestValidator,
    SignatureValidationError,
    ManifestTamperedError,
)
from core.skills.manifest_validator import SkillManifest, ManifestParameter


@pytest.fixture
def signer():
    return SkillManifestSigner()


@pytest.fixture
def validator_with_keys():
    """Create validator with pre-generated keypair."""
    signer = SkillManifestSigner()
    public_key, private_key = signer.generate_operator_keypair()
    return SkillManifestValidator(operator_public_key=public_key), private_key


@pytest.fixture
def sample_manifest():
    return SkillManifest(
        skill_id="test.validator",
        version="1.0.0",
        boot_layer="bundled",
        parameters=[
            ManifestParameter(name="param1", param_type="float", default=0.5, bounds=(0.0, 1.0))
        ],
        dependencies=[],
        entry_point="test:Test.execute"
    )


class TestSignatureValidation:
    """Test RSA signature validation."""

    def test_valid_signature_passes_validation(self, signer, sample_manifest):
        """Valid signature passes validation."""
        public_key, private_key = signer.generate_operator_keypair()
        signature = signer.sign_manifest(sample_manifest, private_key)

        validator = SkillManifestValidator(operator_public_key=public_key)
        assert validator.validate_signature(sample_manifest, signature) is True

    def test_invalid_signature_fails_validation(self, signer, sample_manifest):
        """Invalid signature fails validation."""
        public_key, _ = signer.generate_operator_keypair()
        invalid_sig = "aW52YWxpZCBzaWduYXR1cmUgZGF0YQ=="  # base64 "invalid signature data"

        validator = SkillManifestValidator(operator_public_key=public_key)
        assert validator.validate_signature(sample_manifest, invalid_sig) is False

    def test_tampered_manifest_fails_validation(self, signer, sample_manifest):
        """Tampering with manifest invalidates signature."""
        public_key, private_key = signer.generate_operator_keypair()
        signature = signer.sign_manifest(sample_manifest, private_key)

        # Tamper with manifest
        tampered = SkillManifest(
            skill_id="test.validator.v2",  # Changed
            version="1.0.0",
            boot_layer="bundled",
            parameters=sample_manifest.parameters,
            dependencies=sample_manifest.dependencies,
            entry_point=sample_manifest.entry_point
        )

        validator = SkillManifestValidator(operator_public_key=public_key)
        assert validator.validate_signature(tampered, signature) is False

    def test_tampered_signature_fails_validation(self, signer, sample_manifest):
        """Tampering with signature fails validation."""
        public_key, private_key = signer.generate_operator_keypair()
        signature = signer.sign_manifest(sample_manifest, private_key)

        # Tamper with signature
        tampered_sig = chr((ord(signature[0]) + 1) % 256) + signature[1:]

        validator = SkillManifestValidator(operator_public_key=public_key)
        assert validator.validate_signature(sample_manifest, tampered_sig) is False

    def test_wrong_key_fails_validation(self, signer, sample_manifest):
        """Signature from different key fails validation."""
        public_key1, private_key1 = signer.generate_operator_keypair()
        public_key2, _ = signer.generate_operator_keypair()

        signature = signer.sign_manifest(sample_manifest, private_key1)

        validator = SkillManifestValidator(operator_public_key=public_key2)
        assert validator.validate_signature(sample_manifest, signature) is False

    def test_signature_validation_error_on_bad_public_key(self, sample_manifest):
        """SignatureValidationError raised when public key not configured."""
        validator = SkillManifestValidator(operator_public_key=None)

        with pytest.raises(SignatureValidationError):
            validator.validate_signature(sample_manifest, "aW52YWxpZA==")

    def test_invalid_base64_signature_returns_false(self, signer, sample_manifest):
        """Invalid base64 signature returns False."""
        public_key, _ = signer.generate_operator_keypair()
        validator = SkillManifestValidator(operator_public_key=public_key)

        # "!!!" is not valid base64
        assert validator.validate_signature(sample_manifest, "!!!") is False


class TestManifestIntegrity:
    """Test manifest integrity checking (hash comparison)."""

    def test_matching_hash_passes_integrity_check(self, sample_manifest):
        """Matching hash passes integrity check."""
        manifest_dict = sample_manifest.to_dict()
        manifest_json = json.dumps(manifest_dict, sort_keys=True, separators=(",", ":"))
        stored_hash = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()

        validator = SkillManifestValidator()
        assert validator.validate_manifest_integrity(sample_manifest, stored_hash) is True

    def test_mismatched_hash_raises_error(self, sample_manifest):
        """Mismatched hash raises ManifestTamperedError."""
        wrong_hash = "0" * 64  # Invalid hash

        validator = SkillManifestValidator()
        with pytest.raises(ManifestTamperedError):
            validator.validate_manifest_integrity(sample_manifest, wrong_hash)

    def test_integrity_error_contains_hashes(self, sample_manifest):
        """ManifestTamperedError message contains both hashes."""
        wrong_hash = "a" * 64

        validator = SkillManifestValidator()
        try:
            validator.validate_manifest_integrity(sample_manifest, wrong_hash)
            pytest.fail("Expected ManifestTamperedError")
        except ManifestTamperedError as e:
            error_msg = str(e)
            assert "hash mismatch" in error_msg
            assert wrong_hash in error_msg


class TestValidatorWithoutPublicKey:
    """Test validator behavior when public key not provided."""

    def test_validate_signature_without_key_raises_error(self, sample_manifest):
        """Calling validate_signature without key raises SignatureValidationError."""
        validator = SkillManifestValidator(operator_public_key=None)

        with pytest.raises(SignatureValidationError):
            validator.validate_signature(sample_manifest, "aW52YWxpZA==")

    def test_get_operator_public_key_without_key_raises_error(self):
        """Calling get_operator_public_key without key raises RuntimeError."""
        validator = SkillManifestValidator(operator_public_key=None)

        with pytest.raises(RuntimeError):
            validator.get_operator_public_key()


class TestComplexManifests:
    """Test validation with complex manifests."""

    def test_validate_manifest_with_many_parameters(self, signer):
        """Validation works with many parameters."""
        public_key, private_key = signer.generate_operator_keypair()

        manifest = SkillManifest(
            skill_id="test.complex",
            version="1.0.0",
            boot_layer="bundled",
            parameters=[
                ManifestParameter(name=f"param_{i}", param_type="float", default=0.5)
                for i in range(20)
            ],
            dependencies=[],
            entry_point="test:Test.execute"
        )

        signature = signer.sign_manifest(manifest, private_key)

        validator = SkillManifestValidator(operator_public_key=public_key)
        assert validator.validate_signature(manifest, signature) is True

    def test_validate_manifest_with_special_characters(self, signer):
        """Validation works with special characters in strings."""
        public_key, private_key = signer.generate_operator_keypair()

        manifest = SkillManifest(
            skill_id="test.special.χαρακτήρες",
            version="1.0.0",
            boot_layer="bundled",
            parameters=[],
            dependencies=[],
            entry_point="test:TestΣ.execute"
        )

        signature = signer.sign_manifest(manifest, private_key)

        validator = SkillManifestValidator(operator_public_key=public_key)
        assert validator.validate_signature(manifest, signature) is True


class TestEndToEndValidation:
    """Test complete sign-validate cycle."""

    def test_sign_multiple_manifests_and_validate_all(self, signer):
        """Multiple manifests can be independently signed and validated."""
        public_key, private_key = signer.generate_operator_keypair()
        validator = SkillManifestValidator(operator_public_key=public_key)

        manifests = [
            SkillManifest(
                skill_id=f"test.manifest.{i}",
                version=f"1.{i}.0",
                boot_layer="bundled",
                parameters=[],
                dependencies=[],
                entry_point="test:Test.execute"
            )
            for i in range(5)
        ]

        signatures = [signer.sign_manifest(m, private_key) for m in manifests]

        for manifest, signature in zip(manifests, signatures):
            assert validator.validate_signature(manifest, signature) is True

    def test_signature_cross_validation_fails(self, signer):
        """Signature for one manifest fails validation for different manifest."""
        public_key, private_key = signer.generate_operator_keypair()
        validator = SkillManifestValidator(operator_public_key=public_key)

        manifest1 = SkillManifest(
            skill_id="test.manifest.1",
            version="1.0.0",
            boot_layer="bundled",
            parameters=[],
            dependencies=[],
            entry_point="test:Test.execute"
        )

        manifest2 = SkillManifest(
            skill_id="test.manifest.2",
            version="1.0.0",
            boot_layer="bundled",
            parameters=[],
            dependencies=[],
            entry_point="test:Test.execute"
        )

        sig1 = signer.sign_manifest(manifest1, private_key)

        # sig1 should not validate manifest2
        assert validator.validate_signature(manifest2, sig1) is False


class TestAuditLogging:
    """Test audit logging integration."""

    def test_signature_validation_can_be_called_without_audit_chain(self, signer, sample_manifest):
        """Validation works without audit chain."""
        public_key, private_key = signer.generate_operator_keypair()
        signature = signer.sign_manifest(sample_manifest, private_key)

        # No audit_chain provided
        validator = SkillManifestValidator(operator_public_key=public_key, audit_chain=None)
        assert validator.validate_signature(sample_manifest, signature) is True

    def test_manifest_integrity_can_be_called_without_audit_chain(self, sample_manifest):
        """Integrity validation works without audit chain."""
        manifest_dict = sample_manifest.to_dict()
        manifest_json = json.dumps(manifest_dict, sort_keys=True, separators=(",", ":"))
        stored_hash = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()

        validator = SkillManifestValidator(audit_chain=None)
        assert validator.validate_manifest_integrity(sample_manifest, stored_hash) is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

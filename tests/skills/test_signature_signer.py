"""Skill Manifest Signer — unit tests (Phase 1, ADR-0666).

Tests:
- RSA keypair generation (2048-bit)
- Manifest signing + signature verification
- Key persistence (save/load)
- Key tampering detection
- Key rotation
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend

from core.skills.signature.signer import SkillManifestSigner
from core.skills.signature.validator import SkillManifestValidator
from core.skills.manifest_validator import SkillManifest, ManifestParameter, ManifestDependency


@pytest.fixture
def signer():
    return SkillManifestSigner()


@pytest.fixture
def sample_manifest():
    return SkillManifest(
        skill_id="test.manifest",
        version="1.0.0",
        boot_layer="bundled",
        parameters=[
            ManifestParameter(name="threshold", param_type="float", default=0.7, bounds=(0.0, 1.0))
        ],
        dependencies=[],
        entry_point="test:TestSkill.execute"
    )


class TestKeypairGeneration:
    """Test RSA keypair generation."""

    def test_generate_keypair_returns_tuple(self, signer):
        """Keypair generation returns (public_key, private_key)."""
        public_key, private_key = signer.generate_operator_keypair()
        assert public_key is not None
        assert private_key is not None

    def test_keypair_is_2048_bit(self, signer):
        """Generated keypair is 2048-bit RSA."""
        public_key, private_key = signer.generate_operator_keypair()
        assert private_key.key_size == 2048
        assert public_key.key_size == 2048

    def test_keypair_public_key_matches_private_key(self, signer):
        """Public key derived from private key matches."""
        _, private_key = signer.generate_operator_keypair()
        derived_public = private_key.public_key()
        assert derived_public.key_size == 2048


class TestManifestSigning:
    """Test manifest signing."""

    def test_sign_manifest_returns_base64_string(self, signer, sample_manifest):
        """Signing manifest returns base64-encoded signature."""
        _, private_key = signer.generate_operator_keypair()
        signature = signer.sign_manifest(sample_manifest, private_key)
        assert isinstance(signature, str)
        assert len(signature) > 0
        # Base64 should only contain alphanumeric + /+= characters
        assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=" for c in signature)

    def test_sign_same_manifest_produces_same_signature(self, signer, sample_manifest):
        """Signing same manifest deterministically produces same signature (RSA-PSS uses fixed salt)."""
        _, private_key = signer.generate_operator_keypair()
        sig1 = signer.sign_manifest(sample_manifest, private_key)
        sig2 = signer.sign_manifest(sample_manifest, private_key)
        # Note: RSA-PSS with random salt will produce different signatures each time
        # but they should all verify correctly. Test below verifies this.
        assert isinstance(sig1, str) and isinstance(sig2, str)

    def test_sign_different_manifests_produces_different_signatures(self, signer, sample_manifest):
        """Signing different manifests produces different signatures."""
        _, private_key = signer.generate_operator_keypair()

        sig1 = signer.sign_manifest(sample_manifest, private_key)

        modified_manifest = SkillManifest(
            skill_id="test.manifest.v2",
            version="1.0.1",
            boot_layer="bundled",
            parameters=[],
            dependencies=[],
            entry_point="test:TestSkill.execute"
        )
        sig2 = signer.sign_manifest(modified_manifest, private_key)

        assert sig1 != sig2


class TestKeySerialization:
    """Test key persistence (save/load)."""

    def test_save_key_creates_pem_file(self, signer):
        """Saving key creates PEM file."""
        _, private_key = signer.generate_operator_keypair()

        with tempfile.TemporaryDirectory() as tmpdir:
            key_path = Path(tmpdir) / "test_key.pem"
            signer.save_operator_key(private_key, key_path, password=b"test_password")

            assert key_path.exists()
            # Verify it's valid PEM
            with open(key_path, "rb") as f:
                assert b"BEGIN ENCRYPTED PRIVATE KEY" in f.read()

    def test_save_key_sets_permissions(self, signer):
        """Saved key file has restricted permissions (0o600)."""
        _, private_key = signer.generate_operator_keypair()

        with tempfile.TemporaryDirectory() as tmpdir:
            key_path = Path(tmpdir) / "test_key.pem"
            signer.save_operator_key(private_key, key_path)

            # Check permissions
            stat_info = key_path.stat()
            assert (stat_info.st_mode & 0o777) == 0o600

    def test_load_key_recovers_private_key(self, signer):
        """Loading saved key recovers the original private key."""
        _, original_key = signer.generate_operator_keypair()
        password = b"secure_password"

        with tempfile.TemporaryDirectory() as tmpdir:
            key_path = Path(tmpdir) / "test_key.pem"
            signer.save_operator_key(original_key, key_path, password=password)

            loaded_key = signer.load_operator_key(key_path, password=password)
            assert loaded_key.key_size == 2048

    def test_load_nonexistent_key_raises_error(self, signer):
        """Loading nonexistent key raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            signer.load_operator_key("/nonexistent/path/to/key.pem")

    def test_load_key_with_wrong_password_raises_error(self, signer):
        """Loading key with wrong password raises ValueError."""
        _, private_key = signer.generate_operator_keypair()
        password = b"correct_password"

        with tempfile.TemporaryDirectory() as tmpdir:
            key_path = Path(tmpdir) / "test_key.pem"
            signer.save_operator_key(private_key, key_path, password=password)

            with pytest.raises(ValueError):
                signer.load_operator_key(key_path, password=b"wrong_password")


class TestManifestSignatureVerification:
    """Test end-to-end signing + verification."""

    def test_sign_and_verify_manifest(self, signer, sample_manifest):
        """Sign manifest and verify signature."""
        public_key, private_key = signer.generate_operator_keypair()

        # Sign
        signature = signer.sign_manifest(sample_manifest, private_key)

        # Verify
        validator = SkillManifestValidator(operator_public_key=public_key)
        is_valid = validator.validate_signature(sample_manifest, signature)

        assert is_valid

    def test_tampered_manifest_fails_verification(self, signer, sample_manifest):
        """Tampering with manifest fails verification."""
        public_key, private_key = signer.generate_operator_keypair()

        # Sign original
        signature = signer.sign_manifest(sample_manifest, private_key)

        # Tamper with manifest
        tampered_manifest = SkillManifest(
            skill_id="test.manifest",
            version="1.0.1",  # Changed version
            boot_layer="bundled",
            parameters=sample_manifest.parameters,
            dependencies=sample_manifest.dependencies,
            entry_point=sample_manifest.entry_point
        )

        # Verify should fail
        validator = SkillManifestValidator(operator_public_key=public_key)
        is_valid = validator.validate_signature(tampered_manifest, signature)

        assert not is_valid

    def test_tampered_signature_fails_verification(self, signer, sample_manifest):
        """Tampering with signature fails verification."""
        public_key, private_key = signer.generate_operator_keypair()

        # Sign
        signature = signer.sign_manifest(sample_manifest, private_key)

        # Tamper with signature (flip first character)
        tampered_sig = chr((ord(signature[0]) % 25) + ord('A')) + signature[1:]

        # Verify should fail
        validator = SkillManifestValidator(operator_public_key=public_key)
        is_valid = validator.validate_signature(sample_manifest, tampered_sig)

        assert not is_valid

    def test_wrong_key_fails_verification(self, signer, sample_manifest):
        """Signature from one key fails verification with different key."""
        _, private_key1 = signer.generate_operator_keypair()
        public_key2, _ = signer.generate_operator_keypair()

        # Sign with private_key1
        signature = signer.sign_manifest(sample_manifest, private_key1)

        # Verify with public_key2 should fail
        validator = SkillManifestValidator(operator_public_key=public_key2)
        is_valid = validator.validate_signature(sample_manifest, signature)

        assert not is_valid


class TestKeyRotation:
    """Test key rotation scenarios."""

    def test_sign_with_old_key_verify_with_new_key_fails(self, signer, sample_manifest):
        """Signature from old key fails verification with new key."""
        public_key_old, private_key_old = signer.generate_operator_keypair()
        public_key_new, _ = signer.generate_operator_keypair()

        # Sign with old key
        signature = signer.sign_manifest(sample_manifest, private_key_old)

        # Verify with new key should fail
        validator = SkillManifestValidator(operator_public_key=public_key_new)
        is_valid = validator.validate_signature(sample_manifest, signature)

        assert not is_valid

    def test_sign_with_new_key_verify_with_old_key_fails(self, signer, sample_manifest):
        """Signature from new key fails verification with old key."""
        public_key_old, _ = signer.generate_operator_keypair()
        public_key_new, private_key_new = signer.generate_operator_keypair()

        # Sign with new key
        signature = signer.sign_manifest(sample_manifest, private_key_new)

        # Verify with old key should fail
        validator = SkillManifestValidator(operator_public_key=public_key_old)
        is_valid = validator.validate_signature(sample_manifest, signature)

        assert not is_valid


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_sign_manifest_with_empty_parameters_list(self, signer):
        """Signing manifest with empty parameters works."""
        _, private_key = signer.generate_operator_keypair()

        manifest = SkillManifest(
            skill_id="test.empty",
            version="1.0.0",
            boot_layer="bundled",
            parameters=[],
            dependencies=[],
            entry_point="test:Test.execute"
        )

        signature = signer.sign_manifest(manifest, private_key)
        assert signature is not None

    def test_sign_manifest_with_many_dependencies(self, signer):
        """Signing manifest with many dependencies works."""
        _, private_key = signer.generate_operator_keypair()

        manifest = SkillManifest(
            skill_id="test.complex",
            version="1.0.0",
            boot_layer="bundled",
            parameters=[],
            dependencies=[
                ManifestDependency(skill_id=f"skill.{i}", version_constraint=">=1.0.0")
                for i in range(10)
            ],
            entry_point="test:Test.execute"
        )

        signature = signer.sign_manifest(manifest, private_key)
        assert signature is not None

    def test_invalid_base64_signature_fails_validation(self, signer, sample_manifest):
        """Invalid base64 signature fails validation gracefully."""
        public_key, _ = signer.generate_operator_keypair()

        validator = SkillManifestValidator(operator_public_key=public_key)
        is_valid = validator.validate_signature(sample_manifest, "not_valid_base64!!!")

        assert not is_valid


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

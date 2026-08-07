"""Tests for package signing (ADR-0268 Phase 3)."""
import base64
import json
import tempfile
from pathlib import Path

import pytest

from core.package_manager.signing import (
    PackageSigner,
    PackageVerifier,
    SigningError,
    get_marketplace_verifier,
)


@pytest.fixture
def rsa_key_pair(tmp_path):
    """Generate RSA key pair for testing."""
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    public_key = private_key.public_key()
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    private_key_file = tmp_path / "private.pem"
    private_key_file.write_bytes(private_pem)

    return {
        "private_path": private_key_file,
        "private_pem": private_pem.decode("utf-8"),
        "public_pem": public_pem.decode("utf-8"),
    }


@pytest.fixture
def test_manifest():
    """Create a test manifest for signing."""
    return {
        "id": "com.example.test-pkg",
        "version": "1.0.0",
        "name": "Test Package",
        "capabilities": ["skill_loading"],
    }


class TestPackageSigner:
    """Tests for PackageSigner."""

    def test_sign_manifest(self, rsa_key_pair, test_manifest):
        """Signing a manifest should produce a valid signature."""
        signer = PackageSigner(rsa_key_pair["private_path"])
        signature = signer.sign_manifest(test_manifest)

        # Signature should be base64-encoded
        assert isinstance(signature, str)
        decoded = base64.b64decode(signature)
        assert len(decoded) > 0

    def test_sign_deterministic(self, rsa_key_pair, test_manifest):
        """Signing the same manifest twice should produce the same signature."""
        signer = PackageSigner(rsa_key_pair["private_path"])
        sig1 = signer.sign_manifest(test_manifest)
        sig2 = signer.sign_manifest(test_manifest)

        assert sig1 == sig2

    def test_get_public_key_pem(self, rsa_key_pair):
        """Getting public key should return valid PEM."""
        signer = PackageSigner(rsa_key_pair["private_path"])
        public_pem = signer.get_public_key_pem()

        assert public_pem.startswith("-----BEGIN PUBLIC KEY-----")
        assert public_pem.endswith("-----END PUBLIC KEY-----\n")

    def test_invalid_key_file(self, tmp_path):
        """Loading invalid key file should raise."""
        bad_key = tmp_path / "bad.pem"
        bad_key.write_text("not a valid key")

        with pytest.raises(SigningError, match="Failed to load private key"):
            PackageSigner(bad_key)


class TestPackageVerifier:
    """Tests for PackageVerifier."""

    def test_verify_valid_signature(self, rsa_key_pair, test_manifest):
        """Verifying a valid signature should succeed."""
        signer = PackageSigner(rsa_key_pair["private_path"])
        signature = signer.sign_manifest(test_manifest)

        verifier = PackageVerifier(rsa_key_pair["public_pem"])
        assert verifier.verify_manifest(test_manifest, signature) is True

    def test_verify_invalid_signature(self, rsa_key_pair, test_manifest):
        """Verifying an invalid signature should fail."""
        verifier = PackageVerifier(rsa_key_pair["public_pem"])
        invalid_signature = base64.b64encode(b"invalid signature").decode("utf-8")

        assert verifier.verify_manifest(test_manifest, invalid_signature) is False

    def test_verify_modified_manifest(self, rsa_key_pair, test_manifest):
        """Verifying signature of modified manifest should fail."""
        signer = PackageSigner(rsa_key_pair["private_path"])
        signature = signer.sign_manifest(test_manifest)

        # Modify manifest
        test_manifest["version"] = "2.0.0"

        verifier = PackageVerifier(rsa_key_pair["public_pem"])
        assert verifier.verify_manifest(test_manifest, signature) is False

    def test_verify_without_key(self, test_manifest):
        """Verifying without a public key should return False."""
        verifier = PackageVerifier(None)
        assert verifier.verify_manifest(test_manifest, "any_signature") is False

    def test_invalid_public_key(self):
        """Loading invalid public key should raise."""
        with pytest.raises(SigningError, match="Failed to load public key"):
            PackageVerifier("not a valid key")


class TestRoundTrip:
    """End-to-end signing and verification tests."""

    def test_sign_and_verify_roundtrip(self, rsa_key_pair, test_manifest):
        """Sign a manifest and verify it with the corresponding public key."""
        signer = PackageSigner(rsa_key_pair["private_path"])
        signature = signer.sign_manifest(test_manifest)

        verifier = PackageVerifier(rsa_key_pair["public_pem"])
        assert verifier.verify_manifest(test_manifest, signature) is True

    def test_multiple_manifests(self, rsa_key_pair):
        """Signing and verifying multiple manifests should work."""
        signer = PackageSigner(rsa_key_pair["private_path"])
        verifier = PackageVerifier(rsa_key_pair["public_pem"])

        manifests = [
            {"id": "pkg-1", "version": "1.0.0"},
            {"id": "pkg-2", "version": "2.0.0"},
            {"id": "pkg-3", "version": "1.5.0"},
        ]

        for manifest in manifests:
            signature = signer.sign_manifest(manifest)
            assert verifier.verify_manifest(manifest, signature) is True


class TestGetMarketplaceVerifier:
    """Tests for marketplace verifier factory."""

    def test_get_marketplace_verifier_without_key(self):
        """Getting marketplace verifier without key file should return verifier with None key."""
        verifier = get_marketplace_verifier()
        assert isinstance(verifier, PackageVerifier)
        assert verifier.public_key is None

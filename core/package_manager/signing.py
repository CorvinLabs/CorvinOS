"""Package signing and verification (ADR-0268 Phase 3)."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

try:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.x509 import load_pem_x509_certificate
except ImportError:
    raise ImportError("cryptography library required for package signing")

logger = logging.getLogger(__name__)


class SigningError(Exception):
    """Raised when signing/verification fails."""

    pass


class PackageSigner:
    """Sign skill packages with RSA-2048."""

    def __init__(self, private_key_path: str | Path):
        """
        Initialize signer with private key.

        Args:
            private_key_path: Path to RSA private key (PEM format)
        """
        self.private_key_path = Path(private_key_path)
        self._load_private_key()

    def _load_private_key(self) -> None:
        """Load RSA private key from disk."""
        try:
            with open(self.private_key_path, "rb") as f:
                key_data = f.read()
            self.private_key = serialization.load_pem_private_key(
                key_data, password=None, backend=default_backend()
            )
            if not isinstance(self.private_key, rsa.RSAPrivateKey):
                raise TypeError("Private key must be RSA key")
        except Exception as e:
            raise SigningError(f"Failed to load private key: {e}") from e

    def sign_manifest(self, manifest: dict[str, Any]) -> str:
        """
        Sign a manifest dictionary.

        Returns:
            Base64-encoded signature
        """
        # Create canonical JSON representation for signing
        manifest_json = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
        manifest_bytes = manifest_json.encode("utf-8")

        # Sign with SHA256
        signature = self.private_key.sign(
            manifest_bytes,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )

        return base64.b64encode(signature).decode("utf-8")

    def get_public_key_pem(self) -> str:
        """Get public key in PEM format (for distribution)."""
        public_key = self.private_key.public_key()
        pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return pem.decode("utf-8")


class PackageVerifier:
    """Verify skill package signatures."""

    def __init__(self, public_key_pem: str | None = None):
        """
        Initialize verifier with public key.

        Args:
            public_key_pem: RSA public key in PEM format (or None for optional verification)
        """
        self.public_key_pem = public_key_pem
        self.public_key = None
        if public_key_pem:
            self._load_public_key()

    def _load_public_key(self) -> None:
        """Load RSA public key from PEM."""
        if not self.public_key_pem:
            return

        try:
            key_data = self.public_key_pem.encode("utf-8")
            self.public_key = serialization.load_pem_public_key(
                key_data, backend=default_backend()
            )
        except Exception as e:
            raise SigningError(f"Failed to load public key: {e}") from e

    def verify_manifest(self, manifest: dict[str, Any], signature_b64: str) -> bool:
        """
        Verify a manifest signature.

        Args:
            manifest: Package manifest dictionary
            signature_b64: Base64-encoded signature

        Returns:
            True if signature is valid, False otherwise

        Raises:
            SigningError if verification fails
        """
        if not self.public_key:
            logger.warning("No public key available for signature verification")
            return False

        try:
            # Recreate canonical JSON for verification
            manifest_json = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
            manifest_bytes = manifest_json.encode("utf-8")

            # Decode signature
            signature = base64.b64decode(signature_b64)

            # Verify signature
            self.public_key.verify(
                signature,
                manifest_bytes,
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
            return True

        except Exception as e:
            logger.debug(f"Signature verification failed: {e}")
            return False


def get_marketplace_verifier() -> PackageVerifier:
    """
    Get verifier for marketplace-signed packages.

    This loads the public key from a well-known location (e.g., ~/.corvin/marketplace_ca.pem)
    or returns a verifier with no key (optional verification mode).

    Returns:
        PackageVerifier instance
    """
    # For now, support optional verification (verifier with no key)
    # In production, this would load the marketplace CA public key
    marketplace_key_path = Path.home() / ".corvin" / "marketplace_ca.pem"

    if marketplace_key_path.exists():
        try:
            with open(marketplace_key_path, "r") as f:
                public_key_pem = f.read()
            return PackageVerifier(public_key_pem)
        except Exception as e:
            logger.warning(f"Failed to load marketplace CA key: {e}")

    # Fallback: optional verification
    return PackageVerifier(None)

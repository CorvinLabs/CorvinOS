"""Operator Key Manager — keypair management + rotation (Phase 1, ADR-0666).

Implements:
1. Operator RSA keypair generation and storage
2. Key rotation (old + new key coexist for 30 days)
3. Current + recent public key retrieval
4. Key lifecycle management
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend

from core.skills.signature.signer import SkillManifestSigner

logger = logging.getLogger(__name__)


class OperatorKeyManager:
    """Manages operator RSA keypair lifecycle.

    Responsibilities:
    - Generate and store operator keypair
    - Support key rotation (30-day overlap period)
    - Provide current and trusted public keys
    - Track key metadata (creation date, rotation history)
    """

    TRUST_WINDOW_DAYS = 30  # Old keys trusted for this long
    KEY_DIR_NAME = "operator_keys"

    def __init__(self, corvin_home: str | Path):
        """Initialize key manager.

        Args:
            corvin_home: Path to ~/.corvin or equivalent
        """
        self.corvin_home = Path(corvin_home)
        self.keys_dir = self.corvin_home / "tenants" / "_default" / "global" / self.KEY_DIR_NAME
        self.keys_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_file = self.keys_dir / "metadata.json"
        self.current_key_file = self.keys_dir / "current_key.pem"
        self.signer = SkillManifestSigner()  # No audit for key generation

    def generate_keypair(self, password: Optional[bytes] = None) -> None:
        """Generate a new operator keypair and save it.

        If a current key exists, it's rotated to old_keys/ first.

        Args:
            password: Optional password for key encryption
        """
        # Generate new keypair
        public_key, private_key = self.signer.generate_operator_keypair()

        # If current key exists, rotate it to old keys
        if self.current_key_file.exists():
            self._rotate_current_key()

        # Save new key as current
        self.signer.save_operator_key(private_key, self.current_key_file, password)

        # Update metadata
        self._update_metadata(public_key)

        logger.info(f"Generated new operator keypair at {self.current_key_file}")

    def current_public_key(self) -> rsa.RSAPublicKey:
        """Get the current operator public key.

        Returns:
            RSA public key object

        Raises:
            FileNotFoundError: if no keypair exists
        """
        if not self.current_key_file.exists():
            raise FileNotFoundError(f"No operator keypair found at {self.current_key_file}")

        metadata = self._load_metadata()
        if "current_key" not in metadata:
            raise ValueError("Keypair metadata corrupted (no current_key)")

        current_key_data = metadata["current_key"]
        public_key_pem = current_key_data.get("public_key_pem")

        if not public_key_pem:
            raise ValueError("Public key not found in metadata")

        # Reconstruct public key from PEM
        public_key_bytes = public_key_pem.encode("utf-8")
        return serialization.load_pem_public_key(
            public_key_bytes,
            backend=default_backend()
        )

    def all_trusted_public_keys(self) -> list[rsa.RSAPublicKey]:
        """Get all currently trusted public keys (current + recent).

        Returns:
            List of RSA public key objects (current key first, then recent keys)
        """
        keys = []
        metadata = self._load_metadata()

        # Add current key
        if "current_key" in metadata:
            try:
                keys.append(self.current_public_key())
            except Exception as e:
                logger.error(f"Failed to load current key: {e}")

        # Add old keys that are still within trust window
        if "old_keys" in metadata:
            now = datetime.now(timezone.utc)
            for old_key_data in metadata["old_keys"]:
                rotated_at = datetime.fromisoformat(old_key_data["rotated_at"])
                age = now - rotated_at

                if age <= timedelta(days=self.TRUST_WINDOW_DAYS):
                    try:
                        public_key_pem = old_key_data["public_key_pem"]
                        public_key_bytes = public_key_pem.encode("utf-8")
                        public_key = serialization.load_pem_public_key(
                            public_key_bytes,
                            backend=default_backend()
                        )
                        keys.append(public_key)
                    except Exception as e:
                        logger.warning(f"Failed to load old key: {e}")

        return keys

    def load_current_private_key(
        self,
        password: Optional[bytes] = None
    ) -> rsa.RSAPrivateKey:
        """Load the current operator private key.

        Args:
            password: Password for decryption (if key is encrypted)

        Returns:
            RSA private key object

        Raises:
            FileNotFoundError: if no keypair exists
        """
        return self.signer.load_operator_key(self.current_key_file, password)

    def _rotate_current_key(self) -> None:
        """Move current key to old_keys with rotation timestamp."""
        metadata = self._load_metadata()

        if "current_key" not in metadata:
            return

        current_key_data = metadata["current_key"]
        current_key_data["rotated_at"] = datetime.now(timezone.utc).isoformat()

        if "old_keys" not in metadata:
            metadata["old_keys"] = []

        metadata["old_keys"].append(current_key_data)

        # Remove very old keys (>30 days)
        now = datetime.now(timezone.utc)
        metadata["old_keys"] = [
            k for k in metadata["old_keys"]
            if (now - datetime.fromisoformat(k["rotated_at"])) <= timedelta(days=self.TRUST_WINDOW_DAYS)
        ]

        metadata["current_key"] = None
        self._save_metadata(metadata)

    def _update_metadata(self, public_key: rsa.RSAPublicKey) -> None:
        """Update metadata with new keypair info."""
        metadata = self._load_metadata()

        # Serialize public key to PEM
        public_key_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode("utf-8")

        metadata["current_key"] = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "public_key_pem": public_key_pem,
            "key_size": 2048
        }

        self._save_metadata(metadata)

    def _load_metadata(self) -> dict:
        """Load keypair metadata from file."""
        if self.metadata_file.exists():
            try:
                with open(self.metadata_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load metadata: {e}")
                return {}
        return {}

    def _save_metadata(self, metadata: dict) -> None:
        """Save keypair metadata to file."""
        try:
            with open(self.metadata_file, "w") as f:
                json.dump(metadata, f, indent=2, default=str)
            self.metadata_file.chmod(0o600)
        except Exception as e:
            logger.error(f"Failed to save metadata: {e}")

    def cleanup_expired_keys(self) -> int:
        """Remove keys older than trust window.

        Returns:
            Number of keys removed
        """
        metadata = self._load_metadata()
        original_count = len(metadata.get("old_keys", []))

        if "old_keys" not in metadata:
            return 0

        now = datetime.now(timezone.utc)
        metadata["old_keys"] = [
            k for k in metadata["old_keys"]
            if (now - datetime.fromisoformat(k["rotated_at"])) <= timedelta(days=self.TRUST_WINDOW_DAYS)
        ]

        removed = original_count - len(metadata["old_keys"])
        if removed > 0:
            self._save_metadata(metadata)
            logger.info(f"Cleaned up {removed} expired keys")

        return removed

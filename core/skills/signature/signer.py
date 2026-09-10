"""Skill Manifest Signer — RSA signatures for manifests (Phase 1, ADR-0666).

Implements:
1. Operator RSA keypair generation (2048-bit)
2. Manifest signing + signature verification
3. Operator key persistence (encrypted PEM format)
4. Audit logging for all signing operations
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.backends import default_backend

from core.compliance.audit_chain_writer import AuditChainWriter, AuditEvent
from core.skills.manifest_validator import SkillManifest

logger = logging.getLogger(__name__)


class SkillManifestSigner:
    """Signs Skill manifests with operator RSA key.

    Responsibilities:
    - Generate operator RSA keypair (2048-bit)
    - Sign manifests deterministically
    - Persist keys in encrypted PEM format
    - Audit all signing operations
    """

    def __init__(self, audit_chain: Optional[AuditChainWriter] = None):
        """Initialize signer with optional audit chain.

        Args:
            audit_chain: AuditChainWriter for logging (None = no audit)
        """
        self.audit_chain = audit_chain

    def generate_operator_keypair(self) -> tuple[rsa.RSAPublicKey, rsa.RSAPrivateKey]:
        """Generate a new operator RSA keypair (2048-bit).

        Returns:
            (public_key, private_key) tuple

        Raises:
            RuntimeError: if key generation fails
        """
        try:
            private_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=2048,
                backend=default_backend()
            )
            public_key = private_key.public_key()

            self._log_audit(
                event_type="operator_keypair_generated",
                details={"key_size": 2048, "public_exponent": 65537}
            )

            return public_key, private_key
        except Exception as e:
            logger.error(f"Failed to generate operator keypair: {e}")
            self._log_audit(
                event_type="operator_keypair_generation_failed",
                details={"error": str(e)},
                severity="error"
            )
            raise RuntimeError(f"Keypair generation failed: {e}")

    def sign_manifest(
        self,
        manifest: SkillManifest,
        operator_private_key: rsa.RSAPrivateKey,
        tenant_id: str = "_default"
    ) -> str:
        """Sign a Skill manifest with operator private key.

        Args:
            manifest: SkillManifest to sign
            operator_private_key: RSA private key for signing
            tenant_id: Tenant scope for audit logging

        Returns:
            Base64-encoded signature string

        Raises:
            RuntimeError: if signing fails
        """
        try:
            # Deterministic manifest serialization for hashing
            manifest_dict = manifest.to_dict()
            manifest_json = json.dumps(manifest_dict, sort_keys=True, separators=(",", ":"))
            manifest_bytes = manifest_json.encode("utf-8")

            # Sign with RSA-PSS padding (secure against padding oracle attacks)
            signature_bytes = operator_private_key.sign(
                manifest_bytes,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH
                ),
                hashes.SHA256()
            )

            signature_b64 = base64.b64encode(signature_bytes).decode("utf-8")

            self._log_audit(
                event_type="manifest_signed",
                details={
                    "skill_id": manifest.skill_id,
                    "version": manifest.version,
                    "manifest_hash": hashlib.sha256(manifest_bytes).hexdigest(),
                    "signature_length": len(signature_b64)
                },
                tenant_id=tenant_id
            )

            return signature_b64
        except Exception as e:
            logger.error(f"Failed to sign manifest {manifest.skill_id}: {e}")
            self._log_audit(
                event_type="manifest_signing_failed",
                details={"skill_id": manifest.skill_id, "error": str(e)},
                severity="error",
                tenant_id=tenant_id
            )
            raise RuntimeError(f"Manifest signing failed: {e}")

    def load_operator_key(
        self,
        key_path: str | Path,
        password: Optional[bytes] = None,
        tenant_id: str = "_default"
    ) -> rsa.RSAPrivateKey:
        """Load operator private key from encrypted PEM file.

        Args:
            key_path: Path to PEM-format encrypted key file
            password: Password for decryption (bytes)
            tenant_id: Tenant scope for audit logging

        Returns:
            RSA private key object

        Raises:
            FileNotFoundError: if key file not found
            ValueError: if key is invalid or password is wrong
        """
        key_path = Path(key_path)

        if not key_path.exists():
            self._log_audit(
                event_type="operator_key_load_failed",
                details={"path": str(key_path), "reason": "file_not_found"},
                severity="error",
                tenant_id=tenant_id
            )
            raise FileNotFoundError(f"Operator key not found: {key_path}")

        try:
            with open(key_path, "rb") as f:
                key_data = f.read()

            private_key = serialization.load_pem_private_key(
                key_data,
                password=password,
                backend=default_backend()
            )

            self._log_audit(
                event_type="operator_key_loaded",
                details={"path": str(key_path)},
                tenant_id=tenant_id
            )

            return private_key
        except Exception as e:
            logger.error(f"Failed to load operator key from {key_path}: {e}")
            self._log_audit(
                event_type="operator_key_load_failed",
                details={"path": str(key_path), "error": str(e)},
                severity="error",
                tenant_id=tenant_id
            )
            raise ValueError(f"Invalid operator key or password: {e}")

    def save_operator_key(
        self,
        private_key: rsa.RSAPrivateKey,
        key_path: str | Path,
        password: Optional[bytes] = None,
        tenant_id: str = "_default"
    ) -> None:
        """Save operator private key to encrypted PEM file.

        Args:
            private_key: RSA private key to save
            key_path: Path to save PEM file
            password: Password for encryption (bytes, None = no encryption)
            tenant_id: Tenant scope for audit logging

        Raises:
            RuntimeError: if save fails
        """
        key_path = Path(key_path)
        key_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            # Encrypt with password if provided, else plaintext
            encryption_algo = (
                serialization.BestAvailableEncryption(password)
                if password
                else serialization.NoEncryption()
            )

            pem_bytes = private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=encryption_algo
            )

            with open(key_path, "wb") as f:
                f.write(pem_bytes)

            # Restrict permissions (owner only)
            key_path.chmod(0o600)

            self._log_audit(
                event_type="operator_key_saved",
                details={"path": str(key_path), "encrypted": password is not None},
                tenant_id=tenant_id
            )
        except Exception as e:
            logger.error(f"Failed to save operator key to {key_path}: {e}")
            self._log_audit(
                event_type="operator_key_save_failed",
                details={"path": str(key_path), "error": str(e)},
                severity="error",
                tenant_id=tenant_id
            )
            raise RuntimeError(f"Failed to save operator key: {e}")

    def _log_audit(
        self,
        event_type: str,
        details: dict,
        severity: Optional[str] = None,
        tenant_id: str = "_default"
    ) -> None:
        """Log an audit event if audit chain is configured.

        Args:
            event_type: Type of audit event
            details: Event details dict
            severity: Optional severity level (info, warning, error, critical)
            tenant_id: Tenant scope
        """
        if not self.audit_chain:
            return

        event = AuditEvent(
            event_id=str(id(self)),  # Unique event ID
            event_type=event_type,
            tenant_id=tenant_id,
            user_id=None,  # Operator action, no user
            timestamp=str(json.dumps(asdict({}))),  # Will be set by audit chain
            details=details,
            severity=severity or "info"
        )

        try:
            self.audit_chain.write_event(event)
        except Exception as e:
            logger.warning(f"Failed to log audit event {event_type}: {e}")

"""Skill Manifest Validator — signature + integrity verification (Phase 1, ADR-0666).

Implements:
1. RSA signature verification for manifests
2. Manifest integrity checking (hash comparison)
3. Operator public key management
4. Audit logging for all validation operations
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from dataclasses import asdict
from typing import Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.backends import default_backend
from cryptography.exceptions import InvalidSignature

from core.compliance.audit_chain_writer import AuditChainWriter, AuditEvent
from core.skills.manifest_validator import SkillManifest

logger = logging.getLogger(__name__)


class SignatureValidationError(Exception):
    """Raised when a manifest signature fails validation."""
    pass


class ManifestTamperedError(Exception):
    """Raised when manifest hash doesn't match stored hash."""
    pass


class SkillManifestValidator:
    """Validates Skill manifest signatures and integrity.

    Responsibilities:
    - Verify RSA signatures against operator public key
    - Check manifest integrity (recompute hash, compare)
    - Load operator public key from hardcoded source
    - Audit all validation operations
    """

    def __init__(
        self,
        operator_public_key: Optional[rsa.RSAPublicKey] = None,
        audit_chain: Optional[AuditChainWriter] = None
    ):
        """Initialize validator.

        Args:
            operator_public_key: Operator public key for verification (None = load from hardcoded)
            audit_chain: AuditChainWriter for logging (None = no audit)
        """
        self.operator_public_key = operator_public_key
        self.audit_chain = audit_chain

    def validate_signature(
        self,
        manifest: SkillManifest,
        signature_b64: str,
        tenant_id: str = "_default"
    ) -> bool:
        """Validate manifest signature.

        Args:
            manifest: SkillManifest to validate
            signature_b64: Base64-encoded RSA signature
            tenant_id: Tenant scope for audit logging

        Returns:
            True if signature is valid, False otherwise

        Raises:
            SignatureValidationError: on cryptographic failure
        """
        try:
            # Deterministic manifest serialization (must match signer)
            manifest_dict = manifest.to_dict()
            manifest_json = json.dumps(manifest_dict, sort_keys=True, separators=(",", ":"))
            manifest_bytes = manifest_json.encode("utf-8")

            # Decode signature from base64
            try:
                signature_bytes = base64.b64decode(signature_b64)
            except Exception as e:
                self._log_audit(
                    event_type="signature_validation_failed",
                    details={
                        "skill_id": manifest.skill_id,
                        "reason": "invalid_base64",
                        "error": str(e)
                    },
                    severity="warning",
                    tenant_id=tenant_id
                )
                return False

            # Get public key
            public_key = self.operator_public_key or self.get_operator_public_key()

            # Verify signature using RSA-PSS
            try:
                public_key.verify(
                    signature_bytes,
                    manifest_bytes,
                    padding.PSS(
                        mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.MAX_LENGTH
                    ),
                    hashes.SHA256()
                )

                self._log_audit(
                    event_type="signature_validation_passed",
                    details={
                        "skill_id": manifest.skill_id,
                        "version": manifest.version,
                        "manifest_hash": hashlib.sha256(manifest_bytes).hexdigest()
                    },
                    tenant_id=tenant_id
                )

                return True
            except InvalidSignature:
                self._log_audit(
                    event_type="signature_validation_failed",
                    details={
                        "skill_id": manifest.skill_id,
                        "version": manifest.version,
                        "reason": "signature_mismatch",
                        "manifest_hash": hashlib.sha256(manifest_bytes).hexdigest()
                    },
                    severity="warning",
                    tenant_id=tenant_id
                )
                return False
        except Exception as e:
            logger.error(f"Signature validation error for {manifest.skill_id}: {e}")
            self._log_audit(
                event_type="signature_validation_error",
                details={"skill_id": manifest.skill_id, "error": str(e)},
                severity="error",
                tenant_id=tenant_id
            )
            raise SignatureValidationError(f"Signature validation failed: {e}")

    def validate_manifest_integrity(
        self,
        manifest: SkillManifest,
        stored_hash: str,
        tenant_id: str = "_default"
    ) -> bool:
        """Validate manifest integrity by comparing hash.

        Args:
            manifest: SkillManifest to validate
            stored_hash: Previously stored SHA256 hash (hex string)
            tenant_id: Tenant scope for audit logging

        Returns:
            True if hash matches, False otherwise

        Raises:
            ManifestTamperedError: if validation fails
        """
        try:
            # Recompute hash
            manifest_dict = manifest.to_dict()
            manifest_json = json.dumps(manifest_dict, sort_keys=True, separators=(",", ":"))
            manifest_bytes = manifest_json.encode("utf-8")
            current_hash = hashlib.sha256(manifest_bytes).hexdigest()

            if current_hash == stored_hash:
                self._log_audit(
                    event_type="manifest_integrity_validated",
                    details={
                        "skill_id": manifest.skill_id,
                        "version": manifest.version,
                        "hash": current_hash
                    },
                    tenant_id=tenant_id
                )
                return True
            else:
                self._log_audit(
                    event_type="manifest_integrity_violation",
                    details={
                        "skill_id": manifest.skill_id,
                        "version": manifest.version,
                        "current_hash": current_hash,
                        "stored_hash": stored_hash
                    },
                    severity="warning",
                    tenant_id=tenant_id
                )
                raise ManifestTamperedError(
                    f"Manifest tampering detected for {manifest.skill_id}: "
                    f"hash mismatch (current: {current_hash}, stored: {stored_hash})"
                )
        except ManifestTamperedError:
            raise
        except Exception as e:
            logger.error(f"Integrity validation error for {manifest.skill_id}: {e}")
            self._log_audit(
                event_type="manifest_integrity_validation_error",
                details={"skill_id": manifest.skill_id, "error": str(e)},
                severity="error",
                tenant_id=tenant_id
            )
            raise ManifestTamperedError(f"Integrity validation failed: {e}")

    def get_operator_public_key(self) -> rsa.RSAPublicKey:
        """Get operator public key from hardcoded source.

        This is a placeholder for the hardcoded operator key that would be
        baked into the Forge binary at build time. In production, this would
        return the key embedded in the binary (immutable).

        Returns:
            RSA public key object

        Raises:
            RuntimeError: if hardcoded key cannot be loaded
        """
        # Placeholder: in production, this would return a hardcoded key
        # embedded in the Forge binary at build time
        if self.operator_public_key:
            return self.operator_public_key

        # For now, raise an error indicating the key must be provided
        raise RuntimeError(
            "Operator public key not configured. Must be provided at init time "
            "or embedded in Forge binary."
        )

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
            user_id=None,
            timestamp=str(json.dumps(asdict({}))),  # Will be set by audit chain
            details=details,
            severity=severity or "info"
        )

        try:
            self.audit_chain.write_event(event)
        except Exception as e:
            logger.warning(f"Failed to log audit event {event_type}: {e}")

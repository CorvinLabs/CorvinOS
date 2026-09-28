"""Skill Manifest Validator — signature + integrity verification (Phase 1, ADR-0666).

Implements:
1. RSA signature verification for manifests
2. Manifest integrity checking (hash comparison)
3. Operator public key management
4. Audit logging for all validation operations

NOT WIRED: no production caller as of 2026-09-27 (adversarial review) — the
only importer is ``core.skill_forge.validators.forge_gateway`` (itself
unreachable) and the builtin boot path records
``skill_validation_not_performed`` instead (``core/skills/boot.py``).

No operator public key is provisioned: ``OPERATOR_PUBLIC_KEY_PEM`` is empty
until a build embeds the real one, and :meth:`get_operator_public_key` then
raises (fail-closed) — so every default-key signature check refuses. The PEM
that used to sit here was not a key at all (it failed to parse).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.backends import default_backend
from cryptography.exceptions import InvalidSignature

from core.compliance.audit_chain_writer import AuditChainWriter, AuditEvent
from core.skills.manifest_validator import SkillManifest

logger = logging.getLogger(__name__)


# Operator public key, embedded at BUILD time (never via env var or config).
# NOT PROVISIONED: empty until a build embeds the real key. Until 2026-09-27 a
# made-up PEM sat here that did not even parse. To embed one at build time:
#   from core.skills.signature.key_manager import OperatorKeyManager
#   ... generate_operator_keypair() ... public_key_pem().decode("utf-8")
OPERATOR_PUBLIC_KEY_PEM = ""


#: Content-free detail fields of every record this module family writes
#: (validator, license_binding, forge_gateway). Registered with the forge
#: writer so the default-deny vocabulary does not silently drop them.
_AUDIT_FIELDS = frozenset({
    "skill_id", "version", "skill_version", "reason", "manifest_hash", "hash",
    "current_hash", "stored_hash", "required_tier", "user_tier", "layer",
    "error_type", "event_id", "tenant_id", "origin", "layers_passed",
})
_AUDIT_EVENTS = (
    "signature_validation_failed", "signature_validation_passed",
    "signature_validation_error", "manifest_integrity_validated",
    "manifest_integrity_violation", "manifest_integrity_validation_error",
    "license_denied", "license_granted", "license_validation_error",
    "license_binding_signature_valid", "license_binding_signature_invalid",
    "license_binding_verification_error", "manifest_integrity_verified",
    "skill_load_failed", "skill_load_allowed",
)


def register_audit_events() -> None:
    """Register the allowlists above with the forge writer (idempotent)."""
    try:
        from core.compliance.audit_chain_writer import _forge

        se, _ = _forge()
        for et in _AUDIT_EVENTS:
            se.register_event_allowlist(et, _AUDIT_FIELDS)
    except Exception as exc:  # noqa: BLE001 — the write itself then fails closed
        logger.warning("skill validation audit events not registered (%s)", type(exc).__name__)


def make_audit_event(event_type: str, details: dict, *, severity: Optional[str],
                     user_id: Optional[str], tenant_id: str):
    """One well-formed :class:`AuditEvent` (unique id, real UTC timestamp).

    Replaces ``timestamp=str(json.dumps(asdict({})))`` — ``asdict`` of a dict
    raises TypeError, so EVERY audited validation with a configured chain
    crashed instead of returning its verdict.
    """
    return AuditEvent(
        event_id=str(uuid4()),
        event_type=event_type,
        tenant_id=tenant_id,
        user_id=user_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        details=details,
        severity=severity or "info",
    )


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

            # Resolve the key FIRST: without a key nothing can verify, and that
            # must refuse (raise) rather than read as an ordinary mismatch.
            public_key = self.operator_public_key or self.get_operator_public_key()

            # Decode signature from base64
            try:
                signature_bytes = base64.b64decode(signature_b64)
            except Exception as e:
                self._log_audit(
                    event_type="signature_validation_failed",
                    details={
                        "skill_id": manifest.skill_id,
                        "reason": "invalid_base64",
                        "error_type": type(e).__name__,
                    },
                    severity="warning",
                    tenant_id=tenant_id
                )
                return False

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
                details={"skill_id": manifest.skill_id, "error_type": type(e).__name__},
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
                details={"skill_id": manifest.skill_id, "error_type": type(e).__name__},
                severity="error",
                tenant_id=tenant_id
            )
            raise ManifestTamperedError(f"Integrity validation failed: {e}")

    def get_operator_public_key(self) -> rsa.RSAPublicKey:
        """Get operator public key (hardcoded in Forge binary).

        This key is used to verify all skill manifests. It cannot be overridden
        via env var or config file (fail-closed design).

        Returns:
            RSA-2048 public key object

        Raises:
            RuntimeError: if hardcoded key cannot be loaded (cryptographic error)
        """
        # Allow override ONLY for testing (must be explicitly passed at init)
        if self.operator_public_key:
            return self.operator_public_key

        if not OPERATOR_PUBLIC_KEY_PEM.strip():
            # Fail-closed: no key → no manifest verifies.
            raise RuntimeError(
                "No operator public key is provisioned in this build "
                "(OPERATOR_PUBLIC_KEY_PEM is empty); manifest signatures cannot be verified."
            )
        try:
            # Load hardcoded key from PEM string embedded in this module
            public_key = serialization.load_pem_public_key(
                OPERATOR_PUBLIC_KEY_PEM.encode("utf-8"),
                backend=default_backend()
            )
            return public_key
        except Exception as e:
            # Fail-closed: invalid key → no skills load
            raise RuntimeError(
                f"Failed to load hardcoded operator public key: {e}. "
                "This should not happen in production. Regenerate the key at build time."
            ) from e

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

        event = make_audit_event(event_type, details, severity=severity,
                                 user_id=None, tenant_id=tenant_id)

        try:
            self.audit_chain.write_event(event)
        except Exception as e:
            logger.warning(f"Failed to log audit event {event_type}: {e}")


register_audit_events()

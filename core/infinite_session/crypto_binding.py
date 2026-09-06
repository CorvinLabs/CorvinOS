"""Phase B: Cryptographic Binding for Session Bridging (ADR-0541).

Provides HMAC-SHA256 signature generation and verification for session snapshots.
Enables cryptographic proof of state continuity across session boundaries.
All signatures are fail-closed: any mismatch → reject snapshot + emit audit event.

Compliance:
- GDPR Art. 30/32: Audit continuity, tamper detection
- Immutability: Snapshots signed with external key; signature verification required before use
- Tenant isolation: Every signature operation scoped by tenant_id
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple
from dataclasses import dataclass
from enum import Enum


class KeyRotationStatus(str, Enum):
    """Key rotation status."""
    ACTIVE = "active"
    ROTATED = "rotated"
    SUSPENDED = "suspended"


@dataclass(frozen=True)
class SignatureMetadata:
    """Immutable metadata for a signature (for audit trail)."""
    signature: str
    algorithm: str  # "hmac-sha256"
    tenant_id: str
    timestamp: str
    key_rotation_id: str
    key_rotation_status: KeyRotationStatus


class CryptoBinding:
    """HMAC-SHA256 cryptographic binding for session snapshots (ADR-0541).

    Provides:
    - Signature generation (snapshot → HMAC-SHA256)
    - Signature verification (reject on mismatch)
    - Key rotation (with rotation audit trail)
    - Fail-closed design (any error → reject)
    """

    def __init__(self, corvin_home: str = None):
        """Initialize crypto binding.

        Args:
            corvin_home: Corvin home directory (defaults to ~/.corvin)
        """
        if corvin_home is None:
            corvin_home = os.path.expanduser("~/.corvin")
        self.corvin_home = Path(corvin_home)
        self.key_dir = self.corvin_home / "keys"
        self.key_dir.mkdir(parents=True, exist_ok=True)

    def _get_key_path(self, tenant_id: str) -> Path:
        """Get path to tenant's signing key (fail-closed on empty tenant_id).

        Args:
            tenant_id: Tenant identifier

        Returns:
            Path to signing key file

        Raises:
            ValueError: If tenant_id is empty
        """
        if not tenant_id or not tenant_id.strip():
            raise ValueError("tenant_id is required (fail-closed)")

        return self.key_dir / f"{tenant_id}.key"

    def _ensure_key_exists(self, tenant_id: str) -> Tuple[bool, str]:
        """Ensure a signing key exists for the tenant.

        Args:
            tenant_id: Tenant identifier

        Returns:
            (success, error_message)

        Notes:
            - If key doesn't exist, generates a new one
            - If key exists, returns existing key
        """
        try:
            if not tenant_id or not tenant_id.strip():
                return False, "tenant_id is required (fail-closed)"

            key_path = self._get_key_path(tenant_id)

            # If key already exists, we're done
            if key_path.exists():
                return True, ""

            # Generate new key (256-bit random bytes, base64-encoded for storage)
            import secrets
            key_material = secrets.token_hex(32)  # 32 bytes = 256 bits
            key_path.write_text(key_material)
            key_path.chmod(0o600)  # Restrict to owner read/write

            return True, ""

        except Exception as e:
            return False, f"Failed to ensure key exists: {str(e)}"

    def _read_key(self, tenant_id: str) -> Tuple[Optional[str], str]:
        """Read signing key for tenant (fail-closed on missing key).

        Args:
            tenant_id: Tenant identifier

        Returns:
            (key_material, error_message)
        """
        try:
            if not tenant_id or not tenant_id.strip():
                return None, "tenant_id is required (fail-closed)"

            key_path = self._get_key_path(tenant_id)

            if not key_path.exists():
                return None, f"Signing key not found for tenant {tenant_id} (fail-closed)"

            key_material = key_path.read_text().strip()
            if not key_material:
                return None, f"Signing key is empty for tenant {tenant_id} (fail-closed)"

            return key_material, ""

        except Exception as e:
            return None, f"Failed to read signing key: {str(e)}"

    def sign_snapshot(
        self,
        tenant_id: str,
        snapshot_hash: str,
        audit_callback: Optional[callable] = None,
    ) -> Tuple[Optional[str], str]:
        """Sign a snapshot hash with HMAC-SHA256 (audit-first).

        Args:
            tenant_id: Tenant identifier
            snapshot_hash: Snapshot hash to sign (SHA256 hex)
            audit_callback: Optional callback to emit audit event before signing

        Returns:
            (signature, error_message)

        Notes:
            - Ensures key exists (creates if missing)
            - Emits audit event BEFORE signing
            - Fail-closed: any error → returns (None, error_message)
        """
        try:
            if not tenant_id or not tenant_id.strip():
                return None, "tenant_id is required (fail-closed)"

            if not snapshot_hash or not snapshot_hash.strip():
                return None, "snapshot_hash is required (fail-closed)"

            # Emit audit event FIRST (audit-first design)
            if audit_callback:
                audit_emitted = audit_callback(
                    event_type="snapshot_signing_started",
                    tenant_id=tenant_id,
                    snapshot_hash=snapshot_hash,
                    timestamp=datetime.utcnow().isoformat() + "Z",
                )
                if not audit_emitted:
                    return None, "Audit event emission failed (fail-closed)"

            # Ensure key exists
            success, error = self._ensure_key_exists(tenant_id)
            if not success:
                return None, error

            # Read key
            key_material, error = self._read_key(tenant_id)
            if error:
                return None, error

            # Sign snapshot hash with HMAC-SHA256
            signature = hmac.new(
                key_material.encode(),
                snapshot_hash.encode(),
                hashlib.sha256,
            ).hexdigest()

            return signature, ""

        except Exception as e:
            return None, f"Failed to sign snapshot: {str(e)}"

    def verify_signature(
        self,
        tenant_id: str,
        snapshot_hash: str,
        signature: str,
        audit_callback: Optional[callable] = None,
    ) -> Tuple[bool, str]:
        """Verify a snapshot signature (fail-closed).

        Args:
            tenant_id: Tenant identifier
            snapshot_hash: Snapshot hash (SHA256 hex)
            signature: Signature to verify (hex string)
            audit_callback: Optional callback to emit audit event

        Returns:
            (is_valid, error_message)

        Notes:
            - Fail-closed: any error → returns (False, error_message)
            - Mismatch → emits audit event + returns (False, "signature mismatch")
        """
        try:
            if not tenant_id or not tenant_id.strip():
                if audit_callback:
                    audit_callback(
                        event_type="signature_verification_failed",
                        tenant_id="<invalid>",
                        reason="tenant_id is empty",
                        timestamp=datetime.utcnow().isoformat() + "Z",
                    )
                return False, "tenant_id is required (fail-closed)"

            if not snapshot_hash or not snapshot_hash.strip():
                if audit_callback:
                    audit_callback(
                        event_type="signature_verification_failed",
                        tenant_id=tenant_id,
                        reason="snapshot_hash is empty",
                        timestamp=datetime.utcnow().isoformat() + "Z",
                    )
                return False, "snapshot_hash is required (fail-closed)"

            if not signature or not signature.strip():
                if audit_callback:
                    audit_callback(
                        event_type="signature_verification_failed",
                        tenant_id=tenant_id,
                        reason="signature is empty",
                        timestamp=datetime.utcnow().isoformat() + "Z",
                    )
                return False, "signature is required (fail-closed)"

            # Read key
            key_material, error = self._read_key(tenant_id)
            if error:
                if audit_callback:
                    audit_callback(
                        event_type="signature_verification_failed",
                        tenant_id=tenant_id,
                        reason=f"Key read failed: {error}",
                        timestamp=datetime.utcnow().isoformat() + "Z",
                    )
                return False, error

            # Compute expected signature
            expected_signature = hmac.new(
                key_material.encode(),
                snapshot_hash.encode(),
                hashlib.sha256,
            ).hexdigest()

            # Compare (constant-time comparison to prevent timing attacks)
            is_valid = hmac.compare_digest(signature, expected_signature)

            if not is_valid:
                if audit_callback:
                    audit_callback(
                        event_type="signature_verification_failed",
                        tenant_id=tenant_id,
                        reason="signature mismatch",
                        timestamp=datetime.utcnow().isoformat() + "Z",
                    )
                return False, "Signature mismatch (fail-closed)"

            return True, ""

        except Exception as e:
            if audit_callback:
                audit_callback(
                    event_type="signature_verification_failed",
                    tenant_id=tenant_id,
                    reason=f"Verification error: {str(e)}",
                    timestamp=datetime.utcnow().isoformat() + "Z",
                )
            return False, f"Failed to verify signature: {str(e)}"

    def rotate_key(
        self,
        tenant_id: str,
        audit_callback: Optional[callable] = None,
    ) -> Tuple[bool, str]:
        """Rotate signing key for tenant (with audit trail).

        Args:
            tenant_id: Tenant identifier
            audit_callback: Optional callback to emit audit event

        Returns:
            (success, error_message)

        Notes:
            - Generates new key
            - Archives old key with rotation timestamp
            - Emits audit event for rotation
        """
        try:
            if not tenant_id or not tenant_id.strip():
                return False, "tenant_id is required (fail-closed)"

            key_path = self._get_key_path(tenant_id)

            # Read old key (if exists)
            old_key = None
            if key_path.exists():
                old_key = key_path.read_text()

            # Archive old key (if exists)
            if old_key:
                archive_dir = self.key_dir / "archive"
                archive_dir.mkdir(parents=True, exist_ok=True)
                timestamp = datetime.utcnow().isoformat().replace(":", "-")
                archive_path = archive_dir / f"{tenant_id}_{timestamp}.key.old"
                archive_path.write_text(old_key)
                archive_path.chmod(0o600)

            # Generate new key
            import secrets
            new_key_material = secrets.token_hex(32)
            key_path.write_text(new_key_material)
            key_path.chmod(0o600)

            # Emit audit event
            if audit_callback:
                audit_callback(
                    event_type="key_rotated",
                    tenant_id=tenant_id,
                    rotation_timestamp=datetime.utcnow().isoformat() + "Z",
                    key_rotation_status=KeyRotationStatus.ACTIVE.value,
                )

            return True, ""

        except Exception as e:
            return False, f"Failed to rotate key: {str(e)}"

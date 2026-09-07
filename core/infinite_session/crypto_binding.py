"""Phase B: Cryptographic Binding for Session Bridging (ADR-0541).

HMAC-SHA256 signatures over CANONICAL JSON payloads, keyed per tenant.

Key material lives at ``<tenant_root>/keys/signing.key`` (mode 0600) where
``tenant_root`` is ``<corvin_home>/tenants/<tenant_id>/infinite_session`` —
``tenant_id`` is validated before it is ever joined into a path and
``corvin_home`` honours ``CORVIN_HOME``.

Fail-closed: any error → ``(None, reason)`` / ``(False, reason)``; a signature
mismatch is reported through the audit callback as
``signature_verification_failed``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Tuple

from core.infinite_session.paths import tenant_root
from core.tenants import validate_tenant_id

ALGORITHM = "hmac-sha256"


def canonical_json(payload: dict[str, Any]) -> bytes:
    """Deterministic JSON bytes (sorted keys, no whitespace, ASCII-safe)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


class KeyRotationStatus(str, Enum):
    ACTIVE = "active"
    ROTATED = "rotated"
    SUSPENDED = "suspended"


@dataclass(frozen=True)
class SignatureMetadata:
    """Immutable metadata for a signature (for audit trail)."""
    signature: str
    algorithm: str
    tenant_id: str
    timestamp: str
    key_rotation_id: str
    key_rotation_status: KeyRotationStatus


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CryptoBinding:
    """HMAC-SHA256 binding, one key per tenant (ADR-0541)."""

    def __init__(self, corvin_home: Optional[str | Path] = None):
        self.corvin_home = Path(corvin_home).expanduser() if corvin_home else None

    # ── key management ───────────────────────────────────────────────────

    def _key_dir(self, tenant_id: str) -> Path:
        validate_tenant_id(tenant_id)
        return tenant_root(tenant_id, self.corvin_home) / "keys"

    def _get_key_path(self, tenant_id: str) -> Path:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id is required (fail-closed)")
        return self._key_dir(tenant_id) / "signing.key"

    def _ensure_key_exists(self, tenant_id: str) -> Tuple[bool, str]:
        try:
            key_path = self._get_key_path(tenant_id)
            if key_path.exists():
                return True, ""
            key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(secrets.token_hex(32))  # 256-bit key
            return True, ""
        except FileExistsError:
            return True, ""
        except (OSError, ValueError) as exc:
            return False, f"Failed to ensure key exists: {exc}"

    def _read_key(self, tenant_id: str) -> Tuple[Optional[bytes], str]:
        try:
            key_path = self._get_key_path(tenant_id)
        except ValueError as exc:
            return None, str(exc)
        if not key_path.exists():
            return None, f"Signing key not found for tenant {tenant_id} (fail-closed)"
        try:
            material = key_path.read_text().strip()
        except OSError as exc:
            return None, f"Failed to read signing key: {exc}"
        if not material:
            return None, f"Signing key is empty for tenant {tenant_id} (fail-closed)"
        return material.encode(), ""

    def hmac_bytes(self, tenant_id: str, message: bytes) -> Tuple[Optional[str], str]:
        """HMAC-SHA256 hex digest of ``message`` under the tenant key (created on first use)."""
        ok, error = self._ensure_key_exists(tenant_id)
        if not ok:
            return None, error
        key, error = self._read_key(tenant_id)
        if error:
            return None, error
        return hmac.new(key, message, hashlib.sha256).hexdigest(), ""

    def verify_bytes(self, tenant_id: str, message: bytes, signature: str) -> Tuple[bool, str]:
        """Constant-time verification; the key must already exist (no auto-create)."""
        if not isinstance(signature, str) or not signature.strip():
            return False, "signature is required (fail-closed)"
        key, error = self._read_key(tenant_id)
        if error:
            return False, error
        expected = hmac.new(key, message, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return False, "Signature mismatch (fail-closed)"
        return True, ""

    # ── payload signing (canonical JSON) ─────────────────────────────────

    def sign_payload(
        self,
        tenant_id: str,
        payload: dict[str, Any],
        audit_callback: Optional[callable] = None,
    ) -> Tuple[Optional[str], str]:
        """Sign the canonical JSON of ``payload`` (audit-first)."""
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            return None, "tenant_id is required (fail-closed)"
        if not isinstance(payload, dict) or not payload:
            return None, "payload is required (fail-closed)"
        if audit_callback:
            emitted = audit_callback(
                event_type="snapshot_signing_started",
                tenant_id=tenant_id,
                payload_sha256=hashlib.sha256(canonical_json(payload)).hexdigest(),
                timestamp=_now(),
            )
            if not emitted:
                return None, "Audit event emission failed (fail-closed)"
        return self.hmac_bytes(tenant_id, canonical_json(payload))

    def verify_payload(
        self,
        tenant_id: str,
        payload: dict[str, Any],
        signature: str,
        audit_callback: Optional[callable] = None,
    ) -> Tuple[bool, str]:
        """Verify ``signature`` over the canonical JSON of ``payload`` (fail-closed)."""
        def _fail(reason: str) -> Tuple[bool, str]:
            if audit_callback:
                audit_callback(
                    event_type="signature_verification_failed",
                    tenant_id=tenant_id if isinstance(tenant_id, str) and tenant_id else "<invalid>",
                    reason=reason,
                    timestamp=_now(),
                )
            return False, reason

        if not isinstance(tenant_id, str) or not tenant_id.strip():
            return _fail("tenant_id is required (fail-closed)")
        if not isinstance(payload, dict) or not payload:
            return _fail("payload is required (fail-closed)")
        ok, error = self.verify_bytes(tenant_id, canonical_json(payload), signature)
        if not ok:
            return _fail(error)
        return True, ""

    # ── string-hash convenience (kept for callers that sign a bare hash) ──

    def sign_snapshot(
        self,
        tenant_id: str,
        snapshot_hash: str,
        audit_callback: Optional[callable] = None,
    ) -> Tuple[Optional[str], str]:
        if not isinstance(snapshot_hash, str) or not snapshot_hash.strip():
            return None, "snapshot_hash is required (fail-closed)"
        return self.sign_payload(tenant_id, {"snapshot_hash": snapshot_hash}, audit_callback)

    def verify_signature(
        self,
        tenant_id: str,
        snapshot_hash: str,
        signature: str,
        audit_callback: Optional[callable] = None,
    ) -> Tuple[bool, str]:
        if not isinstance(snapshot_hash, str) or not snapshot_hash.strip():
            if audit_callback:
                audit_callback(
                    event_type="signature_verification_failed",
                    tenant_id=tenant_id or "<invalid>",
                    reason="snapshot_hash is empty",
                    timestamp=_now(),
                )
            return False, "snapshot_hash is required (fail-closed)"
        return self.verify_payload(
            tenant_id, {"snapshot_hash": snapshot_hash}, signature, audit_callback
        )

    # ── rotation ─────────────────────────────────────────────────────────

    def rotate_key(
        self,
        tenant_id: str,
        audit_callback: Optional[callable] = None,
    ) -> Tuple[bool, str]:
        """Generate a new key; the old one is archived (0600) with a timestamp."""
        try:
            key_path = self._get_key_path(tenant_id)
        except ValueError as exc:
            return False, str(exc)
        try:
            if key_path.exists():
                archive_dir = key_path.parent / "archive"
                archive_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                stamp = _now().replace(":", "-")
                archive_path = archive_dir / f"signing_{stamp}.key.old"
                archive_path.write_text(key_path.read_text())
                archive_path.chmod(0o600)
                key_path.unlink()
            ok, error = self._ensure_key_exists(tenant_id)
            if not ok:
                return False, error
        except OSError as exc:
            return False, f"Failed to rotate key: {exc}"
        if audit_callback:
            audit_callback(
                event_type="key_rotated",
                tenant_id=tenant_id,
                rotation_timestamp=_now(),
                key_rotation_status=KeyRotationStatus.ACTIVE.value,
            )
        return True, ""

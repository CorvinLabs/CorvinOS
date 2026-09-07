"""Loop Hijacking Mitigation: Cryptographic Signature Validation for Feedback (ADR-0640).

HMAC-SHA256 signatures over CANONICAL JSON payloads, keyed per tenant.
Prevents attackers from manipulating α weights via unvalidated feedback.

Compliance: GDPR Art. 5 (integrity), Art. 32 (security)

Key material lives at ``<tenant_root>/keys/feedback_signing.key`` (mode 0600) where
``tenant_root`` is ``<corvin_home>/tenants/<tenant_id>/``.

Fail-closed: any error → reject; a signature mismatch is reported through the audit
callback as ``feedback_signature_verification_failed``.

Integration with feedback_sink.py:
  on_feedback(feedback) must call validator.validate_feedback_signature() FIRST
  before processing. Reject if signature invalid (audit log + raise).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from core.paths.tenant import corvin_home
from core.tenants import validate_tenant_id


def canonical_json(payload: dict[str, Any]) -> bytes:
    """Deterministic JSON bytes (sorted keys, no whitespace, ASCII-safe).

    Ensures that the same payload always produces the same canonical form,
    preventing signature bypass via JSON formatting tricks.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


@dataclass(frozen=True)
class FeedbackSignature:
    """Immutable feedback signature metadata (for audit trail)."""
    signature: str                    # HMAC-SHA256 hex digest
    algorithm: str                    # "hmac-sha256"
    tenant_id: str                    # Tenant scope
    timestamp: str                    # ISO 8601 UTC when signed
    payload_hash: str                 # SHA256 of canonical JSON (for audit)


class FeedbackSignatureValidator:
    """Validates cryptographic signatures on feedback to prevent hijacking attacks.

    Implements HMAC-SHA256 signing over canonical JSON payloads, one key per tenant.

    Threat Model:
    - Attacker manipulates α weights by crafting fake feedback with high confidence
    - Attacker intercepts and modifies feedback in transit
    - Attacker replays old feedback with altered timestamps

    Mitigations:
    1. HMAC-SHA256 signature on all feedback fields
    2. Tenant-scoped keys prevent cross-tenant attacks
    3. Canonical JSON encoding prevents format-based bypasses
    4. Fail-closed: any signature error = reject
    5. Audit trail logs all validation attempts
    """

    def __init__(self, corvin_home_override: Optional[str | Path] = None):
        """Initialize validator (optionally with custom corvin_home for testing).

        Args:
            corvin_home_override: Optional override for corvin_home (for testing)
        """
        self.corvin_home = Path(corvin_home_override) if corvin_home_override else corvin_home()

    # ── key management ───────────────────────────────────────────────────

    def _key_dir(self, tenant_id: str) -> Path:
        """Get keys directory for a tenant (fail-closed validation)."""
        validate_tenant_id(tenant_id)
        return self.corvin_home / "tenants" / tenant_id / "keys"

    def _get_key_path(self, tenant_id: str) -> Path:
        """Get path to tenant's feedback signing key."""
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id is required (fail-closed)")
        return self._key_dir(tenant_id) / "feedback_signing.key"

    def _ensure_key_exists(self, tenant_id: str) -> Tuple[bool, str]:
        """Ensure tenant has a signing key (create on first use).

        Returns:
            (success: bool, error_message: str)
        """
        try:
            key_path = self._get_key_path(tenant_id)
            if key_path.exists():
                return True, ""
            # Create key directory with restricted permissions
            key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Create key file with 256-bit random material (restricted permissions)
            fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(secrets.token_hex(32))  # 256-bit = 64 hex chars
            return True, ""
        except FileExistsError:
            return True, ""  # Key already exists (race condition OK)
        except (OSError, ValueError) as exc:
            return False, f"Failed to ensure key exists: {exc}"

    def _read_key(self, tenant_id: str) -> Tuple[Optional[bytes], str]:
        """Read tenant's signing key (fail-closed if missing).

        Returns:
            (key_bytes, error_message) — one is None
        """
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

    # ── feedback signing ─────────────────────────────────────────────────

    def sign_feedback(
        self,
        tenant_id: str,
        feedback_dict: Dict[str, Any],
        audit_callback: Optional[callable] = None,
    ) -> Tuple[Optional[str], str]:
        """Sign a feedback dictionary (HMAC-SHA256 of canonical JSON).

        Args:
            tenant_id: Tenant ID (validated)
            feedback_dict: Feedback fields to sign (as dict)
            audit_callback: Optional function to emit audit events

        Returns:
            (signature_hex: str, error_message: str) — one is None
        """
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            return None, "tenant_id is required (fail-closed)"

        if not isinstance(feedback_dict, dict):
            return None, "feedback_dict must be a dict (fail-closed)"

        # Emit audit event
        if audit_callback:
            payload_bytes = canonical_json(feedback_dict)
            audit_callback(
                event_type="feedback_signing_started",
                tenant_id=tenant_id,
                payload_sha256=hashlib.sha256(payload_bytes).hexdigest(),
            )

        # Ensure key exists
        ok, error = self._ensure_key_exists(tenant_id)
        if not ok:
            return None, error

        # Read key and sign
        key, error = self._read_key(tenant_id)
        if error:
            return None, error

        payload_bytes = canonical_json(feedback_dict)
        signature = hmac.new(key, payload_bytes, hashlib.sha256).hexdigest()

        return signature, ""

    # ── feedback verification ────────────────────────────────────────────

    def validate_feedback_signature(
        self,
        tenant_id: str,
        feedback_dict: Dict[str, Any],
        signature: str,
        audit_callback: Optional[callable] = None,
    ) -> Tuple[bool, str]:
        """Validate a feedback signature (constant-time verification).

        Fail-closed: any error or mismatch → (False, error_reason)

        Args:
            tenant_id: Tenant ID
            feedback_dict: Feedback fields (must be identical to what was signed)
            signature: HMAC-SHA256 hex digest to verify
            audit_callback: Optional audit event emitter

        Returns:
            (is_valid: bool, error_message: str)
        """
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            if audit_callback:
                audit_callback(
                    event_type="feedback_signature_verification_failed",
                    reason="tenant_id is required",
                    tenant_id="unknown",
                )
            return False, "tenant_id is required (fail-closed)"

        if not isinstance(signature, str) or not signature.strip():
            if audit_callback:
                audit_callback(
                    event_type="feedback_signature_verification_failed",
                    reason="signature is required",
                    tenant_id=tenant_id,
                )
            return False, "signature is required (fail-closed)"

        if not isinstance(feedback_dict, dict):
            if audit_callback:
                audit_callback(
                    event_type="feedback_signature_verification_failed",
                    reason="feedback_dict must be a dict",
                    tenant_id=tenant_id,
                )
            return False, "feedback_dict must be a dict (fail-closed)"

        # Read key (must already exist — don't auto-create on verify)
        key, error = self._read_key(tenant_id)
        if error:
            if audit_callback:
                audit_callback(
                    event_type="feedback_signature_verification_failed",
                    reason=error,
                    tenant_id=tenant_id,
                )
            return False, error

        # Compute canonical JSON and expected signature
        payload_bytes = canonical_json(feedback_dict)
        expected = hmac.new(key, payload_bytes, hashlib.sha256).hexdigest()

        # Constant-time comparison (prevent timing attacks)
        is_valid = hmac.compare_digest(signature, expected)

        if not is_valid:
            if audit_callback:
                audit_callback(
                    event_type="feedback_signature_verification_failed",
                    reason="Signature mismatch",
                    tenant_id=tenant_id,
                    provided_signature=signature[:16] + "...",  # Partial, for audit only
                    payload_sha256=hashlib.sha256(payload_bytes).hexdigest(),
                )

        return is_valid, "" if is_valid else "Signature mismatch (fail-closed)"

    # ── batch verification (for testing/bulk operations) ──────────────────

    def validate_feedback_batch(
        self,
        tenant_id: str,
        feedback_items: list[tuple[Dict[str, Any], str]],
        audit_callback: Optional[callable] = None,
    ) -> Tuple[int, list[str]]:
        """Validate multiple feedback items (returns count of valid + error list).

        Args:
            tenant_id: Tenant ID
            feedback_items: List of (feedback_dict, signature) tuples
            audit_callback: Optional audit event emitter

        Returns:
            (valid_count: int, errors: list[str])
        """
        valid_count = 0
        errors = []

        for idx, (feedback_dict, signature) in enumerate(feedback_items):
            is_valid, error = self.validate_feedback_signature(
                tenant_id, feedback_dict, signature, audit_callback
            )
            if is_valid:
                valid_count += 1
            else:
                errors.append(f"Item {idx}: {error}")

        return valid_count, errors


__all__ = [
    "FeedbackSignatureValidator",
    "FeedbackSignature",
    "canonical_json",
]

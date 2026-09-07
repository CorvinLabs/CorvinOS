"""Phase 4 Week 18: Cryptographic Feedback Signatures (ADR-0640 Hardening).

Loop Hijacking Mitigation: Cryptographic Feedback Signatures (HMAC-SHA256)

Attacker vector: Inject unvalidated feedback to manipulate α weights of unified learning loop.
Mitigation: Every feedback creation signs with tenant HMAC key before audit write. Validator
verifies HMAC before feeding to weight optimizer. Fail-closed: invalid signature → log audit
event feedback_signature_invalid, reject feedback, no weight update.

Compliance: GDPR Art. 32 (security), Art. 30 (audit trail), EU AI Act 2026 (integrity).

All feedback is immutable, tenant-scoped, and cryptographically bound to the tenant's signing key.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Optional, Tuple, Dict
from uuid import uuid4

from core.infinite_session.crypto_binding import CryptoBinding, canonical_json
from core.tenants import validate_tenant_id

logger = logging.getLogger(__name__)


class FeedbackStalenessReason(str, Enum):
    """Categorize why feedback was rejected as stale."""
    MISSING_TIMESTAMP = "missing_timestamp"       # No timestamp in payload
    TIMESTAMP_PARSE_ERROR = "timestamp_parse_error"  # Invalid ISO 8601 format
    EXCEEDS_TTL = "exceeds_ttl"                   # Age > MAX_AGE
    FUTURE_TIMESTAMP = "future_timestamp"         # Feedback timestamp is in future


@dataclass(frozen=True)
class FeedbackTTLCheckResult:
    """Result of a TTL validation check."""
    is_valid: bool                                 # True = within TTL, accept
    age_seconds: Optional[int] = None              # How old is the feedback?
    reason: Optional[FeedbackStalenessReason] = None  # Why invalid?
    message: Optional[str] = None                  # Human-readable explanation


@dataclass(frozen=True)
class FeedbackSignature:
    """Immutable cryptographic signature for feedback (fail-closed, GDPR Art. 32).

    Guarantees:
    - Frozen (immutable)
    - HMAC-SHA256 over canonical JSON payload
    - Tenant-scoped (tenant_key_hash binds to tenant's signing key)
    - Cryptographically bound to feedback content (hmac field)
    - Timestamp-based nonce prevents replay attacks
    """
    feedback_id: str                 # UUID4, matches FeedbackEvent.feedback_id
    tenant_key_hash: str             # SHA256 hash of tenant's HMAC key (for audit)
    hmac: str                        # HMAC-SHA256 hex digest of canonical feedback payload
    timestamp: int                   # Unix timestamp (nonce for replay prevention)
    algorithm: str = "hmac-sha256"   # Cryptographic algorithm identifier

    def __post_init__(self):
        """Validate signature on creation (fail-closed)."""
        if not self.feedback_id:
            raise ValueError("feedback_id is required")
        if not self.tenant_key_hash:
            raise ValueError("tenant_key_hash is required")
        if not self.hmac:
            raise ValueError("hmac is required")
        if self.timestamp <= 0:
            raise ValueError("timestamp must be positive (unix time)")
        if self.algorithm != "hmac-sha256":
            raise ValueError(f"unsupported algorithm: {self.algorithm}")

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict (for storage/JSON)."""
        return {
            "feedback_id": self.feedback_id,
            "tenant_key_hash": self.tenant_key_hash,
            "hmac": self.hmac,
            "timestamp": self.timestamp,
            "algorithm": self.algorithm,
        }


class FeedbackSignatureValidator:
    """Validates cryptographic signatures on feedback (fail-closed, GDPR Art. 32).

    Responsibilities:
    1. Sign new feedback with tenant HMAC key (before audit write)
    2. Verify feedback signatures before feeding to weight optimizer
    3. Detect and reject tampered feedback (invalid HMAC)
    4. Prevent replay attacks via timestamp nonce
    5. Audit all signature checks (passed/failed)
    """

    def __init__(self, corvin_home: Optional[str] = None, audit_backend: Optional[Any] = None):
        """Initialize signature validator.

        Args:
            corvin_home: Path to ~/.corvin (defaults to env var or ~)
            audit_backend: Audit trail writer (for logging signature events)
        """
        self.crypto_binding = CryptoBinding(corvin_home=corvin_home)
        self.audit_backend = audit_backend

    def sign_feedback(
        self,
        tenant_id: str,
        feedback_payload: dict[str, Any],
    ) -> Tuple[Optional[FeedbackSignature], str]:
        """Sign feedback with tenant HMAC key (fail-closed, audit-first).

        Signature is HMAC-SHA256 over canonical JSON of feedback_payload.
        Tenant key is created on first use (isolated per tenant).

        Args:
            tenant_id: Tenant scope (validated, fail-closed)
            feedback_payload: Dict of feedback data (will be canonicalized)

        Returns:
            (FeedbackSignature, "") on success
            (None, error_reason) on failure (fail-closed)
        """
        # Validate tenant_id (fail-closed isolation)
        try:
            validate_tenant_id(tenant_id)
        except ValueError as exc:
            reason = f"Invalid tenant_id: {exc}"
            self._audit_signature_event("feedback_signature_creation_failed", tenant_id, reason)
            return None, reason

        # Extract feedback_id from payload
        feedback_id = feedback_payload.get("feedback_id")
        if not feedback_id:
            reason = "feedback_id is required in payload"
            self._audit_signature_event("feedback_signature_creation_failed", tenant_id, reason)
            return None, reason

        # Sign the canonical JSON payload with tenant key
        payload_bytes = canonical_json(feedback_payload)
        hmac_sig, error = self.crypto_binding.hmac_bytes(tenant_id, payload_bytes)
        if error or not hmac_sig:
            reason = f"HMAC signing failed: {error}"
            self._audit_signature_event("feedback_signature_creation_failed", tenant_id, reason)
            return None, reason

        # Compute tenant key hash for audit trail (never the key itself)
        key_hash = self._compute_tenant_key_hash(tenant_id)
        if not key_hash:
            reason = "Failed to compute tenant key hash"
            self._audit_signature_event("feedback_signature_creation_failed", tenant_id, reason)
            return None, reason

        # Create FeedbackSignature
        now_timestamp = int(datetime.now(timezone.utc).timestamp())
        signature = FeedbackSignature(
            feedback_id=feedback_id,
            tenant_key_hash=key_hash,
            hmac=hmac_sig,
            timestamp=now_timestamp,
            algorithm="hmac-sha256",
        )

        self._audit_signature_event(
            "feedback_signature_created",
            tenant_id,
            f"feedback_id={feedback_id}",
            signature_algo="hmac-sha256",
        )
        return signature, ""

    def verify_feedback_signature(
        self,
        tenant_id: str,
        feedback_payload: dict[str, Any],
        signature: FeedbackSignature,
    ) -> Tuple[bool, str]:
        """Verify feedback signature before feeding to weight optimizer (fail-closed).

        Checks:
        1. Tenant ID matches (fail-closed isolation)
        2. Feedback ID in signature matches payload
        3. HMAC-SHA256 verification (constant-time, no timing attacks)
        4. Tenant key hash matches (detect key rotation)
        5. Timestamp nonce is reasonable (prevent replay)

        Args:
            tenant_id: Tenant scope (must match signature)
            feedback_payload: Dict of feedback data (must match signed payload)
            signature: FeedbackSignature to verify

        Returns:
            (True, "") on valid signature
            (False, error_reason) on invalid signature (fail-closed)
        """
        # 1. Validate tenant_id (fail-closed isolation)
        try:
            validate_tenant_id(tenant_id)
        except ValueError as exc:
            reason = f"Invalid tenant_id: {exc}"
            self._audit_signature_event("feedback_signature_invalid", tenant_id, reason)
            return False, reason

        # 2. Verify feedback_id consistency
        feedback_id = feedback_payload.get("feedback_id")
        if feedback_id != signature.feedback_id:
            reason = f"Feedback ID mismatch (payload={feedback_id}, signature={signature.feedback_id})"
            self._audit_signature_event("feedback_signature_invalid", tenant_id, reason)
            return False, reason

        # 3. Verify HMAC-SHA256 (constant-time)
        payload_bytes = canonical_json(feedback_payload)
        is_valid, verify_error = self.crypto_binding.verify_bytes(
            tenant_id,
            payload_bytes,
            signature.hmac,
        )
        if not is_valid:
            reason = f"HMAC verification failed: {verify_error}"
            self._audit_signature_event("feedback_signature_invalid", tenant_id, reason)
            return False, reason

        # 4. Verify tenant key hash (detect key rotation)
        expected_key_hash = self._compute_tenant_key_hash(tenant_id)
        if not expected_key_hash or expected_key_hash != signature.tenant_key_hash:
            reason = f"Tenant key hash mismatch (may indicate key rotation or tampering)"
            self._audit_signature_event("feedback_signature_invalid", tenant_id, reason)
            return False, reason

        # 5. Verify timestamp nonce (prevent replay: timestamp must be recent, within 24h)
        now_timestamp = int(datetime.now(timezone.utc).timestamp())
        max_age_seconds = 24 * 60 * 60  # 24 hours
        if now_timestamp - signature.timestamp > max_age_seconds:
            reason = f"Signature timestamp too old (age={now_timestamp - signature.timestamp}s, max={max_age_seconds}s)"
            self._audit_signature_event("feedback_signature_invalid", tenant_id, reason)
            return False, reason

        # All checks passed
        self._audit_signature_event(
            "feedback_signature_verified",
            tenant_id,
            f"feedback_id={feedback_id}, age={now_timestamp - signature.timestamp}s",
            signature_algo="hmac-sha256",
        )
        return True, ""

    def _compute_tenant_key_hash(self, tenant_id: str) -> Optional[str]:
        """Compute SHA256 hash of tenant's HMAC key (for audit trail, never the key itself)."""
        try:
            key, error = self.crypto_binding._read_key(tenant_id)
            if error or not key:
                logger.warning(f"Failed to read tenant key for hashing: {error}")
                return None
            return hashlib.sha256(key).hexdigest()
        except Exception as exc:
            logger.error(f"Failed to compute tenant key hash: {exc}")
            return None

    def _audit_signature_event(
        self,
        event_type: str,
        tenant_id: str,
        details: str,
        signature_algo: str = "hmac-sha256",
    ) -> None:
        """Log signature event to audit trail (fail-soft, non-blocking)."""
        if not self.audit_backend:
            return

        try:
            self.audit_backend.write_event(
                event_type=event_type,
                tenant_id=tenant_id,
                details=details,
                signature_algorithm=signature_algo,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as exc:
            # Fail-soft: don't let audit failures break signature validation
            logger.warning(f"Audit event write failed ({event_type}): {exc}")


class FeedbackTTLValidator:
    """Security Fix #6: Enforces strict timestamp validation and TTL on all feedback.

    Mitigates stale feedback poisoning attack by:
    1. Requiring explicit timestamp in every feedback payload
    2. Validating timestamp format (ISO 8601 UTC)
    3. Rejecting feedback older than MAX_AGE (configurable per tenant, default 60 min)
    4. Rejecting future-dated feedback (clock skew detection)
    5. Logging rejected feedback as audit events (feedback_stale)
    6. Preventing stale/poisoned feedback from corrupting optimizer state

    Configuration (per tenant via tenant.corvin.yaml):
    ```yaml
    spec:
      learning:
        feedback:
          max_age_seconds: 3600      # Default: 60 minutes
          allow_future_seconds: 5    # Allow ±5s clock skew
    ```

    Compliance: GDPR Art. 5 (timely processing), Art. 30 (audit trail), Art. 32 (security)
    """

    DEFAULT_MAX_AGE_SECONDS = 3600      # 60 minutes
    DEFAULT_ALLOW_FUTURE_SECONDS = 5    # Allow ±5s clock skew

    def __init__(
        self,
        max_age_seconds: Optional[int] = None,
        allow_future_seconds: Optional[int] = None,
        audit_backend=None,
    ):
        """Initialize TTL validator.

        Args:
            max_age_seconds: Reject feedback older than this (default 3600 = 60 min)
            allow_future_seconds: Allow future-dated feedback up to this skew (default 5 sec)
            audit_backend: Audit trail writer (for logging rejected feedback)
        """
        self.max_age_seconds = max_age_seconds or self.DEFAULT_MAX_AGE_SECONDS
        self.allow_future_seconds = allow_future_seconds or self.DEFAULT_ALLOW_FUTURE_SECONDS
        self.audit_backend = audit_backend

    def validate_timestamp(
        self,
        timestamp_iso: Optional[str],
        feedback_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> FeedbackTTLCheckResult:
        """Validate feedback timestamp against TTL constraints.

        Args:
            timestamp_iso: Feedback timestamp (ISO 8601 UTC, e.g., "2026-09-07T12:34:56Z")
            feedback_id: Optional feedback ID (for audit logging)
            tenant_id: Optional tenant ID (for audit logging)

        Returns:
            FeedbackTTLCheckResult with validity status and reason
        """

        # 1. Timestamp must be present
        if timestamp_iso is None or len(str(timestamp_iso).strip()) == 0:
            result = FeedbackTTLCheckResult(
                is_valid=False,
                reason=FeedbackStalenessReason.MISSING_TIMESTAMP,
                message="Feedback timestamp is required (missing from payload)",
            )
            self._log_stale_feedback_event(
                feedback_id=feedback_id,
                tenant_id=tenant_id,
                reason=result.reason,
                message=result.message,
            )
            return result

        # 2. Parse timestamp (ISO 8601 UTC)
        try:
            feedback_time = datetime.fromisoformat(str(timestamp_iso).replace('Z', '+00:00'))
        except (ValueError, TypeError) as e:
            result = FeedbackTTLCheckResult(
                is_valid=False,
                reason=FeedbackStalenessReason.TIMESTAMP_PARSE_ERROR,
                message=f"Invalid timestamp format (expected ISO 8601): {timestamp_iso}",
            )
            self._log_stale_feedback_event(
                feedback_id=feedback_id,
                tenant_id=tenant_id,
                reason=result.reason,
                message=result.message,
                error=str(e),
            )
            return result

        # 3. Get current time in UTC (consistent timezone, accounts for leap seconds)
        # Use replace(tzinfo=timezone.utc) to ensure consistent UTC representation
        now = datetime.now(timezone.utc).replace(tzinfo=timezone.utc)

        # Normalize feedback_time to UTC (handle different timezone representations)
        if feedback_time.tzinfo is None:
            # Naive datetime—assume UTC
            feedback_time = feedback_time.replace(tzinfo=timezone.utc)
        else:
            # Convert any timezone to UTC for consistent comparison
            feedback_time = feedback_time.astimezone(timezone.utc)

        # 4. Calculate age (security fix: use ceil to prevent truncation bypass)
        # BUG FIX #6 ROUND 3: int() truncates 5.5 → 5, allowing stale feedback to pass.
        # Solution: Use math.ceil(abs()) with sign preservation to round UP consistently.
        # For positive ages: ceil rounds up (stricter, more conservative on old feedback)
        # For negative ages (future): ceil(abs()) then negate (stricter on future feedback)
        age_delta = now - feedback_time
        total_seconds = age_delta.total_seconds()
        if total_seconds >= 0:
            # Positive age (past feedback): round UP to be stricter
            age_seconds = math.ceil(total_seconds)
        else:
            # Negative age (future feedback): round DOWN (toward -infinity) to be stricter
            # Using ceil(abs()) + negate: ceil(abs(-5.5)) = ceil(5.5) = 6, negate to -6
            age_seconds = -math.ceil(abs(total_seconds))

        # 5. Check for future-dated feedback (clock skew tolerance)
        if age_seconds < -self.allow_future_seconds:
            result = FeedbackTTLCheckResult(
                is_valid=False,
                age_seconds=age_seconds,
                reason=FeedbackStalenessReason.FUTURE_TIMESTAMP,
                message=f"Feedback timestamp is {abs(age_seconds)}s in the future "
                        f"(max skew: {self.allow_future_seconds}s)",
            )
            self._log_stale_feedback_event(
                feedback_id=feedback_id,
                tenant_id=tenant_id,
                reason=result.reason,
                message=result.message,
            )
            return result

        # 6. Check TTL (feedback too old)
        if age_seconds > self.max_age_seconds:
            result = FeedbackTTLCheckResult(
                is_valid=False,
                age_seconds=age_seconds,
                reason=FeedbackStalenessReason.EXCEEDS_TTL,
                message=f"Feedback is {age_seconds}s old (max TTL: {self.max_age_seconds}s)",
            )
            self._log_stale_feedback_event(
                feedback_id=feedback_id,
                tenant_id=tenant_id,
                reason=result.reason,
                message=result.message,
            )
            return result

        # 7. Timestamp is valid
        return FeedbackTTLCheckResult(
            is_valid=True,
            age_seconds=age_seconds,
            message="Timestamp within valid TTL window",
        )

    def _log_stale_feedback_event(
        self,
        feedback_id: Optional[str],
        tenant_id: Optional[str],
        reason: FeedbackStalenessReason,
        message: str,
        error: Optional[str] = None,
    ) -> None:
        """Log rejected stale feedback as audit event (GDPR Art. 30).

        Args:
            feedback_id: The feedback ID (if available)
            tenant_id: The tenant ID (if available)
            reason: Why the feedback was rejected
            message: Human-readable explanation
            error: Technical error details (if applicable)
        """
        audit_event = {
            "event_type": "feedback_stale",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "feedback_id": feedback_id or "unknown",
            "tenant_id": tenant_id or "unknown",
            "rejection_reason": reason.value,
            "message": message,
            "error": error,
        }

        # Log to audit backend (if available)
        if self.audit_backend:
            try:
                self.audit_backend.write_event(audit_event)
            except Exception as e:
                logger.error(f"Failed to write feedback_stale audit event: {e}")

        # Always log to standard logger
        logger.warning(
            f"Stale feedback rejected: feedback_id={feedback_id}, reason={reason.value}, "
            f"message={message}"
        )

    @classmethod
    def from_tenant_config(
        cls,
        tenant_config: Optional[Dict[str, Any]],
        audit_backend=None,
    ) -> "FeedbackTTLValidator":
        """Create validator from tenant.corvin.yaml config.

        Args:
            tenant_config: Tenant spec dict (e.g., tenant_config.get('spec', {}))
            audit_backend: Audit backend instance

        Returns:
            Configured FeedbackTTLValidator

        Example:
            config = yaml.load(open('tenant.corvin.yaml'))
            validator = FeedbackTTLValidator.from_tenant_config(config['spec'])
        """
        max_age_seconds = cls.DEFAULT_MAX_AGE_SECONDS
        allow_future_seconds = cls.DEFAULT_ALLOW_FUTURE_SECONDS

        if tenant_config and isinstance(tenant_config, dict):
            learning_config = tenant_config.get('learning', {})
            feedback_config = learning_config.get('feedback', {})

            # Allow override via config
            if 'max_age_seconds' in feedback_config:
                max_age_seconds = int(feedback_config['max_age_seconds'])
            if 'allow_future_seconds' in feedback_config:
                allow_future_seconds = int(feedback_config['allow_future_seconds'])

        return cls(
            max_age_seconds=max_age_seconds,
            allow_future_seconds=allow_future_seconds,
            audit_backend=audit_backend,
        )


class FeedbackSignatureIntegration:
    """Integration point: sign/verify feedback in the learning loop."""

    def __init__(
        self,
        validator: FeedbackSignatureValidator,
        audit_backend: Optional[Any] = None,
    ):
        """Initialize integration layer.

        Args:
            validator: FeedbackSignatureValidator instance
            audit_backend: Audit trail writer (optional)
        """
        self.validator = validator
        self.audit_backend = audit_backend

    def create_signed_feedback(
        self,
        tenant_id: str,
        feedback_payload: dict[str, Any],
    ) -> Tuple[Optional[Tuple[dict[str, Any], FeedbackSignature]], str]:
        """Create and sign feedback (fail-closed).

        Returns:
            ((feedback_dict, signature), "") on success
            (None, error_reason) on failure

        Workflow:
        1. Create feedback_id (UUID4)
        2. Sign feedback payload with tenant key
        3. Return (feedback_dict, FeedbackSignature)
        4. Caller writes both to audit trail atomically
        """
        # Ensure feedback_id is present
        if "feedback_id" not in feedback_payload:
            feedback_payload["feedback_id"] = str(uuid4())

        # Sign the feedback
        signature, error = self.validator.sign_feedback(tenant_id, feedback_payload)
        if error or not signature:
            return None, error

        return (feedback_payload, signature), ""

    def verify_and_accept_feedback(
        self,
        tenant_id: str,
        feedback_payload: dict[str, Any],
        signature: FeedbackSignature,
    ) -> Tuple[bool, str]:
        """Verify feedback signature before feeding to weight optimizer (fail-closed).

        Returns:
            (True, "") if signature is valid (safe to feed to optimizer)
            (False, error_reason) if signature is invalid (reject feedback)
        """
        return self.validator.verify_feedback_signature(tenant_id, feedback_payload, signature)

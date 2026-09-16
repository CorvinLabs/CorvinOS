"""A2A Delegation Verifier - Validate RSA-signed Tasks for Licensing Gate

Implements ADR-0704: Licensing Phase 2 - A2A RSA Gate
- Verify RSA signatures on delegation tasks
- Validate license tier (member-only access)
- Check revocation list (CRL)
- Audit event emission for every verification
- Fail-closed: deny by default unless all checks pass

License: Apache-2.0
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple

from core.licensing.member_credential import (
    MemberCredential,
    SignedTask,
    RSAKeyPair,
    LicenseTier,
    CredentialStore,
)

logger = logging.getLogger(__name__)


# ============================================================================
# CRL (Certificate Revocation List)
# ============================================================================

class RevocationList:
    """Manages revoked credentials."""

    def __init__(self, corvin_home: str):
        """Initialize revocation list.

        Args:
            corvin_home: Path to ~/.corvin
        """
        self.corvin_home = Path(corvin_home)
        self.crl_file = self.corvin_home / "licensing" / "crl.json"
        self.crl_file.parent.mkdir(parents=True, exist_ok=True)
        self.revoked: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        """Load revocation list from disk."""
        if self.crl_file.exists():
            try:
                with open(self.crl_file) as f:
                    data = json.load(f)
                self.revoked = data.get("revoked", {})
                logger.info(f"Loaded CRL with {len(self.revoked)} entries")
            except Exception as e:
                logger.error(f"Failed to load CRL: {e}")

    def _save(self) -> None:
        """Save revocation list to disk."""
        try:
            with open(self.crl_file, "w") as f:
                json.dump(
                    {
                        "revoked": self.revoked,
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                    f,
                    indent=2,
                )
        except Exception as e:
            logger.error(f"Failed to save CRL: {e}")

    def revoke(self, credential_id: str, reason: str = "") -> None:
        """Revoke a credential."""
        self.revoked[credential_id] = {
            "revoked_at": datetime.utcnow().isoformat(),
            "reason": reason,
        }
        self._save()
        logger.info(f"Revoked credential {credential_id}")

    def is_revoked(self, credential_id: str) -> bool:
        """Check if credential is revoked."""
        return credential_id in self.revoked

    def get_revocation_reason(self, credential_id: str) -> Optional[str]:
        """Get the revocation reason."""
        entry = self.revoked.get(credential_id)
        return entry.get("reason") if entry else None


# ============================================================================
# Verification Result
# ============================================================================

@dataclass
class VerificationResult:
    """Result of task verification."""

    is_valid: bool
    task_id: str
    member_id: str
    credential_id: str
    license_tier: str

    # Details
    checks_passed: List[str] = field(default_factory=list)
    checks_failed: List[str] = field(default_factory=list)
    error_message: str = ""

    # Audit
    verified_at: str = ""
    verification_latency_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "is_valid": self.is_valid,
            "task_id": self.task_id,
            "member_id": self.member_id,
            "credential_id": self.credential_id,
            "license_tier": self.license_tier,
            "checks_passed": self.checks_passed,
            "checks_failed": self.checks_failed,
            "error_message": self.error_message,
            "verified_at": self.verified_at,
            "verification_latency_ms": self.verification_latency_ms,
        }


# ============================================================================
# A2A Delegation Verifier
# ============================================================================

class A2ADelegationVerifier:
    """Verifies RSA-signed A2A delegation tasks.

    Implements fail-closed validation:
    - Signature must be valid
    - Credential must exist and not be expired
    - Credential must not be revoked
    - License tier must be 'member' or 'enterprise'
    - Task must not be expired
    - All checks logged to audit trail
    """

    def __init__(self, corvin_home: str):
        """Initialize verifier.

        Args:
            corvin_home: Path to ~/.corvin
        """
        self.corvin_home = Path(corvin_home)
        self.credential_store = CredentialStore(corvin_home)
        self.revocation_list = RevocationList(corvin_home)
        self.audit_file = self.corvin_home / "licensing" / "verification_audit.jsonl"
        self.audit_file.parent.mkdir(parents=True, exist_ok=True)

    def verify_signed_task(self, signed_task: SignedTask) -> VerificationResult:
        """Verify a signed delegation task.

        Args:
            signed_task: The signed task to verify

        Returns:
            VerificationResult with validation details
        """
        import time
        start_time = time.time()

        result = VerificationResult(
            is_valid=False,
            task_id=signed_task.task_id,
            member_id=signed_task.member_id,
            credential_id=signed_task.credential_id,
            license_tier="unknown",
        )

        # ====================================================================
        # Check 1: Credential Must Exist
        # ====================================================================

        credential = self.credential_store.load(signed_task.credential_id)
        if not credential:
            result.checks_failed.append(
                f"Credential not found: {signed_task.credential_id}"
            )
            result.error_message = "Credential not found"
            self._audit(result, "credential_not_found")
            return result

        result.license_tier = credential.license_tier
        result.checks_passed.append("Credential found")

        # ====================================================================
        # Check 2: Credential Must Not Be Expired
        # ====================================================================

        if credential.is_expired():
            result.checks_failed.append(
                f"Credential expired at {credential.expires_at}"
            )
            result.error_message = "Credential has expired"
            self._audit(result, "credential_expired")
            return result

        result.checks_passed.append("Credential not expired")

        # ====================================================================
        # Check 3: Credential Must Not Be Revoked
        # ====================================================================

        if self.revocation_list.is_revoked(signed_task.credential_id):
            reason = self.revocation_list.get_revocation_reason(signed_task.credential_id)
            result.checks_failed.append(f"Credential revoked: {reason}")
            result.error_message = "Credential has been revoked"
            self._audit(result, "credential_revoked")
            return result

        result.checks_passed.append("Credential not revoked")

        # ====================================================================
        # Check 4: License Tier Must Permit Delegation
        # ====================================================================

        if not credential.can_delegate():
            result.checks_failed.append(
                f"License tier '{credential.license_tier}' does not permit delegation"
            )
            result.error_message = "Insufficient license tier for A2A delegation"
            self._audit(result, "insufficient_license_tier")
            return result

        result.checks_passed.append("License tier permits delegation")

        # ====================================================================
        # Check 5: Signature Must Be Valid
        # ====================================================================

        if not self._verify_signature(signed_task, credential):
            result.checks_failed.append("Signature verification failed")
            result.error_message = "Invalid task signature"
            self._audit(result, "signature_invalid")
            return result

        result.checks_passed.append("Signature valid")

        # ====================================================================
        # Check 6: Task Must Not Be Expired
        # ====================================================================

        if signed_task.is_expired():
            result.checks_failed.append(
                f"Task expired {signed_task.ttl_seconds} seconds after creation"
            )
            result.error_message = "Task has expired"
            self._audit(result, "task_expired")
            return result

        result.checks_passed.append("Task not expired")

        # ====================================================================
        # Verification Passed
        # ====================================================================

        result.is_valid = True
        result.checks_passed.append("All checks passed")
        result.verified_at = datetime.utcnow().isoformat()
        result.verification_latency_ms = int((time.time() - start_time) * 1000)

        self._audit(result, "verification_passed")
        logger.info(f"Verified task {signed_task.task_id} from {signed_task.member_id}")

        return result

    def _verify_signature(self, signed_task: SignedTask, credential: MemberCredential) -> bool:
        """Verify RSA signature on task.

        Args:
            signed_task: The signed task
            credential: The member's credential

        Returns:
            True if signature is valid
        """
        try:
            # Reconstruct the data that was signed
            # (member_id + operation + payload hash)
            payload_json = json.dumps(signed_task.payload, sort_keys=True)
            message_parts = [
                signed_task.member_id,
                signed_task.operation,
                payload_json,
                signed_task.signed_at,
            ]
            message = "|".join(message_parts).encode()

            # Load public key from credential
            public_key = RSAKeyPair.from_public_pem(
                credential.public_key_pem.encode()
            )

            # Verify signature
            signature_bytes = bytes.fromhex(signed_task.signature)
            return RSAKeyPair.verify(public_key, message, signature_bytes)

        except Exception as e:
            logger.error(f"Signature verification error: {e}")
            return False

    def revoke_credential(self, credential_id: str, reason: str = "") -> None:
        """Revoke a credential (immediate effect).

        Args:
            credential_id: The credential to revoke
            reason: Revocation reason
        """
        self.revocation_list.revoke(credential_id, reason)
        self._audit_revocation(credential_id, reason)

    def _audit(self, result: VerificationResult, event_type: str) -> None:
        """Write verification audit event.

        Args:
            result: The verification result
            event_type: Type of event (verification_passed, signature_invalid, etc.)
        """
        event = {
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": f"a2a_{event_type}",
            "task_id": result.task_id,
            "member_id": result.member_id,
            "credential_id": result.credential_id,
            "is_valid": result.is_valid,
            "license_tier": result.license_tier,
            "checks_passed": result.checks_passed,
            "checks_failed": result.checks_failed,
            "error_message": result.error_message,
            "verification_latency_ms": result.verification_latency_ms,
        }

        try:
            with open(self.audit_file, "a") as f:
                f.write(json.dumps(event) + "\n")
        except Exception as e:
            logger.error(f"Failed to write audit: {e}")

    def _audit_revocation(self, credential_id: str, reason: str) -> None:
        """Write revocation audit event."""
        event = {
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": "a2a_credential_revoked",
            "credential_id": credential_id,
            "reason": reason,
        }

        try:
            with open(self.audit_file, "a") as f:
                f.write(json.dumps(event) + "\n")
        except Exception as e:
            logger.error(f"Failed to write revocation audit: {e}")

    def get_verification_audit(
        self,
        limit: int = 100,
        member_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get recent verification audit events.

        Args:
            limit: Max events to return
            member_id: Filter by member (optional)

        Returns:
            List of audit events
        """
        events = []
        try:
            with open(self.audit_file) as f:
                for line in f:
                    if line.strip():
                        event = json.loads(line)
                        if member_id and event.get("member_id") != member_id:
                            continue
                        events.append(event)
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.error(f"Failed to read audit: {e}")

        # Return most recent events
        return list(reversed(events))[:limit]

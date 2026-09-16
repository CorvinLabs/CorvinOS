"""Authority Server - Issue and Manage Credentials for A2A Licensing

Implements ADR-0704: Licensing Phase 2 - A2A RSA Gate
- Issue credentials to members with specified tier
- Maintain credential lifecycle
- Revoke compromised credentials
- Audit all credential operations
- Operator-only access (no public endpoint)

License: Apache-2.0
"""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any

from core.licensing.member_credential import (
    MemberCredential,
    RSAKeyPair,
    LicenseTier,
    CredentialStore,
)
from core.licensing.a2a_verifier import RevocationList

logger = logging.getLogger(__name__)


# ============================================================================
# Credential Issuance Response
# ============================================================================

@dataclass
class IssuanceResult:
    """Result of credential issuance."""

    success: bool
    credential: Optional[MemberCredential] = None
    error_message: str = ""
    issue_id: str = ""  # Unique issuance tracking ID

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "success": self.success,
            "credential": self.credential.to_dict() if self.credential else None,
            "error_message": self.error_message,
            "issue_id": self.issue_id,
        }


# ============================================================================
# Authority Server
# ============================================================================

class AuthorityServer:
    """Issues and manages member credentials for A2A licensing.

    Responsibilities:
    - Generate RSA keypairs for new credentials
    - Issue credentials with expiry dates
    - Maintain credential ledger (audit-only, append-only)
    - Coordinate with revocation list
    - Track credential usage
    """

    # Default credential validity period
    DEFAULT_VALIDITY_DAYS = 90

    def __init__(self, corvin_home: str):
        """Initialize authority server.

        Args:
            corvin_home: Path to ~/.corvin
        """
        self.corvin_home = Path(corvin_home)
        self.credential_store = CredentialStore(corvin_home)
        self.revocation_list = RevocationList(corvin_home)

        self.ledger_file = self.corvin_home / "licensing" / "issuance_ledger.jsonl"
        self.ledger_file.parent.mkdir(parents=True, exist_ok=True)

    def issue_credential(
        self,
        member_id: str,
        license_tier: str,
        validity_days: int = DEFAULT_VALIDITY_DAYS,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> IssuanceResult:
        """Issue a new credential to a member.

        Args:
            member_id: The member receiving the credential
            license_tier: The license tier (free, member, enterprise)
            validity_days: How many days until expiry (default 90)
            metadata: Optional metadata (e.g., organization, contact)

        Returns:
            IssuanceResult with credential or error
        """
        issue_id = str(uuid.uuid4())

        try:
            # Validate license tier
            valid_tiers = [tier.value for tier in LicenseTier]
            if license_tier not in valid_tiers:
                return IssuanceResult(
                    success=False,
                    error_message=f"Invalid license tier: {license_tier}",
                    issue_id=issue_id,
                )

            # Generate RSA keypair for this credential
            keypair = RSAKeyPair.generate()
            public_key_pem = keypair.public_pem().decode()

            # Create credential
            now = datetime.utcnow()
            expiry = now + timedelta(days=validity_days)

            credential = MemberCredential(
                member_id=member_id,
                license_tier=license_tier,
                issued_at=now.isoformat() + "Z",
                expires_at=expiry.isoformat() + "Z",
                public_key_pem=public_key_pem,
                issuer="corvin_authority",
                metadata=metadata or {},
            )

            # Sign the credential (self-sign with issuer's key)
            # In production, this would use a proper CA
            credential_json = json.dumps({
                "member_id": credential.member_id,
                "license_tier": credential.license_tier,
                "issued_at": credential.issued_at,
                "expires_at": credential.expires_at,
                "public_key_pem": credential.public_key_pem,
                "issuer": credential.issuer,
            }, sort_keys=True)

            credential.signature = keypair.sign(credential_json.encode()).hex()

            # Persist credential
            self.credential_store.save(credential)

            # Audit issuance
            self._audit_issuance(credential, issue_id, "success")

            logger.info(
                f"Issued credential {credential.credential_id} to {member_id} "
                f"(tier={license_tier}, expires={expiry.date()})"
            )

            return IssuanceResult(
                success=True,
                credential=credential,
                issue_id=issue_id,
            )

        except Exception as e:
            logger.error(f"Failed to issue credential: {e}")
            self._audit_issuance(
                None,
                issue_id,
                f"error: {str(e)}",
                member_id=member_id,
                license_tier=license_tier,
            )
            return IssuanceResult(
                success=False,
                error_message=f"Issuance failed: {str(e)}",
                issue_id=issue_id,
            )

    def renew_credential(
        self,
        credential_id: str,
        validity_days: int = DEFAULT_VALIDITY_DAYS,
    ) -> IssuanceResult:
        """Renew an existing credential.

        Args:
            credential_id: The credential to renew
            validity_days: New validity period

        Returns:
            IssuanceResult with new credential or error
        """
        # Load existing credential
        old_credential = self.credential_store.load(credential_id)
        if not old_credential:
            return IssuanceResult(
                success=False,
                error_message=f"Credential not found: {credential_id}",
            )

        # Issue new credential with same properties
        result = self.issue_credential(
            member_id=old_credential.member_id,
            license_tier=old_credential.license_tier,
            validity_days=validity_days,
            metadata=old_credential.metadata,
        )

        if result.success:
            logger.info(f"Renewed credential {credential_id} for {old_credential.member_id}")
            self._audit_renewal(credential_id, result.credential.credential_id)

        return result

    def downgrade_tier(
        self,
        credential_id: str,
        new_tier: str,
    ) -> IssuanceResult:
        """Downgrade a credential's license tier.

        Args:
            credential_id: The credential to downgrade
            new_tier: The new license tier

        Returns:
            IssuanceResult with downgraded credential or error
        """
        # Load existing credential
        credential = self.credential_store.load(credential_id)
        if not credential:
            return IssuanceResult(
                success=False,
                error_message=f"Credential not found: {credential_id}",
            )

        # Validate new tier
        valid_tiers = [tier.value for tier in LicenseTier]
        if new_tier not in valid_tiers:
            return IssuanceResult(
                success=False,
                error_message=f"Invalid license tier: {new_tier}",
            )

        # Create downgraded credential (same public key, new tier)
        old_tier = credential.license_tier
        credential.license_tier = new_tier

        # Persist updated credential
        self.credential_store.save(credential)

        self._audit_downgrade(credential_id, old_tier, new_tier)
        logger.info(f"Downgraded credential {credential_id} from {old_tier} to {new_tier}")

        return IssuanceResult(
            success=True,
            credential=credential,
        )

    def record_delegation_usage(
        self,
        task_id: str,
        credential_id: str,
        member_id: str,
        operation: str,
        result: str,  # "success" or "failure"
    ) -> None:
        """Record when a credential was used for delegation.

        Args:
            task_id: The delegated task ID
            credential_id: The credential used
            member_id: The member that delegated
            operation: The operation performed
            result: Whether it succeeded
        """
        event = {
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": "a2a_delegation_recorded",
            "task_id": task_id,
            "credential_id": credential_id,
            "member_id": member_id,
            "operation": operation,
            "result": result,
        }

        try:
            with open(self.ledger_file, "a") as f:
                f.write(json.dumps(event) + "\n")
        except Exception as e:
            logger.error(f"Failed to record delegation usage: {e}")

    def get_credential_info(self, credential_id: str) -> Optional[Dict[str, Any]]:
        """Get credential info (for management UI).

        Args:
            credential_id: The credential to look up

        Returns:
            Credential details or None if not found
        """
        credential = self.credential_store.load(credential_id)
        if not credential:
            return None

        is_revoked = self.revocation_list.is_revoked(credential_id)
        revocation_reason = self.revocation_list.get_revocation_reason(credential_id)

        return {
            "credential_id": credential.credential_id,
            "member_id": credential.member_id,
            "license_tier": credential.license_tier,
            "issued_at": credential.issued_at,
            "expires_at": credential.expires_at,
            "is_expired": credential.is_expired(),
            "remaining_ttl_seconds": credential.get_remaining_ttl_seconds(),
            "is_revoked": is_revoked,
            "revocation_reason": revocation_reason,
            "is_valid": credential.is_valid() and not is_revoked,
            "can_delegate": credential.can_delegate() and not is_revoked,
        }

    def list_member_credentials(
        self,
        member_id: str,
        include_expired: bool = False,
    ) -> List[Dict[str, Any]]:
        """List all credentials for a member.

        Args:
            member_id: The member to query
            include_expired: Include expired credentials

        Returns:
            List of credential info dicts
        """
        credentials = self.credential_store.list_for_member(member_id)
        result = []

        for credential in credentials:
            if credential.is_expired() and not include_expired:
                continue

            info = self.get_credential_info(credential.credential_id)
            if info:
                result.append(info)

        return result

    def revoke_credential(
        self,
        credential_id: str,
        reason: str = "Revoked by authority",
    ) -> bool:
        """Revoke a credential (immediate effect).

        Args:
            credential_id: The credential to revoke
            reason: Revocation reason

        Returns:
            True if revocation succeeded
        """
        try:
            credential = self.credential_store.load(credential_id)
            if not credential:
                logger.warning(f"Cannot revoke: credential not found {credential_id}")
                return False

            self.revocation_list.revoke(credential_id, reason)

            self._audit_revocation(credential_id, credential.member_id, reason)
            logger.info(f"Revoked credential {credential_id}: {reason}")

            return True

        except Exception as e:
            logger.error(f"Failed to revoke credential: {e}")
            return False

    # ========================================================================
    # Audit Logging
    # ========================================================================

    def _audit_issuance(
        self,
        credential: Optional[MemberCredential],
        issue_id: str,
        status: str,
        member_id: Optional[str] = None,
        license_tier: Optional[str] = None,
    ) -> None:
        """Audit credential issuance event."""
        event = {
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": "a2a_credential_issued",
            "issue_id": issue_id,
            "status": status,
            "member_id": credential.member_id if credential else member_id,
            "credential_id": credential.credential_id if credential else None,
            "license_tier": credential.license_tier if credential else license_tier,
        }

        try:
            with open(self.ledger_file, "a") as f:
                f.write(json.dumps(event) + "\n")
        except Exception as e:
            logger.error(f"Failed to audit issuance: {e}")

    def _audit_renewal(self, old_id: str, new_id: str) -> None:
        """Audit credential renewal event."""
        event = {
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": "a2a_credential_renewed",
            "old_credential_id": old_id,
            "new_credential_id": new_id,
        }

        try:
            with open(self.ledger_file, "a") as f:
                f.write(json.dumps(event) + "\n")
        except Exception as e:
            logger.error(f"Failed to audit renewal: {e}")

    def _audit_downgrade(self, credential_id: str, old_tier: str, new_tier: str) -> None:
        """Audit credential tier downgrade event."""
        event = {
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": "a2a_credential_downgraded",
            "credential_id": credential_id,
            "old_tier": old_tier,
            "new_tier": new_tier,
        }

        try:
            with open(self.ledger_file, "a") as f:
                f.write(json.dumps(event) + "\n")
        except Exception as e:
            logger.error(f"Failed to audit downgrade: {e}")

    def _audit_revocation(self, credential_id: str, member_id: str, reason: str) -> None:
        """Audit credential revocation event."""
        event = {
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": "a2a_credential_revoked_by_authority",
            "credential_id": credential_id,
            "member_id": member_id,
            "reason": reason,
        }

        try:
            with open(self.ledger_file, "a") as f:
                f.write(json.dumps(event) + "\n")
        except Exception as e:
            logger.error(f"Failed to audit revocation: {e}")

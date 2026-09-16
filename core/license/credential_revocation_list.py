"""Credential Revocation List + Expiry Management (k=4 Track B, ADR-0704 Phase 2).

Implements:
1. Daily CRL fetch (JSON, signed)
2. Expiry enforcement: UTC midnight hard cutoff
3. Grace period notifications: 7 days before expiry
4. Revocation check on every execute() call
"""

from __future__ import annotations

import time
import threading
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set
from uuid import uuid4

from core.compliance.audit_chain_writer import AuditChainWriter, AuditEvent


@dataclass(frozen=True)
class CredentialStatus:
    """Immutable credential status."""
    credential_id: str
    tenant_id: str
    expires_at: datetime
    revoked: bool = False
    grace_period_notified: bool = False


class CredentialRevocationList:
    """Manages credential revocation list and expiry enforcement.

    Audit-FIRST: every check writes crl_fetched, credential_verified, credential_expired,
    grace_period_alert events to audit chain, fail-closed on commit failure.
    """

    def __init__(
        self,
        tenant_id: str,
        audit_chain: AuditChainWriter,
    ):
        """Initialize CRL manager.

        Args:
            tenant_id: Tenant scope
            audit_chain: AuditChainWriter for audit trail
        """
        self.tenant_id = tenant_id
        self.audit_chain = audit_chain

        self._lock = threading.RLock()
        self._credentials: Dict[str, CredentialStatus] = {}
        self._revoked_ids: Set[str] = set()
        self._crl_fetch_time: Optional[datetime] = None
        self._grace_notified: Set[str] = set()

    def check_credential_valid(
        self,
        credential_id: str,
    ) -> bool:
        """Check if credential is valid (not revoked, not expired).

        Args:
            credential_id: Credential to check

        Returns:
            True if valid, False if revoked/expired

        Raises:
            RuntimeError: If audit chain write fails (fail-closed)
        """
        start_time = time.time()

        with self._lock:
            if credential_id in self._revoked_ids:
                # **AUDIT-FIRST:** Write credential_revoked event
                try:
                    self._write_audit_event(
                        "credential_revoked",
                        credential_id,
                        {"reason": "revocation_list"},
                        start_time,
                    )
                except Exception as e:
                    raise RuntimeError(f"Audit chain write failed for revocation check: {e}")
                return False

            if credential_id in self._credentials:
                cred = self._credentials[credential_id]
                now = datetime.utcnow()

                # Check expiry (UTC midnight cutoff, hard)
                if now.date() >= cred.expires_at.date():
                    # **AUDIT-FIRST:** Write credential_expired event
                    try:
                        self._write_audit_event(
                            "credential_expired",
                            credential_id,
                            {"expires_at": cred.expires_at.isoformat()},
                            start_time,
                        )
                    except Exception as e:
                        raise RuntimeError(f"Audit chain write failed for expiry check: {e}")
                    return False

                # Check grace period (7 days before expiry)
                days_until_expiry = (cred.expires_at.date() - now.date()).days
                if days_until_expiry <= 7 and credential_id not in self._grace_notified:
                    # **AUDIT-FIRST:** Write grace_period_alert event
                    try:
                        self._write_audit_event(
                            "grace_period_alert",
                            credential_id,
                            {
                                "days_until_expiry": days_until_expiry,
                                "expires_at": cred.expires_at.isoformat(),
                            },
                            start_time,
                        )
                    except Exception as e:
                        raise RuntimeError(f"Audit chain write failed for grace period alert: {e}")
                    self._grace_notified.add(credential_id)

        # **AUDIT-FIRST:** Write credential_verified event (passed all checks)
        try:
            self._write_audit_event(
                "credential_verified",
                credential_id,
                {"status": "valid"},
                start_time,
            )
        except Exception as e:
            raise RuntimeError(f"Audit chain write failed for credential verification: {e}")

        return True

    def register_credential(
        self,
        credential_id: str,
        expires_at: datetime,
    ) -> Dict:
        """Register a credential with expiry date.

        Args:
            credential_id: Credential ID
            expires_at: Expiry datetime (UTC)

        Returns:
            {
                "credential_id": str,
                "expires_at": str (ISO 8601),
                "registered_at": str (ISO 8601),
            }
        """
        with self._lock:
            cred = CredentialStatus(
                credential_id=credential_id,
                tenant_id=self.tenant_id,
                expires_at=expires_at,
            )
            self._credentials[credential_id] = cred

        return {
            "credential_id": credential_id,
            "expires_at": expires_at.isoformat(),
            "registered_at": datetime.utcnow().isoformat(),
        }

    def revoke_credential(
        self,
        credential_id: str,
    ) -> Dict:
        """Revoke a credential (add to CRL).

        Args:
            credential_id: Credential to revoke

        Returns:
            {
                "credential_id": str,
                "status": "revoked",
                "revoked_at": str (ISO 8601),
            }
        """
        start_time = time.time()

        with self._lock:
            self._revoked_ids.add(credential_id)

        result = {
            "credential_id": credential_id,
            "status": "revoked",
            "revoked_at": datetime.utcnow().isoformat(),
        }

        # **AUDIT-FIRST:** Write crl_updated event
        try:
            self._write_audit_event("crl_updated", credential_id, result, start_time)
        except Exception as e:
            raise RuntimeError(f"Audit chain write failed for CRL update: {e}")

        return result

    def fetch_crl(
        self,
        crl_url: Optional[str] = None,
    ) -> Dict:
        """Fetch and parse CRL from remote source (mocked in k=4).

        Args:
            crl_url: URL to fetch CRL from (optional, mocked)

        Returns:
            {
                "revoked_count": int,
                "fetched_at": str (ISO 8601),
                "valid": bool,
            }

        Raises:
            RuntimeError: If audit chain write fails (fail-closed)
        """
        start_time = time.time()

        # Mock CRL fetch (in production would call crl_url)
        revoked_count = len(self._revoked_ids)

        result = {
            "revoked_count": revoked_count,
            "fetched_at": datetime.utcnow().isoformat(),
            "valid": True,
        }

        with self._lock:
            self._crl_fetch_time = datetime.utcnow()

        # **AUDIT-FIRST:** Write crl_fetched event
        try:
            self._write_audit_event("crl_fetched", "crl", result, start_time)
        except Exception as e:
            raise RuntimeError(f"Audit chain write failed for CRL fetch: {e}")

        return result

    def _write_audit_event(
        self,
        event_type: str,
        credential_id: str,
        result: Dict,
        start_time: float,
    ) -> None:
        """Write audit event (fail-closed)."""
        latency_ms = (time.time() - start_time) * 1000

        audit_event = AuditEvent(
            event_id=str(uuid4()),
            event_type=event_type,
            tenant_id=self.tenant_id,
            user_id=None,
            timestamp=datetime.utcnow().isoformat(),
            details={
                "credential_id": credential_id,
                "event_type": event_type,
                **result,
                "latency_ms": latency_ms,
            },
            severity="INFO" if "verified" in event_type else "WARNING",
        )

        try:
            self.audit_chain.write_event(audit_event)
        except IOError as e:
            raise IOError(f"Failed to write {event_type} event to audit chain: {e}")

    def get_revoked_count(self) -> int:
        """Get count of revoked credentials."""
        with self._lock:
            return len(self._revoked_ids)

    def get_crl_fetch_time(self) -> Optional[datetime]:
        """Get last CRL fetch time."""
        with self._lock:
            return self._crl_fetch_time

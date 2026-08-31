"""Boot Verification Tripwire — ADR-0328

Verify audit chain integrity on startup. Fail-closed: if chain is unreachable
or integrity check fails, crash immediately (no override via env var).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional
import sys


class BootState(Enum):
    """Boot state enumeration."""
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    FAILED = "failed"
    DEGRADED = "degraded"


class BootVerificationError(Exception):
    """Raised when boot verification fails (fail-closed)."""

    def __init__(self, message: str, reason: str = "unknown"):
        self.message = message
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True)
class BootVerificationResult:
    """Immutable boot verification result."""

    state: BootState
    chain_reachable: bool
    chain_intact: bool
    error_message: Optional[str] = None
    last_audit_hash: Optional[str] = None


class BootVerifier:
    """Verify audit chain integrity on startup (fail-closed)."""

    def __init__(self):
        """Initialize boot verifier."""
        self._verified = False
        self._result: Optional[BootVerificationResult] = None

    def verify_startup(self, *, tenant_id: str) -> BootVerificationResult:
        """Verify chain is reachable and intact.

        Args:
            tenant_id: Tenant context

        Returns:
            BootVerificationResult

        Raises:
            BootVerificationError: If chain unreachable or integrity fails
                (fail-closed: no recovery)
        """
        try:
            # Attempt to reach audit chain writer
            chain_reachable = self._check_chain_reachable(tenant_id)

            if not chain_reachable:
                raise BootVerificationError(
                    "Audit chain writer unreachable",
                    reason="chain_unreachable",
                )

            # Verify last entry's hash
            chain_intact = self._verify_chain_integrity(tenant_id)

            if not chain_intact:
                raise BootVerificationError(
                    "Audit chain integrity check failed",
                    reason="integrity_failed",
                )

            # All checks passed
            result = BootVerificationResult(
                state=BootState.VERIFIED,
                chain_reachable=True,
                chain_intact=True,
                last_audit_hash="hash_verified",
            )
            self._result = result
            self._verified = True
            return result

        except BootVerificationError:
            # Fail-closed: crash immediately, no recovery
            raise

    def is_verified(self) -> bool:
        """Check if boot verification passed."""
        return self._verified

    def get_result(self) -> Optional[BootVerificationResult]:
        """Get boot verification result."""
        return self._result

    def _check_chain_reachable(self, tenant_id: str) -> bool:
        """Check if audit chain writer is reachable.

        Placeholder: real implementation would attempt connection.
        """
        # Simulated check (always True for now)
        return True

    def _verify_chain_integrity(self, tenant_id: str) -> bool:
        """Verify last audit entry's hash.

        Placeholder: real implementation would hash-chain verify.
        """
        # Simulated check (always True for now)
        return True

    def crash_if_not_verified(self) -> None:
        """Crash immediately if not verified.

        Fail-closed: no env override, no fallback.
        """
        if not self._verified:
            sys.exit(1)

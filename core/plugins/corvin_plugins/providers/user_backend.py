"""User backend provider - ADR-0233.

Singleton registry for user authentication + consent validation.
Implements per-user credential validation and consent gates.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional, Protocol
import threading

_logger = logging.getLogger(__name__)

# Thread-safe singleton
_lock = threading.Lock()
_active_backend: Optional['UserBackend'] = None


@dataclass(frozen=True)
class UserCredential:
    """User authentication credential."""
    user_id: str
    tenant_id: str
    auth_type: str  # "local", "oauth", "token"
    verified: bool = False
    expires_at: Optional[str] = None


@dataclass(frozen=True)
class UserConsent:
    """User consent record."""
    user_id: str
    tenant_id: str
    consent_type: str  # "telemetry", "learning", "healing_traces", "geo_tracking"
    granted: bool
    granted_at: Optional[str] = None
    expires_at: Optional[str] = None


class UserBackend(Protocol):
    """Protocol for user backend implementations."""

    async def verify_credential(self, credential: UserCredential) -> bool:
        """Verify a user credential.

        Args:
            credential: The credential to verify

        Returns:
            True if credential is valid, False otherwise
        """
        ...

    async def get_user_id(self, tenant_id: str, auth_header: Optional[str] = None) -> Optional[str]:
        """Get user_id from auth context.

        Args:
            tenant_id: The tenant context
            auth_header: Optional auth header value

        Returns:
            user_id if authenticated, None otherwise
        """
        ...

    async def grant_consent(self, consent: UserConsent) -> bool:
        """Grant user consent for a feature/capability.

        Args:
            consent: The consent record

        Returns:
            True if granted, False if denied/revoked
        """
        ...

    async def check_consent(self, user_id: str, tenant_id: str, consent_type: str) -> bool:
        """Check if user has granted consent.

        Args:
            user_id: The user
            tenant_id: The tenant
            consent_type: Type of consent to check

        Returns:
            True if consent granted and not expired, False otherwise
        """
        ...

    async def revoke_consent(self, user_id: str, tenant_id: str, consent_type: str) -> bool:
        """Revoke user consent (GDPR Art. 7).

        Args:
            user_id: The user
            tenant_id: The tenant
            consent_type: Type of consent to revoke

        Returns:
            True if revoked, False if not found/already revoked
        """
        ...

    async def health_check(self) -> bool:
        """Check backend health."""
        ...


class DefaultUserBackend:
    """Default in-process user backend."""

    def __init__(self):
        """Initialize the user backend."""
        # In-memory stores for testing/local development
        self._credentials: dict[str, UserCredential] = {}
        self._consents: dict[tuple[str, str, str], UserConsent] = {}  # (user_id, tenant_id, type) -> consent
        self._lock = threading.Lock()

    async def verify_credential(self, credential: UserCredential) -> bool:
        """Verify a user credential."""
        try:
            with self._lock:
                # For local-only mode, accept any non-empty user_id
                if credential.auth_type == "local" and credential.user_id:
                    return True

                # Check if credential is in store
                key = f"{credential.tenant_id}:{credential.user_id}"
                stored = self._credentials.get(key)

                if not stored:
                    return False

                # Check expiration
                if stored.expires_at:
                    expires = datetime.fromisoformat(stored.expires_at)
                    if expires < datetime.now(timezone.utc):
                        return False

                return stored.verified
        except Exception as e:
            _logger.error(f"Credential verification failed: {e}")
            return False

    async def get_user_id(self, tenant_id: str, auth_header: Optional[str] = None) -> Optional[str]:
        """Get user_id from auth context."""
        try:
            # For local-only mode, derive from TCP peer or return default
            if not auth_header:
                # Local mode: return a default user
                return f"user@{tenant_id}"
            # Future: Parse OAuth / token headers
            return None
        except Exception as e:
            _logger.error(f"Failed to get user_id: {e}")
            return None

    async def grant_consent(self, consent: UserConsent) -> bool:
        """Grant user consent."""
        try:
            with self._lock:
                key = (consent.user_id, consent.tenant_id, consent.consent_type)
                self._consents[key] = consent
                _logger.info(f"Consent granted: {consent.user_id}/{consent.consent_type}")
                return True
        except Exception as e:
            _logger.error(f"Failed to grant consent: {e}")
            return False

    async def check_consent(self, user_id: str, tenant_id: str, consent_type: str) -> bool:
        """Check if user has granted consent."""
        try:
            with self._lock:
                key = (user_id, tenant_id, consent_type)
                consent = self._consents.get(key)

                if not consent:
                    # No consent record = deny (fail-closed)
                    return False

                if not consent.granted:
                    # Explicitly revoked
                    return False

                # Check expiration
                if consent.expires_at:
                    expires = datetime.fromisoformat(consent.expires_at)
                    if expires < datetime.now(timezone.utc):
                        return False

                return True
        except Exception as e:
            _logger.error(f"Consent check failed: {e}")
            return False

    async def revoke_consent(self, user_id: str, tenant_id: str, consent_type: str) -> bool:
        """Revoke user consent."""
        try:
            with self._lock:
                key = (user_id, tenant_id, consent_type)
                if key in self._consents:
                    # Mark as revoked
                    old = self._consents[key]
                    self._consents[key] = UserConsent(
                        user_id=old.user_id,
                        tenant_id=old.tenant_id,
                        consent_type=old.consent_type,
                        granted=False,
                        granted_at=old.granted_at,
                        expires_at=datetime.now(timezone.utc).isoformat()
                    )
                    _logger.info(f"Consent revoked: {user_id}/{consent_type}")
                    return True
                return False
        except Exception as e:
            _logger.error(f"Consent revocation failed: {e}")
            return False

    async def health_check(self) -> bool:
        """Check backend health."""
        try:
            # Simple check: can we verify a test credential?
            test_cred = UserCredential(
                user_id="test",
                tenant_id="__health_check__",
                auth_type="local"
            )
            return await self.verify_credential(test_cred)
        except Exception:
            return False


def get_active() -> UserBackend:
    """Get the currently active user backend."""
    global _active_backend
    with _lock:
        if _active_backend is None:
            _active_backend = DefaultUserBackend()
        return _active_backend


def set_active(backend: UserBackend) -> None:
    """Set the active user backend (for testing)."""
    global _active_backend
    with _lock:
        _active_backend = backend

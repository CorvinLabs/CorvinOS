"""Token System — M1 Implementation

Token lifecycle for console-plugin authentication (ADR-0954).

Architecture:
- TokenStore: Abstract protocol for token operations
- TokenPayload: Immutable token metadata (user_id, tenant_id, scopes, expires_at)
- InMemoryTokenStore: Simple in-memory implementation (no persistence)
- DispatcherAuthError: Raised on token validation failure (fail-closed)

Compliance:
- All tokens carry tenant_id (ADR-0007 isolation)
- Tokens are revocable (GDPR Art. 6 consent withdrawal)
- Token validation fails closed (no token → DispatcherAuthError)
- Tokens expire automatically after TTL (default 24h)

See: ADR-0954 (Console Dispatcher Architecture) in Corvin-ADR.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import uuid


class DispatcherAuthError(Exception):
    """Token validation failed — fail-closed, no dispatch proceeds."""
    pass


@dataclass(frozen=True)
class TokenPayload:
    """Immutable token metadata.

    Frozen to prevent mutation; carried through dispatch pipeline.
    """
    token_id: str
    tenant_id: str
    user_id: str
    expires_at: datetime
    scopes: List[str]  # ["read", "render", "admin"]
    created_at: datetime = field(default_factory=datetime.utcnow)


class TokenStore(ABC):
    """Abstract token storage protocol.

    M1: InMemoryTokenStore (simple, no external deps)
    M3+: Can add Redis or persistent file-based store
    """

    @abstractmethod
    async def generate(
        self,
        user_id: str,
        tenant_id: str,
        scopes: List[str],
        expires_at: Optional[datetime] = None,
    ) -> str:
        """Generate new token.

        Args:
            user_id: User identifier
            tenant_id: Tenant scope (ADR-0007, mandatory)
            scopes: List of granted scopes (["read", "render", "admin"])
            expires_at: Optional expiration time (default: now + 24h)

        Returns:
            Token string (opaque, unique)

        Raises:
            DispatcherAuthError: If generation fails
        """
        pass

    @abstractmethod
    async def validate(self, token: str) -> TokenPayload:
        """Validate token and return payload (fail-closed).

        Args:
            token: Token string to validate

        Returns:
            TokenPayload if valid and not expired

        Raises:
            DispatcherAuthError: If invalid, expired, or revoked
        """
        pass

    @abstractmethod
    async def revoke(self, token: str) -> bool:
        """Revoke token (GDPR consent withdrawal).

        Args:
            token: Token string to revoke

        Returns:
            True if revoked, False if not found
        """
        pass


class InMemoryTokenStore(TokenStore):
    """Simple in-memory token storage (M1 MVP).

    No persistence; all tokens lost on restart.
    Suitable for local development and single-instance deployments.

    For multi-instance deployments, use RedisTokenStore (M3+).
    """

    def __init__(self, token_ttl_hours: int = 24):
        """Initialize token store.

        Args:
            token_ttl_hours: Token lifetime in hours (default 24)
        """
        self.token_ttl = timedelta(hours=token_ttl_hours)
        self._tokens: Dict[str, TokenPayload] = {}
        self._revoked: set = set()

    async def generate(
        self,
        user_id: str,
        tenant_id: str,
        scopes: List[str],
        expires_at: Optional[datetime] = None,
    ) -> str:
        """Generate new token (in-memory, unique).

        Generates a random token_id, creates TokenPayload, stores in dict.
        """
        if not tenant_id:
            raise DispatcherAuthError("tenant_id is mandatory (ADR-0007)")
        if not user_id:
            raise DispatcherAuthError("user_id is mandatory")

        token_id = str(uuid.uuid4())
        if not expires_at:
            expires_at = datetime.utcnow() + self.token_ttl

        payload = TokenPayload(
            token_id=token_id,
            tenant_id=tenant_id,
            user_id=user_id,
            expires_at=expires_at,
            scopes=scopes if scopes else ["read"],
        )

        self._tokens[token_id] = payload
        return token_id

    async def validate(self, token: str) -> TokenPayload:
        """Validate token — fail-closed on any error.

        Checks:
        1. Token exists
        2. Not revoked
        3. Not expired
        4. Carries tenant_id (ADR-0007)
        """
        if not token:
            raise DispatcherAuthError("Token is empty")

        if token in self._revoked:
            raise DispatcherAuthError(f"Token {token} is revoked")

        payload = self._tokens.get(token)
        if not payload:
            raise DispatcherAuthError(f"Token {token} not found")

        if datetime.utcnow() > payload.expires_at:
            raise DispatcherAuthError(f"Token {token} expired")

        if not payload.tenant_id:
            raise DispatcherAuthError("Token missing tenant_id (ADR-0007 violation)")

        return payload

    async def revoke(self, token: str) -> bool:
        """Revoke token (GDPR consent withdrawal).

        Once revoked, token cannot be validated again (fail-closed).
        """
        if token in self._tokens:
            self._revoked.add(token)
            return True
        return False

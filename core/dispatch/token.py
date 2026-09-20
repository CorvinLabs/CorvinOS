"""Token System — M0 Planning Artifact

This module defines the token lifecycle for console-plugin authentication.

M0 Status: Stub only — contains docstrings and TODO markers.
M1: Full implementation (TokenStore protocol, InMemoryTokenStore, generate/validate/revoke).

Architecture:
- TokenStore: Abstract protocol for token operations
- TokenPayload: Immutable token metadata (user_id, tenant_id, scopes, expires_at)
- InMemoryTokenStore: Simple in-memory implementation (no persistence)

Compliance:
- All tokens carry tenant_id (ADR-0007 isolation)
- Tokens are revocable (GDPR Art. 6 consent withdrawal)
- Token validation fails closed (no token → DispatcherAuthError)

See: ADR-NEW (Console Dispatcher Architecture) in Corvin-ADR after M1.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional


@dataclass(frozen=True)
class TokenPayload:
    """Immutable token metadata.

    M1 will implement generation and validation.
    """
    token_id: str
    tenant_id: str
    user_id: str
    expires_at: datetime
    scopes: List[str]  # ["read", "render", "admin"]


class TokenStore(ABC):
    """Abstract token storage protocol.

    M1 will provide InMemoryTokenStore implementation.
    M3+ can add Redis or persistent file-based store.
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
            tenant_id: Tenant scope (ADR-0007)
            scopes: List of granted scopes
            expires_at: Optional expiration time

        Returns:
            Token string

        Raises:
            DispatcherAuthError: If generation fails
        """
        pass

    @abstractmethod
    async def validate(self, token: str) -> TokenPayload:
        """Validate token and return payload.

        Args:
            token: Token string to validate

        Returns:
            TokenPayload if valid

        Raises:
            DispatcherAuthError: If invalid or expired
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


# TODO (M1): Implement InMemoryTokenStore(TokenStore)
# TODO (M1): Add DispatcherAuthError exception
# TODO (M1): Wire token validation into ConsoleDispatcher.dispatch()

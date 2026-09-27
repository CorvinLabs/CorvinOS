"""Identity Provider Abstract Interface (Phase C k=5).

This interface is the contract that ADR-0007 OIDC team implements.
Local dev uses LocalDevIdentityProvider; production will use OIDCIdentityProvider.

Frozen Assumptions (for ADR-0007 wiring):
  - Token is a string (opaque or JWT)
  - resolve_user(token) returns User with roles, tenant_id, email
  - Tenant isolation: User.tenant_id filters all queries
  - Audit: every permission check logged
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


class AuthError(Exception):
    """Authentication failure (invalid token, expired, etc.)."""


class AuthorizationError(Exception):
    """Authorization failure (insufficient permissions)."""


@dataclass(frozen=True)
class IdentityToken:
    """Opaque or JWT token returned by authenticate().

    Local dev: simple string (user_id)
    OIDC: JWT from /token endpoint
    Bridge: Bearer token from upstream system
    """

    token: str  # Opaque string; never inspect, only pass to resolve_user()


@dataclass(frozen=True)
class User:
    """User identity + roles (tenant-scoped).

    Fields:
      user_id: str — unique user identifier (email, username, or UUID)
      email: str — user email (for audit attribution)
      tenant_id: str — tenant scope (GDPR Art. 5 isolation)
      roles: list[str] — tenant-scoped role names (e.g., ["admin", "reviewer"])
      groups: list[str] — optional group membership (e.g., ["engineering", "research"])
    """

    user_id: str
    email: str
    tenant_id: str
    roles: list[str]
    groups: list[str] = None

    def __post_init__(self):
        if not self.user_id:
            raise ValueError("user_id is required")
        if not self.email:
            raise ValueError("email is required")
        if not self.tenant_id:
            raise ValueError("tenant_id is required (GDPR Art. 5)")
        if not isinstance(self.roles, list) or not self.roles:
            raise ValueError("roles must be a non-empty list")
        if self.groups is None:
            object.__setattr__(self, "groups", [])

    def has_role(self, role: str) -> bool:
        """Check if user has a specific role."""
        return role in self.roles


class IdentityProvider(ABC):
    """Abstract identity provider. ADR-0007 OIDC implements this.

    LocalDevIdentityProvider implements this for offline dev/test.
    OIDCIdentityProvider (from ADR-0007) implements this for production.
    """

    @abstractmethod
    async def authenticate(self, credentials: dict) -> IdentityToken:
        """Authenticate user with provided credentials.

        Args:
            credentials: dict with provider-specific fields
                        (e.g., {"user_id": "alice", "password": "xxx"} for local dev,
                         or {"code": "..."} for OIDC)

        Returns:
            IdentityToken: opaque token for later resolve_user() calls

        Raises:
            AuthError: if authentication fails (invalid credentials, network error, etc.)
        """

    @abstractmethod
    async def resolve_user(self, token: IdentityToken) -> User:
        """Resolve token to User (with roles, tenant_id, etc.).

        Args:
            token: IdentityToken from authenticate()

        Returns:
            User: with user_id, email, tenant_id, roles

        Raises:
            AuthError: if token is invalid, expired, or cannot be resolved
        """

    @abstractmethod
    async def verify_token(self, token: IdentityToken) -> bool:
        """Check if token is still valid (not expired, not revoked).

        Args:
            token: IdentityToken to verify

        Returns:
            bool: True if token is valid, False if expired/revoked/invalid
        """

    @abstractmethod
    async def revoke_token(self, token: IdentityToken) -> None:
        """Revoke a token (logout, session end, etc.).

        Args:
            token: IdentityToken to revoke

        Raises:
            AuthError: if revocation fails
        """

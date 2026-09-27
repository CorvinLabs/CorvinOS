"""LocalDevIdentityProvider: Offline authentication for local development.

No OIDC, no external auth system required.
Users authenticate with plain credentials (dev only, never production).

Token Format: opaque string "user_id-nonce" (not JWT, deliberately simple)
Session Lifetime: matches console session (1h idle timeout, 8h absolute)

Roles: configurable per user via env var or config file.
Default: everyone is admin (for local dev convenience)
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .identity import AuthError, IdentityProvider, IdentityToken, User

_log = logging.getLogger(__name__)


_TOKEN_STORE: dict[str, dict] = {}  # In-memory token store (dev only)
_TOKEN_STORE_LOCK = __import__("asyncio").Lock()

# Default roles for local dev users (override via CORVIN_RBAC_DEFAULT_ROLES env var)
_DEFAULT_ROLES = ["admin", "reviewer", "viewer"]

# Token lifetime (match console session)
_TOKEN_LIFETIME_S = 8 * 60 * 60  # 8 hours (absolute timeout)


class LocalDevIdentityProvider(IdentityProvider):
    """Local development identity provider (no OIDC, no auth server).

    Used for:
      - Local development (localhost)
      - Integration tests
      - E2E testing without auth infrastructure

    NEVER use in production — credentials are plain text and validation is minimal.
    """

    def __init__(self, tenant_id: str = "_default"):
        """Initialize local identity provider.

        Args:
            tenant_id: tenant scope for all users (default: "_default")
        """
        self.tenant_id = tenant_id
        self._setup_roles()

    def _setup_roles(self) -> None:
        """Load role configuration from env or use defaults."""
        roles_env = os.getenv("CORVIN_RBAC_DEFAULT_ROLES", "").strip()
        if roles_env:
            try:
                self.default_roles = roles_env.split(",")
                _log.info(f"LocalDevIdentityProvider using roles from env: {self.default_roles}")
            except Exception as e:
                _log.warning(f"Failed to parse CORVIN_RBAC_DEFAULT_ROLES: {e}, using defaults")
                self.default_roles = _DEFAULT_ROLES
        else:
            self.default_roles = _DEFAULT_ROLES

    async def authenticate(self, credentials: dict) -> IdentityToken:
        """Authenticate user with plain credentials.

        Expected credentials:
          - user_id: str (required)
          - email: str (optional, defaults to "{user_id}@localhost")
          - password: str (required, but not validated in dev mode)

        Returns:
            IdentityToken with opaque token

        Raises:
            AuthError: if user_id or password missing
        """
        user_id = credentials.get("user_id", "").strip()
        password = credentials.get("password", "").strip()
        email = credentials.get("email", "").strip()

        if not user_id:
            raise AuthError("user_id is required")
        if not password:
            raise AuthError("password is required (dev mode: any non-empty string)")

        if not email:
            email = f"{user_id}@localhost"

        # Generate token (opaque string, not JWT)
        nonce = secrets.token_hex(16)  # 32-char hex
        token_string = f"{user_id}-{nonce}"

        # Store token metadata (dev only, in-memory, lost on restart)
        async with _TOKEN_STORE_LOCK:
            _TOKEN_STORE[token_string] = {
                "user_id": user_id,
                "email": email,
                "created_at": time.time(),
                "expires_at": time.time() + _TOKEN_LIFETIME_S,
                "revoked": False,
            }

        _log.info(f"LocalDevIdentityProvider: authenticated {user_id}")
        return IdentityToken(token=token_string)

    async def resolve_user(self, token: IdentityToken) -> User:
        """Resolve token to User (with roles).

        Args:
            token: IdentityToken from authenticate()

        Returns:
            User: with user_id, email, tenant_id, roles

        Raises:
            AuthError: if token invalid or expired
        """
        async with _TOKEN_STORE_LOCK:
            token_data = _TOKEN_STORE.get(token.token)

        if not token_data:
            raise AuthError(f"token not found: {token.token[:20]}...")

        if token_data.get("revoked"):
            raise AuthError(f"token revoked: {token.token[:20]}...")

        if time.time() >= token_data.get("expires_at", 0):
            raise AuthError(f"token expired: {token.token[:20]}...")

        return User(
            user_id=token_data["user_id"],
            email=token_data["email"],
            tenant_id=self.tenant_id,
            roles=self.default_roles,  # All local users get default roles (admin by default)
            groups=[],
        )

    async def verify_token(self, token: IdentityToken) -> bool:
        """Check if token is still valid.

        Args:
            token: IdentityToken to verify

        Returns:
            bool: True if valid, False if expired/revoked/invalid
        """
        async with _TOKEN_STORE_LOCK:
            token_data = _TOKEN_STORE.get(token.token)

        if not token_data:
            return False

        if token_data.get("revoked"):
            return False

        if time.time() >= token_data.get("expires_at", 0):
            return False

        return True

    async def revoke_token(self, token: IdentityToken) -> None:
        """Revoke a token (logout).

        Args:
            token: IdentityToken to revoke

        Raises:
            AuthError: if token not found
        """
        async with _TOKEN_STORE_LOCK:
            token_data = _TOKEN_STORE.get(token.token)

        if not token_data:
            raise AuthError(f"token not found: {token.token[:20]}...")

        async with _TOKEN_STORE_LOCK:
            _TOKEN_STORE[token.token]["revoked"] = True

        _log.info(f"LocalDevIdentityProvider: revoked token for {token_data['user_id']}")

    async def cleanup_expired_tokens(self) -> int:
        """Remove expired tokens from store (optional housekeeping).

        Returns:
            int: number of tokens cleaned up
        """
        async with _TOKEN_STORE_LOCK:
            now = time.time()
            expired = [
                k
                for k, v in _TOKEN_STORE.items()
                if now >= v.get("expires_at", 0) or v.get("revoked")
            ]
            for k in expired:
                del _TOKEN_STORE[k]
            return len(expired)

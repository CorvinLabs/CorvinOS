"""FastAPI Middleware for RBAC Integration (Phase C k=5).

Middleware flow:
  1. Extract token from request (Authorization header or cookie)
  2. Call identity_provider.resolve_user(token)
  3. Attach user to request.state.user
  4. Routes use request.state.user for permission checks

Usage in routes:
  @router.post("/tasks")
  async def create_task(
      request: Request,
      body: TaskCreateRequest,
      policy: RBACPolicy = Depends(get_rbac_policy),
  ) -> TaskResponse:
      user = request.state.user
      await policy.require_permission(user, Permission.TASK_CREATE)
      # create task...
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from fastapi import Request, HTTPException, Depends
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .identity import IdentityProvider, IdentityToken, AuthError, AuthorizationError, User
from .policy import RBACPolicy, Permission

_log = logging.getLogger(__name__)


class RBACMiddleware(BaseHTTPMiddleware):
    """Extract user from token and attach to request.state."""

    def __init__(self, app, identity_provider: IdentityProvider):
        super().__init__(app)
        self.identity_provider = identity_provider

    async def dispatch(self, request: Request, call_next):
        """Extract token, resolve user, attach to request.state."""
        request.state.user = None

        # Extract token from Authorization header or cookie
        token_string = self._extract_token(request)

        if not token_string:
            # No token — proceed (routes check permission individually)
            return await call_next(request)

        try:
            # Resolve user from token
            token = IdentityToken(token=token_string)
            user = await self.identity_provider.resolve_user(token)
            request.state.user = user
            _log.debug(f"RBACMiddleware: resolved user {user.user_id}")

        except AuthError as e:
            _log.warning(f"RBACMiddleware: auth error: {e}")
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized (invalid or expired token)"},
            )

        except Exception as e:
            _log.error(f"RBACMiddleware: unexpected error: {e}")
            return JSONResponse(
                status_code=500,
                content={"detail": "Internal server error"},
            )

        return await call_next(request)

    def _extract_token(self, request: Request) -> Optional[str]:
        """Extract token from Authorization header or cookie."""
        # Try Authorization header first
        auth_header = request.headers.get("Authorization", "").strip()
        if auth_header.startswith("Bearer "):
            return auth_header[7:]  # "Bearer {token}"

        # TODO: Try session cookie (corvin_console_sid)
        # For now, only Authorization header is supported

        return None


def get_rbac_policy(request: Request) -> RBACPolicy:
    """FastAPI dependency to get RBAC policy.

    Usage:
      @router.post("/tasks")
      async def create_task(
          request: Request,
          policy: RBACPolicy = Depends(get_rbac_policy),
      ):
          user = request.state.user
          await policy.require_permission(user, Permission.TASK_CREATE)
    """
    # TODO: Load policy from config or inject from app state
    return RBACPolicy()


async def require_user(request: Request) -> User:
    """FastAPI dependency to require authenticated user.

    Raises HTTPException(401) if no user in request.state.

    Usage:
      @router.post("/tasks")
      async def create_task(
          user: User = Depends(require_user),
      ):
          # user is guaranteed to be set
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user


async def require_permission(
    permission: Permission,
) -> Callable[[Request], None]:
    """FastAPI dependency to require specific permission.

    Usage:
      @router.post("/tasks")
      async def create_task(
          request: Request,
          _: None = Depends(require_permission(Permission.TASK_CREATE)),
      ):
          # permission is guaranteed or 403 raised
    """

    async def _check(request: Request, policy: RBACPolicy = Depends(get_rbac_policy)) -> None:
        user = getattr(request.state, "user", None)
        if not user:
            raise HTTPException(status_code=401, detail="Unauthorized")

        try:
            await policy.require_permission(user, permission)
        except AuthorizationError as e:
            _log.warning(f"Permission denied: {e}")
            raise HTTPException(status_code=403, detail="Forbidden") from e

    return _check

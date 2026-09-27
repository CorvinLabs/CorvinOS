"""RBAC Policy Enforcement (Phase C k=5).

Generic permission system independent of identity implementation.
Role → Permission mapping stored in config (role -> [permissions]).
Every permission check is audited (GDPR Art. 30, 32).

Permission Format: "resource:action"
  - resource: task, approval, rollback, version, audit-trail
  - action: create, read, update, approve, reject, rollback

Roles (extensible):
  - admin: all permissions
  - reviewer: can approve/reject, read all
  - viewer: read-only
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .identity import User, AuthorizationError

_log = logging.getLogger(__name__)


class Permission(str, Enum):
    """Permission names (resource:action format)."""

    # Task permissions
    TASK_CREATE = "task:create"
    TASK_READ = "task:read"
    TASK_UPDATE = "task:update"
    TASK_DELETE = "task:delete"

    # Approval permissions
    APPROVAL_APPROVE = "approval:approve"
    APPROVAL_REJECT = "approval:reject"

    # Rollback permissions
    ROLLBACK_EXECUTE = "rollback:execute"
    ROLLBACK_READ = "rollback:read"

    # Audit permissions
    AUDIT_READ = "audit:read"
    AUDIT_EXPORT = "audit:export"


# Default role -> permissions mapping (extensible via config file or env)
_DEFAULT_RBAC = {
    "admin": [
        Permission.TASK_CREATE,
        Permission.TASK_READ,
        Permission.TASK_UPDATE,
        Permission.TASK_DELETE,
        Permission.APPROVAL_APPROVE,
        Permission.APPROVAL_REJECT,
        Permission.ROLLBACK_EXECUTE,
        Permission.ROLLBACK_READ,
        Permission.AUDIT_READ,
        Permission.AUDIT_EXPORT,
    ],
    "reviewer": [
        Permission.TASK_READ,
        Permission.TASK_UPDATE,
        Permission.APPROVAL_APPROVE,
        Permission.APPROVAL_REJECT,
        Permission.ROLLBACK_READ,
        Permission.AUDIT_READ,
    ],
    "viewer": [
        Permission.TASK_READ,
        Permission.AUDIT_READ,
    ],
}


@dataclass(frozen=True)
class PermissionCheckContext:
    """Audit context for permission checks.

    Fields:
      resource: str — what is being accessed (task, approval, rollback, etc.)
      action: str — what operation (create, approve, delete, etc.)
      task_id: Optional[str] — which task (for audit trail)
      reason: Optional[str] — why check is happening (audit detail)
    """

    resource: str
    action: str
    task_id: Optional[str] = None
    reason: Optional[str] = None


class RBACPolicy:
    """Permission enforcement engine.

    Independent of identity implementation (works with any IdentityProvider).
    Every permission check is audited.
    """

    def __init__(self, role_permissions: Optional[dict[str, list[Permission]]] = None):
        """Initialize RBAC policy.

        Args:
            role_permissions: role -> [Permission] mapping
                            (default: _DEFAULT_RBAC)
        """
        self.role_permissions = role_permissions or _DEFAULT_RBAC

    async def check_permission(
        self,
        user: User,
        permission: Permission,
        context: Optional[PermissionCheckContext] = None,
    ) -> bool:
        """Check if user has permission.

        Args:
            user: User with roles
            permission: Permission to check
            context: PermissionCheckContext for audit

        Returns:
            bool: True if user has permission, False otherwise

        Raises:
            AuthorizationError: if permission denied (depending on strict mode)
        """
        context = context or PermissionCheckContext(
            resource=permission.split(":")[0],
            action=permission.split(":")[1],
        )

        # Check if user has permission
        has_perm = False
        for role in user.roles:
            if permission in self.role_permissions.get(role, []):
                has_perm = True
                break

        # Audit permission check
        await self._audit_permission_check(
            user=user,
            permission=permission,
            context=context,
            granted=has_perm,
        )

        return has_perm

    async def require_permission(
        self,
        user: User,
        permission: Permission,
        context: Optional[PermissionCheckContext] = None,
    ) -> None:
        """Require permission; raise if denied.

        Args:
            user: User with roles
            permission: Permission to require
            context: PermissionCheckContext for audit

        Raises:
            AuthorizationError: if permission denied
        """
        granted = await self.check_permission(user, permission, context)
        if not granted:
            resource, action = permission.split(":")
            raise AuthorizationError(
                f"user {user.user_id} denied {resource}:{action} "
                f"(roles: {user.roles})"
            )

    async def _audit_permission_check(
        self,
        user: User,
        permission: Permission,
        context: PermissionCheckContext,
        granted: bool,
    ) -> None:
        """Audit a permission check.

        TODO: Integrate with core/task_tracking/audit.py
        Currently logs; should emit AuditEvent.
        """
        resource, action = permission.split(":")
        status = "granted" if granted else "denied"

        _log.info(
            f"permission_check: user={user.user_id} tenant={user.tenant_id} "
            f"{resource}:{action} {status} (roles={user.roles})"
        )

    def get_user_permissions(self, user: User) -> set[Permission]:
        """Get all permissions user has.

        Args:
            user: User with roles

        Returns:
            set[Permission]: all permissions granted to user
        """
        permissions = set()
        for role in user.roles:
            permissions.update(self.role_permissions.get(role, []))
        return permissions

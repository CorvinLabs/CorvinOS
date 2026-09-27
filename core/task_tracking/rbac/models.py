"""RBAC Models: Role and RoleAssignment dataclasses (Phase C k=5).

For future: persist role definitions and assignments to database.
Currently in-memory; can be extended to SQLite via tasks.db.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .policy import Permission


@dataclass(frozen=True)
class Role:
    """Role definition (immutable).

    Fields:
      name: str — role identifier (admin, reviewer, viewer, etc.)
      description: str — human-readable role description
      permissions: list[Permission] — permissions granted to this role
      created_at: str — ISO-8601 creation timestamp
      updated_at: str — ISO-8601 last update timestamp
    """

    name: str
    description: str
    permissions: list[Permission]
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self):
        if not self.name or len(self.name) > 64:
            raise ValueError("role name required, max 64 chars")
        if not isinstance(self.permissions, list) or not self.permissions:
            raise ValueError("permissions must be non-empty list")


@dataclass(frozen=True)
class RoleAssignment:
    """Assignment of role to user (tenant-scoped).

    Fields:
      user_id: str — user identifier
      role_name: str — role to assign
      tenant_id: str — tenant scope (GDPR Art. 5)
      assigned_by: str — who assigned the role (for audit)
      assigned_at: str — ISO-8601 assignment timestamp
      expires_at: Optional[str] — when assignment expires (optional, for temp access)
    """

    user_id: str
    role_name: str
    tenant_id: str
    assigned_by: str
    assigned_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    expires_at: Optional[str] = None

    def __post_init__(self):
        if not self.user_id or len(self.user_id) > 256:
            raise ValueError("user_id required, max 256 chars")
        if not self.role_name or len(self.role_name) > 64:
            raise ValueError("role_name required, max 64 chars")
        if not self.tenant_id or len(self.tenant_id) > 64:
            raise ValueError("tenant_id required (GDPR Art. 5), max 64 chars")
        if not self.assigned_by or len(self.assigned_by) > 256:
            raise ValueError("assigned_by required, max 256 chars")

    def is_active(self) -> bool:
        """Check if assignment is still active (not expired)."""
        if not self.expires_at:
            return True
        now = datetime.now(timezone.utc).isoformat()
        return now < self.expires_at

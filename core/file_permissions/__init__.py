"""File Permission Hardener — ADR-0295

Fine-grained file-write protection with fail-closed semantics.
Integrates with L10 Path-Gate and audit trail for comprehensive access control.
"""

from .manager import (
    FilePermissionManager,
    PermissionDenied,
    PermissionType,
    PermissionRule,
)

__all__ = [
    "FilePermissionManager",
    "PermissionDenied",
    "PermissionType",
    "PermissionRule",
]

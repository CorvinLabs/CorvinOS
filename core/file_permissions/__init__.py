"""
File Permission Hardener — ADR-0295

Fail-closed file-write protection. No file writes outside permitted zones.
Integrates with L10 path-gate.
"""

from core.file_permissions.hardener import (
    HARDENER,
    PermissionError,
    PermissionHardener,
    allow_zone,
    check_write,
    is_write_allowed,
)

__all__ = [
    "PermissionHardener",
    "PermissionError",
    "HARDENER",
    "check_write",
    "is_write_allowed",
    "allow_zone",
]

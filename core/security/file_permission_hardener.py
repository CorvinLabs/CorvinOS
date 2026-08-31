"""File Permission Hardener — fail-closed file-write protection (ADR-0295).

Layer between L10 path-gate and filesystem operations.
Validates writes against operator's declared directory set.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional, Set

logger = logging.getLogger(__name__)


class PermissionDeniedError(Exception):
    """Raised when write permission denied."""
    pass


class OperationMode(str, Enum):
    """File operation type."""
    READ = "read"
    WRITE = "write"
    APPEND = "append"
    DELETE = "delete"
    CREATE_DIR = "create_dir"


@dataclass(frozen=True)
class FileOperation:
    """A file operation request."""
    path: Path
    mode: OperationMode
    tenant_id: str = "_default"
    user_id: Optional[str] = None


@dataclass(frozen=True)
class PermissionResult:
    """Result of permission check."""
    allowed: bool
    reason: Optional[str] = None


class FilePermissionHardener:
    """Fail-closed file-write protection.

    Validates writes against operator's declared directory set.
    Rejects any write outside allowed boundaries.
    """

    def __init__(self, tenant_id: str = "_default"):
        """Initialize hardener.

        Args:
            tenant_id: Tenant for isolation
        """
        self.tenant_id = tenant_id
        # Allowed directories per tenant
        self.allowed_dirs: dict[str, Set[Path]] = {tenant_id: set()}
        # Audit log
        self.audit_log: list[FileOperation] = []
        self._lock = __import__("threading").RLock()

    def register_allowed_directory(self, path: Path | str) -> None:
        """Register a directory as allowed for writes.

        Args:
            path: Directory to allow

        Should be called during initialization by operator.
        """
        with self._lock:
            path = Path(path).resolve()

            if path not in self.allowed_dirs[self.tenant_id]:
                self.allowed_dirs[self.tenant_id].add(path)
                logger.info(f"Registered allowed directory: {path}")

    def check_write_permission(self, path: Path | str) -> PermissionResult:
        """Check if write to path is allowed.

        Fail-closed: any doubt results in rejection.

        Args:
            path: Path to write to

        Returns:
            PermissionResult(allowed=True/False, reason if denied)
        """
        try:
            path = Path(path).resolve()
        except Exception as e:
            logger.error(f"Failed to resolve path: {e}")
            return PermissionResult(
                allowed=False,
                reason=f"Path resolution failed: {e}"
            )

        with self._lock:
            # Check if path is within any allowed directory
            for allowed_dir in self.allowed_dirs[self.tenant_id]:
                try:
                    path.relative_to(allowed_dir)
                    # Success: path is within allowed_dir
                    return PermissionResult(allowed=True)
                except ValueError:
                    # Not within this directory, continue
                    continue

            # Path not within any allowed directory
            allowed_list = ", ".join(str(d) for d in self.allowed_dirs[self.tenant_id])
            return PermissionResult(
                allowed=False,
                reason=f"Path outside allowed directories ({allowed_list})"
            )

    def check_read_permission(self, path: Path | str) -> PermissionResult:
        """Check if read from path is allowed.

        For now, allows all reads (can be restricted later).

        Args:
            path: Path to read from

        Returns:
            PermissionResult
        """
        # Read permissions are less strict than write
        # Could be enhanced to check ownership, ACLs, etc.
        try:
            path = Path(path).resolve()
            return PermissionResult(allowed=True)
        except Exception as e:
            logger.error(f"Failed to resolve path: {e}")
            return PermissionResult(
                allowed=False,
                reason=f"Path resolution failed: {e}"
            )

    def check_delete_permission(self, path: Path | str) -> PermissionResult:
        """Check if delete is allowed.

        Delete permissions = write permissions to parent + the file.

        Args:
            path: Path to delete

        Returns:
            PermissionResult
        """
        try:
            path = Path(path).resolve()
        except Exception as e:
            logger.error(f"Failed to resolve path: {e}")
            return PermissionResult(
                allowed=False,
                reason=f"Path resolution failed: {e}"
            )

        # Check if we can write to parent directory and the path itself
        with self._lock:
            # Check parent directory
            parent = path.parent
            can_write_parent = any(
                parent >= dir or str(parent).startswith(str(dir))
                for dir in self.allowed_dirs[self.tenant_id]
            )

            if not can_write_parent:
                return PermissionResult(
                    allowed=False,
                    reason=f"Parent directory {parent} not in allowed set"
                )

            # Check path itself (should be within allowed)
            can_delete_path = any(
                parent == dir or path.parent == dir
                for dir in self.allowed_dirs[self.tenant_id]
            )

            if can_delete_path:
                return PermissionResult(allowed=True)

            return PermissionResult(
                allowed=False,
                reason=f"File {path} not in allowed set"
            )

    def check_operation(self, operation: FileOperation) -> PermissionResult:
        """Check if operation is allowed.

        Dispatches to specific checker based on operation type.

        Args:
            operation: FileOperation to check

        Returns:
            PermissionResult
        """
        with self._lock:
            # Log operation
            self.audit_log.append(operation)

        if operation.mode == OperationMode.READ:
            return self.check_read_permission(operation.path)
        elif operation.mode == OperationMode.WRITE:
            return self.check_write_permission(operation.path)
        elif operation.mode == OperationMode.APPEND:
            return self.check_write_permission(operation.path)
        elif operation.mode == OperationMode.DELETE:
            return self.check_delete_permission(operation.path)
        elif operation.mode == OperationMode.CREATE_DIR:
            return self.check_write_permission(operation.path)
        else:
            return PermissionResult(
                allowed=False,
                reason=f"Unknown operation mode: {operation.mode}"
            )

    def assert_permission(self, path: Path | str, mode: OperationMode) -> None:
        """Assert permission or raise PermissionDeniedError.

        Fail-closed: raises on denial.

        Args:
            path: Path to check
            mode: Operation type

        Raises:
            PermissionDeniedError: if not allowed
        """
        operation = FileOperation(
            path=Path(path),
            mode=mode,
            tenant_id=self.tenant_id
        )

        result = self.check_operation(operation)

        if not result.allowed:
            logger.warning(f"Permission denied: {result.reason}")
            raise PermissionDeniedError(result.reason or f"Permission denied for {path}")

        logger.debug(f"Permission granted: {path} ({mode})")

    def get_audit_log(self) -> list[FileOperation]:
        """Get audit log of all operations.

        Returns:
            List of FileOperation records
        """
        with self._lock:
            return list(self.audit_log)

    def clear_audit_log(self) -> None:
        """Clear audit log."""
        with self._lock:
            self.audit_log.clear()

    def get_allowed_directories(self) -> Set[Path]:
        """Get currently allowed directories.

        Returns:
            Set of allowed Path objects
        """
        with self._lock:
            return set(self.allowed_dirs[self.tenant_id])


# Global hardener instances per tenant
_hardeners: dict[str, FilePermissionHardener] = {}
_hardener_lock = __import__("threading").RLock()


def get_hardener(tenant_id: str = "_default") -> FilePermissionHardener:
    """Get or create hardener for tenant.

    Args:
        tenant_id: Tenant ID

    Returns:
        FilePermissionHardener instance
    """
    with _hardener_lock:
        if tenant_id not in _hardeners:
            _hardeners[tenant_id] = FilePermissionHardener(tenant_id)

        return _hardeners[tenant_id]


def reset_hardeners() -> None:
    """Reset all hardener instances (for testing)."""
    global _hardeners
    with _hardener_lock:
        _hardeners.clear()

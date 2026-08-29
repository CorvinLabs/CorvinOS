"""Platform-specific file permission setup (Windows ACL + Unix chmod).

Ensures secure file permissions across platforms:
  - Unix/Linux/macOS: chmod(0o600) for sensitive files
  - Windows: NTFS ACL restriction (documents best-effort approach)

Follows audit.py pattern: try platform-specific import, degrade gracefully on
ImportError. Windows has no fcntl equivalent; we use os.chmod() which is a no-op
but documents intent.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional


# Platform detection
_IS_WINDOWS = sys.platform == "win32"


def _setup_unix_permissions(path: Path, mode: int = 0o600) -> bool:
    """Set Unix-style permissions on file (chmod).

    Args:
        path: File or directory path
        mode: Permission mode (default 0o600 for user-read-write only)

    Returns:
        True if successful, False if path doesn't exist or operation failed
    """
    if not path.exists():
        return False

    try:
        os.chmod(path, mode)
        return True
    except OSError:
        return False


def _setup_windows_permissions(path: Path) -> bool:
    """Set Windows NTFS ACL permissions on file.

    Attempts to restrict file to current user only via NTFS ACL.
    On non-Windows platforms or if win32com is unavailable, returns False
    (operation is degraded, not critical).

    Args:
        path: File or directory path

    Returns:
        True if ACL was modified, False if unavailable/degraded
    """
    if not _IS_WINDOWS:
        return False

    if not path.exists():
        return False

    try:
        import win32security  # type: ignore[import]
        import ntsecuritycon  # type: ignore[import]

        # Get current user's SID
        import win32api  # type: ignore[import]
        user = win32api.GetUserName()
        domain = win32api.GetDomainName()
        full_user = f"{domain}\\{user}" if domain else user

        # Resolve SID
        sid = win32security.LookupAccountName(None, full_user)[0]

        # Set ACL: owner full control, others denied
        dacl = win32security.ACL()
        dacl.AddAccessAllowedAce(
            win32security.ACL_REVISION,
            ntsecuritycon.FILE_ALL_ACCESS,
            sid
        )

        # Apply to file
        win32security.SetFileSecurity(
            str(path),
            win32security.DACL_SECURITY_INFORMATION,
            dacl
        )
        return True
    except (ImportError, Exception):
        # win32security not available or operation failed
        return False


def setup_file_permissions(
    path: Path,
    mode: int = 0o600,
    require_success: bool = False,
) -> bool:
    """Set secure file permissions (platform-aware).

    Attempts to restrict file to current user only:
      - Unix/Linux/macOS: os.chmod(mode)
      - Windows: NTFS ACL via win32security (graceful degrade if unavailable)

    Args:
        path: File or directory path
        mode: Unix permission mode (ignored on Windows)
        require_success: If True, raise on failure; if False, log and return False

    Returns:
        True if permissions were successfully set, False otherwise

    Raises:
        RuntimeError: If require_success=True and operation failed
    """
    if not path.exists():
        if require_success:
            raise RuntimeError(f"File does not exist: {path}")
        return False

    # Try platform-specific approach
    if _IS_WINDOWS:
        success = _setup_windows_permissions(path) or _setup_unix_permissions(path, mode)
    else:
        success = _setup_unix_permissions(path, mode)

    if not success and require_success:
        raise RuntimeError(f"Failed to set permissions on {path}")

    return success


def setup_audit_file_permissions(audit_path: Path) -> bool:
    """Set strict permissions on audit trail file (read-write owner only).

    The audit file must not be world-readable (contains operational data).
    This function ensures 0o600 on Unix and attempts ACL on Windows.

    Args:
        audit_path: Path to audit.jsonl file

    Returns:
        True if permissions set successfully
    """
    return setup_file_permissions(audit_path, mode=0o600, require_success=False)


def setup_socket_directory_permissions(socket_dir: Path) -> bool:
    """Set secure permissions on daemon socket directory.

    Socket directory must be restricted to owner (daemon socket inside).
    Uses 0o700 (read-write-execute owner only) to allow file creation inside.

    Args:
        socket_dir: Path to directory containing daemon sockets

    Returns:
        True if permissions set successfully
    """
    return setup_file_permissions(socket_dir, mode=0o700, require_success=False)


def setup_corvin_home_permissions(corvin_home: Path) -> dict[str, bool]:
    """Initialize secure permissions on all critical corvin_home files.

    Sets up permissions for:
      - corvin_home/audit.jsonl (0o600)
      - corvin_home/run/ directory (0o700)
      - corvin_home/tenants/*/audit.jsonl (0o600)

    Args:
        corvin_home: Path to ~/.corvin home directory

    Returns:
        Dict mapping file paths to permission-setup success status
    """
    results = {}

    # Audit file in root
    audit_file = corvin_home / "audit.jsonl"
    if audit_file.exists():
        results[str(audit_file)] = setup_audit_file_permissions(audit_file)

    # Run directory (socket/daemon)
    run_dir = corvin_home / "run"
    if run_dir.exists():
        results[str(run_dir)] = setup_socket_directory_permissions(run_dir)

    # Tenant audit files
    tenants_dir = corvin_home / "tenants"
    if tenants_dir.exists():
        for tenant_dir in tenants_dir.iterdir():
            if not tenant_dir.is_dir():
                continue
            tenant_audit = tenant_dir / "audit.jsonl"
            if tenant_audit.exists():
                results[str(tenant_audit)] = setup_audit_file_permissions(tenant_audit)

    return results

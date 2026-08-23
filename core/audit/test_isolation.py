"""Test audit isolation — prevent test writes from polluting production audit chain (ADR-0328)."""

import os
from pathlib import Path
from contextvars import ContextVar
from typing import Optional


# Context variable to track if we're in a test
_test_mode: ContextVar[bool] = ContextVar("audit_test_mode", default=False)
_test_audit_root: ContextVar[Optional[Path]] = ContextVar("test_audit_root", default=None)


def enable_test_audit_isolation(test_root: Optional[Path] = None) -> None:
    """Enable test audit isolation (redirect writes to test directory).

    Args:
        test_root: Test-specific audit root (default: /tmp/corvin_test_audit)
    """
    if test_root is None:
        test_root = Path("/tmp/corvin_test_audit")

    test_root.mkdir(parents=True, exist_ok=True)
    _test_mode.set(True)
    _test_audit_root.set(test_root)


def disable_test_audit_isolation() -> None:
    """Disable test audit isolation (restore production writes)."""
    _test_mode.set(False)
    _test_audit_root.set(None)


def is_test_mode() -> bool:
    """Check if audit isolation is enabled."""
    return _test_mode.get()


def get_test_audit_root() -> Optional[Path]:
    """Get test audit root directory."""
    return _test_audit_root.get()


def should_isolate_audit_write(file_path: Path) -> bool:
    """Determine if this audit write should be redirected to test directory.

    Args:
        file_path: Original audit file path

    Returns:
        True if write should be isolated (redirected to test dir)
    """
    if not is_test_mode():
        return False

    # Don't isolate if explicitly marked as production
    if os.environ.get("AUDIT_PRODUCTION_WRITE") == "1":
        return False

    return True


def redirect_audit_path_if_test(original_path: Path) -> Path:
    """Redirect audit path to test directory if in test mode.

    Args:
        original_path: Original path (e.g., ~/.corvin/audit.jsonl)

    Returns:
        Redirected path (if in test mode) or original (if production)
    """
    if not should_isolate_audit_write(original_path):
        return original_path

    test_root = get_test_audit_root()
    if not test_root:
        return original_path

    # Preserve relative structure under test root
    # E.g., ~/.corvin/audit.jsonl → /tmp/corvin_test_audit/audit.jsonl
    filename = original_path.name
    return test_root / filename

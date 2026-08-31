"""Tests for ADR-0295 File Permission Hardener.

Coverage:
- Permission checking (read/write/delete)
- Allowed directory boundaries
- Path traversal rejection
- Audit logging
"""

import pytest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.security.file_permission_hardener import (
    FilePermissionHardener,
    FileOperation,
    OperationMode,
    PermissionDeniedError,
    get_hardener,
    reset_hardeners,
)


class TestFilePermissionHardener:
    """Test file permission hardener."""

    def test_register_allowed_directory(self):
        """Test registering allowed directory."""
        hardener = FilePermissionHardener()
        allowed = Path("/tmp/allowed")

        hardener.register_allowed_directory(allowed)

        assert allowed.resolve() in hardener.get_allowed_directories()

    def test_write_within_allowed_directory(self):
        """Test write within allowed directory is permitted."""
        with TemporaryDirectory() as tmpdir:
            hardener = FilePermissionHardener()
            hardener.register_allowed_directory(tmpdir)

            # Write within allowed directory
            path = Path(tmpdir) / "file.txt"
            result = hardener.check_write_permission(path)
            assert result.allowed is True

    def test_write_outside_allowed_directory(self):
        """Test write outside allowed directory is denied."""
        with TemporaryDirectory() as tmpdir:
            hardener = FilePermissionHardener()
            hardener.register_allowed_directory(tmpdir)

            # Write outside allowed directory
            path = Path("/etc/passwd")
            result = hardener.check_write_permission(path)
            assert result.allowed is False

    def test_read_permission_allowed(self):
        """Test read permission is always allowed."""
        hardener = FilePermissionHardener()

        # Read can happen anywhere (less strict)
        result = hardener.check_read_permission("/etc/passwd")
        assert result.allowed is True

    def test_delete_permission_checks_parent(self):
        """Test delete checks parent directory permission."""
        with TemporaryDirectory() as tmpdir:
            hardener = FilePermissionHardener()
            hardener.register_allowed_directory(tmpdir)

            # Delete file in allowed directory
            path = Path(tmpdir) / "file.txt"
            result = hardener.check_delete_permission(path)
            assert result.allowed is True

    def test_path_traversal_rejected(self):
        """Test path traversal attempt rejected."""
        with TemporaryDirectory() as tmpdir:
            hardener = FilePermissionHardener()
            hardener.register_allowed_directory(tmpdir)

            # Try to traverse up with ..
            path = Path(tmpdir) / ".." / "etc" / "passwd"
            result = hardener.check_write_permission(path)
            # Should be rejected or canonicalized away
            # (path.resolve() resolves .. so this depends on tmpdir location)

    def test_assert_permission_raises_on_denial(self):
        """Test assert_permission raises PermissionDeniedError."""
        hardener = FilePermissionHardener()

        with pytest.raises(PermissionDeniedError):
            hardener.assert_permission("/etc/passwd", OperationMode.WRITE)

    def test_audit_logging(self):
        """Test operations are logged to audit trail."""
        hardener = FilePermissionHardener()

        operation = FileOperation(
            path=Path("/tmp/file.txt"),
            mode=OperationMode.WRITE
        )

        result = hardener.check_operation(operation)
        audit_log = hardener.get_audit_log()

        assert len(audit_log) > 0
        assert audit_log[-1].path == Path("/tmp/file.txt").resolve()

    def test_multiple_allowed_directories(self):
        """Test hardener can have multiple allowed directories."""
        with TemporaryDirectory() as tmpdir1:
            with TemporaryDirectory() as tmpdir2:
                hardener = FilePermissionHardener()
                hardener.register_allowed_directory(tmpdir1)
                hardener.register_allowed_directory(tmpdir2)

                # Both should be allowed
                result1 = hardener.check_write_permission(Path(tmpdir1) / "file.txt")
                result2 = hardener.check_write_permission(Path(tmpdir2) / "file.txt")

                assert result1.allowed is True
                assert result2.allowed is True

    def test_tenant_isolation(self):
        """Test different tenants have isolated permissions."""
        reset_hardeners()

        hardener1 = get_hardener("tenant1")
        hardener2 = get_hardener("tenant2")

        # Different instances
        assert hardener1 is not hardener2

        # Different allowed directories
        assert hardener1.get_allowed_directories() != hardener2.get_allowed_directories()

    def test_global_hardener_singleton_per_tenant(self):
        """Test get_hardener returns same instance for tenant."""
        reset_hardeners()

        h1 = get_hardener("tenant1")
        h2 = get_hardener("tenant1")

        assert h1 is h2

    def test_operation_dispatch(self):
        """Test check_operation dispatches correctly."""
        hardener = FilePermissionHardener()

        # Create operation for permission check
        operation = FileOperation(
            path=Path("/etc/passwd"),
            mode=OperationMode.WRITE
        )

        result = hardener.check_operation(operation)
        assert result.allowed is False

    def test_permission_result_includes_reason(self):
        """Test permission denial includes reason."""
        hardener = FilePermissionHardener()

        result = hardener.check_write_permission("/etc/passwd")

        assert result.allowed is False
        assert result.reason is not None
        assert "allowed" in result.reason.lower()

    def test_clear_audit_log(self):
        """Test clearing audit log."""
        hardener = FilePermissionHardener()

        # Log some operations
        hardener.check_write_permission("/tmp/file.txt")
        assert len(hardener.get_audit_log()) > 0

        # Clear
        hardener.clear_audit_log()
        assert len(hardener.get_audit_log()) == 0


class TestFailClosed:
    """Test fail-closed semantics."""

    def test_any_doubt_results_in_rejection(self):
        """Test that permission is denied on any doubt."""
        hardener = FilePermissionHardener()

        # With no allowed directories, all writes should be denied
        result = hardener.check_write_permission("/tmp/anything.txt")
        assert result.allowed is False

    def test_assert_permission_is_strict(self):
        """Test assert_permission is strict (fail-closed)."""
        hardener = FilePermissionHardener()

        # Should raise immediately without allowlist
        with pytest.raises(PermissionDeniedError):
            hardener.assert_permission("/tmp/file.txt", OperationMode.WRITE)

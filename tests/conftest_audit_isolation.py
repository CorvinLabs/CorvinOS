"""Pytest conftest addon — auto-isolate audit writes in tests (drop into tests/conftest.py)."""

import pytest
from pathlib import Path
from core.audit.test_isolation import enable_test_audit_isolation, disable_test_audit_isolation


@pytest.fixture(scope="session", autouse=True)
def audit_isolation_session():
    """Enable test audit isolation for entire session."""
    test_root = Path("/tmp/pytest_audit_isolation")
    enable_test_audit_isolation(test_root)
    yield
    disable_test_audit_isolation()


@pytest.fixture(autouse=True)
def audit_isolation_cleanup(tmp_path):
    """Cleanup test audit dir after each test."""
    yield
    # Optionally clear test audit dir between tests
    import shutil
    test_audit = Path("/tmp/pytest_audit_isolation")
    if test_audit.exists():
        # Keep for diagnostics, just note that it shouldn't grow
        pass

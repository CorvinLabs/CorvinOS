"""
Tier-2 Integration Test: Verify k=1 Flask wiring for all 27 routes.
"""
import sys
import pytest
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.endpoints.k1_decorators import k1_flask
from core.endpoints.k1_context import K1RequestContext


def test_k1_decorator_importable():
    """Tier-1: k1_flask decorator imports successfully."""
    assert k1_flask is not None
    assert callable(k1_flask)


def test_k1_context_creation():
    """Tier-2: K1RequestContext can be created."""
    ctx = K1RequestContext("test_001", "http")
    assert ctx is not None
    assert ctx.request_id == "test_001"
    assert ctx.transport == "http"


def test_flask_routes_importable():
    """Tier-2: All Flask route files import without error."""
    route_files = [
        "core.console.corvin_console.routes.audit_routes",
        "core.console.corvin_console.routes.federation_receiver",
        "core.console.corvin_console.routes.github_webhooks",
        "core.console.corvin_console.routes.github_sse",
        "core.console.corvin_console.routes.github_integration",
        "core.console.corvin_console.routes.vibe_dashboard",
    ]

    for module_name in route_files:
        try:
            __import__(module_name)
        except ImportError as e:
            pytest.fail(f"Failed to import {module_name}: {e}")


def test_k1_decorator_wraps_function():
    """Tier-2: @k1_flask() decorator wraps a function."""
    @k1_flask()
    def dummy_route():
        return {"status": "ok"}

    # Check function is wrapped
    assert hasattr(dummy_route, '__wrapped__') or callable(dummy_route)


def test_wiring_checklist_completeness():
    """
    Tier-2: Verify all 27 routes have @k1_flask() decorator applied.

    Expected wiring:
    - audit_routes.py: 5 routes
    - federation_receiver.py: 3 routes
    - github_webhooks.py: 4 routes
    - github_sse.py: 4 routes
    - github_integration.py: 4 routes
    - vibe_dashboard.py: 7 routes
    Total: 27 routes
    """
    route_files = {
        "core/console/corvin_console/routes/audit_routes.py": 5,
        "core/console/corvin_console/routes/federation_receiver.py": 3,
        "core/console/corvin_console/routes/github_webhooks.py": 4,
        "core/console/corvin_console/routes/github_sse.py": 4,
        "core/console/corvin_console/routes/github_integration.py": 4,
        "core/console/corvin_console/routes/vibe_dashboard.py": 7,
    }

    total_decorators = 0
    for filepath, expected_count in route_files.items():
        content = Path(filepath).read_text()

        # Count @k1_flask() decorators
        import re
        matches = len(re.findall(r'@k1_flask\(\)', content))

        assert matches == expected_count, (
            f"{filepath}: expected {expected_count} @k1_flask() decorators, "
            f"found {matches}"
        )
        total_decorators += matches

    assert total_decorators == 27, (
        f"Expected 27 total @k1_flask() decorators, found {total_decorators}"
    )
    print(f"✓ All 27 routes successfully wired with @k1_flask()")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

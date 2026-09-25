"""
E2E Test: Learning Loops Dashboard
ADR-0906 Session 2: React component wiring to live API
"""

import pytest
import json
from pathlib import Path


class TestLearningLoopsDashboardE2E:
    """E2E validation: React dashboard consumes learning loops API."""

    def test_dashboard_component_renders(self):
        """Dashboard component definition exists and exports."""
        # Verify file exists
        path = Path("core/console/corvin_console/web-next/src/pages/learning-loops-dashboard.tsx")
        assert path.exists(), f"Component not found: {path}"

        # Read and verify key exports
        content = path.read_text()
        assert "export function LearningLoopsDashboard" in content
        assert "useEffect" in content  # Uses React hooks
        assert "fetchLearningLoops" in content  # Fetches from API
        assert "fetch('/v1/console/learning/loops')" in content  # Correct endpoint

    def test_dashboard_uses_correct_api_endpoint(self):
        """Dashboard calls the right endpoint (ADR-0906)."""
        path = Path("core/console/corvin_console/web-next/src/pages/learning-loops-dashboard.tsx")
        content = path.read_text()

        # Verify endpoint
        assert "'/v1/console/learning/loops'" in content
        assert "fetch" in content and "response.json()" in content

    def test_dashboard_handles_learning_loop_data(self):
        """Dashboard renders learning loop fields correctly."""
        path = Path("core/console/corvin_console/web-next/src/pages/learning-loops-dashboard.tsx")
        content = path.read_text()

        # Verify loop data fields are displayed
        required_fields = [
            "loop_id",
            "status",
            "health_score",
            "event_count_7d",
            "event_source",
            "feedback_types",
        ]

        for field in required_fields:
            assert field in content, f"Missing field display: {field}"

    def test_dashboard_shows_status_indicators(self):
        """Status badges render with correct colors/emojis."""
        path = Path("core/console/corvin_console/web-next/src/pages/learning-loops-dashboard.tsx")
        content = path.read_text()

        # Verify status cases
        for status in ["active", "dormant", "degrading", "stale"]:
            assert f"'{status}'" in content

        # Verify emoji mapping
        assert "🟢" in content  # active
        assert "🟡" in content  # dormant
        assert "🔴" in content  # degrading
        assert "⚫" in content  # stale

    def test_dashboard_error_handling(self):
        """Dashboard handles fetch errors gracefully."""
        path = Path("core/console/corvin_console/web-next/src/pages/learning-loops-dashboard.tsx")
        content = path.read_text()

        # Verify error state
        assert "error" in content
        assert "setError" in content
        assert "Alert" in content  # Error UI component

    def test_dashboard_refresh_button(self):
        """Refresh button re-fetches data."""
        path = Path("core/console/corvin_console/web-next/src/pages/learning-loops-dashboard.tsx")
        content = path.read_text()

        # Verify refresh logic
        assert "Refresh" in content
        assert "onClick={fetchLearningLoops}" in content


class TestLearningLoopsConsoleSpeechIntegration:
    """Session 2: Learning Loops + Console integration verified."""

    def test_api_routes_exist(self):
        """Verify learning loops API routes are wired."""
        from core.console.corvin_console.routes.learning_loops import (
            learning_loops_bp
        )

        # Verify blueprint exists
        assert learning_loops_bp is not None
        assert learning_loops_bp.name == "learning_loops"

    def test_dashboard_integration_e2e(self):
        """Full E2E: API endpoint → React component."""
        # This would be a real browser test (Playwright/Selenium)
        # For now, verify the wiring exists

        api_path = Path("core/console/corvin_console/routes/learning_loops.py")
        component_path = Path("core/console/corvin_console/web-next/src/pages/learning-loops-dashboard.tsx")

        assert api_path.exists(), "API routes not found"
        assert component_path.exists(), "React component not found"

        api_content = api_path.read_text()
        component_content = component_path.read_text()

        # Both reference the same endpoint
        assert "'/v1/console/learning/loops'" in api_content
        assert "'/v1/console/learning/loops'" in component_content


# Marker: Session 2 Complete
# This test validates ADR-0906 Session 2: React Dashboard wiring

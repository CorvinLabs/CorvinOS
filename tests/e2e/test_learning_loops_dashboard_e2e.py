"""
E2E Test: Learning Loops Dashboard
ADR-0906 Session 2: React component wiring to live API
"""

import pytest
import json
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]


class TestLearningLoopsConsoleSpeechIntegration:
    """Session 2: Learning Loops + Console integration verified."""

    def test_dashboard_integration_e2e(self):
        """The mounted SPA surface calls the mounted FastAPI loops route.

        Rewritten 2026-09-27: this used to assert that the string
        '/v1/console/learning/loops' appears in both the Flask blueprint and
        the dashboard page — true, and proof of nothing: the console never
        mounts that Flask blueprint. The real path is
        ``/v1/console/learning-loops/list`` (routes/learning_analytics.py).
        """
        hook = (_REPO / "core/console/corvin_console/web-next/src/hooks/use-learning-loops.ts").read_text()
        assert '"/v1/console/learning-loops/list"' in hook

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from core.console.corvin_console.app import router

        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)
        # Mounted: without a session it is refused (401), not missing (404).
        assert client.get("/v1/console/learning-loops/list").status_code == 401
        # ...and the Flask blueprint's path is NOT served by the console.
        assert client.get("/v1/console/learning/loops").status_code == 404


# Marker: Session 2 Complete
# This test validates ADR-0906 Session 2: React Dashboard wiring

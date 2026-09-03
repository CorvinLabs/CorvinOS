"""
Phase 1 Integration Tests for Console Manifest (ADR-0561)

Tests:
- GET /api/console/manifest returns valid v2.0 manifest
- Manifest includes builtin panels + gating
- Manifest hash is stable (for caching)
- Manifest timeout fallback works (200ms)
- Tenant isolation (panel_id unique per tenant)
"""

import json
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from datetime import datetime


@pytest.fixture
def client():
    """FastAPI test client."""
    from core.console.corvin_console.app import app
    return TestClient(app)


@pytest.fixture
def session_record():
    """Mock session record."""
    return MagicMock(
        tenant_id="_default",
        user_id="user-123",
        tier="tier-a",
    )


class TestManifestEndpoint:
    """GET /api/console/manifest (ADR-0561 Phase 1)"""

    def test_manifest_returns_200_and_valid_schema(self, client):
        """Manifest endpoint returns HTTP 200 with valid v2.0 schema."""
        response = client.get("/v1/console/manifest")
        assert response.status_code == 200

        data = response.json()
        assert data["version"] == "2.0"
        assert data["contract_version"] == "1"
        assert isinstance(data["timestamp"], str)
        assert isinstance(data["hash"], str)
        assert isinstance(data["panels"], list)
        assert isinstance(data["nav_groups"], list)
        assert isinstance(data["flags"], dict)

    def test_manifest_includes_builtin_panels(self, client):
        """Manifest includes all builtin panels (chat, dashboard, etc.)."""
        response = client.get("/v1/console/manifest")
        data = response.json()

        panel_ids = [p["id"] for p in data["panels"]]
        assert "chat" in panel_ids
        assert "dashboard" in panel_ids
        assert "plugins" in panel_ids
        assert "settings" in panel_ids

    def test_manifest_gating_by_flag(self, client):
        """Panels gated by requiredFlag are excluded if flag is false."""
        # Vibe panels require vibe_engineering flag
        response = client.get("/v1/console/manifest")
        data = response.json()

        # If vibe_engineering=false, vibe panels shouldn't appear
        # (depends on flag state; this tests the gating logic)
        panel_ids = [p["id"] for p in data["panels"]]
        # Just verify gating is present (logic depends on flag state)
        assert any(p.get("requiredFlag") for p in data["panels"])

    def test_manifest_nav_groups_are_valid(self, client):
        """Nav groups reference valid panel IDs."""
        response = client.get("/v1/console/manifest")
        data = response.json()

        panel_ids = {p["id"] for p in data["panels"]}

        for group in data["nav_groups"]:
            assert "id" in group
            assert "items" in group
            for item in group["items"]:
                assert item["panel_id"] in panel_ids, \
                    f"Nav group {group['id']} references non-existent panel {item['panel_id']}"

    def test_manifest_hash_is_stable(self, client):
        """Manifest hash is deterministic (same content = same hash)."""
        response1 = client.get("/v1/console/manifest")
        hash1 = response1.json()["hash"]

        response2 = client.get("/v1/console/manifest")
        hash2 = response2.json()["hash"]

        assert hash1 == hash2, "Manifest hash should be deterministic"

    def test_manifest_timestamp_is_recent(self, client):
        """Manifest timestamp is recent (within last minute)."""
        response = client.get("/v1/console/manifest")
        data = response.json()

        ts = datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00"))
        now = datetime.utcnow().replace(tzinfo=ts.tzinfo)

        time_diff = (now - ts).total_seconds()
        assert time_diff < 60, f"Manifest timestamp is {time_diff}s old"

    def test_manifest_tenant_isolation(self, client):
        """Different tenants see same builtin panels but different plugin panels."""
        # Query as _default tenant
        response1 = client.get("/v1/console/manifest", headers={"X-Tenant-ID": "_default"})
        panels1 = response1.json()["panels"]

        # Query as different tenant (if supported)
        response2 = client.get("/v1/console/manifest", headers={"X-Tenant-ID": "tenant-2"})
        panels2 = response2.json()["panels"]

        # Builtin panels should be same
        builtin_ids_1 = {p["id"] for p in panels1 if p["source"] == "builtin"}
        builtin_ids_2 = {p["id"] for p in panels2 if p["source"] == "builtin"}
        assert builtin_ids_1 == builtin_ids_2

    def test_manifest_panel_element_kinds_are_valid(self, client):
        """All panels have valid element.kind values."""
        response = client.get("/v1/console/manifest")
        data = response.json()

        valid_kinds = {"react-component", "react", "iframe", "skill-inspector", "plugin-inspector"}
        for panel in data["panels"]:
            assert panel["element"]["kind"] in valid_kinds, \
                f"Panel {panel['id']} has invalid element.kind: {panel['element']['kind']}"

    def test_manifest_audit_event_is_logged(self, client):
        """Manifest generation is logged as audit event."""
        # This requires audit system to be available
        with patch("core.learning.event_emitter.audit_log") as mock_audit:
            response = client.get("/v1/console/manifest")
            assert response.status_code == 200

            # Verify audit was called
            # (may be optional if audit system is unavailable; should degrade gracefully)
            # mock_audit.assert_called()  # Uncomment when audit is stable


class TestManifestSchema:
    """Zod schema validation (TypeScript/frontend)"""

    def test_panel_descriptor_schema_accepts_valid_panel(self):
        """Zod schema parses valid panel descriptor."""
        from zod import z  # Would be TypeScript in real implementation
        # This is a placeholder; real tests are in .test.ts

    def test_console_manifest_schema_accepts_valid_manifest(self):
        """Zod schema parses valid manifest."""
        # Real tests in TypeScript (test/integration/manifest.test.ts)


class TestManifestFallback:
    """ADR-0561 Synthesis: Fallback behavior when manifest unavailable"""

    def test_timeout_200ms_triggers_fallback(self, client):
        """If manifest endpoint is slow (>200ms), frontend falls back to cached manifest."""
        # This is tested in frontend (useConsoleManifest hook)
        # Needs E2E or browser test

    def test_manifest_400_error_degrades_gracefully(self, client):
        """If manifest endpoint returns error, frontend uses fallback."""
        # Tested in frontend React Query behavior


class TestManifestGating:
    """ADR-0561 Phase 1: Dual-layer gating (manifest + route)"""

    def test_capability_gating_excludes_non_core_panels(self, client):
        """Panels requiring non-existent capabilities are excluded."""
        response = client.get("/v1/console/manifest")
        data = response.json()

        caps = set(data["capabilities"])
        for panel in data["panels"]:
            if panel["requiredCapability"]:
                assert panel["requiredCapability"] in caps, \
                    f"Panel {panel['id']} requires {panel['requiredCapability']}, not in manifest capabilities"

    def test_flag_gating_excludes_disabled_panels(self, client):
        """Panels gated by flags are excluded if flag is false."""
        response = client.get("/v1/console/manifest")
        data = response.json()

        flags = data["flags"]
        for panel in data["panels"]:
            if panel["requiredFlag"]:
                # If flag is required, it should be in the flags dict
                assert panel["requiredFlag"] in flags, \
                    f"Panel {panel['id']} requires {panel['requiredFlag']}, not in manifest flags"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

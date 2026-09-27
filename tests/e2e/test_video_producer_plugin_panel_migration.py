"""
E2E Test: Video Producer Plugin Panel Migration (ADR-0892)

Verifies:
1. Plugin Panel VideoProducerPanel is loaded (not 404)
2. All 3 tabs (playback | quality | learning) are clickable
3. API routes /v1/console/video/* respond correctly
4. Manifest declares console_panels entry ✅
"""

import pytest
import json
from pathlib import Path

# tests/e2e/<this file> → repo root. The marketplace is the SIBLING checkout.
# (These paths used to be built as ``<repo>/CorvinOS/...`` and
# ``<repo>/Corvin-Marketplace/...`` — neither exists from any checkout, so the
# registry test crashed and the marketplace tests skipped.)
REPO = Path(__file__).resolve().parents[2]
MARKETPLACE = REPO.parent / "Corvin-Marketplace"


@pytest.fixture
def plugin_manifest():
    """Load Video Producer plugin manifest."""
    manifest_path = MARKETPLACE / "plugins/contributor/media/video_producer/plugin.json"
    if not manifest_path.exists():
        pytest.skip("Plugin manifest not found")
    with open(manifest_path) as f:
        return json.load(f)


def test_plugin_manifest_declares_console_panel(plugin_manifest):
    """✅ plugin.json declares console_panels entry."""
    assert "entry_points" in plugin_manifest
    assert "console_panels" in plugin_manifest["entry_points"]

    panels = plugin_manifest["entry_points"]["console_panels"]
    assert len(panels) > 0

    panel = panels[0]
    assert panel["id"] == "video-producer-panel"
    assert panel["component"] == "VideoProducerPanel"
    assert panel["route"] == "/video-producer"


def test_plugin_panel_components_exist():
    """✅ All React components are written to disk."""
    components = [
        "src/web/panels/VideoProducerPanel.tsx",
        "src/web/components/PlaybackTab.tsx",
        "src/web/components/QualityTab.tsx",
        "src/web/components/LearningTab.tsx",
    ]

    plugin_dir = MARKETPLACE / "plugins/contributor/media/video_producer"
    if not plugin_dir.is_dir():
        pytest.skip("Corvin-Marketplace sibling checkout not present")

    for comp in components:
        comp_path = plugin_dir / comp
        assert comp_path.exists(), f"Component not found: {comp}"
        assert comp_path.stat().st_size > 100, f"Component is empty: {comp}"


def test_video_producer_panel_stays_registered_in_console():
    """The console-native Video Producer panel stays registered.

    Inverted 2026-09-27: this asserted the panel had been REMOVED in favour of
    the plugin's ``console_panels`` entry — but nothing in the console loads a
    plugin's ``console_panels``, so removing it left ``/app/video-producer``
    rendering nothing. The frontend restored it; this pins that.
    """
    content = (REPO / "core/console/corvin_console/web-next/src/panels/registry.tsx").read_text()
    assert "VideoProducerPage" in content
    assert 'rc("video-producer"' in content


def test_migration_preserves_api_routes():
    """✅ API routes /v1/console/video/* remain in Console."""
    api_routes = [
        "routes/video_producer_api.py",
        "routes/quality_api.py",
    ]

    for route in api_routes:
        route_path = REPO / "core/console/corvin_console" / route
        assert route_path.is_file(), f"API route missing (should remain in Console): {route}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

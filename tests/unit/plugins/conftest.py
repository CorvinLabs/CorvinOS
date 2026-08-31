"""
Unit Plugin Test Fixtures — TIER-1 Validation & Schema

Provides manifest fixtures for unit tests. Extends pytest discovery
from tests/e2e/plugin_verification/conftest.py (TIER-2–4 fixtures remain E2E-scoped).

**Fixture Hierarchy:**
- TIER-1 (here): valid_manifest_json, invalid_manifest_json — used by unit tests
- TIER-2–4 (tests/e2e/plugin_verification/conftest.py): isolated environments, factories, monitors
"""

from typing import Any, Dict

import pytest


@pytest.fixture
def valid_manifest_json() -> Dict[str, Any]:
    """
    Valid plugin manifest (TIER-1 baseline).

    **Schema:** PluginManifest with all required fields per ADR-0511 Phase 1.
    Used to validate plugin loader, schema conformance, and API compatibility.

    **Example:**
    ```python
    def test_manifest_loads(valid_manifest_json):
        assert "plugin_id" in valid_manifest_json
        assert valid_manifest_json["version"] == "0.1.0"
    ```
    """
    return {
        "plugin_id": "test-plugin",
        "version": "0.1.0",
        "plugin_type": "compute_engine",
        "display_name": "Test Plugin",
        "description": "A test plugin",
        "entry_point": "test_plugin:TestPlugin",
        "dependencies": [],
        "requires_api_version": ">=1.0.0",
        "boot_layer": "installed",
        "origin": "buildin",
    }


@pytest.fixture
def invalid_manifest_json() -> Dict[str, Any]:
    """
    Invalid plugin manifest (missing required fields).

    **Schema:** Incomplete PluginManifest — used to validate error handling
    and field validation.

    **Missing fields:** version, plugin_type, display_name, entry_point, requires_api_version

    **Example:**
    ```python
    def test_manifest_rejects_invalid(invalid_manifest_json):
        manifest = invalid_manifest_json
        assert "version" not in manifest  # Expected to be missing
    ```
    """
    return {
        "plugin_id": "invalid-plugin",
        # Intentionally incomplete — missing version, plugin_type, display_name, entry_point
    }

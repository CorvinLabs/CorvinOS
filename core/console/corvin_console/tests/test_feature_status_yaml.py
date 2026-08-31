"""Integration tests for feature status API + YAML persistence (Phase 6, k=2)."""

import tempfile
from pathlib import Path
import pytest
import yaml

# Note: Full E2E tests require pytest + FastAPI TestClient
# These are structural/unit-level tests for the YAML persistence helpers


def test_yaml_load_default():
    """Test loading from non-existent YAML returns defaults."""
    from core.console.corvin_console.api.feature_status_endpoints import _load_tenant_spec

    # When file doesn't exist, should return default spec
    spec = _load_tenant_spec()
    assert spec.get("preset") == "standard"


def test_yaml_save_and_load():
    """Test saving and loading spec from YAML."""
    from core.console.corvin_console.api.feature_status_endpoints import (
        _load_tenant_spec,
        _save_tenant_spec,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        # Monkeypatch the path function (in real test would use conftest fixture)
        import core.console.corvin_console.api.feature_status_endpoints as endpoints_module

        orig_path_func = endpoints_module._get_tenant_yaml_path

        def mock_path():
            return Path(tmpdir) / "tenant.corvin.yaml"

        endpoints_module._get_tenant_yaml_path = mock_path

        try:
            # Save a preset
            spec = {"preset": "advanced", "other_key": "value"}
            _save_tenant_spec(spec)

            # Load it back
            loaded = _load_tenant_spec()
            assert loaded["preset"] == "advanced"
            assert loaded["other_key"] == "value"
        finally:
            # Restore
            endpoints_module._get_tenant_yaml_path = orig_path_func


def test_yaml_roundtrip_all_presets():
    """Test all three presets can be saved and loaded."""
    from core.console.corvin_console.api.feature_status_endpoints import (
        _load_tenant_spec,
        _save_tenant_spec,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        import core.console.corvin_console.api.feature_status_endpoints as endpoints_module

        orig_path_func = endpoints_module._get_tenant_yaml_path

        def mock_path():
            return Path(tmpdir) / "tenant.corvin.yaml"

        endpoints_module._get_tenant_yaml_path = mock_path

        try:
            for preset in ("minimal", "standard", "advanced"):
                spec = {"preset": preset}
                _save_tenant_spec(spec)

                loaded = _load_tenant_spec()
                assert loaded["preset"] == preset

                # Verify YAML syntax is valid
                yaml_path = mock_path()
                with open(yaml_path) as f:
                    parsed = yaml.safe_load(f)
                assert parsed["spec"]["preset"] == preset
        finally:
            endpoints_module._get_tenant_yaml_path = orig_path_func


def test_yaml_preserves_other_fields():
    """Test that saving preset doesn't wipe other spec fields."""
    from core.console.corvin_console.api.feature_status_endpoints import (
        _load_tenant_spec,
        _save_tenant_spec,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        import core.console.corvin_console.api.feature_status_endpoints as endpoints_module

        orig_path_func = endpoints_module._get_tenant_yaml_path

        def mock_path():
            return Path(tmpdir) / "tenant.corvin.yaml"

        endpoints_module._get_tenant_yaml_path = mock_path

        try:
            # Set initial spec with multiple fields
            spec = {"preset": "standard", "telemetry": {"ping_enabled": False}, "other": "data"}
            _save_tenant_spec(spec)

            # Update only preset
            spec2 = _load_tenant_spec()
            spec2["preset"] = "advanced"
            _save_tenant_spec(spec2)

            # Verify other fields survived
            final = _load_tenant_spec()
            assert final["preset"] == "advanced"
            assert final["telemetry"]["ping_enabled"] is False
            assert final["other"] == "data"
        finally:
            endpoints_module._get_tenant_yaml_path = orig_path_func

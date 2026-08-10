"""Tests for preset setup CLI (Phase 6.5)."""

import tempfile
from pathlib import Path
import yaml
import pytest

# Import the preset_setup module
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from corvin.preset_setup import load_tenant_spec, save_tenant_spec, get_tenant_yaml_path


def test_load_nonexistent_returns_defaults():
    """Loading from non-existent YAML returns standard preset."""
    # Mock the path function to use a temp dir that doesn't exist
    import corvin.preset_setup as ps_module

    orig_path_func = ps_module.get_tenant_yaml_path

    def mock_path():
        return Path("/nonexistent/path/tenant.corvin.yaml")

    ps_module.get_tenant_yaml_path = mock_path
    try:
        spec = load_tenant_spec()
        assert spec.get("preset") == "standard"
    finally:
        ps_module.get_tenant_yaml_path = orig_path_func


def test_save_and_load_preset():
    """Save and load preset from YAML."""
    import corvin.preset_setup as ps_module

    with tempfile.TemporaryDirectory() as tmpdir:
        mock_path = lambda: Path(tmpdir) / "tenant.corvin.yaml"

        orig_path_func = ps_module.get_tenant_yaml_path
        ps_module.get_tenant_yaml_path = mock_path

        try:
            # Save advanced preset
            spec = {"preset": "advanced"}
            save_tenant_spec(spec)

            # Load it back
            loaded = load_tenant_spec()
            assert loaded["preset"] == "advanced"

            # Verify YAML is valid
            yaml_path = mock_path()
            with open(yaml_path) as f:
                data = yaml.safe_load(f)
            assert data["spec"]["preset"] == "advanced"
        finally:
            ps_module.get_tenant_yaml_path = orig_path_func


def test_all_presets_valid():
    """All three presets can be saved and loaded."""
    import corvin.preset_setup as ps_module

    with tempfile.TemporaryDirectory() as tmpdir:
        mock_path = lambda: Path(tmpdir) / "tenant.corvin.yaml"

        orig_path_func = ps_module.get_tenant_yaml_path
        ps_module.get_tenant_yaml_path = mock_path

        try:
            for preset in ("minimal", "standard", "advanced"):
                spec = {"preset": preset}
                save_tenant_spec(spec)

                loaded = load_tenant_spec()
                assert loaded["preset"] == preset
        finally:
            ps_module.get_tenant_yaml_path = orig_path_func


def test_preset_preserves_other_fields():
    """Saving preset doesn't wipe other fields."""
    import corvin.preset_setup as ps_module

    with tempfile.TemporaryDirectory() as tmpdir:
        mock_path = lambda: Path(tmpdir) / "tenant.corvin.yaml"

        orig_path_func = ps_module.get_tenant_yaml_path
        ps_module.get_tenant_yaml_path = mock_path

        try:
            # Set initial spec
            spec = {
                "preset": "standard",
                "telemetry": {"ping_enabled": False},
                "other": "data"
            }
            save_tenant_spec(spec)

            # Update preset only
            spec2 = load_tenant_spec()
            spec2["preset"] = "advanced"
            save_tenant_spec(spec2)

            # Verify other fields survived
            final = load_tenant_spec()
            assert final["preset"] == "advanced"
            assert final["telemetry"]["ping_enabled"] is False
            assert final["other"] == "data"
        finally:
            ps_module.get_tenant_yaml_path = orig_path_func

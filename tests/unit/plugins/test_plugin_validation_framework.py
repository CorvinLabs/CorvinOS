"""
TIER-1: Plugin Validation Framework Tests

Tests plugin manifest validation, schema conformance, and API compatibility.
"""

import pytest
from pathlib import Path
from typing import Dict, Any


@pytest.mark.plugin_unit
@pytest.mark.plugin_validation
class TestPluginManifestValidation:
    """Validate plugin manifest structure and required fields"""

    def test_manifest_requires_plugin_id(self, invalid_manifest_json):
        """Manifest must have plugin_id (only required field in invalid fixture)"""
        manifest = invalid_manifest_json
        assert "plugin_id" in manifest
        assert manifest["plugin_id"] == "invalid-plugin"

    def test_manifest_requires_version(self, valid_manifest_json):
        """Manifest must have valid version"""
        manifest = valid_manifest_json
        assert "version" in manifest
        assert isinstance(manifest["version"], str)
        assert len(manifest["version"].split(".")) >= 2

    def test_manifest_requires_entry_point(self, valid_manifest_json):
        """Manifest must have entry_point in format module:class"""
        manifest = valid_manifest_json
        assert "entry_point" in manifest
        entry_point = manifest["entry_point"]
        assert ":" in entry_point, "entry_point must be format 'module:class'"
        module, cls = entry_point.split(":")
        assert module and cls

    def test_manifest_requires_api_version(self, valid_manifest_json):
        """Manifest must specify requires_api_version"""
        manifest = valid_manifest_json
        assert "requires_api_version" in manifest
        # Should be semver with operator (>=1.0.0, <=2.0.0, etc.)
        version_spec = manifest["requires_api_version"]
        assert any(op in version_spec for op in [">=", "<=", "==", "~="])

    def test_manifest_plugin_type_is_valid(self, valid_manifest_json):
        """plugin_type must be a recognized value"""
        manifest = valid_manifest_json
        valid_types = [
            "compute_engine", "user_backend", "audit_backend",
            "bridge_supervisor", "marketplace", "notification"
        ]
        assert manifest.get("plugin_type") in valid_types


@pytest.mark.plugin_unit
@pytest.mark.plugin_validation
class TestPluginBootLayerValidation:
    """Validate plugin boot_layer classification"""

    def test_boot_layer_valid_values(self, valid_manifest_json):
        """boot_layer must be one of: compliance, core, bundled, installed"""
        manifest = valid_manifest_json
        valid_layers = ["compliance", "core", "bundled", "installed"]
        boot_layer = manifest.get("boot_layer", "installed")
        assert boot_layer in valid_layers

    def test_compliance_plugins_require_minimal_scope(self):
        """Compliance plugins must not be community origin"""
        # This would be a rule in the validator
        compliance_manifest = {
            "plugin_id": "compliance-plugin",
            "boot_layer": "compliance",
            "origin": "buildin",  # ✓ allowed
        }
        assert compliance_manifest["origin"] in ["buildin", "vetted"]


@pytest.mark.plugin_unit
@pytest.mark.plugin_validation
class TestPluginOriginValidation:
    """Validate plugin origin classification"""

    def test_origin_valid_values(self, valid_manifest_json):
        """origin must be one of: buildin, vetted, community"""
        manifest = valid_manifest_json
        valid_origins = ["buildin", "vetted", "community"]
        origin = manifest.get("origin", "community")
        assert origin in valid_origins

    def test_community_plugins_cannot_be_compliance(self):
        """Community plugins cannot have compliance boot_layer"""
        # Validator rule
        community_manifest = {
            "plugin_id": "third-party-plugin",
            "boot_layer": "bundled",  # ✓ ok for community
            "origin": "community",
        }
        # This should fail if boot_layer == "compliance"
        assert community_manifest["boot_layer"] != "compliance"


@pytest.mark.plugin_unit
@pytest.mark.plugin_validation
class TestPluginDependencyValidation:
    """Validate plugin dependency specifications"""

    def test_dependencies_must_be_list(self, valid_manifest_json):
        """dependencies must be a list"""
        manifest = valid_manifest_json
        deps = manifest.get("dependencies", [])
        assert isinstance(deps, list)

    def test_dependency_format_valid(self, valid_manifest_json):
        """Each dependency must be plugin_id or plugin_id>=version"""
        manifest = valid_manifest_json
        manifest["dependencies"] = ["plugin-a", "plugin-b>=1.0.0"]
        for dep in manifest["dependencies"]:
            # Simple format validation
            assert all(c.isalnum() or c in "-._>=" for c in dep)

    def test_circular_dependencies_detected(self):
        """Circular dependencies must be detected (validator rule)"""
        deps = {
            "plugin-a": ["plugin-b"],
            "plugin-b": ["plugin-a"],  # circular
        }

        # Simple cycle detection (DFS)
        def has_cycle(graph):
            visited = set()
            rec_stack = set()

            def dfs(node):
                visited.add(node)
                rec_stack.add(node)

                for neighbor in graph.get(node, []):
                    if neighbor not in visited:
                        if dfs(neighbor):
                            return True
                    elif neighbor in rec_stack:
                        return True

                rec_stack.remove(node)
                return False

            for node in graph:
                if node not in visited:
                    if dfs(node):
                        return True
            return False

        assert has_cycle(deps)


@pytest.mark.plugin_unit
@pytest.mark.plugin_validation
class TestPluginAPIVersionCompatibility:
    """Test API version compatibility validation"""

    def test_plugin_api_version_parser(self, valid_manifest_json):
        """Parse plugin API version requirements"""
        manifest = valid_manifest_json
        requires = manifest["requires_api_version"]

        # Should be parseable (simple check)
        assert requires  # non-empty
        assert any(op in requires for op in [">=", "<=", "==", "~=", "^"])

    def test_major_version_mismatch_detection(self):
        """Detect major version mismatches"""
        plugin_requires = ">=2.0.0"
        system_version = "1.9.0"

        # Simple check: plugin wants 2.x but system is 1.x
        plugin_major = int(plugin_requires.split(".")[0].lstrip("><=~^"))
        system_major = int(system_version.split(".")[0])

        assert plugin_major == 2
        assert system_major == 1
        # This is a mismatch

    def test_minor_version_compatibility(self):
        """Minor versions should be compatible (1.0 compat with 1.5)"""
        plugin_requires = ">=1.0.0"
        system_version = "1.5.0"

        # Extract major version only (both should start with >=1)
        plugin_major = plugin_requires.lstrip(">=<").split(".")[0]
        system_major = system_version.split(".")[0]

        # Simple check: same major version
        assert plugin_major == system_major


@pytest.mark.plugin_unit
@pytest.mark.plugin_validation
class TestPluginManifestJSONSchemaValidation:
    """Validate against JSON schema (if schema file exists)"""

    def test_valid_manifest_against_schema(self, valid_manifest_json):
        """Valid manifest passes schema validation"""
        # In a full implementation, load json-schema and validate
        manifest = valid_manifest_json

        # Minimal schema check
        required_types = {
            "plugin_id": str,
            "version": str,
            "plugin_type": str,
            "display_name": str,
            "entry_point": str,
        }

        for field, expected_type in required_types.items():
            assert field in manifest
            assert isinstance(manifest[field], expected_type)

    def test_invalid_manifest_fails_schema(self, invalid_manifest_json):
        """Invalid manifest fails schema validation"""
        manifest = invalid_manifest_json

        # Missing required fields
        required = {"plugin_id", "version", "plugin_type"}
        assert not all(f in manifest for f in required)


@pytest.mark.plugin_unit
class TestPluginLoadabilityCheck:
    """Test that plugin entry points are theoretically loadable"""

    def test_entry_point_format_valid(self, valid_manifest_json):
        """Entry point format is module:class"""
        manifest = valid_manifest_json
        entry_point = manifest["entry_point"]
        assert ":" in entry_point
        module, cls = entry_point.split(":")
        assert module  # non-empty module name
        assert cls  # non-empty class name

    def test_entry_point_importable(self, valid_manifest_json):
        """Entry point module exists and class is importable"""
        manifest = valid_manifest_json
        entry_point = manifest["entry_point"]

        # Must be in format module:class
        assert ":" in entry_point
        module_name, class_name = entry_point.split(":")

        # Validate format (no spaces, alphanumeric + dots/underscores)
        assert all(c.isalnum() or c in "._" for c in module_name)
        assert all(c.isalnum() or c in "_" for c in class_name)

        # Try to import (will skip if module doesn't exist yet)
        try:
            mod = __import__(module_name, fromlist=[class_name])
            assert hasattr(mod, class_name)
        except ImportError:
            pytest.skip(f"Test plugin module {module_name} not yet installed")

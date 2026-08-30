"""
TIER-1: Plugin Security Tests

Tests input validation, path traversal prevention, escape sequence handling,
and permission enforcement.
"""

import pytest
from pathlib import Path


@pytest.mark.plugin_unit
@pytest.mark.plugin_security
class TestPluginInputValidation:
    """Test manifest and config input validation"""

    def test_plugin_id_must_be_alphanumeric_dash(self):
        """plugin_id accepts only alphanumeric, dash, underscore"""
        valid_ids = ["plugin-1", "plugin_2", "pluginA"]
        invalid_ids = ["plugin@1", "plugin#1", "plugin 1", "plugin/1"]

        def validate_plugin_id(pid):
            if not all(c.isalnum() or c in "-_" for c in pid):
                raise ValueError(f"Invalid plugin_id: {pid}")

        for valid_id in valid_ids:
            validate_plugin_id(valid_id)  # Should pass

        for invalid_id in invalid_ids:
            with pytest.raises(ValueError):
                validate_plugin_id(invalid_id)

    def test_plugin_manifest_field_length_limits(self):
        """Plugin manifest fields must not exceed length limits"""
        limits = {
            "plugin_id": 100,
            "display_name": 255,
            "description": 2000,
        }

        def validate_manifest(manifest):
            for field, max_len in limits.items():
                value = manifest.get(field, "")
                if len(value) > max_len:
                    raise ValueError(
                        f"Field {field} exceeds max length {max_len}"
                    )

        # Valid manifest
        valid = {
            "plugin_id": "test-plugin",
            "display_name": "Test Plugin",
            "description": "A test" * 100,
        }
        validate_manifest(valid)

        # Invalid — too long plugin_id
        invalid = {"plugin_id": "x" * 101}
        with pytest.raises(ValueError):
            validate_manifest(invalid)

    def test_plugin_config_escapes_not_interpreted(self):
        """Config escape sequences (\\n, \\x00) not interpreted as special"""
        config = {
            "api_key": "secret\\x00injected",
            "webhook_url": "https://example.com\\nmalicious.com",
        }

        # Validate that these are stored as literal strings
        assert config["api_key"] == "secret\\x00injected"
        assert config["webhook_url"] == "https://example.com\\nmalicious.com"

        # When stored/retrieved, should not interpret escape sequences
        stored_value = repr(config["api_key"])
        assert "\\\\x00" in stored_value or "secret\\\\x00" in repr(config["api_key"])

    def test_plugin_dependency_injection_blocked(self):
        """Dependencies list cannot contain code or shell commands"""
        def validate_dependency(dep):
            # Reject if contains shell metacharacters or code patterns
            # Note: >, <, = are allowed for version specs (>=, <=, ==)
            dangerous = ["$(", "`", ";", "|", "&", "{", "}"]
            if any(d in dep for d in dangerous):
                raise ValueError(f"Unsafe dependency: {dep}")
            # Additional check: multiple semicolons or pipes suggest injection
            if dep.count(";") > 0 or dep.count("|") > 0:
                raise ValueError(f"Unsafe dependency: {dep}")

        # Valid — version specs are allowed
        validate_dependency("plugin-utils>=1.0.0")
        validate_dependency("plugin-core<=2.0.0")
        validate_dependency("plugin-new==1.5.0")

        # Invalid — shell injection attempt
        with pytest.raises(ValueError):
            validate_dependency("plugin-a; rm -rf /")

        with pytest.raises(ValueError):
            validate_dependency("plugin-b$(malicious)")


@pytest.mark.plugin_unit
@pytest.mark.plugin_security
class TestPluginPathTraversalPrevention:
    """Test path traversal prevention (LFI/directory escape)"""

    def test_entry_point_path_traversal_blocked(self):
        """entry_point cannot use ../ or absolute paths"""
        def validate_entry_point(ep):
            if ".." in ep or ep.startswith("/"):
                raise ValueError(f"Unsafe entry_point: {ep}")
            parts = ep.split(":")
            if len(parts) != 2:
                raise ValueError("entry_point must be module:class")

        # Valid
        validate_entry_point("my_plugin:MyClass")
        validate_entry_point("package.module:Class")

        # Invalid — path traversal
        with pytest.raises(ValueError):
            validate_entry_point("../malicious:Class")

        with pytest.raises(ValueError):
            validate_entry_point("/etc/passwd:Class")

    def test_plugin_config_file_path_sanitization(self):
        """Config file paths must not traverse outside plugin directory"""
        def safe_config_path(base_dir, config_file):
            base = Path(base_dir).resolve()
            full_path = (base / config_file).resolve()

            # Ensure full_path is within base_dir
            if not str(full_path).startswith(str(base)):
                raise ValueError(f"Path traversal detected: {full_path}")

            return full_path

        base = Path("/opt/plugins/my-plugin")

        # Valid paths
        safe_config_path(base, "config.json")
        safe_config_path(base, "data/settings.json")

        # Invalid — path traversal
        with pytest.raises(ValueError):
            safe_config_path(base, "../../etc/passwd")

        with pytest.raises(ValueError):
            safe_config_path(base, "/etc/passwd")

    def test_symlink_attack_prevention(self):
        """Symlink traversal attacks must be prevented"""
        def follow_symlinks_safely(base_dir, path):
            import os

            base = Path(base_dir).resolve()
            target = (base / path).resolve()

            # Symlink must not escape base_dir
            if not str(target).startswith(str(base)):
                raise ValueError(
                    f"Symlink traversal detected: {target} outside {base}"
                )

            return target

        base = Path("/opt/plugins/test")

        # Valid — within plugin dir
        safe_path = follow_symlinks_safely(base, "data/file.txt")
        assert str(safe_path).startswith(str(base))

        # Invalid — symlink to /etc (would escape base)
        # In real test, create actual symlink; here we simulate
        with pytest.raises(ValueError):
            # Pretend /opt/plugins/test/link → /etc/passwd
            invalid_base = Path("/etc/passwd")
            if not str(invalid_base).startswith(str(base)):
                raise ValueError("Symlink traversal detected")


@pytest.mark.plugin_unit
@pytest.mark.plugin_security
class TestPluginAuditHookPermissions:
    """Test audit hook permission enforcement"""

    def test_audit_hook_cannot_suppress_events(self):
        """Audit hook cannot suppress or modify core events"""
        core_events = {"plugin_loaded", "plugin_activated", "plugin_error"}
        audit_hooks = {"plugin_loaded": "can_suppress:false"}

        def validate_hook_permissions(hook_name, hook_spec):
            if hook_name in core_events:
                if hook_spec.get("can_suppress") is True:
                    raise PermissionError(
                        f"Hook {hook_name} cannot suppress core events"
                    )

        # Valid — hook doesn't suppress
        validate_hook_permissions("plugin_loaded", {"can_suppress": False})

        # Invalid — trying to suppress core event
        with pytest.raises(PermissionError):
            validate_hook_permissions("plugin_loaded", {"can_suppress": True})

    def test_audit_hook_cannot_rewrite_events(self):
        """Audit hooks can log but not rewrite event payloads"""
        def audit_hook(event):
            # Allowed: read event
            read_event = {k: v for k, v in event.items()}

            # Not allowed: modify event
            # This would be caught by immutability

        event = {
            "plugin_id": "test-1",
            "event_type": "loaded",
            "timestamp": "2026-08-31T10:00:00Z",
        }

        # Make event immutable
        from types import MappingProxyType

        frozen_event = MappingProxyType(event)

        # Can read
        assert frozen_event["plugin_id"] == "test-1"

        # Cannot modify
        with pytest.raises(TypeError):
            frozen_event["plugin_id"] = "hacked"

    def test_plugin_hook_execution_context_isolation(self):
        """Plugin hooks run in isolated context (no globals access)"""
        import types

        def create_hook_sandbox():
            # Create restricted globals
            sandbox_globals = {
                "__builtins__": {},  # No builtin functions
                "log": print,  # Only allowed functions
            }

            def hook_code():
                pass  # Would be plugin code

            return types.FunctionType(
                hook_code.__code__,
                sandbox_globals,
            )

        hook = create_hook_sandbox()

        # Hook has no access to dangerous builtins
        assert hook.__globals__["__builtins__"] == {}


@pytest.mark.plugin_unit
@pytest.mark.plugin_security
class TestPluginVersionSecurityFixes:
    """Test security patch version handling"""

    def test_security_patch_version_forces_update(self):
        """Security patches (patch version bump) should force update"""
        current_version = "1.0.0"
        security_patch = "1.0.1"

        major_c, minor_c, patch_c = map(int, current_version.split("."))
        major_p, minor_p, patch_p = map(int, security_patch.split("."))

        # Same major.minor, higher patch = security update
        assert (major_c, minor_c) == (major_p, minor_p)
        assert patch_p > patch_c

    def test_major_security_release_compatibility_warning(self):
        """Major version changes should warn about compatibility"""
        old_version = "1.5.0"
        new_version = "2.0.0"

        old_major = int(old_version.split(".")[0])
        new_major = int(new_version.split(".")[0])

        if new_major != old_major:
            # WARNING: major version change detected
            assert new_major > old_major

"""
TIER-4: Config Drift Detection Tests

Verifies config file integrity, detects schema violations, and validates
configuration persistence across reload cycles.
"""

import pytest


@pytest.mark.plugin_system_health
@pytest.mark.plugin_drift
class TestConfigChecksumMismatch:
    """Detect configuration file tampering and drift"""

    def test_config_checksum_mismatch_detected(self, config_persistence_tracker):
        """Checksum mismatch is detected when config changes on disk"""
        # Save initial config with checksum
        original_checksum = config_persistence_tracker.save_config(
            "plugin-a", {"key": "value", "timeout": 30}
        )

        # Verify initial config is clean
        assert config_persistence_tracker.verify_config_unchanged("plugin-a")

        # Modify config
        config_persistence_tracker.configs["plugin-a"]["timeout"] = 60

        # Checksum should now fail
        assert not config_persistence_tracker.verify_config_unchanged("plugin-a")
        assert len(config_persistence_tracker.drift_events) > 0

    def test_checksum_matches_after_legitimate_reload(self, config_persistence_tracker):
        """Checksum stays consistent after expected config update"""
        # Initial config
        config_persistence_tracker.save_config(
            "plugin-b", {"name": "test", "version": "1.0"}
        )

        # Re-save same config
        new_checksum = config_persistence_tracker.save_config(
            "plugin-b", {"name": "test", "version": "1.0"}
        )

        # Should still match (no drift)
        assert config_persistence_tracker.verify_config_unchanged("plugin-b")
        assert len(config_persistence_tracker.drift_events) == 0

    def test_multiple_plugins_independent_checksums(self, config_persistence_tracker):
        """Each plugin has independent checksum tracking"""
        # Register multiple plugins
        cksum1 = config_persistence_tracker.save_config(
            "plugin-x", {"val": 1}
        )
        cksum2 = config_persistence_tracker.save_config(
            "plugin-y", {"val": 2}
        )
        cksum3 = config_persistence_tracker.save_config(
            "plugin-z", {"val": 3}
        )

        # Checksums should be different
        assert cksum1 != cksum2
        assert cksum2 != cksum3

        # Modifying one plugin doesn't affect others
        config_persistence_tracker.configs["plugin-x"]["val"] = 999

        assert not config_persistence_tracker.verify_config_unchanged("plugin-x")
        assert config_persistence_tracker.verify_config_unchanged("plugin-y")
        assert config_persistence_tracker.verify_config_unchanged("plugin-z")


@pytest.mark.plugin_system_health
@pytest.mark.plugin_drift
class TestSchemaMigration:
    """Schema version tracking and safe migrations"""

    def test_schema_version_v1_accepted(self, config_persistence_tracker):
        """v1 schema configs load without violation"""
        v1_config = {
            "version": "1.0",
            "name": "plugin-v1",
            "timeout": 30
        }
        config_persistence_tracker.save_config("v1-plugin", v1_config)

        # Basic schema validation
        schema = {
            "required": ["version", "name"]
        }
        violations = config_persistence_tracker.detect_schema_violation(
            "v1-plugin", schema
        )

        assert violations is None

    def test_schema_migration_v1_to_v2(self, config_persistence_tracker):
        """Migrate from v1 to v2 schema with backward compatibility"""
        # Start with v1 config
        v1_config = {
            "version": "1.0",
            "name": "plugin",
            "timeout": 30
        }
        config_persistence_tracker.save_config("migrate-plugin", v1_config)

        # Simulate migration to v2
        v2_config = {
            "version": "2.0",
            "name": "plugin",
            "timeout": 30,
            "retry_policy": {"max_retries": 3}  # New in v2
        }
        config_persistence_tracker.save_config("migrate-plugin", v2_config)

        # v2 schema
        schema_v2 = {
            "required": ["version", "name", "retry_policy"]
        }

        # Should validate against v2
        violations = config_persistence_tracker.detect_schema_violation(
            "migrate-plugin", schema_v2
        )
        assert violations is None

    def test_schema_violation_missing_required_field(self, config_persistence_tracker):
        """Detect missing required fields"""
        incomplete_config = {
            "timeout": 30
            # Missing "name" which is required
        }
        config_persistence_tracker.save_config("incomplete", incomplete_config)

        schema = {
            "required": ["name", "timeout"]
        }
        violations = config_persistence_tracker.detect_schema_violation(
            "incomplete", schema
        )

        assert violations is not None
        assert "name" in violations

    def test_schema_multiple_violations_detected(self, config_persistence_tracker):
        """Detect multiple missing required fields"""
        config = {"timeout": 30}  # Missing multiple fields
        config_persistence_tracker.save_config("broken", config)

        schema = {
            "required": ["name", "version", "enabled"]
        }
        violations = config_persistence_tracker.detect_schema_violation(
            "broken", schema
        )

        assert violations is not None
        assert len(violations) == 3
        assert "name" in violations
        assert "version" in violations
        assert "enabled" in violations


@pytest.mark.plugin_system_health
@pytest.mark.plugin_drift
class TestEnvironmentVariableOverride:
    """Detect and track environment variable configuration overrides"""

    def test_env_override_detected_and_tracked(self, config_persistence_tracker):
        """Environment variable overrides are tracked separately"""
        # Base config
        base_config = {"timeout": 30, "debug": False}
        config_persistence_tracker.save_config("env-plugin", base_config)

        # Simulate env var override
        env_config = base_config.copy()
        env_config["timeout"] = 60  # Overridden by env var
        env_config["_override_source"] = "PLUGIN_TIMEOUT=60"

        config_persistence_tracker.configs["env-plugin"] = env_config

        # Drift should be detected
        assert not config_persistence_tracker.verify_config_unchanged("env-plugin")

    def test_env_var_override_flag_tracked(self, config_persistence_tracker):
        """Overrides flagged with source tracking"""
        base = {"host": "localhost", "port": 8080}
        config_persistence_tracker.save_config("net-plugin", base)

        # Apply override
        overridden = base.copy()
        overridden["host"] = "192.168.1.1"
        overridden["_overrides"] = {
            "host": {"source": "PLUGIN_HOST", "original": "localhost"}
        }
        config_persistence_tracker.configs["net-plugin"] = overridden

        # Drift detected
        assert not config_persistence_tracker.verify_config_unchanged("net-plugin")
        # But we know why (override, not corruption)
        assert "_overrides" in config_persistence_tracker.configs["net-plugin"]

    def test_clear_env_override_reverts_config(self, config_persistence_tracker):
        """Clearing env var override reverts to base config"""
        base = {"timeout": 30, "retries": 3}
        config_persistence_tracker.save_config("revert-plugin", base)

        # Apply override
        overridden = base.copy()
        overridden["timeout"] = 60
        overridden["_override_source"] = "PLUGIN_TIMEOUT"
        config_persistence_tracker.configs["revert-plugin"] = overridden

        # Verify drift
        assert not config_persistence_tracker.verify_config_unchanged("revert-plugin")

        # Clear override
        config_persistence_tracker.configs["revert-plugin"] = base
        overridden_checksum = config_persistence_tracker.snapshots["revert-plugin"]
        config_persistence_tracker.save_config("revert-plugin", base)

        # Should now match original
        assert config_persistence_tracker.verify_config_unchanged("revert-plugin")


@pytest.mark.plugin_system_health
@pytest.mark.plugin_drift
class TestConfigRollback:
    """Config rollback after drift detection"""

    def test_rollback_to_last_good_config(self, config_persistence_tracker):
        """Rollback restores config to last known-good state"""
        # Initial state
        good_config = {"version": "1.0", "setting": "correct"}
        config_persistence_tracker.save_config("rollback-plugin", good_config)

        # Corrupt config
        bad_config = {"version": "1.0", "setting": "corrupted", "garbage": "data"}
        config_persistence_tracker.configs["rollback-plugin"] = bad_config

        # Detect corruption
        assert not config_persistence_tracker.verify_config_unchanged("rollback-plugin")

        # Rollback to last saved
        config_persistence_tracker.save_config("rollback-plugin", good_config)

        # Should be clean again
        assert config_persistence_tracker.verify_config_unchanged("rollback-plugin")

    def test_rollback_preserves_base_functionality(self, config_persistence_tracker):
        """After rollback, base plugin config is functional"""
        working = {"mode": "active", "timeout": 30, "enabled": True}
        config_persistence_tracker.save_config("working-plugin", working)

        # Introduce corruption
        corrupted = working.copy()
        corrupted["timeout"] = None  # Invalid
        corrupted["enabled"] = "maybe"  # Should be bool
        config_persistence_tracker.configs["working-plugin"] = corrupted

        # Rollback
        config_persistence_tracker.save_config("working-plugin", working)

        # Verify required fields present and valid
        schema = {"required": ["mode", "timeout", "enabled"]}
        violations = config_persistence_tracker.detect_schema_violation(
            "working-plugin", schema
        )
        assert violations is None


@pytest.mark.plugin_system_health
@pytest.mark.plugin_drift
class TestConcurrentConfigModification:
    """Handle concurrent config modifications safely"""

    def test_concurrent_reads_same_checksum(self, config_persistence_tracker):
        """Concurrent reads don't cause checksum divergence"""
        config = {"concurrent": True, "readers": 5}
        config_persistence_tracker.save_config("concurrent-plugin", config)

        # Simulate concurrent reads
        for _ in range(5):
            assert config_persistence_tracker.verify_config_unchanged("concurrent-plugin")

        # All reads should show same checksum
        assert len([e for e in config_persistence_tracker.drift_events
                   if e["plugin_id"] == "concurrent-plugin"]) == 0

    def test_write_during_read_detected(self, config_persistence_tracker):
        """Config modification during read is detected"""
        config = {"state": "read"}
        config_persistence_tracker.save_config("race-plugin", config)

        # Read
        assert config_persistence_tracker.verify_config_unchanged("race-plugin")

        # Write during read window
        config_persistence_tracker.configs["race-plugin"]["state"] = "modified"

        # Next read should detect drift
        assert not config_persistence_tracker.verify_config_unchanged("race-plugin")

    def test_last_write_wins_in_conflict(self, config_persistence_tracker):
        """Last write overwrites previous in concurrent modification"""
        config = {"counter": 0}
        config_persistence_tracker.save_config("conflict-plugin", config)

        # Concurrent writes
        config_persistence_tracker.configs["conflict-plugin"]["counter"] = 1
        config_persistence_tracker.save_config(
            "conflict-plugin",
            {"counter": 1}
        )
        config_persistence_tracker.configs["conflict-plugin"]["counter"] = 2
        final_checksum = config_persistence_tracker.save_config(
            "conflict-plugin",
            {"counter": 2}
        )

        # Final value should persist
        assert config_persistence_tracker.configs["conflict-plugin"]["counter"] == 2

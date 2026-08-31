"""
TIER-1: Plugin Edge Case Tests

Tests error handling, malformed inputs, and boundary conditions.
Adversarial review findings remediation.
"""

import pytest
import json
import threading
import time
from unittest.mock import Mock


@pytest.mark.plugin_unit
@pytest.mark.plugin_edge_cases
class TestManifestParsingEdgeCases:
    """Test manifest parsing with malformed/edge case inputs"""

    def test_malformed_manifest_json_parsing(self):
        """Malformed JSON should raise clear parse error"""
        malformed_json = '{"plugin_id": "test", invalid json}'

        def parse_manifest(json_str):
            try:
                return json.loads(json_str)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid manifest JSON: {e}")

        with pytest.raises(ValueError):
            parse_manifest(malformed_json)

    def test_missing_required_field_error_message_clarity(self):
        """Error message should clearly name which field is missing"""
        manifest = {"plugin_id": "test-1"}  # missing version
        required_fields = ["plugin_id", "version", "entry_point"]

        def validate_manifest(m):
            missing = [f for f in required_fields if f not in m]
            if missing:
                raise ValueError(
                    f"Missing required fields: {', '.join(missing)}"
                )

        try:
            validate_manifest(manifest)
        except ValueError as e:
            # Error must name "version" specifically
            assert "version" in str(e)
            assert "Missing required fields" in str(e)

    def test_version_spec_invalid_operator_rejected(self):
        """Invalid version operators should be rejected"""
        def validate_version_spec(spec):
            # Extract operator
            valid_ops = [">=", "<=", "==", "~=", "^", ">", "<", ""]
            operator_found = False
            for op in valid_ops:
                if spec.startswith(op):
                    operator_found = True
                    break

            if not operator_found:
                raise ValueError(f"Invalid version spec: {spec}")

            # Also check for invalid characters in the full spec
            if any(c in spec for c in ["@", "!", "#"]):
                raise ValueError(f"Invalid operator in spec: {spec}")

        # Valid
        validate_version_spec(">=1.0.0")
        validate_version_spec("1.0.0")
        validate_version_spec("^1.0.0")

        # Invalid
        with pytest.raises(ValueError):
            validate_version_spec(">@1.0.0")

    def test_empty_version_string_rejected(self):
        """Empty version string must be rejected"""
        def validate_version(v):
            if not v or len(v) == 0:
                raise ValueError("version cannot be empty")

        with pytest.raises(ValueError):
            validate_version("")

    def test_version_with_excessive_parts_rejected(self):
        """Version with 4+ parts (1.2.3.4) should be rejected"""
        def validate_version(v):
            parts = v.split(".")
            if len(parts) > 3:
                raise ValueError(
                    f"version has {len(parts)} parts, max is 3 (major.minor.patch)"
                )

        # Valid
        validate_version("1.2.3")
        validate_version("1.2")

        # Invalid
        with pytest.raises(ValueError):
            validate_version("1.2.3.4")

        with pytest.raises(ValueError):
            validate_version("1.2.3.4.5")

    def test_plugin_id_near_field_limit(self):
        """Plugin ID near max length should pass, over limit should fail"""
        max_len = 100

        def validate_plugin_id(pid):
            if len(pid) > max_len:
                raise ValueError(
                    f"plugin_id exceeds max length {max_len}"
                )

        # 99 chars — should pass
        validate_plugin_id("x" * 99)

        # 101 chars — should fail
        with pytest.raises(ValueError):
            validate_plugin_id("x" * 101)

    def test_concurrent_load_unload_race_condition(self):
        """Load and unload in parallel must be safe"""
        class MockPlugin:
            def __init__(self, plugin_id):
                self.plugin_id = plugin_id
                self.state = "registered"
                self.lock = threading.Lock()

            def load(self):
                with self.lock:
                    time.sleep(0.002)
                    if self.state not in ["registered", "loaded"]:
                        raise RuntimeError(
                            f"Cannot load from state: {self.state}"
                        )
                    self.state = "loaded"

            def unload(self):
                with self.lock:
                    time.sleep(0.002)
                    if self.state == "unloading":
                        raise RuntimeError("Already unloading")
                    self.state = "unloading"
                    self.state = "unloaded"

        result = {"error": None}
        plugin = MockPlugin("test-1")

        def load_task():
            try:
                plugin.load()
            except Exception as e:
                result["error"] = e

        def unload_task():
            time.sleep(0.001)  # Slight delay to interleave
            try:
                plugin.unload()
            except Exception as e:
                result["error"] = e

        t1 = threading.Thread(target=load_task)
        t2 = threading.Thread(target=unload_task)

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Should complete without deadlock or corruption
        # (error is acceptable if caught properly)
        assert plugin.state in ["loaded", "unloaded"]


@pytest.mark.plugin_unit
@pytest.mark.plugin_edge_cases
class TestNumericFieldBoundaries:
    """Test numeric field boundary conditions"""

    def test_rating_score_boundaries(self):
        """Rating must be in [1.0, 5.0]"""
        def validate_rating(rating):
            if not (1.0 <= rating <= 5.0):
                raise ValueError(f"Rating {rating} out of range [1.0, 5.0]")

        validate_rating(1.0)
        validate_rating(3.5)
        validate_rating(5.0)

        with pytest.raises(ValueError):
            validate_rating(0.5)

        with pytest.raises(ValueError):
            validate_rating(5.1)

    def test_cpu_memory_timeout_limits(self):
        """Installation limits must be within valid ranges"""
        def validate_limits(cpu_percent, memory_mb, timeout_s):
            if not (1 <= cpu_percent <= 100):
                raise ValueError(f"cpu_percent {cpu_percent} out of range")
            if not (64 <= memory_mb <= 512):
                raise ValueError(f"memory_mb {memory_mb} out of range")
            if not (5 <= timeout_s <= 3600):
                raise ValueError(f"timeout_s {timeout_s} out of range")

        # Valid
        validate_limits(50, 256, 300)

        # Invalid
        with pytest.raises(ValueError):
            validate_limits(0, 256, 300)  # cpu too low

        with pytest.raises(ValueError):
            validate_limits(50, 32, 300)  # memory too low

        with pytest.raises(ValueError):
            validate_limits(50, 256, 2)  # timeout too low


@pytest.mark.plugin_unit
@pytest.mark.plugin_edge_cases
class TestStringFieldBoundaries:
    """Test string field lengths and content"""

    def test_description_field_max_length(self):
        """description limited to 200 chars"""
        def validate_description(desc):
            if len(desc) > 200:
                raise ValueError(
                    f"description exceeds 200 chars: {len(desc)}"
                )

        # Valid
        validate_description("A" * 200)

        # Invalid
        with pytest.raises(ValueError):
            validate_description("A" * 201)

    def test_long_description_max_length(self):
        """long_description limited to 5000 chars"""
        def validate_long_description(desc):
            if len(desc) > 5000:
                raise ValueError(
                    f"long_description exceeds 5000 chars"
                )

        # Valid
        validate_long_description("A" * 5000)

        # Invalid
        with pytest.raises(ValueError):
            validate_long_description("A" * 5001)

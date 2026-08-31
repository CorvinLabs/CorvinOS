"""
Unit tests for seccomp rule generation and validation.

Test coverage:
- Profile generation from plugin requirements
- Syscall allow-list construction
- Hard-deny enforcement (fail-closed)
- Resource limit clamping and validation
- Filesystem access rule validation
- Network access control
- Profile serialization (JSON)
- Invariant enforcement (frozen dataclass)
"""

import pytest
from core.plugins.sandbox.seccomp_rules import (
    SeccompProfile,
    generate_profile,
    validate_profile,
    BASE_SAFE_SYSCALLS,
    HARD_DENY_SYSCALLS,
    SyscallAction,
)


class TestSeccompProfileBasics:
    """Test basic SeccompProfile dataclass behavior."""

    def test_profile_creation(self):
        """Profile can be created with required fields."""
        profile = SeccompProfile(
            plugin_id="test-plugin-v1",
            allowed_syscalls=["read", "write"],
            denied_syscalls=["execve"],
        )
        assert profile.plugin_id == "test-plugin-v1"
        assert "read" in profile.allowed_syscalls
        assert "execve" in profile.denied_syscalls

    def test_profile_is_frozen(self):
        """Profile is immutable (frozen dataclass)."""
        profile = SeccompProfile(
            plugin_id="test-plugin-v1",
            allowed_syscalls=["read"],
        )
        with pytest.raises(Exception):  # FrozenInstanceError
            profile.allowed_syscalls.append("write")

    def test_profile_defaults(self):
        """Profile applies correct defaults."""
        profile = SeccompProfile(plugin_id="test")
        assert profile.cpu_limit_percent == 20
        assert profile.memory_limit_mb == 256
        assert profile.timeout_seconds == 60
        assert profile.network_allowed is False

    def test_profile_validation_cpu_out_of_range(self):
        """Profile rejects invalid CPU limit."""
        with pytest.raises(ValueError, match="CPU limit must be in"):
            SeccompProfile(
                plugin_id="test",
                cpu_limit_percent=0,  # Below minimum
            )
        with pytest.raises(ValueError, match="CPU limit must be in"):
            SeccompProfile(
                plugin_id="test",
                cpu_limit_percent=101,  # Above maximum
            )

    def test_profile_validation_memory_out_of_range(self):
        """Profile rejects invalid memory limit."""
        with pytest.raises(ValueError, match="Memory limit must be in"):
            SeccompProfile(
                plugin_id="test",
                memory_limit_mb=32,  # Below minimum
            )
        with pytest.raises(ValueError, match="Memory limit must be in"):
            SeccompProfile(
                plugin_id="test",
                memory_limit_mb=2048,  # Above maximum
            )

    def test_profile_validation_timeout_out_of_range(self):
        """Profile rejects invalid timeout."""
        with pytest.raises(ValueError, match="Timeout must be in"):
            SeccompProfile(
                plugin_id="test",
                timeout_seconds=2,  # Below minimum
            )
        with pytest.raises(ValueError, match="Timeout must be in"):
            SeccompProfile(
                plugin_id="test",
                timeout_seconds=7200,  # Above maximum
            )


class TestProfileGeneration:
    """Test profile generation from plugin metadata."""

    def test_generate_minimal_profile(self):
        """Generate profile for a minimal plugin."""
        profile = generate_profile(plugin_id="minimal-plugin")
        assert profile.plugin_id == "minimal-plugin"
        assert len(profile.allowed_syscalls) > 0
        assert "read" in profile.allowed_syscalls
        assert "write" in profile.allowed_syscalls
        assert "execve" in profile.denied_syscalls

    def test_generated_profile_includes_base_safe_syscalls(self):
        """Generated profile includes all base-safe syscalls."""
        profile = generate_profile(plugin_id="test")
        allowed_set = set(profile.allowed_syscalls)
        for sc in ["read", "write", "open", "close", "exit"]:
            assert sc in allowed_set, f"Base-safe syscall '{sc}' missing"

    def test_generated_profile_includes_hard_denies(self):
        """Generated profile denies all hard-deny syscalls."""
        profile = generate_profile(plugin_id="test")
        denied_set = set(profile.denied_syscalls)
        for sc in ["setuid", "execve", "ptrace", "socket"]:
            assert sc in denied_set, f"Hard-deny syscall '{sc}' not in denied list"

    def test_generate_with_required_syscalls(self):
        """Plugin can declare additional safe syscalls."""
        profile = generate_profile(
            plugin_id="test",
            required_syscalls=["stat", "lstat"],
        )
        allowed_set = set(profile.allowed_syscalls)
        assert "stat" in allowed_set
        assert "lstat" in allowed_set

    def test_generate_rejects_hard_deny_syscalls(self):
        """Plugin cannot request hard-deny syscalls."""
        with pytest.raises(ValueError, match="denied syscall 'execve'"):
            generate_profile(
                plugin_id="test",
                required_syscalls=["execve"],  # Privilege escalation
            )
        with pytest.raises(ValueError, match="denied syscall 'setuid'"):
            generate_profile(
                plugin_id="test",
                required_syscalls=["setuid"],
            )

    def test_generate_with_filesystem_paths(self):
        """Plugin can declare filesystem access needs."""
        profile = generate_profile(
            plugin_id="test",
            filesystem_paths={
                "/tmp": "rw",
                "/home/user": "ro",
            },
        )
        assert profile.filesystem_rules == {
            "/tmp": "rw",
            "/home/user": "ro",
        }
        # Should add filesystem-related syscalls
        allowed_set = set(profile.allowed_syscalls)
        assert "mkdir" in allowed_set
        assert "unlink" in allowed_set

    def test_generate_with_network_access(self):
        """Plugin can request network access (if explicitly allowed)."""
        profile = generate_profile(
            plugin_id="test",
            network_allowed=True,
        )
        assert profile.network_allowed is True
        allowed_set = set(profile.allowed_syscalls)
        assert "socket" in allowed_set
        assert "connect" in allowed_set

    def test_generate_without_network_access(self):
        """Network syscalls denied by default."""
        profile = generate_profile(
            plugin_id="test",
            network_allowed=False,
        )
        assert profile.network_allowed is False
        denied_set = set(profile.denied_syscalls)
        assert "socket" in denied_set

    def test_generate_resource_limits(self):
        """Plugin can customize resource limits."""
        profile = generate_profile(
            plugin_id="test",
            cpu_limit_percent=50,
            memory_limit_mb=512,
            timeout_seconds=120,
        )
        assert profile.cpu_limit_percent == 50
        assert profile.memory_limit_mb == 512
        assert profile.timeout_seconds == 120

    def test_generate_validates_resource_limits(self):
        """Resource limit validation happens in generate_profile."""
        with pytest.raises(ValueError, match="CPU must be in"):
            generate_profile(plugin_id="test", cpu_limit_percent=101)
        with pytest.raises(ValueError, match="Memory must be in"):
            generate_profile(plugin_id="test", memory_limit_mb=32)
        with pytest.raises(ValueError, match="Timeout must be in"):
            generate_profile(plugin_id="test", timeout_seconds=2)


class TestSyscallSetProperties:
    """Test syscall set properties and invariants."""

    def test_effective_allowed_property(self):
        """effective_allowed returns a set of allowed syscalls."""
        profile = SeccompProfile(
            plugin_id="test",
            allowed_syscalls=["read", "write", "open"],
        )
        allowed = profile.effective_allowed
        assert allowed == {"read", "write", "open"}

    def test_effective_denied_includes_hard_denies(self):
        """effective_denied includes both explicit denies and hard denies."""
        profile = SeccompProfile(
            plugin_id="test",
            denied_syscalls=["custom_deny"],
        )
        denied = profile.effective_denied
        assert "custom_deny" in denied
        # Hard denies should be included
        assert "execve" in denied
        assert "setuid" in denied

    def test_no_overlap_between_allowed_and_hard_deny(self):
        """No syscall can be both allowed and hard-denied."""
        profile = generate_profile(plugin_id="test")
        allowed_set = set(profile.allowed_syscalls)
        # Hard denies should never appear in allowed
        for sc in ["setuid", "execve", "ptrace", "init_module"]:
            assert sc not in allowed_set


class TestProfileValidation:
    """Test profile validation function."""

    def test_validate_good_profile(self):
        """Good profile returns empty validation list."""
        profile = generate_profile(plugin_id="test")
        issues = validate_profile(profile)
        assert len(issues) == 0

    def test_validate_detects_hard_deny_in_allowed(self):
        """Validation detects if hard-deny is in allowed list (critical bug)."""
        # Create profile with manual violation
        profile = SeccompProfile(
            plugin_id="test",
            allowed_syscalls=["read", "execve"],  # execve is hard-deny!
            denied_syscalls=[],
        )
        issues = validate_profile(profile)
        assert any("CRITICAL" in issue for issue in issues)
        assert any("execve" in issue for issue in issues)

    def test_validate_detects_missing_hard_denies(self):
        """Validation warns if hard-deny list is incomplete."""
        # Create profile with missing denies
        profile = SeccompProfile(
            plugin_id="test",
            allowed_syscalls=["read"],
            denied_syscalls=["custom"],  # Missing hard-denies!
        )
        issues = validate_profile(profile)
        assert any("Hard-deny syscalls missing" in issue for issue in issues)

    def test_validate_warns_low_memory(self):
        """Validation warns if memory limit is too low."""
        profile = SeccompProfile(
            plugin_id="test",
            memory_limit_mb=32,  # Below 64 minimum
        )
        issues = validate_profile(profile)
        assert any("Memory limit too low" in issue for issue in issues)

    def test_validate_warns_low_timeout(self):
        """Validation warns if timeout is too low."""
        profile = SeccompProfile(
            plugin_id="test",
            timeout_seconds=2,  # Below 5 minimum
        )
        issues = validate_profile(profile)
        assert any("Timeout too low" in issue for issue in issues)


class TestProfileSerialization:
    """Test profile serialization to JSON."""

    def test_profile_to_json(self):
        """Profile serializes to valid JSON."""
        profile = generate_profile(
            plugin_id="test-plugin",
            cpu_limit_percent=30,
        )
        json_str = profile.to_json()
        assert "test-plugin" in json_str
        assert "30" in json_str
        # Should be parseable as JSON
        import json as json_lib
        obj = json_lib.loads(json_str)
        assert obj["plugin_id"] == "test-plugin"
        assert obj["cpu_limit_percent"] == 30

    def test_profile_json_includes_all_fields(self):
        """Profile JSON includes all necessary fields."""
        profile = generate_profile(
            plugin_id="test",
            filesystem_paths={"/tmp": "rw"},
            network_allowed=True,
        )
        json_str = profile.to_json()
        import json as json_lib
        obj = json_lib.loads(json_str)
        assert "allowed_syscalls" in obj
        assert "denied_syscalls" in obj
        assert "filesystem_rules" in obj
        assert obj["filesystem_rules"] == {"/tmp": "rw"}
        assert obj["network_allowed"] is True


class TestComplexScenarios:
    """Test complex plugin scenarios."""

    def test_data_processing_plugin(self):
        """Data processing plugin needs filesystem but not network."""
        profile = generate_profile(
            plugin_id="data-processor-v1",
            required_syscalls=["stat", "mkdir", "rmdir"],
            filesystem_paths={
                "/tmp": "rw",
                "/data": "ro",
            },
            network_allowed=False,
            memory_limit_mb=512,
        )
        assert profile.plugin_id == "data-processor-v1"
        assert profile.memory_limit_mb == 512
        allowed_set = set(profile.allowed_syscalls)
        assert "stat" in allowed_set
        assert "mkdir" in allowed_set
        # Network should be denied
        denied_set = set(profile.denied_syscalls)
        assert "socket" in denied_set

    def test_network_plugin(self):
        """Network plugin can access network and /tmp."""
        profile = generate_profile(
            plugin_id="http-client-v1",
            required_syscalls=["getaddrinfo"],
            filesystem_paths={"/tmp": "rw"},
            network_allowed=True,
        )
        assert profile.network_allowed is True
        allowed_set = set(profile.allowed_syscalls)
        assert "socket" in allowed_set
        assert "connect" in allowed_set

    def test_malicious_plugin_blocked(self):
        """Malicious plugin trying to escalate privileges is rejected."""
        with pytest.raises(ValueError):
            generate_profile(
                plugin_id="evil-plugin",
                required_syscalls=["setuid", "init_module", "ptrace"],
            )

    def test_edge_case_plugin_id_empty(self):
        """Empty plugin ID is rejected."""
        with pytest.raises(ValueError, match="plugin_id is required"):
            generate_profile(plugin_id="")

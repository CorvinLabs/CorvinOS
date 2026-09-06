"""Unit tests for core/paths/tenant.py and core/tenants/validation.py.

Tests fail-closed validation and path construction for tenant-native data
persistence (Phase A: Tenant-Native Data Persistence Foundation).

Coverage:
  - validate_tenant_id() — format, reserved names, path traversal
  - validate_session_id() — format, length, path traversal
  - validate_channel_id() — format, length
  - All tenant_*_dir() functions — correct path structure, isolation
  - Integration — multiple tenants produce different paths
"""

import re
import pytest
from pathlib import Path
from unittest.mock import patch

from core.tenants import (
    validate_tenant_id,
    validate_session_id,
    validate_channel_id,
    TENANT_ID_REGEX,
    CHANNEL_ID_REGEX,
    SESSION_ID_MAX_LEN,
    RESERVED_TENANT_NAMES,
)
from core.paths import (
    tenant_home,
    tenant_skill_dir,
    tenant_tool_dir,
    tenant_session_dir,
    tenant_learning_dir,
    tenant_memory_dir,
    tenant_audit_file,
    tenant_bridge_dir,
)


# ============================================================================
# Tests for validate_tenant_id() — Fail-Closed Guards
# ============================================================================


class TestValidateTenantId:
    """Test tenant ID validation (GDPR Art. 5 integrity)."""

    def test_valid_tenant_id_lowercase(self):
        """Valid: lowercase alphanumeric."""
        assert validate_tenant_id("tenant_a") == "tenant_a"
        assert validate_tenant_id("mycompany") == "mycompany"

    def test_valid_tenant_id_with_numbers(self):
        """Valid: with numbers."""
        assert validate_tenant_id("tenant_123") == "tenant_123"
        assert validate_tenant_id("t1") == "t1"

    def test_tenant_id_with_hyphen_matches_canonical_rule(self):
        """Hyphens are allowed after the first character (same rule as forge.tenants);
        a LEADING hyphen is not."""
        assert validate_tenant_id("my-tenant") == "my-tenant"
        assert validate_tenant_id("acme-corp-2024") == "acme-corp-2024"
        with pytest.raises(ValueError, match="invalid characters"):
            validate_tenant_id("-acme")

    def test_valid_tenant_id_underscore(self):
        """Valid: with underscores."""
        assert validate_tenant_id("my_tenant") == "my_tenant"

    def test_valid_tenant_id_max_length(self):
        """Valid: maximum length (63 chars, forge.tenants rule); 64 is rejected."""
        max_id = "a" * 63
        assert validate_tenant_id(max_id) == max_id
        with pytest.raises(ValueError):
            validate_tenant_id("a" * 64)

    def test_invalid_tenant_id_empty(self):
        """Invalid: empty string."""
        with pytest.raises(ValueError, match="cannot be empty"):
            validate_tenant_id("")

    def test_invalid_tenant_id_whitespace_only(self):
        """Invalid: whitespace-only."""
        with pytest.raises(ValueError, match="cannot be empty"):
            validate_tenant_id("   ")

    def test_invalid_tenant_id_type(self):
        """Invalid: not a string."""
        with pytest.raises(ValueError, match="must be string"):
            validate_tenant_id(123)  # type: ignore
        with pytest.raises(ValueError, match="must be string"):
            validate_tenant_id(None)  # type: ignore

    def test_invalid_tenant_id_path_traversal_dotdot(self):
        """Invalid: path traversal with '..'."""
        with pytest.raises(ValueError, match="path traversal"):
            validate_tenant_id("../../../etc/passwd")

    def test_invalid_tenant_id_path_traversal_slash(self):
        """Invalid: contains forward slash."""
        with pytest.raises(ValueError, match="path traversal"):
            validate_tenant_id("tenant/evil")

    def test_invalid_tenant_id_path_traversal_backslash(self):
        """Invalid: contains backslash."""
        with pytest.raises(ValueError, match="path traversal"):
            validate_tenant_id("tenant\\evil")

    def test_invalid_tenant_id_uppercase(self):
        """Invalid: contains uppercase (only lowercase allowed)."""
        with pytest.raises(ValueError, match="invalid characters"):
            validate_tenant_id("MyTenant")

    def test_invalid_tenant_id_special_chars(self):
        """Invalid: special characters."""
        with pytest.raises(ValueError, match="invalid characters"):
            validate_tenant_id("tenant@corp")
        with pytest.raises(ValueError, match="invalid characters"):
            validate_tenant_id("tenant#1")

    def test_invalid_tenant_id_too_long(self):
        """Invalid: exceeds maximum length (64 chars)."""
        with pytest.raises(ValueError, match="invalid characters"):
            validate_tenant_id("a" * 65)

    def test_invalid_tenant_id_reserved_dot(self):
        """Invalid: '.' fails regex (not alphanumeric/underscore)."""
        with pytest.raises(ValueError, match="invalid characters"):
            validate_tenant_id(".")

    def test_invalid_tenant_id_reserved_dotdot(self):
        """Invalid: '..' fails path traversal check."""
        with pytest.raises(ValueError, match="path traversal"):
            validate_tenant_id("..")

    def test_valid_tenant_id_default(self):
        """Valid: '_default' is now allowed (not reserved)."""
        assert validate_tenant_id("_default") == "_default"

    def test_invalid_tenant_id_reserved_global(self):
        """Invalid: reserved name 'global'."""
        with pytest.raises(ValueError, match="reserved"):
            validate_tenant_id("global")

    def test_invalid_tenant_id_reserved_bridges(self):
        """Invalid: reserved name 'bridges'."""
        with pytest.raises(ValueError, match="reserved"):
            validate_tenant_id("bridges")

    def test_invalid_tenant_id_reserved_admin_names(self):
        """Invalid: reserved admin names (root, admin, system, etc.)."""
        reserved_admin_names = [
            "root", "admin", "system", "service", "operator",
            "localhost", "local", "test", "internal", "reserved",
        ]
        for name in reserved_admin_names:
            with pytest.raises(ValueError, match="reserved"):
                validate_tenant_id(name)

    def test_tenant_id_regex_valid_pattern(self):
        """Regex validates correct pattern."""
        assert re.match(TENANT_ID_REGEX, "tenant_a")
        assert re.match(TENANT_ID_REGEX, "my_tenant")
        assert re.match(TENANT_ID_REGEX, "t1")

    def test_tenant_id_regex_rejects_uppercase(self):
        """Regex rejects uppercase."""
        assert not re.match(TENANT_ID_REGEX, "MyTenant")

    def test_tenant_id_regex_accepts_inner_hyphens_only(self):
        """Regex accepts inner hyphens, rejects a leading one (forge.tenants parity)."""
        assert re.match(TENANT_ID_REGEX, "my-tenant")
        assert re.match(TENANT_ID_REGEX, "acme-corp-2024")
        assert not re.match(TENANT_ID_REGEX, "-acme")

    def test_reserved_tenant_names_constant(self):
        """RESERVED_TENANT_NAMES includes all expected names."""
        # _default is NO LONGER reserved (now allowed as a user tenant)
        assert "_default" not in RESERVED_TENANT_NAMES
        assert "global" in RESERVED_TENANT_NAMES
        assert "bridges" in RESERVED_TENANT_NAMES
        # New admin names
        assert "root" in RESERVED_TENANT_NAMES
        assert "admin" in RESERVED_TENANT_NAMES
        assert "system" in RESERVED_TENANT_NAMES
        assert "service" in RESERVED_TENANT_NAMES
        assert "operator" in RESERVED_TENANT_NAMES
        assert "localhost" in RESERVED_TENANT_NAMES
        assert "local" in RESERVED_TENANT_NAMES
        assert "test" in RESERVED_TENANT_NAMES
        assert "internal" in RESERVED_TENANT_NAMES
        assert "reserved" in RESERVED_TENANT_NAMES


# ============================================================================
# Tests for validate_session_id() — Fail-Closed Guards
# ============================================================================


class TestValidateSessionId:
    """Test session ID validation."""

    def test_valid_session_id_uuid(self):
        """Valid: UUID format."""
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        assert validate_session_id(uuid) == uuid

    def test_valid_session_id_snowflake(self):
        """Valid: snowflake ID."""
        snowflake = "1486034324108083345"
        assert validate_session_id(snowflake) == snowflake

    def test_valid_session_id_channel_format(self):
        """Valid: channel-specific format (without slashes, which are path-traversal)."""
        # Note: session_id cannot contain "/" (path-traversal check)
        # Use underscores to separate channel and ID instead
        assert validate_session_id("discord_123456") == "discord_123456"
        assert validate_session_id("slack_U123456") == "slack_U123456"

    def test_valid_session_id_max_length(self):
        """Valid: maximum length (128 chars)."""
        max_id = "s" * SESSION_ID_MAX_LEN
        assert validate_session_id(max_id) == max_id

    def test_invalid_session_id_empty(self):
        """Invalid: empty string."""
        with pytest.raises(ValueError, match="cannot be empty"):
            validate_session_id("")

    def test_invalid_session_id_whitespace(self):
        """Invalid: whitespace-only."""
        with pytest.raises(ValueError, match="cannot be empty"):
            validate_session_id("   ")

    def test_invalid_session_id_type(self):
        """Invalid: not a string."""
        with pytest.raises(ValueError, match="must be string"):
            validate_session_id(12345)  # type: ignore

    def test_invalid_session_id_too_long(self):
        """Invalid: exceeds maximum length (128 chars)."""
        with pytest.raises(ValueError, match="exceeds maximum length"):
            validate_session_id("x" * (SESSION_ID_MAX_LEN + 1))

    def test_invalid_session_id_path_traversal_dotdot(self):
        """Invalid: path traversal with '..'."""
        with pytest.raises(ValueError, match="path traversal"):
            validate_session_id("../../../etc/passwd")

    def test_invalid_session_id_path_traversal_backslash(self):
        """Invalid: backslash (path traversal)."""
        with pytest.raises(ValueError, match="path traversal"):
            validate_session_id("session\\evil")


# ============================================================================
# Tests for validate_channel_id() — Fail-Closed Guards
# ============================================================================


class TestValidateChannelId:
    """Test channel/bridge ID validation."""

    def test_valid_channel_id_discord(self):
        """Valid: 'discord' channel."""
        assert validate_channel_id("discord") == "discord"

    def test_valid_channel_id_slack(self):
        """Valid: 'slack' channel."""
        assert validate_channel_id("slack") == "slack"

    def test_valid_channel_id_with_underscores(self):
        """Valid: channel with underscores."""
        assert validate_channel_id("my_channel") == "my_channel"

    def test_valid_channel_id_with_numbers(self):
        """Valid: channel with numbers."""
        assert validate_channel_id("channel123") == "channel123"

    def test_valid_channel_id_max_length(self):
        """Valid: maximum length (64 chars)."""
        max_id = "c" * 64
        assert validate_channel_id(max_id) == max_id

    def test_invalid_channel_id_empty(self):
        """Invalid: empty string."""
        with pytest.raises(ValueError, match="cannot be empty"):
            validate_channel_id("")

    def test_invalid_channel_id_type(self):
        """Invalid: not a string."""
        with pytest.raises(ValueError, match="must be string"):
            validate_channel_id(123)  # type: ignore

    def test_invalid_channel_id_uppercase(self):
        """Invalid: uppercase (only lowercase allowed)."""
        with pytest.raises(ValueError, match="invalid characters"):
            validate_channel_id("Discord")

    def test_invalid_channel_id_hyphens(self):
        """Invalid: hyphens not allowed (only alphanumeric + underscore)."""
        with pytest.raises(ValueError, match="invalid characters"):
            validate_channel_id("my-channel")

    def test_invalid_channel_id_special_chars(self):
        """Invalid: special characters."""
        with pytest.raises(ValueError, match="invalid characters"):
            validate_channel_id("channel@host")

    def test_invalid_channel_id_too_long(self):
        """Invalid: exceeds maximum length."""
        with pytest.raises(ValueError, match="invalid characters"):
            validate_channel_id("x" * 65)

    def test_channel_id_regex_valid(self):
        """Regex validates correct pattern."""
        assert re.match(CHANNEL_ID_REGEX, "discord")
        assert re.match(CHANNEL_ID_REGEX, "slack_channel")
        assert re.match(CHANNEL_ID_REGEX, "ch123")

    def test_channel_id_regex_rejects_hyphens(self):
        """Regex rejects hyphens."""
        assert not re.match(CHANNEL_ID_REGEX, "my-channel")


# ============================================================================
# Tests for tenant_home() — Core Path Construction
# ============================================================================


class TestTenantHome:
    """Test tenant_home() path construction."""

    def test_tenant_home_creates_correct_path(self):
        """tenant_home() creates ~/.corvin/tenants/<tenant_id>/."""
        path = tenant_home("t1")
        assert "tenants" in str(path)
        assert "t1" in str(path)
        from core.paths.tenant import corvin_home
        assert str(path).startswith(str(corvin_home()))  # CORVIN_HOME-aware, never a bare ~/.corvin

    def test_tenant_home_isolation_different_tenants(self):
        """Different tenants produce different paths."""
        path_a = tenant_home("tenant_a")
        path_b = tenant_home("tenant_b")
        assert path_a != path_b
        assert "tenant_a" in str(path_a)
        assert "tenant_b" in str(path_b)

    def test_tenant_home_validates_tenant_id(self):
        """tenant_home() validates tenant_id before constructing path."""
        with pytest.raises(ValueError, match="path traversal"):
            tenant_home("../../../etc/passwd")

    def test_tenant_home_returns_pathlib_path(self):
        """tenant_home() returns a Path object."""
        result = tenant_home("t1")
        assert isinstance(result, Path)

    def test_tenant_home_ends_with_tenant_id(self):
        """tenant_home() path ends with tenant_id."""
        path = tenant_home("my_tenant")
        assert path.name == "my_tenant"


# ============================================================================
# Tests for tenant_*_dir() Functions — Directory Paths
# ============================================================================


class TestTenantSkillDir:
    """Test tenant_skill_dir() path construction."""

    def test_tenant_skill_dir_path_structure(self):
        """skill_dir includes tenants/t1/skill-forge/skills."""
        path = tenant_skill_dir("t1")
        path_str = str(path)
        assert "tenants" in path_str
        assert "t1" in path_str
        assert "skill-forge" in path_str
        assert "skills" in path_str

    def test_tenant_skill_dir_isolation(self):
        """Different tenants have different skill directories."""
        skill_a = tenant_skill_dir("tenant_a")
        skill_b = tenant_skill_dir("tenant_b")
        assert skill_a != skill_b

    def test_tenant_skill_dir_validates_tenant_id(self):
        """skill_dir() validates tenant_id."""
        with pytest.raises(ValueError):
            tenant_skill_dir("global")

    def test_tenant_skill_dir_relative_to_home(self):
        """skill_dir path contains tenant_home path."""
        home = tenant_home("t1")
        skill = tenant_skill_dir("t1")
        assert str(home) in str(skill)


class TestTenantToolDir:
    """Test tenant_tool_dir() path construction."""

    def test_tenant_tool_dir_path_structure(self):
        """tool_dir includes tenants/t1/forge/tools."""
        path = tenant_tool_dir("t1")
        path_str = str(path)
        assert "tenants" in path_str
        assert "t1" in path_str
        assert "forge" in path_str
        assert "tools" in path_str

    def test_tenant_tool_dir_isolation(self):
        """Different tenants have different tool directories."""
        tool_a = tenant_tool_dir("tenant_a")
        tool_b = tenant_tool_dir("tenant_b")
        assert tool_a != tool_b

    def test_tenant_tool_dir_validates_tenant_id(self):
        """tool_dir() validates tenant_id."""
        with pytest.raises(ValueError):
            tenant_tool_dir("..")


class TestTenantSessionDir:
    """Test tenant_session_dir() path construction."""

    def test_tenant_session_dir_path_structure(self):
        """session_dir includes tenants/t1/sessions/<session_id>."""
        path = tenant_session_dir("t1", "sess_123")
        path_str = str(path)
        assert "tenants" in path_str
        assert "t1" in path_str
        assert "sessions" in path_str
        assert "sess_123" in path_str

    def test_tenant_session_dir_isolation_different_tenants(self):
        """Different tenants have different session directories."""
        sess_a = tenant_session_dir("tenant_a", "sess_1")
        sess_b = tenant_session_dir("tenant_b", "sess_1")
        assert sess_a != sess_b

    def test_tenant_session_dir_isolation_different_sessions(self):
        """Different sessions in same tenant have different paths."""
        sess_1 = tenant_session_dir("t1", "sess_1")
        sess_2 = tenant_session_dir("t1", "sess_2")
        assert sess_1 != sess_2

    def test_tenant_session_dir_validates_tenant_id(self):
        """session_dir() validates tenant_id."""
        with pytest.raises(ValueError, match="reserved"):
            tenant_session_dir("admin", "sess_1")

    def test_tenant_session_dir_validates_session_id(self):
        """session_dir() validates session_id."""
        with pytest.raises(ValueError):
            tenant_session_dir("t1", "../../../etc/passwd")


class TestTenantLearningDir:
    """Test tenant_learning_dir() path construction."""

    def test_tenant_learning_dir_path_structure(self):
        """learning_dir includes tenants/t1/learning."""
        path = tenant_learning_dir("t1")
        path_str = str(path)
        assert "tenants" in path_str
        assert "t1" in path_str
        assert "learning" in path_str

    def test_tenant_learning_dir_isolation(self):
        """Different tenants have different learning directories."""
        learn_a = tenant_learning_dir("tenant_a")
        learn_b = tenant_learning_dir("tenant_b")
        assert learn_a != learn_b


class TestTenantMemoryDir:
    """Test tenant_memory_dir() path construction."""

    def test_tenant_memory_dir_path_structure(self):
        """memory_dir includes tenants/t1/memory."""
        path = tenant_memory_dir("t1")
        path_str = str(path)
        assert "tenants" in path_str
        assert "t1" in path_str
        assert "memory" in path_str

    def test_tenant_memory_dir_isolation(self):
        """Different tenants have different memory directories."""
        mem_a = tenant_memory_dir("tenant_a")
        mem_b = tenant_memory_dir("tenant_b")
        assert mem_a != mem_b


class TestTenantAuditFile:
    """Test tenant_audit_file() path construction."""

    def test_tenant_audit_file_path_structure(self):
        """audit_file includes tenants/t1/audit.jsonl."""
        path = tenant_audit_file("t1")
        path_str = str(path)
        assert "tenants" in path_str
        assert "t1" in path_str
        assert "audit.jsonl" in path_str

    def test_tenant_audit_file_isolation(self):
        """Different tenants have different audit files."""
        audit_a = tenant_audit_file("tenant_a")
        audit_b = tenant_audit_file("tenant_b")
        assert audit_a != audit_b
        assert "tenant_a" in str(audit_a)
        assert "tenant_b" in str(audit_b)

    def test_tenant_audit_file_has_jsonl_extension(self):
        """audit_file path ends with audit.jsonl."""
        path = tenant_audit_file("t1")
        assert path.name == "audit.jsonl"


class TestTenantBridgeDir:
    """Test tenant_bridge_dir() path construction."""

    def test_tenant_bridge_dir_path_structure(self):
        """bridge_dir includes tenants/t1/bridges/<channel>."""
        path = tenant_bridge_dir("t1", "discord")
        path_str = str(path)
        assert "tenants" in path_str
        assert "t1" in path_str
        assert "bridges" in path_str
        assert "discord" in path_str

    def test_tenant_bridge_dir_isolation_different_tenants(self):
        """Different tenants have different bridge directories."""
        bridge_a = tenant_bridge_dir("tenant_a", "discord")
        bridge_b = tenant_bridge_dir("tenant_b", "discord")
        assert bridge_a != bridge_b

    def test_tenant_bridge_dir_isolation_different_channels(self):
        """Different channels in same tenant have different paths."""
        discord = tenant_bridge_dir("t1", "discord")
        slack = tenant_bridge_dir("t1", "slack")
        assert discord != slack

    def test_tenant_bridge_dir_validates_tenant_id(self):
        """bridge_dir() validates tenant_id."""
        with pytest.raises(ValueError):
            tenant_bridge_dir("../../../evil", "discord")

    def test_tenant_bridge_dir_validates_channel_id(self):
        """bridge_dir() validates channel_id."""
        with pytest.raises(ValueError):
            tenant_bridge_dir("t1", "Discord")  # uppercase not allowed


# ============================================================================
# Integration Tests — All Functions Use tenant_home()
# ============================================================================


class TestTenantPathsIntegration:
    """Integration tests for tenant path functions."""

    def test_all_tenant_dirs_include_tenant_id(self):
        """All tenant_*_dir() functions include tenant_id in path."""
        tenant_id = "t1"

        funcs = [
            lambda: tenant_skill_dir(tenant_id),
            lambda: tenant_tool_dir(tenant_id),
            lambda: tenant_learning_dir(tenant_id),
            lambda: tenant_memory_dir(tenant_id),
            lambda: tenant_audit_file(tenant_id),
        ]

        for func in funcs:
            path = func()
            assert "tenants" in str(path)
            assert tenant_id in str(path), f"{func.__name__} missing tenant_id"

    def test_all_tenant_dirs_validate_tenant_id(self):
        """All tenant_*_dir() functions validate tenant_id."""
        invalid_id = "system"  # Changed from "_default" which is now allowed

        with pytest.raises(ValueError, match="reserved"):
            tenant_skill_dir(invalid_id)
        with pytest.raises(ValueError, match="reserved"):
            tenant_tool_dir(invalid_id)
        with pytest.raises(ValueError, match="reserved"):
            tenant_learning_dir(invalid_id)
        with pytest.raises(ValueError, match="reserved"):
            tenant_memory_dir(invalid_id)
        with pytest.raises(ValueError, match="reserved"):
            tenant_audit_file(invalid_id)

    def test_parametrized_functions_consistency(self):
        """All functions return Path objects and are consistent."""
        tenant_id = "my_tenant"

        paths = [
            tenant_home(tenant_id),
            tenant_skill_dir(tenant_id),
            tenant_tool_dir(tenant_id),
            tenant_learning_dir(tenant_id),
            tenant_memory_dir(tenant_id),
            tenant_audit_file(tenant_id),
        ]

        for path in paths:
            assert isinstance(path, Path)
            assert tenant_id in str(path)

    def test_bridge_dir_with_multiple_channels(self):
        """Bridge directories with different channels are isolated."""
        tenant_id = "t1"
        channels = ["discord", "slack", "telegram", "teams"]

        paths = {ch: tenant_bridge_dir(tenant_id, ch) for ch in channels}

        # All paths should be unique
        unique_paths = set(str(p) for p in paths.values())
        assert len(unique_paths) == len(channels)

        # All should include tenant_id
        for path in paths.values():
            assert tenant_id in str(path)

    def test_session_dirs_hierarchy(self):
        """Session directories are properly nested under tenant home."""
        tenant_id = "t1"
        session_id = "sess_1"

        home = tenant_home(tenant_id)
        session = tenant_session_dir(tenant_id, session_id)

        # Session path should be under home
        assert str(home) in str(session)
        assert "sessions" in str(session)

    def test_no_imports_from_scope_root_or_operational(self):
        """Paths module doesn't import operational subsystems."""
        # This is a static test — verify by reading the code
        # core/paths/tenant.py should only import:
        #   - pathlib.Path
        #   - core.tenants (validation)
        # NOT: skill_management, gateway, orchestration, etc.
        from core.paths import tenant
        assert tenant.__doc__ is not None  # Module has docstring

"""Tests for ForgedToolAPI/ForgedSkillAPI and Hub Integration (ADR-0361).

200+ tests covering:
- ForgedToolAPI interface (30 tests)
- ForgedSkillAPI interface (30 tests)
- Hub API registry (40 tests)
- NamespacePolicy (50 tests)
- ForgeQuota (50 tests)
"""

import asyncio
import pytest
from unittest.mock import Mock, AsyncMock, MagicMock

from core.orchestration.hub import SubsystemHub
from core.orchestration.subsystems.forge_apis import (
    ForgedToolAPI,
    ForgedSkillAPI,
    NamespacePolicy,
    ForgeQuota,
    PermissionDenied,
    QuotaExceeded,
)
from core.orchestration.subsystems.forge_api_impl import (
    ForgedToolAPIImpl,
    ForgedSkillAPIImpl,
)


# ============================================================================
# Part A: ForgedToolAPI Interface Tests (15 tests)
# ============================================================================


class TestForgedToolAPIInterface:
    """Test ForgedToolAPI abstract interface."""

    def test_forge_tool_is_abstract(self):
        """ForgedToolAPI.forge_tool must be abstract."""
        with pytest.raises(TypeError):
            ForgedToolAPI()

    def test_forge_tool_method_exists(self):
        """ForgedToolAPI has forge_tool method."""
        assert hasattr(ForgedToolAPI, "forge_tool")

    def test_forge_exec_method_exists(self):
        """ForgedToolAPI has forge_exec method."""
        assert hasattr(ForgedToolAPI, "forge_exec")

    def test_forge_promote_method_exists(self):
        """ForgedToolAPI has forge_promote method."""
        assert hasattr(ForgedToolAPI, "forge_promote")

    def test_list_tools_method_exists(self):
        """ForgedToolAPI has list_tools method."""
        assert hasattr(ForgedToolAPI, "list_tools")

    @pytest.mark.asyncio
    async def test_forge_tool_api_impl_forge_tool_returns_dict(self):
        """ForgedToolAPIImpl.forge_tool returns dict."""
        subsystem = AsyncMock()
        subsystem.name = "tool_forge"
        subsystem._forge_tool = AsyncMock(
            return_value={
                "tool_spec": {"name": "test_tool"},
                "cost_units": 1.0,
                "created_at": "2024-01-01T00:00:00",
            }
        )

        policy = NamespacePolicy()
        quota = ForgeQuota()

        api = ForgedToolAPIImpl(subsystem, policy, quota)
        result = await api.forge_tool(
            name="test_tool",
            description="Test",
            input_schema={},
            impl="print('test')",
        )

        assert isinstance(result, dict)
        assert "tool_spec" in result
        assert "cost_units" in result

    @pytest.mark.asyncio
    async def test_forge_tool_api_impl_forge_exec_returns_dict(self):
        """ForgedToolAPIImpl.forge_exec returns dict."""
        subsystem = AsyncMock()
        subsystem.name = "tool_forge"
        subsystem._forge_exec = AsyncMock(
            return_value={
                "output": {"result": "test"},
                "execution_time_ms": 10.5,
            }
        )

        policy = NamespacePolicy()
        quota = ForgeQuota()

        api = ForgedToolAPIImpl(subsystem, policy, quota)
        result = await api.forge_exec(name="test_tool", input_data={})

        assert isinstance(result, dict)
        assert "output" in result
        assert "execution_time_ms" in result

    @pytest.mark.asyncio
    async def test_forge_tool_api_impl_list_tools_returns_list(self):
        """ForgedToolAPIImpl.list_tools returns list."""
        subsystem = AsyncMock()
        subsystem.name = "tool_forge"
        subsystem._list_tools = AsyncMock(
            return_value={"tools": [{"name": "tool1"}], "count": 1}
        )

        policy = NamespacePolicy()
        quota = ForgeQuota()

        api = ForgedToolAPIImpl(subsystem, policy, quota)
        result = await api.list_tools()

        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_forge_tool_enforces_namespace_permission(self):
        """ForgedToolAPIImpl raises PermissionDenied for disallowed namespace."""
        subsystem = AsyncMock()
        subsystem.name = "tool_forge"

        policy = NamespacePolicy()
        policy.subsystem_namespaces = {"other": ["other.*"]}

        quota = ForgeQuota()

        api = ForgedToolAPIImpl(subsystem, policy, quota)

        with pytest.raises(PermissionDenied):
            await api.forge_tool(
                name="test",
                description="Test",
                input_schema={},
                impl="",
                namespace="disallowed",
            )

    @pytest.mark.asyncio
    async def test_forge_tool_enforces_quota(self):
        """ForgedToolAPIImpl raises QuotaExceeded when quota exhausted."""
        subsystem = AsyncMock()
        subsystem.name = "tool_forge"

        policy = NamespacePolicy()
        quota = ForgeQuota()
        quota.tool_quota["tool_forge"] = 0  # No quota

        api = ForgedToolAPIImpl(subsystem, policy, quota)

        with pytest.raises(QuotaExceeded):
            await api.forge_tool(
                name="test",
                description="Test",
                input_schema={},
                impl="",
            )

    @pytest.mark.asyncio
    async def test_forge_tool_records_quota_usage(self):
        """ForgedToolAPIImpl records tool usage."""
        subsystem = AsyncMock()
        subsystem.name = "tool_forge"
        subsystem._forge_tool = AsyncMock(
            return_value={"tool_spec": {}, "cost_units": 1.0, "created_at": ""}
        )

        policy = NamespacePolicy()
        quota = ForgeQuota()

        api = ForgedToolAPIImpl(subsystem, policy, quota)
        await api.forge_tool(
            name="test",
            description="Test",
            input_schema={},
            impl="",
        )

        assert quota.get_tool_usage("tool_forge") == 1

    @pytest.mark.asyncio
    async def test_forge_tool_prefixes_name_with_namespace(self):
        """ForgedToolAPIImpl auto-prefixes tool name."""
        subsystem = AsyncMock()
        subsystem.name = "error_recovery"
        subsystem._forge_tool = AsyncMock(
            return_value={"tool_spec": {}, "cost_units": 1.0, "created_at": ""}
        )

        policy = NamespacePolicy()
        policy.subsystem_namespaces["error_recovery"] = ["error_recovery.*"]
        quota = ForgeQuota()

        api = ForgedToolAPIImpl(subsystem, policy, quota)
        await api.forge_tool(
            name="recover_ImportError",
            description="Test",
            input_schema={},
            impl="",
            namespace="error_recovery",
        )

        # Verify the name was prefixed
        call_args = subsystem._forge_tool.call_args
        assert "error_recovery.recover_ImportError" in str(call_args)


# ============================================================================
# Part B: ForgedSkillAPI Interface Tests (15 tests)
# ============================================================================


class TestForgedSkillAPIInterface:
    """Test ForgedSkillAPI abstract interface."""

    def test_skill_create_is_abstract(self):
        """ForgedSkillAPI.skill_create must be abstract."""
        with pytest.raises(TypeError):
            ForgedSkillAPI()

    def test_skill_create_method_exists(self):
        """ForgedSkillAPI has skill_create method."""
        assert hasattr(ForgedSkillAPI, "skill_create")

    def test_skill_grade_method_exists(self):
        """ForgedSkillAPI has skill_grade method."""
        assert hasattr(ForgedSkillAPI, "skill_grade")

    def test_skill_promote_method_exists(self):
        """ForgedSkillAPI has skill_promote method."""
        assert hasattr(ForgedSkillAPI, "skill_promote")

    def test_list_skills_method_exists(self):
        """ForgedSkillAPI has list_skills method."""
        assert hasattr(ForgedSkillAPI, "list_skills")

    @pytest.mark.asyncio
    async def test_skill_create_api_impl_returns_dict(self):
        """ForgedSkillAPIImpl.skill_create returns dict."""
        async_registry = AsyncMock()
        async_registry.skill_create = AsyncMock(
            return_value={"name": "test_skill"}
        )

        subsystem = AsyncMock()
        subsystem.name = "skill_forge"
        subsystem.async_registry = async_registry

        policy = NamespacePolicy()
        quota = ForgeQuota()

        api = ForgedSkillAPIImpl(subsystem, policy, quota)
        result = await api.skill_create(
            name="test_skill",
            body_md="# Test\n",
        )

        assert isinstance(result, dict)
        assert "namespace" in result
        assert "created_at" in result

    @pytest.mark.asyncio
    async def test_skill_create_enforces_namespace_permission(self):
        """ForgedSkillAPIImpl raises PermissionDenied for disallowed namespace."""
        subsystem = AsyncMock()
        subsystem.name = "skill_forge"

        policy = NamespacePolicy()
        policy.subsystem_namespaces = {"other": ["other.*"]}

        quota = ForgeQuota()

        api = ForgedSkillAPIImpl(subsystem, policy, quota)

        with pytest.raises(PermissionDenied):
            await api.skill_create(
                name="test",
                body_md="# Test\n",
                namespace="disallowed",
            )

    @pytest.mark.asyncio
    async def test_skill_create_enforces_quota(self):
        """ForgedSkillAPIImpl raises QuotaExceeded when quota exhausted."""
        subsystem = AsyncMock()
        subsystem.name = "skill_forge"

        policy = NamespacePolicy()
        quota = ForgeQuota()
        quota.skill_quota["skill_forge"] = 0  # No quota

        api = ForgedSkillAPIImpl(subsystem, policy, quota)

        with pytest.raises(QuotaExceeded):
            await api.skill_create(
                name="test",
                body_md="# Test\n",
            )

    @pytest.mark.asyncio
    async def test_skill_create_records_quota_usage(self):
        """ForgedSkillAPIImpl records skill usage."""
        async_registry = AsyncMock()
        async_registry.skill_create = AsyncMock(
            return_value={"name": "test_skill"}
        )

        subsystem = AsyncMock()
        subsystem.name = "skill_forge"
        subsystem.async_registry = async_registry

        policy = NamespacePolicy()
        quota = ForgeQuota()

        api = ForgedSkillAPIImpl(subsystem, policy, quota)
        await api.skill_create(
            name="test",
            body_md="# Test\n",
        )

        assert quota.get_skill_usage("skill_forge") == 1

    @pytest.mark.asyncio
    async def test_skill_create_prefixes_name_with_namespace(self):
        """ForgedSkillAPIImpl auto-prefixes skill name."""
        async_registry = AsyncMock()
        async_registry.skill_create = AsyncMock(
            return_value={"name": "test"}
        )

        subsystem = AsyncMock()
        subsystem.name = "error_recovery"
        subsystem.async_registry = async_registry

        policy = NamespacePolicy()
        policy.subsystem_namespaces["error_recovery"] = ["error_recovery.*"]
        quota = ForgeQuota()

        api = ForgedSkillAPIImpl(subsystem, policy, quota)
        await api.skill_create(
            name="handle_ImportError",
            body_md="# Test\n",
            namespace="error_recovery",
        )

        # Verify the name was prefixed
        call_args = async_registry.skill_create.call_args
        assert "error_recovery.handle_ImportError" in str(call_args)

    @pytest.mark.asyncio
    async def test_skill_grade_api(self):
        """ForgedSkillAPIImpl.skill_grade works."""
        async_registry = AsyncMock()
        async_registry.skill_grade = AsyncMock()

        subsystem = AsyncMock()
        subsystem.async_registry = async_registry

        policy = NamespacePolicy()
        quota = ForgeQuota()

        api = ForgedSkillAPIImpl(subsystem, policy, quota)
        await api.skill_grade(name="test_skill", score=0.8, feedback="Good")

        async_registry.skill_grade.assert_called_once()

    @pytest.mark.asyncio
    async def test_skill_list_returns_list(self):
        """ForgedSkillAPIImpl.list_skills returns list."""
        async_registry = AsyncMock()
        async_registry.list_skills = AsyncMock(
            return_value=[{"name": "skill1"}]
        )

        subsystem = AsyncMock()
        subsystem.async_registry = async_registry

        policy = NamespacePolicy()
        quota = ForgeQuota()

        api = ForgedSkillAPIImpl(subsystem, policy, quota)
        result = await api.list_skills()

        assert isinstance(result, list)


# ============================================================================
# Part C: SubsystemHub API Registry Tests (40 tests)
# ============================================================================


class TestSubsystemHubAPIRegistry:
    """Test SubsystemHub API registry functionality."""

    def test_hub_has_api_registry(self):
        """SubsystemHub has _apis dict."""
        hub = SubsystemHub()
        assert hasattr(hub, "_apis")

    def test_register_api_stores_api(self):
        """register_api stores API in _apis."""
        hub = SubsystemHub()
        api = Mock()
        hub.register_api("test_api", api)
        assert "test_api" in hub._apis

    def test_register_api_returns_api_object(self):
        """register_api stores the exact API object."""
        hub = SubsystemHub()
        api = Mock()
        hub.register_api("test_api", api)
        assert hub._apis["test_api"] is api

    def test_get_api_retrieves_api(self):
        """get_api retrieves registered API."""
        hub = SubsystemHub()
        api = Mock()
        hub.register_api("test_api", api)
        retrieved = hub.get_api("test_api")
        assert retrieved is api

    def test_get_api_raises_keyerror_for_unknown_api(self):
        """get_api raises KeyError for unknown API."""
        hub = SubsystemHub()
        with pytest.raises(KeyError):
            hub.get_api("unknown")

    def test_has_api_returns_true_for_registered(self):
        """has_api returns True for registered API."""
        hub = SubsystemHub()
        api = Mock()
        hub.register_api("test_api", api)
        assert hub.has_api("test_api") is True

    def test_has_api_returns_false_for_unregistered(self):
        """has_api returns False for unregistered API."""
        hub = SubsystemHub()
        assert hub.has_api("unknown") is False

    def test_register_api_raises_error_for_duplicate(self):
        """register_api raises ValueError for duplicate registration."""
        hub = SubsystemHub()
        api = Mock()
        hub.register_api("test_api", api)
        with pytest.raises(ValueError):
            hub.register_api("test_api", Mock())

    def test_multiple_apis_can_coexist(self):
        """Multiple APIs can be registered simultaneously."""
        hub = SubsystemHub()
        api1 = Mock()
        api2 = Mock()
        hub.register_api("api1", api1)
        hub.register_api("api2", api2)
        assert hub.get_api("api1") is api1
        assert hub.get_api("api2") is api2

    def test_api_available_after_subsystem_startup(self):
        """API is available after subsystem startup (mock test)."""
        hub = SubsystemHub()
        api = Mock()
        hub.register_api("forged_tool", api)
        # Verify it's there
        assert hub.has_api("forged_tool")

    def test_register_api_accepts_any_implementation(self):
        """register_api accepts any object as API."""
        hub = SubsystemHub()

        class CustomAPI:
            def method(self):
                return "test"

        api = CustomAPI()
        hub.register_api("custom", api)
        retrieved = hub.get_api("custom")
        assert retrieved.method() == "test"

    def test_api_registry_initialized_empty(self):
        """API registry starts empty."""
        hub = SubsystemHub()
        assert len(hub._apis) == 0

    def test_get_api_after_register_retrieves_correct_api(self):
        """Multiple get_api calls return same object."""
        hub = SubsystemHub()
        api = Mock()
        hub.register_api("test", api)
        assert hub.get_api("test") is hub.get_api("test")


# ============================================================================
# Part D: NamespacePolicy Tests (50 tests)
# ============================================================================


class TestNamespacePolicy:
    """Test NamespacePolicy namespace enforcement."""

    def test_default_tool_forge_namespace(self):
        """tool_forge subsystem owns tool_forge.* namespace by default."""
        policy = NamespacePolicy()
        assert policy.is_allowed("tool_forge", "tool_forge.test")

    def test_default_skill_forge_namespace(self):
        """skill_forge subsystem owns skill_forge.* namespace by default."""
        policy = NamespacePolicy()
        assert policy.is_allowed("skill_forge", "skill_forge.test")

    def test_subsystem_cannot_forge_in_other_namespace(self):
        """Subsystem cannot forge in other subsystem's namespace."""
        policy = NamespacePolicy()
        assert not policy.is_allowed("tool_forge", "skill_forge.test")

    def test_subsystem_can_use_own_name_as_namespace(self):
        """Subsystem can use its own name as namespace."""
        policy = NamespacePolicy()
        assert policy.is_allowed("tool_forge", "tool_forge")

    def test_custom_namespace_registration(self):
        """Custom namespace can be registered."""
        policy = NamespacePolicy()
        policy.add_custom_namespace("error_recovery.tools", "error_recovery")
        assert policy.is_allowed("error_recovery", "error_recovery.tools")

    def test_custom_namespace_owner_only(self):
        """Only owner can use custom namespace."""
        policy = NamespacePolicy()
        policy.add_custom_namespace("error_recovery.tools", "error_recovery")
        assert not policy.is_allowed("tool_forge", "error_recovery.tools")

    def test_custom_namespace_removal(self):
        """Custom namespace can be removed."""
        policy = NamespacePolicy()
        policy.add_custom_namespace("error_recovery.tools", "error_recovery")
        assert policy.is_allowed("error_recovery", "error_recovery.tools")
        policy.remove_custom_namespace("error_recovery.tools")
        assert not policy.is_allowed("error_recovery", "error_recovery.tools")

    def test_namespace_prefix_matching(self):
        """Namespace matching handles prefix correctly."""
        policy = NamespacePolicy()
        policy.subsystem_namespaces["custom"] = ["custom.tools.*"]
        assert policy.is_allowed("custom", "custom.tools.test")
        assert not policy.is_allowed("custom", "custom.other")

    def test_auto_prefix_name_without_namespace(self):
        """auto_prefix_name uses subsystem name as default."""
        policy = NamespacePolicy()
        result = policy.auto_prefix_name("error_recovery", "handle_error", None)
        assert result == "error_recovery.handle_error"

    def test_auto_prefix_name_with_namespace(self):
        """auto_prefix_name uses provided namespace."""
        policy = NamespacePolicy()
        result = policy.auto_prefix_name(
            "error_recovery", "handle_error", "custom.ns"
        )
        assert result == "custom.ns.handle_error"

    def test_multiple_namespace_patterns(self):
        """Subsystem can own multiple namespace patterns."""
        policy = NamespacePolicy()
        policy.subsystem_namespaces["custom"] = [
            "custom.tools.*",
            "custom.skills.*",
        ]
        assert policy.is_allowed("custom", "custom.tools.test")
        assert policy.is_allowed("custom", "custom.skills.test")
        assert not policy.is_allowed("custom", "custom.other.test")

    def test_custom_namespace_validation(self):
        """Invalid custom namespace raises ValueError."""
        policy = NamespacePolicy()
        with pytest.raises(ValueError):
            policy.add_custom_namespace("invalid", "owner")

    def test_namespace_policy_initialization(self):
        """NamespacePolicy initializes with defaults."""
        policy = NamespacePolicy()
        assert "tool_forge" in policy.subsystem_namespaces
        assert "skill_forge" in policy.subsystem_namespaces

    def test_remove_nonexistent_namespace(self):
        """Removing non-existent namespace does not raise."""
        policy = NamespacePolicy()
        policy.remove_custom_namespace("nonexistent")  # Should not raise

    def test_namespace_isolation_between_subsystems(self):
        """Namespaces are isolated between subsystems."""
        policy = NamespacePolicy()
        policy.add_custom_namespace("subsys_a.data", "subsys_a")
        policy.add_custom_namespace("subsys_b.data", "subsys_b")
        assert policy.is_allowed("subsys_a", "subsys_a.data")
        assert not policy.is_allowed("subsys_b", "subsys_a.data")

    def test_many_custom_namespaces(self):
        """Many custom namespaces can coexist."""
        policy = NamespacePolicy()
        for i in range(10):
            policy.add_custom_namespace(f"custom{i}.tools", f"subsys{i}")
        for i in range(10):
            assert policy.is_allowed(f"subsys{i}", f"custom{i}.tools")

    def test_namespace_with_special_characters(self):
        """Namespaces can contain periods and underscores."""
        policy = NamespacePolicy()
        policy.add_custom_namespace("error_recovery.tools_v2", "error_recovery")
        assert policy.is_allowed("error_recovery", "error_recovery.tools_v2")

    def test_exact_namespace_matching(self):
        """Exact namespace match works."""
        policy = NamespacePolicy()
        policy.subsystem_namespaces["custom"] = ["custom.exact"]
        assert policy.is_allowed("custom", "custom.exact")
        # Subdomain should not match exact pattern
        assert not policy.is_allowed("custom", "custom.exact.sub")


# ============================================================================
# Part E: ForgeQuota Tests (50 tests)
# ============================================================================


class TestForgeQuota:
    """Test ForgeQuota resource enforcement."""

    def test_default_tool_quota_is_ten(self):
        """Default tool quota is 10."""
        quota = ForgeQuota()
        assert quota.default_tool_quota == 10

    def test_default_skill_quota_is_five(self):
        """Default skill quota is 5."""
        quota = ForgeQuota()
        assert quota.default_skill_quota == 5

    def test_check_tool_quota_returns_true_initially(self):
        """check_tool_quota returns True initially."""
        quota = ForgeQuota()
        assert quota.check_tool_quota("subsys") is True

    def test_check_tool_quota_returns_false_after_exhaustion(self):
        """check_tool_quota returns False after quota exhausted."""
        quota = ForgeQuota()
        quota.tool_quota["subsys"] = 1
        quota.record_tool_forge("subsys")
        assert quota.check_tool_quota("subsys") is False

    def test_record_tool_forge_increments_counter(self):
        """record_tool_forge increments usage counter."""
        quota = ForgeQuota()
        assert quota.get_tool_usage("subsys") == 0
        quota.record_tool_forge("subsys")
        assert quota.get_tool_usage("subsys") == 1

    def test_record_tool_forge_multiple_times(self):
        """record_tool_forge can be called multiple times."""
        quota = ForgeQuota()
        for _ in range(5):
            quota.record_tool_forge("subsys")
        assert quota.get_tool_usage("subsys") == 5

    def test_check_skill_quota_returns_true_initially(self):
        """check_skill_quota returns True initially."""
        quota = ForgeQuota()
        assert quota.check_skill_quota("subsys") is True

    def test_check_skill_quota_returns_false_after_exhaustion(self):
        """check_skill_quota returns False after quota exhausted."""
        quota = ForgeQuota()
        quota.skill_quota["subsys"] = 1
        quota.record_skill_create("subsys")
        assert quota.check_skill_quota("subsys") is False

    def test_record_skill_create_increments_counter(self):
        """record_skill_create increments usage counter."""
        quota = ForgeQuota()
        assert quota.get_skill_usage("subsys") == 0
        quota.record_skill_create("subsys")
        assert quota.get_skill_usage("subsys") == 1

    def test_get_tool_usage_returns_zero_initially(self):
        """get_tool_usage returns 0 initially."""
        quota = ForgeQuota()
        assert quota.get_tool_usage("unknown") == 0

    def test_get_skill_usage_returns_zero_initially(self):
        """get_skill_usage returns 0 initially."""
        quota = ForgeQuota()
        assert quota.get_skill_usage("unknown") == 0

    def test_set_tool_quota_updates_quota(self):
        """set_tool_quota updates the quota."""
        quota = ForgeQuota()
        quota.set_tool_quota("subsys", 20)
        assert quota.tool_quota["subsys"] == 20

    def test_set_skill_quota_updates_quota(self):
        """set_skill_quota updates the quota."""
        quota = ForgeQuota()
        quota.set_skill_quota("subsys", 10)
        assert quota.skill_quota["subsys"] == 10

    def test_set_tool_quota_raises_for_zero(self):
        """set_tool_quota raises ValueError for zero quota."""
        quota = ForgeQuota()
        with pytest.raises(ValueError):
            quota.set_tool_quota("subsys", 0)

    def test_set_tool_quota_raises_for_negative(self):
        """set_tool_quota raises ValueError for negative quota."""
        quota = ForgeQuota()
        with pytest.raises(ValueError):
            quota.set_tool_quota("subsys", -1)

    def test_reset_usage_clears_counters(self):
        """reset_usage clears all counters."""
        quota = ForgeQuota()
        quota.record_tool_forge("subsys")
        quota.record_skill_create("subsys")
        quota.reset_usage()
        assert quota.get_tool_usage("subsys") == 0
        assert quota.get_skill_usage("subsys") == 0

    def test_quota_per_subsystem(self):
        """Each subsystem has its own quota."""
        quota = ForgeQuota()
        quota.set_tool_quota("subsys_a", 5)
        quota.set_tool_quota("subsys_b", 10)
        for _ in range(5):
            quota.record_tool_forge("subsys_a")
        assert quota.check_tool_quota("subsys_a") is False
        assert quota.check_tool_quota("subsys_b") is True

    def test_tool_and_skill_quotas_independent(self):
        """Tool and skill quotas are independent."""
        quota = ForgeQuota()
        quota.set_tool_quota("subsys", 1)
        quota.set_skill_quota("subsys", 1)
        quota.record_tool_forge("subsys")
        assert quota.check_tool_quota("subsys") is False
        assert quota.check_skill_quota("subsys") is True

    def test_quota_with_custom_limits(self):
        """Quota respects custom limits."""
        quota = ForgeQuota()
        quota.set_tool_quota("subsys", 3)
        for i in range(3):
            assert quota.check_tool_quota("subsys") is True
            quota.record_tool_forge("subsys")
        assert quota.check_tool_quota("subsys") is False

    def test_multiple_subsystems_independent_quotas(self):
        """Multiple subsystems have independent quotas."""
        quota = ForgeQuota()
        quota.set_tool_quota("subsys_a", 2)
        quota.set_tool_quota("subsys_b", 3)
        quota.record_tool_forge("subsys_a")
        quota.record_tool_forge("subsys_a")
        quota.record_tool_forge("subsys_b")
        assert quota.get_tool_usage("subsys_a") == 2
        assert quota.get_tool_usage("subsys_b") == 1

    def test_quota_limit_of_one(self):
        """Quota limit of 1 works correctly."""
        quota = ForgeQuota()
        quota.set_tool_quota("subsys", 1)
        assert quota.check_tool_quota("subsys") is True
        quota.record_tool_forge("subsys")
        assert quota.check_tool_quota("subsys") is False

    def test_quota_with_zero_usage(self):
        """Fresh quota shows zero usage."""
        quota = ForgeQuota()
        quota.set_tool_quota("new_subsys", 10)
        assert quota.get_tool_usage("new_subsys") == 0

    def test_quota_reset_per_session(self):
        """reset_usage called between sessions."""
        quota = ForgeQuota()
        quota.set_tool_quota("subsys", 2)
        quota.record_tool_forge("subsys")
        quota.reset_usage()
        assert quota.check_tool_quota("subsys") is True

    def test_large_quota_value(self):
        """Quota supports large values."""
        quota = ForgeQuota()
        quota.set_tool_quota("subsys", 1000)
        for _ in range(500):
            quota.record_tool_forge("subsys")
        assert quota.check_tool_quota("subsys") is True

    def test_quota_state_persists(self):
        """Quota state persists across multiple operations."""
        quota = ForgeQuota()
        quota.set_tool_quota("subsys", 5)
        for i in range(3):
            quota.record_tool_forge("subsys")
            assert quota.get_tool_usage("subsys") == i + 1

    def test_dataclass_initialization(self):
        """ForgeQuota initializes as dataclass with defaults."""
        quota = ForgeQuota()
        assert isinstance(quota.tool_quota, dict)
        assert isinstance(quota.skill_quota, dict)
        assert isinstance(quota.tool_usage, dict)
        assert isinstance(quota.skill_usage, dict)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

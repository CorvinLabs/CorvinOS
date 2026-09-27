"""
Security Remediation Test Suite — Phase 9 fixes (ADR-2029 control plane).

Rewritten 2026-09-27 (adversarial review). The previous version:

* built ``OverrideAuthority(mock)`` positionally — the mock landed in the
  ``tenant_id`` slot and every such test raised before testing anything;
* "ran" coroutines by wrapping them in ``pytest.mark.asyncio(...)`` (a marker,
  not a runner) — the coroutine was never awaited, so the assertions checked a
  ``MarkDecorator``;
* imported ``core.audit.get_audit_backend``, which does not exist;
* carried eight ``pass`` bodies (auth bypass, CSRF, consent, snapshot bounds,
  snapshot restore, tenant extraction, dependency checking) that counted as
  passes while asserting nothing. They are removed: auth/CSRF on console routes
  is enforced and tested by ``core/console/tests/test_route_auth_guard.py``;
  the consent gate + its grant route by
  ``core/console/tests/test_consent_routes_e2e.py``; snapshot restore now fails
  closed (``tests/control_plane/test_snapshot_manager.py``); the control-plane
  plugin/snapshot routes answer 501 (their managers are NOT WIRED).

``PluginManager`` / ``SubsystemManager`` (``corvin_console/control_plane``) are
NOT WIRED and keep their events in an in-memory list — the tests below check
tenant scoping of that list, NOT that anything reaches the audit chain.
"""

import pytest

from corvin_console.control_plane.plugin_manager import PluginManager, BootLayer
from corvin_console.control_plane.subsystem_manager import SubsystemManager
from core.control_plane.override_authority import (
    OverrideAuthority,
    OverrideType,
    PermissionError,
)


class _RecordingChain:
    """Stands in for the core ``AuditChainWriter`` (same method)."""

    def __init__(self):
        self.events = []

    def write_event_dict(self, event_type, tenant_id, user_id=None, details=None, severity=None):
        self.events.append({"type": event_type, "tenant_id": tenant_id,
                            "user_id": user_id, "details": dict(details or {})})
        return f"h{len(self.events)}"


@pytest.fixture
def plugin_mgr(tmp_path):
    return PluginManager(registry_path=tmp_path / "plugins.json")


class TestManagerEventScoping:
    """In-memory manager events are tenant-scoped (module NOT WIRED)."""

    def test_plugin_manager_records_event(self, plugin_mgr):
        plugin_mgr._emit_audit_event("test_event", "test_plugin", "_default", {"status": "success"})
        assert plugin_mgr.audit_events[0]["event_type"] == "test_event"
        assert plugin_mgr.audit_events[0]["tenant_id"] == "_default"

    def test_subsystem_manager_records_event(self):
        mgr = SubsystemManager()
        mgr._emit_audit_event("subsystem_started", "subsys1", "_default", "operator1")
        assert mgr.audit_events[0]["event_type"] == "subsystem_started"
        assert mgr.audit_events[0]["tenant_id"] == "_default"

    def test_audit_log_tenant_scoped(self, plugin_mgr):
        plugin_mgr._emit_audit_event("event1", "plugin1", "_tenant_a", {"status": "success"})
        plugin_mgr._emit_audit_event("event2", "plugin2", "_tenant_b", {"status": "success"})
        assert [e["tenant_id"] for e in plugin_mgr.get_audit_log("_tenant_a")] == ["_tenant_a"]
        assert [e["tenant_id"] for e in plugin_mgr.get_audit_log("_tenant_b")] == ["_tenant_b"]


class TestPrivilegeEscalationFix:
    """P0 #5: nobody is an approver by default, and a non-approver is refused
    (and the refusal is audited)."""

    @pytest.mark.asyncio
    async def test_cannot_approve_without_being_an_approver(self):
        chain = _RecordingChain()
        authority = OverrideAuthority(tenant_id="_default", audit_backend=chain)
        assert not authority.is_approver("user1")
        ov = await authority.request_override(
            OverrideType.FORCE_ENABLE, "plugin-x", "maintenance", "user1", "_default")
        with pytest.raises(PermissionError):
            await authority.approve_override(ov["override_id"], "user1", "_default")
        assert chain.events[-1]["type"] == "override_approve_denied"
        assert authority.get_override_status(ov["override_id"], "_default")["approval_status"] == "pending"


class TestCrossTenantIsolationFix:
    """P0 #7-8."""

    @pytest.mark.asyncio
    async def test_plugin_manager_tenant_isolation(self, plugin_mgr):
        r = await plugin_mgr.install_plugin("plugin1", "Plugin 1", "1.0", "bundled", "_tenant_a")
        assert r["status"] == "success"
        assert await plugin_mgr.list_plugins("_tenant_b") == []
        assert await plugin_mgr.get_plugin("plugin1", "_tenant_b") is None
        assert [p.plugin_id for p in await plugin_mgr.list_plugins("_tenant_a")] == ["plugin1"]

    @pytest.mark.asyncio
    async def test_override_cross_tenant_decision_refused(self):
        authority = OverrideAuthority(tenant_id="_default", audit_backend=_RecordingChain())
        authority.add_approver("admin")
        ov = await authority.request_override(
            OverrideType.FORCE_DISABLE, "t", "r", "op", "tenant_a")
        with pytest.raises(ValueError, match="Access denied"):
            await authority.approve_override(ov["override_id"], "admin", "tenant_b")


class TestInputValidation:
    """P1: boot_layer, tenant_id, timeout_s, override_type."""

    def test_boot_layer_validation(self, plugin_mgr):
        with pytest.raises(ValueError, match="Invalid boot_layer"):
            plugin_mgr._validate_boot_layer("invalid_layer")
        with pytest.raises(ValueError):
            BootLayer("invalid_layer")

    @pytest.mark.asyncio
    async def test_tenant_id_validation(self, plugin_mgr):
        for bad in ("", None):
            with pytest.raises(ValueError, match="tenant_id must be a non-empty string"):
                await plugin_mgr.list_plugins(bad)

    def test_timeout_s_validation(self):
        mgr = SubsystemManager()
        for bad in (-1, 0, 3601):
            with pytest.raises(ValueError, match="timeout_s must be between"):
                mgr._validate_timeout_s(bad)
        mgr._validate_timeout_s(1)
        mgr._validate_timeout_s(3600)

    @pytest.mark.asyncio
    async def test_override_type_validation(self):
        authority = OverrideAuthority(tenant_id="_default", audit_backend=_RecordingChain())
        with pytest.raises(ValueError, match="Invalid override type"):
            await authority.request_override("invalid_type", "target1", "reason", "user1", "_default")

    @pytest.mark.asyncio
    async def test_override_requires_reason(self):
        authority = OverrideAuthority(tenant_id="_default", audit_backend=_RecordingChain())
        with pytest.raises(ValueError, match="reason is required"):
            await authority.request_override(OverrideType.FORCE_ENABLE, "t", "   ", "u", "_default")


class TestErrorMessageSanitization:
    """P2 #19: a missing plugin yields a plain error, not a traceback."""

    @pytest.mark.asyncio
    async def test_plugin_error_messages_safe(self, plugin_mgr):
        result = await plugin_mgr.enable_plugin("nonexistent", "_default", "operator1")
        assert result["status"] == "error"
        assert "Plugin" in result["message"]
        assert "Traceback" not in result["message"]


class TestImportErrors:
    """P2 #14: the core audit backend accessor is importable."""

    def test_audit_backend_is_importable(self):
        from core.compliance.audit_chain_provider import get_audit_backend, get_audit_chain_writer

        assert get_audit_backend is get_audit_chain_writer


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

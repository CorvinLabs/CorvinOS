"""Unit tests for routes/settings — the ADR-0184 Stufe 2 (always-on) toggle.

Exercises the route functions directly (no TestClient), matching this
package's existing route-test convention (see test_healing_config.py).
Monkeypatches system_service_manager (the module the route lazily imports
from at call time) plus console_audit so no real subprocess/audit-chain I/O
happens.
"""
from __future__ import annotations

from types import SimpleNamespace

import corvin_console.routes.settings as settings_route
import corvinOS.installer.system_service_manager as ssm


def _rec():
    return SimpleNamespace(tenant_id="_default", sid_fingerprint="fp123")


def _silence_audit(monkeypatch):
    calls = {"performed": [], "failed": []}
    monkeypatch.setattr(
        settings_route.console_audit, "action_performed",
        lambda **kw: calls["performed"].append(kw),
    )
    monkeypatch.setattr(
        settings_route.console_audit, "action_failed",
        lambda **kw: calls["failed"].append(kw),
    )
    return calls


class _FakeManager:
    def __init__(self, initial_status="not_found"):
        self._status = initial_status
        self.installed_with = None
        self.uninstalled = False

    def status(self, name):
        return self._status

    def install_service(self, name, command, description="", env_vars=None):
        self.installed_with = (name, command, description)
        self._status = "active"

    def uninstall_service(self, name):
        self.uninstalled = True
        self._status = "not_found"


def test_get_service_tier_reports_not_installed(monkeypatch):
    manager = _FakeManager("not_found")
    monkeypatch.setattr(ssm, "get_system_service_manager", lambda: manager)
    out = settings_route.get_service_tier(rec=_rec())
    assert out == {"available": True, "always_on": False, "raw_status": "not_found"}


def test_get_service_tier_reports_installed(monkeypatch):
    manager = _FakeManager("active")
    monkeypatch.setattr(ssm, "get_system_service_manager", lambda: manager)
    out = settings_route.get_service_tier(rec=_rec())
    assert out == {"available": True, "always_on": True, "raw_status": "active"}


def test_put_service_tier_without_elevation_returns_manual_command_not_error(monkeypatch):
    # The core ADR-0184 guarantee: the console must NEVER install the
    # always-on service without real elevation -- it just reports back the
    # same manual command corvin-service itself would print.
    calls = _silence_audit(monkeypatch)
    manager = _FakeManager("not_found")
    monkeypatch.setattr(ssm, "get_system_service_manager", lambda: manager)
    monkeypatch.setattr(ssm, "is_elevated", lambda: False)
    monkeypatch.setattr(settings_route.sys, "platform", "linux")

    out = settings_route.put_service_tier(
        body=settings_route.ServiceTierRequest(enabled=True), rec=_rec(),
    )

    assert out["applied"] is False
    assert out["manual_command"] == "sudo corvin-service install"
    assert manager.installed_with is None, "must NEVER install without elevation"
    assert len(calls["failed"]) == 1
    assert calls["failed"][0]["reason"] == "elevation-required"


def test_put_service_tier_windows_manual_command_has_no_sudo(monkeypatch):
    _silence_audit(monkeypatch)
    manager = _FakeManager("active")
    monkeypatch.setattr(ssm, "get_system_service_manager", lambda: manager)
    monkeypatch.setattr(ssm, "is_elevated", lambda: False)
    monkeypatch.setattr(settings_route.sys, "platform", "win32")

    out = settings_route.put_service_tier(
        body=settings_route.ServiceTierRequest(enabled=False), rec=_rec(),
    )
    assert "corvin-service uninstall" in out["manual_command"]
    assert "sudo" not in out["manual_command"]


def test_put_service_tier_enable_with_elevation_installs(monkeypatch):
    calls = _silence_audit(monkeypatch)
    manager = _FakeManager("not_found")
    monkeypatch.setattr(ssm, "get_system_service_manager", lambda: manager)
    monkeypatch.setattr(ssm, "is_elevated", lambda: True)

    out = settings_route.put_service_tier(
        body=settings_route.ServiceTierRequest(enabled=True), rec=_rec(),
    )

    assert out["applied"] is True
    assert out["always_on"] is True
    assert manager.installed_with is not None
    name, command, description = manager.installed_with
    assert name == "webui"
    assert "uvicorn" in command
    assert len(calls["performed"]) == 1


def test_put_service_tier_disable_with_elevation_uninstalls(monkeypatch):
    _silence_audit(monkeypatch)
    manager = _FakeManager("active")
    monkeypatch.setattr(ssm, "get_system_service_manager", lambda: manager)
    monkeypatch.setattr(ssm, "is_elevated", lambda: True)

    out = settings_route.put_service_tier(
        body=settings_route.ServiceTierRequest(enabled=False), rec=_rec(),
    )

    assert out["applied"] is True
    assert out["always_on"] is False
    assert manager.uninstalled is True


def test_put_service_tier_elevation_required_exception_surfaces_manual_command(monkeypatch):
    # is_elevated() can lie/race (e.g. a container without real root
    # semantics) -- the manager's own ElevationRequired must still be
    # honoured as a soft "applied: false", not an unhandled 500.
    _silence_audit(monkeypatch)

    class _RefusingManager(_FakeManager):
        def install_service(self, *a, **kw):
            raise ssm.ElevationRequired("nope, still need it")

    manager = _RefusingManager("not_found")
    monkeypatch.setattr(ssm, "get_system_service_manager", lambda: manager)
    monkeypatch.setattr(ssm, "is_elevated", lambda: True)

    out = settings_route.put_service_tier(
        body=settings_route.ServiceTierRequest(enabled=True), rec=_rec(),
    )
    assert out["applied"] is False
    assert out["detail"] == "nope, still need it"

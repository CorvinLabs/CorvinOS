"""E2E-wiring-proof for the Console web_surface plugin (ADR-0356, P2.5).

Proves, through the REAL plugin boundaries (protocol runtime-check, manifest
validation, the bootstrap injection path), that the Console is a reachable,
protocol-conformant bundled/builtin plugin — and that declaring it is ship-dark
(a default install injects nothing).
"""
from __future__ import annotations

import importlib

import pytest

from corvin_plugins.protocol import (
    KNOWN_PLUGIN_TYPES,
    CorvinPlugin,
    HealthStatus,
    WebSurface,
)
from corvin_plugins.console import ConsolePlugin, declaration_entry
from corvin_plugins.console import plugin as console_plugin
from corvin_plugins import bootstrap


def test_web_surface_is_a_known_plugin_type():
    assert "web_surface" in KNOWN_PLUGIN_TYPES


def test_console_plugin_satisfies_both_protocols():
    p = ConsolePlugin()
    # runtime_checkable protocols — the real contract boundary
    assert isinstance(p, CorvinPlugin)
    assert isinstance(p, WebSurface)
    assert p.plugin_id == "console"
    assert p.plugin_type == "web_surface"
    assert p.boot_layer == "bundled"      # disableable — a UI is not compliance
    assert p.origin == "builtin"
    assert p.mount_path == "/console/"


def test_declaration_entry_shape_and_class_path_is_reachable():
    entry = declaration_entry()
    assert entry["id"] == "console"
    assert entry["boot_layer"] == "bundled"
    # The class_path must resolve to the real class — reachability, not a string.
    mod_name, cls_name = entry["class_path"].split(":")
    mod = importlib.import_module(mod_name)
    cls = getattr(mod, cls_name)
    assert cls is ConsolePlugin


def test_manifest_validation_accepts_web_surface():
    from corvin_plugins.manifest import PluginRecord, BootLayer, PluginOrigin

    rec = PluginRecord(
        plugin_id="console",
        version="1.0.0",
        display_name="CorvinOS Console",
        plugin_type="web_surface",
        boot_layer=BootLayer.BUNDLED,
        origin=PluginOrigin.BUILTIN,
    )
    assert rec.plugin_type == "web_surface"


def test_health_check_returns_ok_healthstatus():
    p = ConsolePlugin()
    hs = p.health_check()
    assert isinstance(hs, HealthStatus)
    # ok=True whether or not the SPA is built (headless is an expected config).
    assert hs.ok is True
    assert hs.details["plugin_id"] == "console"


def test_ship_dark_off_injects_nothing(monkeypatch):
    """Default install (flag off) → the boot injects no console declaration."""
    monkeypatch.setattr(console_plugin, "console_flag_enabled", lambda tid: False)
    injected = bootstrap._bundled_console_declaration("_default", [])
    assert injected == []


def test_flag_on_injects_console_declaration(monkeypatch):
    """Flag on → the boot injects exactly the console declaration entry."""
    monkeypatch.setattr(console_plugin, "console_flag_enabled", lambda tid: True)
    injected = bootstrap._bundled_console_declaration("_default", [])
    assert len(injected) == 1
    assert injected[0]["id"] == "console"
    assert injected[0]["boot_layer"] == "bundled"


def test_flag_on_but_operator_already_declared_console_is_skipped(monkeypatch):
    """The operator's own {id: console} entry wins — no duplicate injection."""
    monkeypatch.setattr(console_plugin, "console_flag_enabled", lambda tid: True)
    declared = [{"id": "console", "class_path": "custom:Thing"}]
    injected = bootstrap._bundled_console_declaration("_default", declared)
    assert injected == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

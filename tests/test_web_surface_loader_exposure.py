"""E2E-wiring-proof for P7 (ADR-0365): the loader-exposure endpoint reads back the
web_surface plugins the loader loaded, closing the P2.5→P7 loop.

Proves through the real function boundary that a LOADED web_surface plugin (the
Console) is exposed with its mount info, and that a default install (nothing loaded)
exposes nothing — ship-dark.
"""
from __future__ import annotations

import sys
import types

import pytest

from corvin_console.routes import capabilities as cap
from corvin_plugins.console.plugin import ConsolePlugin


def _patch_registry(monkeypatch, plugins_for):
    """Install a fake corvin_plugins.registry.plugins_by_boot_layer."""
    mod = sys.modules.get("corvin_plugins.registry")
    if mod is None:
        mod = types.ModuleType("corvin_plugins.registry")
        sys.modules["corvin_plugins.registry"] = mod
    monkeypatch.setattr(mod, "plugins_by_boot_layer", plugins_for, raising=False)


def test_loaded_console_surface_is_exposed(monkeypatch):
    console = ConsolePlugin()
    _patch_registry(monkeypatch, lambda bl: [console] if bl == "bundled" else [])

    surfaces = cap._loaded_web_surfaces()

    assert len(surfaces) == 1
    s = surfaces[0]
    assert s["id"] == "console"
    assert s["mount_path"] == "/console/"
    assert s["boot_layer"] == "bundled"
    assert isinstance(s["has_spa"], bool)


def test_ship_dark_nothing_loaded_exposes_nothing(monkeypatch):
    _patch_registry(monkeypatch, lambda bl: [])
    assert cap._loaded_web_surfaces() == []


def test_non_web_surface_plugins_are_ignored(monkeypatch):
    class FakeBridge:
        plugin_id = "discord-bridge"
        plugin_type = "bridge_channel"
    _patch_registry(monkeypatch, lambda bl: [FakeBridge()] if bl == "bundled" else [])
    assert cap._loaded_web_surfaces() == []


def test_registry_absent_degrades_to_empty(monkeypatch):
    # Simulate the registry raising on query — must not propagate, must be [].
    def boom(bl):
        raise RuntimeError("registry unavailable")
    _patch_registry(monkeypatch, boom)
    assert cap._loaded_web_surfaces() == []


def test_duplicate_ids_deduplicated(monkeypatch):
    c1, c2 = ConsolePlugin(), ConsolePlugin()
    # same id in both boot layers → exposed once
    _patch_registry(monkeypatch, lambda bl: [c1] if bl == "bundled" else [c2])
    surfaces = cap._loaded_web_surfaces()
    assert [s["id"] for s in surfaces] == ["console"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

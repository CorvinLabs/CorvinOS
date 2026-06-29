"""The new detailed, self-registering nerve fibers (ResourceFiber / LogHealthFiber
/ ConfigDriftFiber) — they probe the system + are picked up by discovery."""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "core" / "console", _REPO / "operator" / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pytest
from corvin_console.aco import nerve_builtins as NB  # type: ignore
from corvin_console.aco.nerve import NerveRegistry  # type: ignore


def test_new_fibers_are_registered_via_discovery():
    NerveRegistry.reset() if hasattr(NerveRegistry, "reset") else None
    NerveRegistry.discover()
    ids = {f["fiber_id"] for f in NerveRegistry.list_fibers()}
    assert {"sys.resources", "aco.log_health", "config.drift"} <= ids


def test_config_drift_detects_corrupt(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "c").mkdir(parents=True)
    (home / "c" / "good.config.json").write_text('{"a":1}', encoding="utf-8")
    (home / "c" / "bad.config.json").write_text("{ broken", encoding="utf-8")
    monkeypatch.setattr(NB, "_home", lambda: home)
    sigs = NB.ConfigDriftFiber().scan()
    assert any(s.signal_type == "config.corrupt" and "bad.config.json" in s.message
               for s in sigs)
    assert all("good.config.json" not in s.message for s in sigs)


def test_log_health_flags_error_spike(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "logs").mkdir(parents=True)
    (home / "logs" / "corvin.log").write_text(
        "\n".join(["ERROR boom"] * 60 + ["INFO ok"] * 10), encoding="utf-8")
    monkeypatch.setattr(NB, "_home", lambda: home)
    sigs = NB.LogHealthFiber().scan()
    assert any(s.signal_type == "log.error_spike" for s in sigs)


def test_resource_fiber_never_throws(tmp_path, monkeypatch):
    monkeypatch.setattr(NB, "_home", lambda: tmp_path)
    assert isinstance(NB.ResourceFiber().scan(), list)   # plenty of disk → []


def test_fibers_return_empty_when_healthy(tmp_path, monkeypatch):
    home = tmp_path / "home"; (home / "logs").mkdir(parents=True)
    (home / "logs" / "corvin.log").write_text("INFO ok\n" * 50, encoding="utf-8")
    monkeypatch.setattr(NB, "_home", lambda: home)
    assert NB.LogHealthFiber().scan() == []
    assert NB.ConfigDriftFiber().scan() == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

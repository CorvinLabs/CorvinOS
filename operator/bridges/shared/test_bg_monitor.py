"""Tests for bg_monitor.py — background task session wakeup monitor."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Redirect CORVIN_HOME and ADAPTER_INBOX to tmp_path for isolation."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "corvin"))
    monkeypatch.setenv("ADAPTER_INBOX", str(tmp_path / "inbox"))
    (tmp_path / "inbox").mkdir()
    (tmp_path / "corvin").mkdir()

    import importlib
    import sys
    # Force re-import so module-level paths are re-evaluated with new env
    sys.modules.pop("bg_monitor", None)
    yield
    sys.modules.pop("bg_monitor", None)


def _import() -> "types.ModuleType":
    import importlib
    import sys
    sys.modules.pop("bg_monitor", None)
    import bg_monitor
    return bg_monitor


def test_run_once_empty_state():
    m = _import()
    assert m.run_once() == 0


def test_touch_creates_entry(tmp_path):
    m = _import()
    m.touch("discord", "uid1", "chat1")
    state = json.loads(m._bg_watch_path().read_text())
    assert "discord:chat1" in state
    entry = state["discord:chat1"]
    assert entry["channel"] == "discord"
    assert entry["from"] == "uid1"
    assert entry["chat_id"] == "chat1"
    assert entry["notified_at"] == 0.0


def test_no_injection_when_too_recent(tmp_path, monkeypatch):
    m = _import()
    monkeypatch.setattr(m, "BGW_IDLE_GRACE", 480.0)
    m.touch("discord", "uid1", "chat1")
    # last_activity is now → not idle yet
    n = m.run_once()
    assert n == 0
    inbox = Path(tmp_path / "inbox")
    assert list(inbox.glob("zz_bgw_*.json")) == []


def test_injection_when_idle(tmp_path, monkeypatch):
    m = _import()
    monkeypatch.setattr(m, "BGW_IDLE_GRACE", 480.0)
    m.touch("discord", "uid1", "chat1")

    # Backdate last_activity to simulate idle session
    state = m._load_state()
    state["discord:chat1"]["last_activity"] = time.time() - 600
    m._save_state(state)

    n = m.run_once()
    assert n == 1
    inbox = Path(tmp_path / "inbox")
    files = list(inbox.glob("zz_bgw_*.json"))
    assert len(files) == 1
    envelope = json.loads(files[0].read_text())
    assert envelope["channel"] == "discord"
    assert envelope["from"] == "uid1"
    assert envelope["chat_id"] == "chat1"
    assert envelope["_bg_wakeup"] is True
    assert "background" in envelope["text"].lower()


def test_no_double_injection(tmp_path, monkeypatch):
    m = _import()
    monkeypatch.setattr(m, "BGW_IDLE_GRACE", 480.0)
    m.touch("discord", "uid1", "chat1")

    state = m._load_state()
    state["discord:chat1"]["last_activity"] = time.time() - 600
    m._save_state(state)

    n1 = m.run_once()
    assert n1 == 1

    # Second call should not inject again (notified_at was just set)
    n2 = m.run_once()
    assert n2 == 0


def test_touch_rearms_after_new_activity(tmp_path, monkeypatch):
    m = _import()
    monkeypatch.setattr(m, "BGW_IDLE_GRACE", 480.0)
    m.touch("discord", "uid1", "chat1")
    state = m._load_state()
    state["discord:chat1"]["last_activity"] = time.time() - 600
    m._save_state(state)
    m.run_once()  # sets notified_at

    # User sends another message → touch() resets notified_at
    m.touch("discord", "uid1", "chat1")
    state = m._load_state()
    assert state["discord:chat1"]["notified_at"] == 0.0


def test_stale_entries_pruned(tmp_path, monkeypatch):
    m = _import()
    monkeypatch.setattr(m, "BGW_MAX_AGE", 3600.0)
    m.touch("discord", "uid1", "chat1")
    state = m._load_state()
    state["discord:chat1"]["last_activity"] = time.time() - 7200  # 2h ago
    m._save_state(state)

    m.run_once()

    state2 = m._load_state()
    assert "discord:chat1" not in state2


def test_multiple_sessions(tmp_path, monkeypatch):
    m = _import()
    monkeypatch.setattr(m, "BGW_IDLE_GRACE", 480.0)
    # Two sessions: one idle, one recent
    m.touch("discord", "uid1", "chat1")
    m.touch("discord", "uid2", "chat2")

    state = m._load_state()
    state["discord:chat1"]["last_activity"] = time.time() - 600  # idle
    # chat2 last_activity = now (recent)
    m._save_state(state)

    n = m.run_once()
    assert n == 1
    inbox = Path(tmp_path / "inbox")
    files = list(inbox.glob("zz_bgw_*.json"))
    assert len(files) == 1
    envelope = json.loads(files[0].read_text())
    assert envelope["chat_id"] == "chat1"


def test_none_chat_id_uses_sender_key(tmp_path):
    m = _import()
    m.touch("telegram", "tguid", None)
    state = m._load_state()
    assert "telegram:tguid" in state

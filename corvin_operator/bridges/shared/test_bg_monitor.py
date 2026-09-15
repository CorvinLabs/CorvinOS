"""Tests for bg_monitor.py — background task session wakeup monitor."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Redirect CORVIN_HOME and ADAPTER_INBOX to tmp_path for isolation."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "corvin"))
    monkeypatch.setenv("ADAPTER_INBOX", str(tmp_path / "inbox"))
    monkeypatch.setenv("ADAPTER_OUTBOX", str(tmp_path / "outbox"))
    (tmp_path / "inbox").mkdir()
    (tmp_path / "outbox").mkdir()
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
    monkeypatch.setattr(m, "BGW_LEGACY_WAKEUP", True)  # opt into legacy wakeup path
    m.touch("discord", "uid1", "chat1")
    # last_activity is now → not idle yet
    n = m.run_once()
    assert n == 0
    inbox = Path(tmp_path / "inbox")
    assert list(inbox.glob("zz_bgw_*.json")) == []


def test_injection_when_idle(tmp_path, monkeypatch):
    m = _import()
    monkeypatch.setattr(m, "BGW_IDLE_GRACE", 480.0)
    monkeypatch.setattr(m, "BGW_LEGACY_WAKEUP", True)  # opt into legacy wakeup path
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
    monkeypatch.setattr(m, "BGW_LEGACY_WAKEUP", True)  # opt into legacy wakeup path
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
    monkeypatch.setattr(m, "BGW_LEGACY_WAKEUP", True)  # opt into legacy wakeup path
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
    monkeypatch.setattr(m, "BGW_LEGACY_WAKEUP", True)  # opt into legacy wakeup path
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


def test_no_spurious_wakeup_by_default(tmp_path, monkeypatch):
    """By default (BGW_LEGACY_WAKEUP off) an idle session must NOT get a
    synthetic wakeup — that path emitted spurious 'All caught up.' spam."""
    m = _import()
    monkeypatch.setattr(m, "BGW_IDLE_GRACE", 480.0)
    assert m.BGW_LEGACY_WAKEUP is False
    m.touch("discord", "uid1", "chat1")
    state = m._load_state()
    state["discord:chat1"]["last_activity"] = time.time() - 600
    m._save_state(state)
    n = m.run_once()
    assert n == 0
    assert list((tmp_path / "inbox").glob("zz_bgw_*.json")) == []


def test_completion_delivered_via_run_once(tmp_path):
    """run_once flushes the durable completion queue to the outbox even with
    the legacy wakeup off — the real notify-on-completion path."""
    m = _import()
    import sys
    sys.modules.pop("completion_notify", None)
    import completion_notify as cn
    tid = cn.register(channel="discord", chat_id="chat1", label="job")
    cn.mark_done(tid, text="all green")
    n = m.run_once()
    assert n == 1
    outs = list((tmp_path / "outbox").glob("cn_*.json"))
    assert len(outs) == 1
    env = json.loads(outs[0].read_text())
    assert env["channel"] == "discord"
    assert env["chat_id"] == "chat1"
    assert "job finished" in env["text"]


def test_tenant_id_captured_in_wakeup(tmp_path, monkeypatch):
    m = _import()
    monkeypatch.setattr(m, "BGW_IDLE_GRACE", 480.0)
    monkeypatch.setattr(m, "BGW_LEGACY_WAKEUP", True)
    m.touch("discord", "uid1", "chat1", tenant_id="acme")
    state = m._load_state()
    assert state["discord:chat1"]["tenant_id"] == "acme"
    state["discord:chat1"]["last_activity"] = time.time() - 600
    m._save_state(state)
    m.run_once()
    files = list((tmp_path / "inbox").glob("zz_bgw_*.json"))
    assert json.loads(files[0].read_text())["tenant_id"] == "acme"


# ─── BGW_IDLE_GRACE / BGW_MAX_AGE raw env-var parsing ──────────────────────
#
# The tests above only ever exercise the *parsed* module attributes via
# monkeypatch.setattr(m, "BGW_IDLE_GRACE", ...) on an already-imported
# module — they never feed a raw env-var value through the real
# `float(os.environ.get(...))` call at module scope (bg_monitor.py lines
# 45-46). The tests below close that gap: one confirms the happy path
# (a valid override actually parses through the real code path), the other
# two document the crash on an invalid value. A crashing module-level import
# cannot be captured with plain monkeypatch/importlib.reload from inside the
# same pytest process (a partial import leaves sys.modules in a broken state
# for every later test in the session), so a subprocess is used to import
# bg_monitor fresh and observe the failure in isolation.


def _run_bg_monitor_import(env_overrides: dict) -> subprocess.CompletedProcess:
    """Import bg_monitor fresh in a subprocess with the given env overrides."""
    shared_dir = Path(__file__).resolve().parent
    env = dict(os.environ)
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", "import bg_monitor"],
        cwd=str(shared_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_import_parses_valid_raw_env_overrides(monkeypatch):
    """A valid BGW_IDLE_GRACE/BGW_MAX_AGE env var actually parses through
    the real module-level `float(os.environ.get(...))` call (not just the
    post-import monkeypatch.setattr shortcut used everywhere else in this
    file)."""
    monkeypatch.setenv("BGW_IDLE_GRACE", "123.5")
    monkeypatch.setenv("BGW_MAX_AGE", "999")
    m = _import()
    assert m.BGW_IDLE_GRACE == 123.5
    assert m.BGW_MAX_AGE == 999.0


def test_import_crashes_on_empty_idle_grace_env():
    """KNOWN BUG (see bugsDiscovered): BGW_IDLE_GRACE="" (e.g. an unresolved
    systemd EnvironmentFile interpolation that expands to empty) crashes the
    module import with a bare ValueError instead of falling back to the
    documented default (480s) or raising a clearly-typed config error. This
    test locks in the CURRENT (broken) behaviour so a future fix shows up as
    an intentional test change rather than silent drift."""
    result = _run_bg_monitor_import({"BGW_IDLE_GRACE": ""})
    assert result.returncode != 0
    assert "ValueError" in result.stderr
    assert "could not convert string to float" in result.stderr
    assert "bg_monitor.py" in result.stderr


def test_import_crashes_on_non_numeric_max_age_env():
    """KNOWN BUG (see bugsDiscovered): same unguarded float() parse crash,
    but for BGW_MAX_AGE with a non-numeric value (e.g. a stray unit like
    "1d" or a typo) instead of an empty string."""
    result = _run_bg_monitor_import({"BGW_MAX_AGE": "abc"})
    assert result.returncode != 0
    assert "ValueError" in result.stderr
    assert "could not convert string to float" in result.stderr
    assert "bg_monitor.py" in result.stderr

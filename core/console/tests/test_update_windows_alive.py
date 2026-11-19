"""A run must not be settled as "interrupted" while the Windows updater still holds its setup lock."""
import json
import os
import sys
import time

import pytest

from corvin_console import update_manager as um


def _state(path, **kw):
    st = {"status": "running", "phase": "triggered", "started_at": time.time() - 30, "trigger": "manual",
          "steps": {"triggered": {"status": "ok", "at": 1, "detail": "manual"}}}
    st.update(kw)
    path.write_text(json.dumps(st), encoding="utf-8")


def _lock(tmp_path, pid):
    d = tmp_path / "corvinos-setup.lock"
    d.mkdir()
    (d / "pid").write_text(str(pid), encoding="utf-8")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows setup lock")
def test_live_updater_lock_means_alive(tmp_path, monkeypatch):
    monkeypatch.setattr(um.tempfile, "gettempdir", lambda: str(tmp_path))
    assert um._windows_updater_alive() is False  # no lock
    _lock(tmp_path, os.getpid())
    assert um._windows_updater_alive() is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows setup lock")
def test_dead_pid_or_garbage_lock_is_not_alive(tmp_path, monkeypatch):
    monkeypatch.setattr(um.tempfile, "gettempdir", lambda: str(tmp_path))
    _lock(tmp_path, 2147480000)  # no such process
    assert um._windows_updater_alive() is False
    (tmp_path / "corvinos-setup.lock" / "pid").write_text("not-a-pid", encoding="utf-8")
    assert um._windows_updater_alive() is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows setup lock")
def test_running_state_is_left_alone_while_updater_lives(tmp_path, monkeypatch):
    monkeypatch.setattr(um.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    _lock(tmp_path / "tmp", os.getpid())
    sp = tmp_path / "update_state.json"
    _state(sp)
    last = um.UpdateManager("t", sp).last_update("2.0.0", None)
    assert last["status"] == "running"
    assert json.loads(sp.read_text(encoding="utf-8"))["status"] == "running"

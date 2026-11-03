"""Windows: a bridge can be stopped without systemd, and a bridge.ps1-started adapter is found."""
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bridge_manager as bm  # noqa: E402

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows-only code path")


def test_stop_channel_ends_task_then_kills_daemon_tree():
    calls = []

    class _R:
        returncode = 0

    with mock.patch.object(bm, "_run", side_effect=lambda cmd, **k: calls.append(cmd) or _R()), \
         mock.patch.object(bm, "_scan_channel_daemon_pid", return_value=(4321, True)), \
         mock.patch("corvin_core.process_signals.kill_tree_windows", return_value=True) as kt:
        out = bm.stop_channel_detached("discord")
    assert out["applied"] is True
    assert calls[0][:3] == ["schtasks", "/End", "/TN"] and calls[0][3] == "CorvinOS-Bridge-discord"
    kt.assert_called_once_with(4321)


def test_adapter_found_by_windows_process_scan(tmp_path):
    adapter = tmp_path / "shared" / "adapter.py"
    snap = {777: f'"python.exe" "{adapter}"'}
    with mock.patch.object(bm, "_corvin_home", return_value=tmp_path), \
         mock.patch.object(bm, "_win_process_snapshot", return_value=(snap, True)):
        assert bm._adapter_running_pid(adapter) == 777

"""ADR-0215 Fix: WhatsApp onboarding-wizard race with systemd-managed daemons.

Before this fix, `start_channel_detached()`'s only duplicate-start guard was
a TCP port probe (`_port_open`). In the race window between a systemd unit
start (`corvin-voice-bridge-whatsapp.service`) and that daemon actually
binding its pairing-QR port, a concurrent wizard click could spawn a SECOND
daemon via a raw, non-systemd-tracked subprocess — an orphan process
`systemctl stop` would never see.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "operator" / "bridges"))

import bridge_manager  # noqa: E402


def _fake_run(returncode: int, stdout: str):
    def _run(*args, **kwargs):
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")
    return _run


def test_systemd_unit_active_true_for_active_state():
    with mock.patch.object(subprocess, "run", _fake_run(0, "active\n")):
        assert bridge_manager._systemd_unit_active("whatsapp") is True


def test_systemd_unit_active_true_for_activating_state():
    # The load-bearing case: systemd unit is IN THE MIDDLE of starting, port
    # not bound yet — this is exactly the race window the fix closes.
    with mock.patch.object(subprocess, "run", _fake_run(3, "activating\n")):
        assert bridge_manager._systemd_unit_active("whatsapp") is True


def test_systemd_unit_active_false_for_inactive_state():
    with mock.patch.object(subprocess, "run", _fake_run(3, "inactive\n")):
        assert bridge_manager._systemd_unit_active("whatsapp") is False


def test_systemd_unit_active_false_when_systemctl_missing():
    with mock.patch.object(subprocess, "run", side_effect=FileNotFoundError()):
        assert bridge_manager._systemd_unit_active("whatsapp") is False


def test_systemd_unit_active_false_on_timeout():
    with mock.patch.object(
        subprocess, "run",
        side_effect=subprocess.TimeoutExpired(cmd="systemctl", timeout=5),
    ):
        assert bridge_manager._systemd_unit_active("whatsapp") is False


def test_start_channel_detached_short_circuits_when_unit_activating():
    """The actual regression: start_channel_detached() must return
    already_running WITHOUT attempting node/npm install when the systemd
    unit is already starting — even though the port isn't open yet."""
    with mock.patch.object(bridge_manager, "_port_open", return_value=False), \
         mock.patch.object(bridge_manager, "_systemd_unit_active", return_value=True), \
         mock.patch.object(bridge_manager, "find_node") as m_find_node:
        result = bridge_manager.start_channel_detached("whatsapp")
        assert result == {"ok": True, "already_running": True, "via": "systemd"}
        m_find_node.assert_not_called()  # must short-circuit BEFORE any install work


def test_start_channel_detached_proceeds_when_neither_port_nor_unit_active():
    """Sanity: the new guard must not swallow the legitimate 'genuinely not
    running yet, go ahead and start it' path."""
    with mock.patch.object(bridge_manager, "_port_open", return_value=False), \
         mock.patch.object(bridge_manager, "_systemd_unit_active", return_value=False), \
         mock.patch.object(bridge_manager, "find_node", return_value=None):
        result = bridge_manager.start_channel_detached("whatsapp")
        # Falls through to the node-missing branch — proves the guard did
        # NOT short-circuit when nothing is actually running.
        assert result["ok"] is False
        assert result.get("node_missing") is True

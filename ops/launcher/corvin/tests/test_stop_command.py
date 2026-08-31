"""`corvin stop` / `corvin gateway stop` — actually stops the running instance.

2026-08-03: an operator asked for a Console command that shuts Corvin down
so it can be restarted with `corvin serve`. Investigating what existed
already found `corvin gateway stop` was silently broken on the most common
native install path: `native_backend.stop()` called `bridge.sh stop`, which
is not a real bridge.sh subcommand (only `up|down|status|restart|
install-units|logs|tail|fg|console|doctor` exist) — every call hit the
usage/error branch and did nothing, with the failure swallowed by
`capture_output=True` and a discarded return code. On Windows `stop()` was
an explicit no-op — the real persistent process there (the Stufe-1 login
autostart Scheduled Task `CorvinOS-Console`, registered by install.ps1 per
ADR-0184) was never touched at all.

The fix reuses ``ops.launcher.service_entry._quiesce_stage1`` — the SAME
cross-platform console-stop logic ``corvin-service`` already uses for the
Stufe1→Stufe2 handoff — instead of re-implementing per-OS subprocess calls
here. It already gets Windows (`schtasks /end`), macOS (`launchctl bootout`
in the right GUI domain), and Linux (`systemctl --user disable --now`) each
right, including SUDO_USER/uid edge cases.

These tests pin the fix:
  * ``stop()`` always calls ``_quiesce_stage1(stop_running=True)`` — the
    single cross-platform console-stop path — never raising even if that
    call fails internally.
  * On POSIX it ADDITIONALLY calls ``bridge.sh down`` (the real subcommand,
    not the non-existent ``stop``) to also tear down messaging-bridge
    channel units, which ``_quiesce_stage1`` does not touch.
  * A failing ``bridge.sh down`` is surfaced (printed), not silently
    discarded.
  * On Windows only the cross-platform call is made — no POSIX-only
    ``bridge.sh`` call.
  * ``corvin stop`` (new top-level alias) parses and dispatches through the
    same code path as ``corvin gateway stop``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_LAUNCHER = _HERE.parents[1]  # ops/launcher

if str(_LAUNCHER) not in sys.path:
    sys.path.insert(0, str(_LAUNCHER))

from corvin import cli, native_backend  # noqa: E402, I001


def _patch_quiesce(monkeypatch):
    """Stub out the cross-platform console-stop call with a spy."""
    calls = []
    monkeypatch.setattr(
        "ops.launcher.service_entry._quiesce_stage1",
        lambda stop_running=True: calls.append(stop_running),
    )
    return calls


# ── native_backend.stop() — cross-platform console stop ────────────────────

def test_stop_always_calls_quiesce_stage1(monkeypatch):
    quiesce_calls = _patch_quiesce(monkeypatch)
    monkeypatch.setattr(native_backend, "_find_bridge_sh", lambda: None)
    monkeypatch.setattr(os, "name", "posix")

    native_backend.stop()

    assert quiesce_calls == [True]


def test_stop_never_raises_if_quiesce_stage1_explodes(monkeypatch):
    monkeypatch.setattr(
        "ops.launcher.service_entry._quiesce_stage1",
        mock.Mock(side_effect=RuntimeError("boom")),
    )
    monkeypatch.setattr(native_backend, "_find_bridge_sh", lambda: None)
    monkeypatch.setattr(os, "name", "posix")

    native_backend.stop()  # must not raise


# ── native_backend.stop() — POSIX bridge-channel teardown ──────────────────

def test_posix_stop_calls_bridge_sh_down(monkeypatch, tmp_path):
    _patch_quiesce(monkeypatch)
    fake_sh = tmp_path / "bridge.sh"
    fake_sh.write_text("#!/bin/bash\n")
    monkeypatch.setattr(native_backend, "_find_bridge_sh", lambda: fake_sh)
    monkeypatch.setattr(os, "name", "posix")

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return mock.Mock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(native_backend.subprocess, "run", fake_run)

    native_backend.stop()

    assert len(calls) == 1
    assert calls[0] == ["bash", str(fake_sh), "down"]
    assert "stop" not in calls[0]  # the old, non-existent subcommand


def test_posix_stop_surfaces_failure(monkeypatch, tmp_path, capsys):
    _patch_quiesce(monkeypatch)
    fake_sh = tmp_path / "bridge.sh"
    fake_sh.write_text("#!/bin/bash\n")
    monkeypatch.setattr(native_backend, "_find_bridge_sh", lambda: fake_sh)
    monkeypatch.setattr(os, "name", "posix")

    def fake_run(cmd, **kwargs):
        return mock.Mock(
            returncode=1, stdout="",
            stderr="systemd --user not available auf diesem Host.",
        )

    monkeypatch.setattr(native_backend.subprocess, "run", fake_run)

    native_backend.stop()

    out = capsys.readouterr().out
    assert "systemd --user not available" in out


def test_posix_stop_no_bridge_sh_is_a_quiet_noop(monkeypatch, capsys):
    _patch_quiesce(monkeypatch)
    monkeypatch.setattr(native_backend, "_find_bridge_sh", lambda: None)
    monkeypatch.setattr(os, "name", "posix")

    called = []
    monkeypatch.setattr(
        native_backend.subprocess, "run",
        lambda *a, **k: called.append(1),
    )

    native_backend.stop()  # must not raise

    assert not called


# ── native_backend.stop() — Windows skips the POSIX-only bridge.sh call ────

def test_windows_stop_skips_bridge_sh_call(monkeypatch):
    quiesce_calls = _patch_quiesce(monkeypatch)
    monkeypatch.setattr(os, "name", "nt")

    called = []
    monkeypatch.setattr(
        native_backend.subprocess, "run",
        lambda *a, **k: called.append(1),
    )

    native_backend.stop()

    assert quiesce_calls == [True]
    assert not called  # bridge.sh is POSIX-only; must not be invoked on Windows


# ── cli.py — `corvin stop` parses and dispatches ───────────────────────────

def test_stop_subcommand_parses():
    parser = cli._build_parser()
    args = parser.parse_args(["stop"])
    assert args.command == "stop"


def test_stop_dispatches_to_gateway_stop_logic(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "cmd_gateway_stop", lambda args: calls.append(1) or 0)
    parser = cli._build_parser()
    args = parser.parse_args(["stop"])
    rc = cli.cmd_stop(args)
    assert rc == 0
    assert calls == [1]


def test_gateway_stop_calls_backend_and_hints_serve(monkeypatch, capsys):
    fake_backend = mock.Mock()
    monkeypatch.setattr(
        "corvin.backend.get", lambda: fake_backend,
    )
    parser = cli._build_parser()
    args = parser.parse_args(["gateway", "stop"])
    rc = cli.cmd_gateway_stop(args)
    assert rc == 0
    fake_backend.stop.assert_called_once()
    out = capsys.readouterr().out
    assert "corvin serve" in out

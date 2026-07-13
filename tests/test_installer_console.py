"""Regression tests for corvinOS/installer/steps/console.py.

`build_frontend()` and `_kill_port()` implement network/process fallback
chains that no test previously exercised (confirmed via repo-wide grep —
see the review that produced this file). Specifically:

  1. `build_frontend()` has 7 distinguishable outcomes: dist already built,
     web-next dir missing (pre-built wheel), package.json missing, npm not
     found, `npm install` failure, `npm run build` failure, and a
     nominally-successful build that still leaves dist/index.html missing.
     The npm-install-vs-build distinction matters because they produce
     different user-facing remediation messages.
  2. `_kill_port()` has a three-way platform/tool fallback chain: Windows
     (netstat + taskkill), lsof, and fuser — used both at fresh install
     (`start_server`) and during `restore()`.

No test file existed for this module before this change.
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from corvinOS.installer.steps import console as console_mod


# ── build_frontend: early-return branches (no subprocess involved) ─────────


def test_build_frontend_returns_true_when_dist_already_built(tmp_path: Path) -> None:
    webnext_dir = tmp_path / "core" / "console" / "corvin_console" / "web-next"
    dist_dir = webnext_dir / "dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html></html>")

    with mock.patch.object(console_mod.subprocess, "run") as m_run:
        ok = console_mod.build_frontend(tmp_path)

    assert ok is True
    m_run.assert_not_called()


def test_build_frontend_returns_true_when_webnext_dir_missing(tmp_path: Path) -> None:
    """Pre-built wheel installs ship without a web-next source tree at all —
    this must be treated as success, not failure."""
    with mock.patch.object(console_mod.subprocess, "run") as m_run:
        ok = console_mod.build_frontend(tmp_path)

    assert ok is True
    m_run.assert_not_called()


def test_build_frontend_returns_true_when_package_json_missing(tmp_path: Path) -> None:
    webnext_dir = tmp_path / "core" / "console" / "corvin_console" / "web-next"
    webnext_dir.mkdir(parents=True)

    with mock.patch.object(console_mod.subprocess, "run") as m_run:
        ok = console_mod.build_frontend(tmp_path)

    assert ok is True
    m_run.assert_not_called()


# ── build_frontend: npm-driven branches ─────────────────────────────────────


def _make_webnext_with_package_json(tmp_path: Path) -> Path:
    webnext_dir = tmp_path / "core" / "console" / "corvin_console" / "web-next"
    webnext_dir.mkdir(parents=True)
    (webnext_dir / "package.json").write_text("{}")
    return webnext_dir


def test_build_frontend_returns_false_when_npm_not_found(tmp_path: Path) -> None:
    _make_webnext_with_package_json(tmp_path)

    with mock.patch.object(console_mod, "_find_npm", return_value=None), \
         mock.patch.object(console_mod.subprocess, "run") as m_run:
        ok = console_mod.build_frontend(tmp_path)

    assert ok is False
    m_run.assert_not_called()


def test_build_frontend_returns_false_on_npm_install_failure(tmp_path: Path) -> None:
    """npm install failing must short-circuit before `npm run build` is ever
    attempted — a distinct failure path from a build failure."""
    webnext_dir = _make_webnext_with_package_json(tmp_path)

    install_result = mock.Mock(returncode=1)
    with mock.patch.object(console_mod, "_find_npm", return_value="npm"), \
         mock.patch.object(console_mod.subprocess, "run", return_value=install_result) as m_run:
        ok = console_mod.build_frontend(tmp_path)

    assert ok is False
    # only the "npm install" call was made — "npm run build" never ran
    m_run.assert_called_once_with(["npm", "install"], cwd=webnext_dir, check=False)


def test_build_frontend_returns_false_on_npm_run_build_failure(tmp_path: Path) -> None:
    """A distinct failure path from npm-install failure: install succeeds,
    build fails."""
    webnext_dir = _make_webnext_with_package_json(tmp_path)

    install_ok = mock.Mock(returncode=0)
    build_fail = mock.Mock(returncode=1)

    with mock.patch.object(console_mod, "_find_npm", return_value="npm"), \
         mock.patch.object(console_mod.subprocess, "run", side_effect=[install_ok, build_fail]) as m_run:
        ok = console_mod.build_frontend(tmp_path)

    assert ok is False
    assert m_run.call_count == 2
    m_run.assert_any_call(["npm", "install"], cwd=webnext_dir, check=False)
    m_run.assert_any_call(["npm", "run", "build"], cwd=webnext_dir, check=False)


def test_build_frontend_returns_false_when_dist_missing_after_successful_build(tmp_path: Path) -> None:
    """A build that reports success but leaves dist/index.html absent
    (broken build config, disk issue, etc.) must not be reported as success."""
    _make_webnext_with_package_json(tmp_path)

    ok_result = mock.Mock(returncode=0)
    with mock.patch.object(console_mod, "_find_npm", return_value="npm"), \
         mock.patch.object(console_mod.subprocess, "run", return_value=ok_result):
        ok = console_mod.build_frontend(tmp_path)

    assert ok is False


def test_build_frontend_returns_true_when_build_actually_produces_dist(tmp_path: Path) -> None:
    """Positive control mirroring the failure test above: when the build
    step really does write dist/index.html, build_frontend must report
    success."""
    webnext_dir = _make_webnext_with_package_json(tmp_path)

    def fake_run(cmd, cwd=None, check=False):
        if cmd[-2:] == ["run", "build"]:
            dist_dir = webnext_dir / "dist"
            dist_dir.mkdir(parents=True, exist_ok=True)
            (dist_dir / "index.html").write_text("<html></html>")
        return mock.Mock(returncode=0)

    with mock.patch.object(console_mod, "_find_npm", return_value="npm"), \
         mock.patch.object(console_mod.subprocess, "run", side_effect=fake_run):
        ok = console_mod.build_frontend(tmp_path)

    assert ok is True


# ── _kill_port: three-way platform/tool fallback chain ──────────────────────


def test_kill_port_windows_parses_netstat_and_taskkills_matching_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(console_mod.sys, "platform", "win32")
    monkeypatch.setattr(console_mod.time, "sleep", mock.Mock())

    netstat_result = mock.Mock(
        stdout=(
            "  Proto  Local Address          Foreign Address        State           PID\n"
            "  TCP    0.0.0.0:8765           0.0.0.0:0              LISTENING       4242\n"
            "  TCP    127.0.0.1:9999         0.0.0.0:0              LISTENING       9999\n"
        ),
    )
    taskkill_result = mock.Mock(returncode=0)

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "netstat":
            return netstat_result
        return taskkill_result

    with mock.patch.object(console_mod.subprocess, "run", side_effect=fake_run):
        console_mod._kill_port(8765)

    assert calls[0] == ["netstat", "-ano"]
    assert ["taskkill", "/F", "/PID", "4242"] in calls
    # the unrelated PID on a different port must never be targeted
    assert ["taskkill", "/F", "/PID", "9999"] not in calls


def test_kill_port_uses_lsof_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(console_mod.sys, "platform", "linux")
    monkeypatch.setattr(console_mod.time, "sleep", mock.Mock())
    monkeypatch.setattr(
        console_mod.shutil, "which",
        lambda name: "/usr/bin/lsof" if name == "lsof" else None,
    )

    lsof_result = mock.Mock(stdout="1111\n2222\n")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return lsof_result

    with mock.patch.object(console_mod.subprocess, "run", side_effect=fake_run):
        console_mod._kill_port(8765)

    assert calls[0] == ["lsof", "-t", "-i", ":8765"]
    assert ["kill", "1111"] in calls
    assert ["kill", "2222"] in calls


def test_kill_port_falls_back_to_fuser_when_lsof_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(console_mod.sys, "platform", "linux")
    monkeypatch.setattr(console_mod.time, "sleep", mock.Mock())
    monkeypatch.setattr(
        console_mod.shutil, "which",
        lambda name: "/usr/bin/fuser" if name == "fuser" else None,
    )

    with mock.patch.object(console_mod.subprocess, "run") as m_run:
        console_mod._kill_port(8765)

    m_run.assert_called_once_with(["fuser", "-k", "8765/tcp"], check=False)


def test_kill_port_is_noop_when_neither_lsof_nor_fuser_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """On a minimal container lacking both lsof and fuser, _kill_port must
    degrade to a no-op (no crash) rather than attempting a command that
    doesn't exist."""
    monkeypatch.setattr(console_mod.sys, "platform", "linux")
    monkeypatch.setattr(console_mod.time, "sleep", mock.Mock())
    monkeypatch.setattr(console_mod.shutil, "which", lambda name: None)

    with mock.patch.object(console_mod.subprocess, "run") as m_run:
        console_mod._kill_port(8765)

    m_run.assert_not_called()


# ── start_server: kill-port-first, immediate-exit, timeout, success ─────────


def test_start_server_kills_port_before_spawning_uvicorn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """start_server must clear any stale listener on the port before it ever
    spawns a new uvicorn process — otherwise the new process can fail to bind
    a port still held by a leftover process from a previous run/restore."""
    monkeypatch.setattr(console_mod.time, "sleep", mock.Mock())

    fake_proc = mock.Mock()
    fake_proc.poll.return_value = None
    fake_proc.returncode = None

    calls = []
    with mock.patch.object(console_mod, "_kill_port", side_effect=lambda p: calls.append(("kill_port", p))) as m_kill, \
         mock.patch.object(console_mod.subprocess, "Popen", side_effect=lambda *a, **k: calls.append(("popen",)) or fake_proc), \
         mock.patch.object(console_mod, "_port_open", return_value=True):
        ok = console_mod.start_server(tmp_path)

    assert ok is True
    m_kill.assert_called_once_with(console_mod._PORT)
    # kill_port must run strictly before Popen spawns the new server process
    assert calls[0] == ("kill_port", console_mod._PORT)
    assert calls[1] == ("popen",)


def test_start_server_returns_false_when_uvicorn_exits_immediately(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If uvicorn crashes on startup (e.g. bad import, port still in use),
    proc.poll() returns a return code right away — start_server must report
    failure instead of waiting out the full 15 s timeout."""
    monkeypatch.setattr(console_mod.time, "sleep", mock.Mock())

    fake_proc = mock.Mock()
    fake_proc.poll.return_value = 1
    fake_proc.returncode = 1

    with mock.patch.object(console_mod, "_kill_port"), \
         mock.patch.object(console_mod.subprocess, "Popen", return_value=fake_proc), \
         mock.patch.object(console_mod, "_port_open") as m_port_open:
        ok = console_mod.start_server(tmp_path)

    assert ok is False
    # must not even bother probing the port once the process is known dead
    m_port_open.assert_not_called()


def test_start_server_returns_false_on_timeout_when_port_never_opens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If the process stays alive but the port never opens, start_server must
    give up after the bounded retry loop rather than hanging forever."""
    monkeypatch.setattr(console_mod.time, "sleep", mock.Mock())

    fake_proc = mock.Mock()
    fake_proc.poll.return_value = None  # still running for the entire loop
    fake_proc.returncode = None

    with mock.patch.object(console_mod, "_kill_port"), \
         mock.patch.object(console_mod.subprocess, "Popen", return_value=fake_proc), \
         mock.patch.object(console_mod, "_port_open", return_value=False) as m_port_open:
        ok = console_mod.start_server(tmp_path)

    assert ok is False
    # bounded retry loop: exactly 30 attempts (15 s / 0.5 s), never unbounded
    assert m_port_open.call_count == 30


def test_start_server_returns_true_as_soon_as_port_opens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Positive control: once the port accepts connections, start_server must
    report success without waiting through the rest of the retry budget."""
    monkeypatch.setattr(console_mod.time, "sleep", mock.Mock())

    fake_proc = mock.Mock()
    fake_proc.poll.return_value = None
    fake_proc.returncode = None

    # port stays closed for the first two checks, then opens
    port_open_results = iter([False, False, True])

    with mock.patch.object(console_mod, "_kill_port"), \
         mock.patch.object(console_mod.subprocess, "Popen", return_value=fake_proc), \
         mock.patch.object(console_mod, "_port_open", side_effect=lambda *a, **k: next(port_open_results)) as m_port_open:
        ok = console_mod.start_server(tmp_path)

    assert ok is True
    assert m_port_open.call_count == 3

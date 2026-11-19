"""The watchdog reads the setup lock where install.sh / update.sh write it (R2-CON-2).

install.sh and update.sh moved the lock from ``${TMPDIR:-/tmp}`` to the
per-user state directory; the watchdog unit that ``scripts/setup.sh`` writes
kept reading the old place, so it no longer stood down during an install or an
update and restarted a console that was still booting.

Nothing here runs an installer or an updater. The lock-path EXPRESSIONS are cut
out of the three scripts and evaluated by a shell under controlled environments;
``scripts/setup.sh`` itself is executed once against a scratch HOME with a stub
``systemctl``, to read the unit it really writes.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SETUP_SH = REPO / "scripts" / "setup.sh"

pytestmark = pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None, reason="POSIX shell required")


def _writer_lock(script: str, env: dict) -> str:
    """LOCK_DIR/pid as *script* (install.sh / update.sh) computes it under *env*."""
    text = (REPO / script).read_text(encoding="utf-8")
    # The line that discards a relative XDG_STATE_HOME runs first, as in the script.
    guard = re.search(r'^case "\$\{XDG_STATE_HOME:-\}" in .*esac$', text, re.M)
    assert guard, f"{script}: no relative-XDG_STATE_HOME guard"
    state = re.search(r'^STATE_DIR=.*$', text, re.M)
    assert guard.start() < state.start()
    guard, state = guard.group(0), state.group(0)
    lock = re.search(r'^LOCK_DIR=.*$', text, re.M).group(0)
    assert "corvinos-setup.lock" in lock
    out = subprocess.run(["sh", "-c", f'{guard}\n{state}\n{lock}\nprintf %s "$LOCK_DIR/pid"'],
                         env=env, capture_output=True, text=True, check=True)
    return out.stdout


def _reader_expr(unit_text: str) -> str:
    """The path the watchdog's ExecStart cats, with systemd's ``$$`` undone."""
    exec_start = next(l for l in unit_text.splitlines() if l.startswith("ExecStart="))
    m = re.search(r'cat "([^"]*corvinos-setup\.lock/pid)"', exec_start)
    assert m, exec_start
    return m.group(1).replace("$$", "$")


def _unit_environment(unit_text: str) -> dict:
    env = {}
    for line in unit_text.splitlines():
        m = re.fullmatch(r'Environment="([A-Z_]+)=(.*)"', line)
        if m:
            env[m.group(1)] = m.group(2).replace("%%", "%")
    return env


def _write_unit(tmp_path: Path, env_extra: dict) -> str:
    """Run scripts/setup.sh --no-autostart in a scratch HOME; return the unit text."""
    home = tmp_path / "home"
    bindir = tmp_path / "bin"
    home.mkdir(exist_ok=True)
    bindir.mkdir(exist_ok=True)
    stub = bindir / "systemctl"
    stub.write_text("#!/bin/sh\ncase \"$*\" in *list-unit-files*) echo corvin-watchdog.service enabled;; esac\nexit 0\n",
                    encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    env = {"HOME": str(home), "PATH": f"{bindir}:/usr/bin:/bin",
           "CORVIN_HOME": str(tmp_path / "corvin-home"), **env_extra}
    out = subprocess.run(["bash", str(SETUP_SH), "--no-autostart"], env=env,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    unit = home / ".config" / "systemd" / "user" / "corvin-watchdog.service"
    text = unit.read_text(encoding="utf-8")
    assert "@@STATE_ENV@@" not in text
    assert not unit.with_name(unit.name + ".tmp").exists()
    return text


@pytest.mark.parametrize("xdg", [None, "/custom/state dir/100%x"])
def test_the_unit_reads_the_lock_both_writers_take(tmp_path, xdg):
    home = tmp_path / "home"
    shell_env = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
    if xdg:
        shell_env["XDG_STATE_HOME"] = xdg
    unit = _write_unit(tmp_path, {"XDG_STATE_HOME": xdg} if xdg else {})

    # What the watchdog evaluates: a systemd user manager has HOME, and only
    # the variables the unit itself sets - NOT the operator's shell exports.
    manager_env = {"HOME": str(home), "PATH": "/usr/bin:/bin", **_unit_environment(unit)}
    reader = subprocess.run(["bash", "-c", f'printf %s "{_reader_expr(unit)}"'],
                            env=manager_env, capture_output=True, text=True, check=True).stdout

    writers = {s: _writer_lock(s, shell_env) for s in ("install.sh", "update.sh")}
    assert writers["install.sh"] == writers["update.sh"]
    assert reader == writers["update.sh"], (reader, writers)
    expected_root = xdg if xdg else f"{home}/.local/state"
    assert reader == f"{expected_root}/corvinos/corvinos-setup.lock/pid"
    assert "TMPDIR" not in _reader_expr(unit)


def test_the_watchdog_stands_down_while_the_lock_owner_lives(tmp_path):
    """Behaviour, not text: the unit's own liveness expression against a real lock."""
    unit = _write_unit(tmp_path, {})
    home = tmp_path / "home"
    exec_start = next(l for l in unit.splitlines() if l.startswith("ExecStart="))
    probe = re.search(r'(kill -0 "\$\$\(cat .*? \|\| echo none\)" 2>/dev/null)', exec_start).group(1).replace("$$", "$")
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
    run = lambda: subprocess.run(["bash", "-c", probe], env=env).returncode  # noqa: E731
    assert run() != 0                                     # no lock: watchdog acts
    lock = home / ".local" / "state" / "corvinos" / "corvinos-setup.lock"
    lock.mkdir(parents=True)
    (lock / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    assert run() == 0                                     # live owner: stands down
    (lock / "pid").write_text("999999999\n", encoding="utf-8")
    assert run() != 0                                     # stale lock: ignored


def test_update_sh_rewrites_the_unit_on_every_update():
    """An existing install only gets the corrected unit because update.sh
    re-runs setup.sh; restart_all then restarts the enabled watchdog."""
    text = (REPO / "update.sh").read_text(encoding="utf-8")
    assert 'bash "$SRC/scripts/setup.sh" --no-autostart' in text
    assert text.index('scripts/setup.sh" --no-autostart') < text.index("restart_all() {")


# ── R3-CON-9: relative XDG_STATE_HOME, and updates started from the console ──

@pytest.mark.parametrize("relative", ["state", "./state", "../state", "~/state"])
def test_a_relative_xdg_state_home_is_ignored_by_all_three(tmp_path, relative):
    home = tmp_path / "home"
    shell_env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "XDG_STATE_HOME": relative}
    unit = _write_unit(tmp_path, {"XDG_STATE_HOME": relative})
    assert "XDG_STATE_HOME" not in _unit_environment(unit)
    manager_env = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
    reader = subprocess.run(["bash", "-c", f'printf %s "{_reader_expr(unit)}"'],
                            env=manager_env, capture_output=True, text=True, check=True).stdout
    writers = {s: _writer_lock(s, shell_env) for s in ("install.sh", "update.sh")}
    expected = f"{home}/.local/state/corvinos/corvinos-setup.lock/pid"
    assert writers == {"install.sh": expected, "update.sh": expected}, writers
    assert reader == expected


def test_the_scripts_say_that_they_ignore_it(tmp_path):
    for script in ("install.sh", "update.sh"):
        text = (REPO / script).read_text(encoding="utf-8")
        guard = re.search(r'^case "\$\{XDG_STATE_HOME:-\}" in .*esac$', text, re.M).group(0)
        out = subprocess.run(["sh", "-c", guard + '\nprintf "[%s]" "${XDG_STATE_HOME:-}"'],
                             env={"PATH": "/usr/bin:/bin", "XDG_STATE_HOME": "rel"},
                             capture_output=True, text=True, check=True)
        assert out.stdout == "[]" and "not an absolute path" in out.stderr, (script, out)
        quiet = subprocess.run(["sh", "-c", guard + '\nprintf "[%s]" "${XDG_STATE_HOME:-}"'],
                               env={"PATH": "/usr/bin:/bin", "XDG_STATE_HOME": "/abs"},
                               capture_output=True, text=True, check=True)
        assert quiet.stdout == "[/abs]" and quiet.stderr == ""


def _update_manager():
    import importlib
    import sys
    sys.path.insert(0, str(REPO / "core" / "console"))
    sys.path.insert(0, str(REPO))
    um = importlib.import_module("corvin_console.update_manager")
    assert Path(um.__file__).resolve().is_relative_to(REPO), um.__file__
    return um


@pytest.mark.parametrize("xdg", [None, "/custom/state dir/100%x"])
@pytest.mark.parametrize("console_xdg", [None, "/somewhere/else", "relative"])
def test_a_console_started_update_takes_the_lock_the_watchdog_reads(tmp_path, monkeypatch, xdg, console_xdg):
    """Install shell (xdg) and console service (console_xdg) differ; the updater
    the console starts must still lock where the unit setup.sh wrote looks."""
    um = _update_manager()
    home = tmp_path / "home"
    unit = _write_unit(tmp_path, {"XDG_STATE_HOME": xdg} if xdg else {})
    manager_env = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
    reader = subprocess.run(["bash", "-c", f'printf %s "{_reader_expr(unit)}"'],
                            env={**manager_env, **_unit_environment(unit)},
                            capture_output=True, text=True, check=True).stdout

    monkeypatch.setenv("HOME", str(home))
    if console_xdg is None:
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_STATE_HOME", console_xdg)

    # (a) systemd-run: the manager's environment plus the --setenv arguments.
    cmd = um.isolated_command(tmp_path / "src", tmp_path / "state.json", tmp_path / "log")
    setenv = dict(a[len("--setenv="):].split("=", 1) for a in cmd if a.startswith("--setenv="))
    isolated_env = {**manager_env, **{k: v for k, v in setenv.items() if k == "XDG_STATE_HOME"}}
    assert _writer_lock("update.sh", isolated_env) == reader

    # (b) direct child: the console's environment, corrected.
    child_env = um.updater_env(tmp_path / "progress")
    direct = {**manager_env, **{k: v for k, v in child_env.items() if k == "XDG_STATE_HOME"}}
    assert _writer_lock("update.sh", direct) == reader
    assert child_env["CORVIN_UPDATE_PROGRESS"] == str(tmp_path / "progress")


@pytest.mark.parametrize("console_xdg,expected", [(None, None), ("/abs/state", "/abs/state"), ("rel", None)])
def test_without_a_watchdog_unit_the_console_environment_decides(tmp_path, monkeypatch, console_xdg, expected):
    um = _update_manager()
    monkeypatch.setenv("HOME", str(tmp_path / "no-units"))
    if console_xdg is None:
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_STATE_HOME", console_xdg)
    assert um.lock_state_home() == expected
    assert ("XDG_STATE_HOME" in um.updater_env(tmp_path / "p")) is (expected is not None)


def test_the_updater_spawn_uses_the_corrected_environment():
    """Call site: UpdateManager builds the child's env with updater_env, and the
    systemd-run wrapper names the state directory."""
    text = (REPO / "core" / "console" / "corvin_console" / "update_manager.py").read_text(encoding="utf-8")
    assert 'kwargs["env"] = updater_env(progress)' in text
    assert '"CORVIN_UPDATE_PROGRESS": str(progress)}' in text
    assert "--setenv=XDG_STATE_HOME={state_home}" in text

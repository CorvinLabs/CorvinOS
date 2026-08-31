"""Regression tests for corvinOS/installer/steps/dependencies.py's Python
package install fallback chain (``pip_install`` + its helpers).

Before this file existed, ``pip_install()`` had zero test coverage: every
consumer (``stt.py``, ``piper.py``, ``console.py``) imports it as
``_pip_install`` and every existing test for those modules mocks
``_pip_install`` out wholesale (``mock.patch.object(stt_mod, "_pip_install")``
etc.) instead of exercising the real implementation. The 5-tier fallback
chain — ensurepip bootstrap, in-venv plain install, ``--user``, PEP 668
stderr-string detection + ``--break-system-packages``, and a dedicated venv
at ``~/.config/corvin-voice/venv`` with ``_persist_venv_python()`` writing
``PY_BIN=`` into ``service.env`` — is exactly the kind of platform-fragile,
string-matched logic that silently breaks fresh installs on PEP 668 hosts
(Debian 12+, Ubuntu 23.10+) without any test catching it.

These tests mock ``subprocess.run`` entirely (no real pip/venv is ever
invoked) and monkeypatch ``Path.home()`` + ``VOICE_CONFIG_DIR`` so nothing
touches the real ``~/.config`` directory of the machine running the suite.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from corvinOS.installer.steps import dependencies as deps_mod


def _force_not_in_venv(monkeypatch: pytest.MonkeyPatch) -> None:
    """pip_install()'s in-venv branch takes a completely different (2-call)
    path; force the "system Python" branch so the 5-tier chain under test
    actually runs, regardless of whatever venv pytest itself happens to run
    inside."""
    monkeypatch.setattr(deps_mod.sys, "prefix", "/fake/prefix")
    monkeypatch.setattr(deps_mod.sys, "base_prefix", "/fake/prefix")
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)


def _completed(cmd: list[str], returncode: int, stderr: str = "", stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)


# ── PEP 668 detection → --break-system-packages succeeds, no venv fallback ──


def test_pip_install_pep668_falls_back_to_break_system_packages_without_venv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--user fails with the PEP 668 'externally-managed' stderr marker;
    --break-system-packages then succeeds. The dedicated-venv tier (tier 5)
    must never be reached — no venv creation call is issued."""
    _force_not_in_venv(monkeypatch)
    python_exe = sys.executable

    def fake_run(cmd, *args, **kwargs):
        if cmd[:4] == [python_exe, "-m", "pip", "--version"]:
            return _completed(cmd, 0, stdout="pip 24.0")
        if "--break-system-packages" in cmd:
            return _completed(cmd, 0)
        if "install" in cmd:
            return _completed(
                cmd, 1,
                stderr="error: externally-managed-environment\n"
                       "This environment is externally managed (PEP 668).",
            )
        raise AssertionError(f"unexpected subprocess.run call reached venv tier: {cmd!r}")

    with mock.patch.object(deps_mod.subprocess, "run", side_effect=fake_run) as m_run:
        ok = deps_mod.pip_install("somepkg")

    assert ok is True
    # Exactly 3 calls: pip --version, --user attempt, --break-system-packages
    # attempt. No 4th call (venv creation) may happen.
    assert m_run.call_count == 3
    assert not any("venv" in call.args[0] for call in m_run.call_args_list)


def test_pip_install_returns_false_on_non_pep668_failure_without_break_system_packages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plain --user failure that is NOT a PEP 668 'externally-managed'
    stderr must give up immediately — it must not try
    --break-system-packages or the venv fallback for an unrelated error
    (e.g. a genuine network failure or a bad package name)."""
    _force_not_in_venv(monkeypatch)
    python_exe = sys.executable

    def fake_run(cmd, *args, **kwargs):
        if cmd[:4] == [python_exe, "-m", "pip", "--version"]:
            return _completed(cmd, 0, stdout="pip 24.0")
        if "install" in cmd:
            return _completed(cmd, 1, stderr="ERROR: No matching distribution found for somepkg")
        raise AssertionError(f"unexpected subprocess.run call: {cmd!r}")

    with mock.patch.object(deps_mod.subprocess, "run", side_effect=fake_run) as m_run:
        ok = deps_mod.pip_install("somepkg")

    assert ok is False
    # Only 2 calls: pip --version + the single --user attempt. No
    # --break-system-packages retry, no venv fallback.
    assert m_run.call_count == 2
    assert not any("--break-system-packages" in call.args[0] for call in m_run.call_args_list)
    assert not any("venv" in call.args[0] for call in m_run.call_args_list)


# ── Both PEP-668 attempts fail → dedicated venv fallback ────────────────────


def test_pip_install_venv_fallback_creates_dedicated_venv_and_persists_py_bin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When both --user and --break-system-packages fail, pip_install() must
    fall back to a dedicated venv at ~/.config/corvin-voice/venv and persist
    PY_BIN= into service.env via _persist_venv_python()."""
    _force_not_in_venv(monkeypatch)
    python_exe = sys.executable

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(deps_mod.Path, "home", classmethod(lambda cls: fake_home))

    voice_config_dir = tmp_path / "voice_config"
    monkeypatch.setenv("VOICE_CONFIG_DIR", str(voice_config_dir))

    def fake_run(cmd, *args, **kwargs):
        if cmd[:4] == [python_exe, "-m", "pip", "--version"]:
            return _completed(cmd, 0, stdout="pip 24.0")
        if "--break-system-packages" in cmd:
            return _completed(cmd, 1, stderr="still externally-managed")
        if cmd[:3] == [python_exe, "-m", "pip"] and "install" in cmd:
            return _completed(
                cmd, 1,
                stderr="error: externally-managed-environment (PEP 668)",
            )
        if cmd[:2] == [python_exe, "-m"] and "venv" in cmd:
            # Real venv creation is mocked out entirely — nothing on disk
            # is actually created by subprocess, only by the code's own
            # mkdir() calls (which target the monkeypatched fake_home).
            return _completed(cmd, 0)
        # Final tier: the venv's own pip binary, invoked directly (not via
        # `-m pip`) — e.g. [".../venv/bin/pip", "install", "--quiet", "somepkg"]
        if cmd[0].endswith("pip") or cmd[0].endswith("pip.exe"):
            return _completed(cmd, 0)
        raise AssertionError(f"unexpected subprocess.run call: {cmd!r}")

    with mock.patch.object(deps_mod.subprocess, "run", side_effect=fake_run):
        ok = deps_mod.pip_install("somepkg")

    assert ok is True

    expected_venv_dir = fake_home / ".config" / "corvin-voice" / "venv"
    expected_venv_python = expected_venv_dir / "bin" / "python3"
    if sys.platform == "win32":
        expected_venv_python = expected_venv_dir / "Scripts" / "python.exe"

    # _persist_venv_python() must have written PY_BIN= to service.env,
    # resolved through the (monkeypatched) voice_config_dir SSOT.
    env_file = voice_config_dir / "service.env"
    assert env_file.is_file(), "service.env was not created by _persist_venv_python"
    content = env_file.read_text()
    assert f"PY_BIN={expected_venv_python}" in content


# ── _persist_venv_python: direct unit coverage ──────────────────────────────


def test_persist_venv_python_writes_py_bin_and_replaces_stale_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_persist_venv_python() must overwrite a stale PY_BIN= line rather
    than appending a duplicate, and must leave other lines untouched."""
    voice_config_dir = tmp_path / "voice_config"
    monkeypatch.setenv("VOICE_CONFIG_DIR", str(voice_config_dir))
    voice_config_dir.mkdir(parents=True)
    env_file = voice_config_dir / "service.env"
    env_file.write_text("SOME_OTHER_VAR=keep-me\nPY_BIN=/stale/old/python3\n")

    new_python = tmp_path / "venv" / "bin" / "python3"
    deps_mod._persist_venv_python(new_python)

    lines = env_file.read_text().splitlines()
    assert "SOME_OTHER_VAR=keep-me" in lines
    py_bin_lines = [l for l in lines if l.startswith("PY_BIN=")]
    assert py_bin_lines == [f"PY_BIN={new_python}"]


# ── _ensure_pip_available: ensurepip bootstrap ──────────────────────────────


def test_ensure_pip_available_bootstraps_via_ensurepip_when_pip_missing() -> None:
    """When `python -m pip --version` fails, _ensure_pip_available() must
    attempt `python -m ensurepip --upgrade` as a bootstrap (critical on
    uv-installed Python, which may ship without pip)."""
    python_exe = "/fake/python3"
    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        if cmd == [python_exe, "-m", "pip", "--version"]:
            return _completed(cmd, 1, stderr="No module named pip")
        if cmd == [python_exe, "-m", "ensurepip", "--upgrade"]:
            return _completed(cmd, 0)
        raise AssertionError(f"unexpected call: {cmd!r}")

    with mock.patch.object(deps_mod.subprocess, "run", side_effect=fake_run):
        deps_mod._ensure_pip_available(python_exe)

    assert [python_exe, "-m", "pip", "--version"] in calls
    assert [python_exe, "-m", "ensurepip", "--upgrade"] in calls


def test_ensure_pip_available_skips_ensurepip_when_pip_already_present() -> None:
    """When pip is already importable, no ensurepip bootstrap call should
    happen at all."""
    python_exe = "/fake/python3"

    def fake_run(cmd, *args, **kwargs):
        if cmd == [python_exe, "-m", "pip", "--version"]:
            return _completed(cmd, 0, stdout="pip 24.0")
        raise AssertionError(f"unexpected call: {cmd!r}")

    with mock.patch.object(deps_mod.subprocess, "run", side_effect=fake_run) as m_run:
        deps_mod._ensure_pip_available(python_exe)

    assert m_run.call_count == 1

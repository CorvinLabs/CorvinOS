#!/usr/bin/env python3
"""Regression: ACS run dir must be a Windows-legal path (no ':' in the session
component). Reported 2026-07-13 on Windows 11:

    OSError: [WinError 123] The filename, directory name, or volume label syntax
    is incorrect:
    'C:\\Users\\sjurk\\.corvin\\tenants\\_default\\sessions\\web:l9SGjbw_hK...\\acs\\runs\\...\\workers'

Root cause: ``acs_runtime._run_dir`` built the session component as the raw
chat_key ``f"{bridge}:{chat}"`` (e.g. "web:<sid>"). The ':' is legal on POSIX but
ILLEGAL in a Windows path component → os.mkdir raised WinError 123 and every ACS
run died on Windows. The main web-session workdir already sanitises this via
``chat_runtime._workdir`` → ``forge.paths.safe_session_subdir``; ``_run_dir`` now
routes through the SAME SSOT so (a) the ':' is sanitised on Windows and (b) the
ACS run dir lands UNDER the real session dir (no reader≠writer drift).
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_REPO = HERE.parents[2]  # operator/bridges/shared → CorvinOS repo root
for _p in (HERE, _REPO / "operator" / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import acs_runtime  # type: ignore  # noqa: E402
from forge import paths as _fp  # type: ignore  # noqa: E402


def test_run_dir_routes_through_safe_session_subdir(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    rd = acs_runtime._run_dir("_default", "web", "l9SGjbw_hKtOOjYw3Agokw", "acs-web-1-abc")
    sessions_base = tmp_path / "tenants" / "_default" / "sessions"
    expected_session = _fp.safe_session_subdir(sessions_base, "web:l9SGjbw_hKtOOjYw3Agokw")
    # The run dir is the SSOT session dir + acs/runs/<run_id> — i.e. _run_dir no
    # longer hand-builds "sessions/web:<sid>" itself.
    assert rd == expected_session / "acs" / "runs" / "acs-web-1-abc", rd


def test_windows_session_component_has_no_colon() -> None:
    # The SSOT sanitiser the fix routes through strips the ':' on Windows so the
    # exact reported WinError 123 can no longer occur.
    safe = _fp.fs_safe_component("web:l9SGjbw_hKtOOjYw3Agokw", windows=True)
    assert ":" not in safe, safe
    assert safe == "web_l9SGjbw_hKtOOjYw3Agokw", safe


def test_run_dir_is_creatable_under_windows_sanitised_tree(monkeypatch, tmp_path) -> None:
    """End-to-end: the sanitised run dir (workers subdir) must be os.mkdir-able —
    the operation that raised WinError 123 before the fix. Uses the Windows-safe
    component explicitly so the assertion holds on any host OS."""
    sessions_base = tmp_path / "tenants" / "_default" / "sessions"
    safe_session = sessions_base / _fp.fs_safe_component("web:l9SGjbw_hK", windows=True)
    workers = safe_session / "acs" / "runs" / "acs-web-1-abc" / "workers"
    workers.mkdir(parents=True, exist_ok=True)  # would raise WinError 123 with a ':'
    assert workers.is_dir()


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))

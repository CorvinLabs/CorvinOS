"""The start-up PATH hook never writes a virtual environment into the user PATH.

The registry is replaced by a fake module, so nothing on the machine changes.
"""
from __future__ import annotations

import sys
import types
from unittest import mock

import pytest

from corvinOS import _win_path_fix

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")


def _run_hook(tmp_path, *, in_venv: bool) -> list[str]:
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    (scripts / "corvin.exe").write_bytes(b"")
    written: list[str] = []
    fake = types.SimpleNamespace(
        HKEY_CURRENT_USER=0, KEY_QUERY_VALUE=1, KEY_SET_VALUE=2, REG_EXPAND_SZ=2,
        OpenKey=lambda *a: object(),
        QueryValueEx=lambda *a: (r"C:\existing", 2),
        SetValueEx=lambda key, name, res, kind, value: written.append(value),
        CloseKey=lambda key: None,
    )
    base = str(tmp_path / "base")
    prefix = str(tmp_path / "venv") if in_venv else base
    with mock.patch.dict(sys.modules, {"winreg": fake}), \
            mock.patch.dict("os.environ", {"PATH": r"C:\existing"}), \
            mock.patch("sysconfig.get_path", return_value=str(scripts)), \
            mock.patch.object(sys, "prefix", prefix), \
            mock.patch.object(sys, "base_prefix", base), \
            mock.patch("ctypes.windll.user32.SendMessageTimeoutW", return_value=1):
        _win_path_fix._ensure_scripts_on_path()
    return written


def test_virtual_environment_is_not_persisted(tmp_path):
    assert _run_hook(tmp_path, in_venv=True) == []


def test_plain_pip_install_is_still_persisted(tmp_path):
    assert _run_hook(tmp_path, in_venv=False) == [rf"C:\existing;{tmp_path / 'Scripts'}"]

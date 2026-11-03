"""Console call sites whose child can leave pipe-holding grandchildren use run_tree_safe.

On Windows a ``.cmd`` shim (npm, claude) is cmd.exe -> node.exe; on timeout
``subprocess.run`` kills only cmd.exe and then waits forever for the pipes the
surviving node.exe holds, hanging the console request.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from corvin_core import process_signals
from corvin_console.routes import bridges

_HOLDER = "import time; time.sleep(60)"


def _shim(tmp_path):
    """A ``.cmd`` shim that, like npm.cmd, runs a grandchild inheriting the pipes."""
    cmd = tmp_path / "fake-npm.cmd"
    cmd.write_text(f'@"{sys.executable}" -c "{_HOLDER}"\r\n', encoding="utf-8")
    return str(cmd)


@pytest.mark.skipif(os.name != "nt", reason="the hang is Windows-specific")
def test_cmd_shim_grandchild_timeout_is_bounded(tmp_path):
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        process_signals.run_tree_safe([_shim(tmp_path), "install"], capture_output=True,
                                      text=True, timeout=2)
    assert time.monotonic() - t0 < 15


@pytest.mark.skipif(os.name != "nt", reason="the hang is Windows-specific")
def test_ensure_npm_modules_returns_on_timeout(tmp_path, monkeypatch):
    chan = tmp_path / "bridges" / "fakechan"
    chan.mkdir(parents=True)
    (chan / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(bridges, "_BRIDGES_DIR", tmp_path / "bridges")
    monkeypatch.setattr(bridges, "_resolve_npm", lambda: _shim(tmp_path))
    real = process_signals.run_tree_safe
    seen = []

    def _short(argv, **kw):          # the site's 300 s would make the test slow
        seen.append(kw["timeout"])
        kw["timeout"] = 2
        return real(argv, **kw)

    monkeypatch.setattr(bridges, "_run_tree_safe", _short)
    t0 = time.monotonic()
    bridges._ensure_npm_modules("fakechan")   # swallows TimeoutExpired
    assert seen == [300]
    assert time.monotonic() - t0 < 15


def test_sites_delegate_to_subprocess_run_when_patched(monkeypatch, tmp_path):
    """POSIX / mocked tests: behaviour is exactly subprocess.run's."""
    chan = tmp_path / "bridges" / "c"
    chan.mkdir(parents=True)
    (chan / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(bridges, "_BRIDGES_DIR", tmp_path / "bridges")
    calls = []
    monkeypatch.setattr(subprocess, "run",
                        lambda argv, **kw: calls.append((argv, kw)) or
                        subprocess.CompletedProcess(argv, 0, "", ""))
    bridges._ensure_npm_modules("c")
    assert calls and calls[0][1]["timeout"] == 300 and calls[0][1]["cwd"] == chan

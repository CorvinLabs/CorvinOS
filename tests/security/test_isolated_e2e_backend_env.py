"""REVIEW-0064 #6: the isolated Playwright backend (web-next/scripts/start-isolated-e2e-backend.sh) names the platform data
dir below its sandbox, so a calling shell's XDG_DATA_HOME / XDG_STATE_HOME / XDG_CACHE_HOME cannot put the sandbox's managed
workspace into the real one.

The script is run for real with ``bash``; only its last step (``exec`` of the backend's Python) is replaced by an exported
shell function that prints the environment it would hand over, so nothing starts and nothing listens.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

_SCRIPT = (Path(__file__).resolve().parents[2] / "core" / "console" / "corvin_console" / "web-next" / "scripts"
           / "start-isolated-e2e-backend.sh")


@pytest.mark.skipif(shutil.which("bash") is None or os.name == "nt", reason="needs a POSIX bash")
def test_the_isolated_backend_puts_every_data_dir_below_its_sandbox():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        real = tmp / "real-user"
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(real),
               "XDG_DATA_HOME": str(real / ".local" / "share"), "XDG_STATE_HOME": str(real / ".local" / "state"),
               "XDG_CACHE_HOME": str(real / ".cache"), "CORVIN_E2E_HOME": str(tmp / "e2e-home"),
               # replaces the script's final `exec <python> -m uvicorn ...`: print what the backend would get, start nothing
               "BASH_FUNC_exec%%": "() { env; }"}
        r = subprocess.run(["bash", str(_SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        got = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
        sandbox = (tmp / "e2e-home-env").resolve()
        for k in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
            assert k in got, (k, sorted(got))
            assert Path(got[k]).resolve().is_relative_to(sandbox), (k, got[k])
        assert not any(str(real) in got[k] for k in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"))
        assert not real.exists(), "the script wrote below the real home"

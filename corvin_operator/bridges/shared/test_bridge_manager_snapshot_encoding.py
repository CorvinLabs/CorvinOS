"""The Windows process snapshot must survive non-ASCII command lines.

The console runs in Python UTF-8 mode, while a piped PowerShell writes in the
OEM code page. One umlaut anywhere in the process list (a user profile such as
``C:\\Users\\Jörg``) raised UnicodeDecodeError in subprocess's reader thread, so
adapter and daemon detection saw nothing.

The probe runs in a child interpreter with ``-X utf8`` to reproduce that mode.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_BRIDGES = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows code pages")

_PROBE = r"""
import subprocess, sys
sys.path.insert(0, sys.argv[1])
import bridge_manager as bm
marker = "J\u00f6rg-M\u00fcller-probe"
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", marker])
try:
    procs, confident = bm._win_process_snapshot()
    assert confident, "snapshot not confident"
    assert marker in procs.get(child.pid, ""), ascii(procs.get(child.pid))
finally:
    child.kill()
print("OK")
"""


def test_snapshot_keeps_umlauts_in_utf8_mode():
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", _PROBE, str(_BRIDGES)],
        capture_output=True, encoding="utf-8", errors="replace", timeout=90,
    )
    assert result.returncode == 0 and "OK" in result.stdout, result.stderr
    assert "UnicodeDecodeError" not in result.stderr, result.stderr

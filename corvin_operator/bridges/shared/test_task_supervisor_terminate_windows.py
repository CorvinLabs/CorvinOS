"""Windows: the supervisor's wedge stop really stops the worker.

``taskkill /T`` without ``/F`` only asks politely; a windowless console worker refuses
("can only be terminated forcefully", rc 128) and kept running next to the resumed
replacement the supervisor launched right after.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import task_supervisor as sup  # noqa: E402

pytestmark = pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows taskkill")


def test_terminate_stops_a_windowless_worker():
    worker = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                              creationflags=0x08000000)  # CREATE_NO_WINDOW, like the real worker
    try:
        time.sleep(0.3)
        sup._terminate(worker.pid)
        assert worker.wait(timeout=10) is not None
    finally:
        if worker.poll() is None:
            worker.kill()

"""Both writers of the corvin-webui command bound uvicorn's graceful shutdown.

Without ``--timeout-graceful-shutdown`` uvicorn waits for every open connection; the
console's chat WebSockets stay open as long as a tab is, so each ``systemctl restart``
sat in "Waiting for connections to close" for TimeoutStopSec (90 s) and ended in SIGKILL
(2026-11-07, three restarts in a row). ``ops/launcher/service_entry.py`` writes the
always-on unit, ``corvinOS/installer/core.py`` the installer's.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("rel", [
    "ops/launcher/service_entry.py",
    "corvinOS/installer/core.py",
])
def test_webui_command_bounds_the_graceful_shutdown(rel):
    src = (REPO / rel).read_text(encoding="utf-8")
    i = src.index("-m uvicorn corvin_gateway.app:app")
    window = src[i:i + 400]
    assert "--timeout-graceful-shutdown 15" in window

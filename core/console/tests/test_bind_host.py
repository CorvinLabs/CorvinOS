"""The always-on host honours ``a2a_lan_bind`` (ADR-2057).

``corvin-webui.service`` and ``bridge.sh console`` hard-coded
``--host 127.0.0.1`` while ``corvin serve``/``corvin-service``/the installer
honoured the flag — so flipping it did nothing on a bridge.sh install. Both now
resolve the host through ``python -m corvin_core.bind_host``.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[3]
CONSOLE = REPO / "core" / "console"


def test_resolver_defaults_to_loopback_and_never_widens_on_error():
    from corvin_core import bind_host

    with mock.patch("corvin_core.feature_flags.is_enabled", return_value=False):
        assert bind_host.resolve_bind_host() == "127.0.0.1"
    with mock.patch("corvin_core.feature_flags.is_enabled", return_value=True):
        assert bind_host.resolve_bind_host() == "0.0.0.0"
    with mock.patch("corvin_core.feature_flags.is_enabled", side_effect=RuntimeError("corrupt overlay")):
        assert bind_host.resolve_bind_host() == "127.0.0.1"


def test_module_entry_point_prints_a_host(tmp_path):
    env = dict(os.environ, CORVIN_HOME=str(tmp_path), PYTHONPATH=str(CONSOLE))
    out = subprocess.run([sys.executable, "-m", "corvin_core.bind_host"],
                         env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() in {"127.0.0.1", "0.0.0.0"}


def test_no_launcher_hard_codes_the_host():
    unit = (REPO / "core/gateway/systemd/corvin-webui.service").read_text()
    execstart = [l for l in unit.splitlines() if l.startswith("ExecStart=")]
    assert execstart and "corvin_core.bind_host" in execstart[0]
    assert "--host 127.0.0.1" not in execstart[0]
    bridge = (REPO / "corvin_operator/bridges/bridge.sh").read_text()
    uvicorn_calls = re.findall(r"uvicorn corvin_gateway\.app:app[^\n]*\n[^\n]*", bridge)
    assert uvicorn_calls, "positive control: bridge.sh launches the gateway"
    assert all("--host 127.0.0.1" not in c for c in uvicorn_calls)

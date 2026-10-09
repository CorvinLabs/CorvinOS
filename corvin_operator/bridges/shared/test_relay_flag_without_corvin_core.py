"""Regression: a subprocess that cannot import ``corvin_core`` (the
orchestration MCP server's narrow PYTHONPATH) must still honour the operator's
``a2a_relay_fallback`` flag. It used to read the ImportError as "flag off", so
MCP-sent A2A tasks skipped the relay and failed ``unreachable`` for a peer that
was only reachable through it."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SHARED = Path(__file__).resolve().parent
REPO = SHARED.parents[2]

_PROBE = (
    "import sys; sys.path.insert(0, %r);"
    "import importlib.util as u; assert u.find_spec('corvin_core') is None;"
    "import remote_trigger_sender as r; print(r._relay_fallback_enabled())"
) % str(SHARED)


def _probe(home: Path) -> str:
    env = {"CORVIN_HOME": str(home), "HOME": str(home), "PATH": "/usr/bin:/bin"}
    out = subprocess.run([sys.executable, "-c", _PROBE], env=env, cwd=str(home),
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-800:]
    return out.stdout.strip()


def _overlay(home: Path, flags: dict | None) -> None:
    d = home / "tenants" / "_default" / "global"
    d.mkdir(parents=True)
    if flags is not None:
        (d / "features.json").write_text(json.dumps({"flags": flags}))


def test_flag_on_in_overlay_is_honoured_without_corvin_core(tmp_path):
    _overlay(tmp_path, {"a2a_relay_fallback": True})
    assert _probe(tmp_path) == "True"


def test_flag_off_or_missing_stays_dark_without_corvin_core(tmp_path):
    _overlay(tmp_path, {"a2a_relay_fallback": False})
    assert _probe(tmp_path) == "False"
    other = tmp_path / "none"
    other.mkdir()
    assert _probe(other) == "False"


def test_corrupt_overlay_fails_dark(tmp_path):
    d = tmp_path / "tenants" / "_default" / "global"
    d.mkdir(parents=True)
    (d / "features.json").write_text("{not json")
    assert _probe(tmp_path) == "False"


def test_orchestration_mcp_pythonpath_carries_corvin_core():
    sys.path.insert(0, str(REPO / "corvin_operator" / "cowork" / "lib"))
    src = (REPO / "corvin_operator" / "cowork" / "lib" / "resolver.py").read_text()
    block = src[src.index('mcp["corvin_orchestration"]'):]
    block = block[:block.index("_ensure_brief")]
    assert "{{CORE_ROOT}}/core/console" in block

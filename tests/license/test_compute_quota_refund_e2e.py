"""A run the worker refused must not cost a unit of the free tier's daily compute quota.

Measured 2026-11-06: the forge MCP server charges ``compute_units_per_day`` BEFORE it asks
the worker, so every refusal (tool not in the user scope, param_grid not matching the tool)
used up one of the ten daily units for a run that never existed. The console route already
refunds on a refusal; the MCP path did not.

Chain: JSON-RPC over stdio -> real forge MCP server -> trial gate + daily quota gate ->
real worker over its Unix socket (real forge runner, bwrap) -> counter file on disk.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
for _p in (_REPO / "corvin_operator" / "forge" / "tests", _REPO / "core" / "compute" / "tests",
           _REPO / "core" / "compute", _REPO / "corvin_operator"):
    sys.path.insert(0, str(_p))

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("bwrap") is None, reason="needs bwrap")

import test_mcp as _harness  # noqa: E402
from _real_worker import real_worker, seed_user_tool  # noqa: E402

QUAD = ("import json, sys\n"
        "d = json.load(sys.stdin)\n"
        "print(json.dumps({'loss': (float(d['x']) - 2) ** 2}))\n")
SCHEMA = {"type": "object", "properties": {"x": {"type": "number"}}, "required": ["x"],
          "additionalProperties": False}


@pytest.fixture
def stack(monkeypatch):
    home = Path(tempfile.mkdtemp(prefix="cq-"))
    monkeypatch.setenv("CORVIN_HOME", str(home))
    seed_user_tool("probe.quad", QUAD, SCHEMA)
    ws = Path(tempfile.mkdtemp(prefix="cqws-"))
    try:
        with real_worker(home) as (_client, _server):
            mcp = _harness.MCPClient(ws / "ws", licence_tier="free")
            mcp.initialize()
            try:
                yield home, mcp
            finally:
                mcp.close()
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(ws, ignore_errors=True)


def _units(home: Path) -> int:
    from license.compute_quota import get_today_count
    return get_today_count(home)


def _compute_run(mcp, **args):
    base = {"loss_metric": "loss", "strategy": "grid", "minimise": True,
            "budget": {"max_iterations": 5, "max_wall_clock_s": 60}}
    base.update(args)
    resp = mcp.request("tools/call", {"name": "compute_run", "arguments": base}, timeout=60)
    return resp


def test_refused_runs_cost_nothing_and_an_accepted_run_costs_one(stack):
    home, mcp = stack
    assert _units(home) == 0

    # 1. a tool that is not in the user-scope registry the worker executes from
    r = _compute_run(mcp, tool_name="probe.session_only", param_grid={"x": [1, 2]})
    assert "ToolNotAvailable" in json.dumps(r), r
    assert _units(home) == 0, "a refused run (unknown tool) was charged"

    # 2. a param_grid that does not match the tool's input_schema
    r = _compute_run(mcp, tool_name="probe.quad", param_grid={"z": [1, 2]})
    assert "ParamGridMismatch" in json.dumps(r), r
    assert _units(home) == 0, "a refused run (param_grid mismatch) was charged"

    # 3. an accepted run is charged exactly once
    r = _compute_run(mcp, tool_name="probe.quad", param_grid={"x": [1, 2, 3]})
    assert "compute_handle" in json.dumps(r), r
    assert _units(home) == 1

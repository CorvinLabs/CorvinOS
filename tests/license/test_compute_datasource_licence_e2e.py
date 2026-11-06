"""The licence's datasource adapter allowlist holds on the compute path.

Free = ``["local_file"]``, Member = unlimited. ``datasource_query`` and the console routes
enforce it; ``compute_run(datasources=[...])`` reached every adapter through the worker's
runner with no licence check at all (adversarial review 2026-11-06).

Chain: JSON-RPC over stdio -> real forge MCP server -> real worker (its own licence read) ->
preflight at submission / the runner's env resolution at execution.
"""
from __future__ import annotations

import json
import time

import pytest

from test_compute_quota_refund_e2e import _compute_run, _units, stack  # noqa: F401


def _write_conn(home, name: str, adapter: str, config: dict | None = None) -> None:
    d = home / "tenants" / "_default" / "datasource_connections"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps({
        "dsi_version": "1", "name": name, "adapter": adapter,
        "config": config or {"host": "localhost", "port": 5432, "database": "d", "user": "u"},
        "data_classification": "INTERNAL", "data_residency": "any"}), encoding="utf-8")


def test_free_tier_refuses_a_postgresql_binding_before_the_run_and_charges_nothing(stack):  # noqa: F811
    home, mcp = stack
    _write_conn(home, "pg", "postgresql")
    r = _compute_run(mcp, tool_name="probe.quad", param_grid={"x": [1, 2]}, datasources=["pg"])
    text = json.dumps(r)
    assert "DatasourceRefused" in text and "requires a Member licence" in text, r
    assert _units(home) == 0


def test_free_tier_accepts_a_local_file_binding(stack):  # noqa: F811
    home, mcp = stack
    _write_conn(home, "csv", "local_file", {"path": "/nonexistent.csv", "format": "csv"})
    r = _compute_run(mcp, tool_name="probe.quad", param_grid={"x": [1, 2]}, datasources=["csv"])
    assert "compute_handle" in json.dumps(r), r


def test_a_datasource_that_does_not_resolve_is_refused_by_name(stack):  # noqa: F811
    home, mcp = stack
    r = _compute_run(mcp, tool_name="probe.quad", param_grid={"x": [1]}, datasources=["nope"])
    assert "DatasourceRefused" in json.dumps(r) and "nope" in json.dumps(r), r
    assert _units(home) == 0


def test_member_licence_allows_the_postgresql_binding(stack):  # noqa: F811
    import license.validator as _v
    home, mcp = stack
    _write_conn(home, "pg", "postgresql")
    _v._set_active_license({"tier": "member", "jti": "t-compute-adapter"})
    try:
        r = _compute_run(mcp, tool_name="probe.quad", param_grid={"x": [1, 2]}, datasources=["pg"])
    finally:
        _v._set_active_license(None)
    assert "compute_handle" in json.dumps(r), r


def test_the_runner_refuses_an_unlicensed_adapter_at_execution_time_too(stack):  # noqa: F811
    """A run bound while Member and resumed after a downgrade: the env resolution at
    execution time is the second line, so the iterations fail naming the licence."""
    from corvin_compute.recovery import resume_run
    from corvin_compute.state import RunStore, new_run_id
    from corvin_compute import cli
    home, _mcp = stack
    _write_conn(home, "pg", "postgresql")
    rid = new_run_id()
    store = RunStore(home, "_default")
    store.write_manifest(rid, {
        "run_id": rid, "tenant_id": "_default", "tool_name": "probe.quad", "strategy": "grid",
        "param_grid": {"x": [1, 2, 3, 4]}, "loss_metric": "loss", "datasources": ["pg"],
        "budget": {"max_iterations": 4, "max_wall_clock_s": 30}, "minimise": True,
        "accepted_at": time.time(), "sensitive_fields": []})
    store.write_summary(rid, {"state": "running", "tenant_id": "_default", "started_at": time.time()})
    runner = cli._build_runner_fn(tenant_id="_default", corvin_home_path=home)
    state = resume_run(home, "_default", rid, runner_fn=runner)
    summary = store.read_summary(rid)
    assert state == "failed", summary
    assert "requires a Member licence" in summary.get("convergence_reason", ""), summary

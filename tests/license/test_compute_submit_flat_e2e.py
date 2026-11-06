"""compute_submit(engine="flat") must run, over the real MCP server and the real worker.

Measured 2026-11-06: COMPUTE_SUBMIT_SCHEMA puts the compute_run fields under ``extra`` for
engine "flat", but the worker read ``tool_name`` / ``param_grid`` from the top level only, so
every such submission died with a bare KeyError (and the trial gate's special case for it was
unreachable). The strategy must also be charged to the counter its clamp used.
"""
from __future__ import annotations

import json
import time

from test_compute_quota_refund_e2e import _units, stack  # noqa: F401  (fixture re-export)


def _call(mcp, name, args, timeout=60):
    return mcp.request("tools/call", {"name": name, "arguments": args}, timeout=timeout)


def _payload(resp) -> dict:
    assert "result" in resp and not resp["result"].get("isError"), resp
    return json.loads("".join(c["text"] for c in resp["result"]["content"]))


def _wait_result(mcp, handle) -> dict:
    for _ in range(40):
        res = _payload(_call(mcp, "compute_result", {"compute_handle": handle, "wait_s": 5}))
        if res.get("state") not in ("running", "queued"):
            return res
        time.sleep(0.2)
    raise AssertionError("run did not finish")


def test_flat_submit_runs_and_finds_the_optimum(stack):  # noqa: F811
    home, mcp = stack
    r = _payload(_call(mcp, "compute_submit", {
        "engine": "flat", "budget": {"max_iterations": 10, "max_wall_clock_s": 60},
        "extra": {"tool_name": "probe.quad", "param_grid": {"x": [0, 1, 2, 3, 4]},
                  "loss_metric": "loss", "strategy": "grid"}}))
    res = _wait_result(mcp, r["compute_handle"])
    assert res["state"] != "failed" and res["best_loss"] == 0.0, res
    assert res["best_params"] == {"x": 2}
    assert _units(home) == 1


def test_flat_submit_without_the_tool_fields_says_what_is_missing(stack):  # noqa: F811
    home, mcp = stack
    resp = _call(mcp, "compute_submit", {"engine": "flat", "budget": {"max_iterations": 3},
                                         "extra": {"loss_metric": "loss"}})
    text = json.dumps(resp)
    assert "extra.tool_name" in text and "KeyError" not in text, text
    assert _units(home) == 0, "a refused submission must not cost a unit"


def test_flat_submit_is_refused_like_compute_run_for_a_tool_outside_the_user_scope(stack):  # noqa: F811
    home, mcp = stack
    resp = _call(mcp, "compute_submit", {
        "engine": "flat", "budget": {"max_iterations": 3},
        "extra": {"tool_name": "probe.elsewhere", "param_grid": {"x": [1]}}})
    assert "ToolNotAvailable" in json.dumps(resp), resp
    assert _units(home) == 0


def test_a_bayesian_flat_submit_is_charged_to_the_bayesian_trial_counter(stack):  # noqa: F811
    home, mcp = stack
    r = _payload(_call(mcp, "compute_submit", {
        "engine": "flat", "budget": {"max_iterations": 6, "max_wall_clock_s": 60},
        "extra": {"tool_name": "probe.quad", "param_grid": {"x": [0, 1, 2, 3, 4, 5, 6, 7]},
                  "loss_metric": "loss", "strategy": "bayesian", "seed": 3}}))
    _wait_result(mcp, r["compute_handle"])
    state = json.loads((home / "global" / "license" / "trial_compute.json").read_text())
    assert state["bayesian_iterations_used"] == 6 and state["iterations_used"] == 0, state

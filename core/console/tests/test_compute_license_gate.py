"""ADR-0146 CON-ACS-01 / CON-JOBS-01: the shared fail-closed compute-quota gate
and its wiring into the two previously-ungated compute-execution entrypoints.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

_CONSOLE = Path(__file__).resolve().parents[1]
if str(_CONSOLE) not in sys.path:
    sys.path.insert(0, str(_CONSOLE))

from corvin_console.routes import _compute_license_gate as G


def test_fail_closed_when_quota_module_unavailable(monkeypatch):
    # license module absent must REFUSE compute (402), never grant unmetered.
    monkeypatch.setattr(G, "_COMPUTE_QUOTA_OK", False)
    monkeypatch.setattr(G, "_cq_increment", None)
    with pytest.raises(HTTPException) as ei:
        G.enforce_compute_quota("_default", "fp123456", audit_action="acs.run_submit")
    assert ei.value.status_code == 402
    assert ei.value.detail["feature"] == "compute_units_per_day"


def test_quota_exceeded_raises_402(monkeypatch):
    def _raise(*_a, **_kw):
        raise G._LicLimitError("compute_units_per_day", 2, 1)
    monkeypatch.setattr(G, "_COMPUTE_QUOTA_OK", True)
    monkeypatch.setattr(G, "_cq_increment", _raise)
    with pytest.raises(HTTPException) as ei:
        G.enforce_compute_quota("_default", "fp123456", audit_action="acs.run_submit")
    assert ei.value.status_code == 402
    assert ei.value.detail["feature"] == "compute_units_per_day"


def test_under_quota_passes(monkeypatch):
    monkeypatch.setattr(G, "_COMPUTE_QUOTA_OK", True)
    monkeypatch.setattr(G, "_cq_increment", lambda *a, **kw: None)
    # Must not raise.
    G.enforce_compute_quota("_default", "fp123456", audit_action="acs.run_submit")


def test_all_compute_entrypoints_call_the_shared_gate():
    """ADR-0147 R3-CON-RUNS-DRIFT-01: every compute-execution entrypoint must
    route through the ONE fail-closed helper so they cannot drift — submit_run
    (/compute/runs), submit_acs_workflow_run (/compute/acs/runs), submit_compute_job
    (/compute/jobs) and trigger_flow_run (/flows/trigger)."""
    routes = _CONSOLE / "corvin_console" / "routes"
    compute = (routes / "compute.py").read_text(encoding="utf-8")
    jobs = (routes / "compute_jobs.py").read_text(encoding="utf-8")
    flows = (routes / "flows.py").read_text(encoding="utf-8")
    # compute.py hosts BOTH submit_run and submit_acs_workflow_run → ≥2 calls.
    assert compute.count("enforce_compute_quota") >= 2, (
        "compute.py must call the shared gate from BOTH submit_run AND "
        "submit_acs_workflow_run (R3-CON-RUNS-DRIFT-01 / CON-ACS-01)"
    )
    assert "enforce_compute_quota" in jobs, "submit_compute_job must call the shared gate (CON-JOBS-01)"
    assert "enforce_compute_quota" in flows, "trigger_flow_run must call the shared gate (FLOW-COMPUTE-01)"
    # The old fail-open inline guard must be gone from submit_run.
    assert "pass  # Operational errors: fail-open" not in compute, (
        "submit_run's fail-open inline quota guard must be removed (R3-CON-RUNS-DRIFT-01)"
    )
    # ADR-0149: the workflow-run surface must charge the daily quota too.
    workflows = (routes / "workflows.py").read_text(encoding="utf-8")
    assert "enforce_compute_quota" in workflows, (
        "start_run (POST /workflows/{wid}/runs) must charge compute_units_per_day "
        "in addition to workflows_concurrent (LIC-WFRUN-01)"
    )


def test_chat_turns_is_a_separate_axis_from_compute():
    """ADR-0150: chat_turns_per_day must use its OWN counter file and limit key,
    so conversational turns do not consume the 1/day compute-workload budget."""
    import sys as _s
    _s.path.insert(0, str(_CONSOLE.parents[1] / "operator"))
    import importlib, tempfile
    cq = importlib.import_module("license.compute_quota")
    import license.validator as _v
    _v._set_active_license(None)  # free tier: compute=1, chat UNLIMITED (None)
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        cq.increment_and_check(home, feature="chat_turns_per_day", counter_file="chat_quota.json")
        assert cq.get_today_count(home, "compute_quota.json") == 0, \
            "a chat turn must not consume the compute_units_per_day counter"
        # Chat is UNLIMITED on every tier (operator decision 2026-06-23): many
        # turns must NEVER raise a limit error. Only the heavier axes (compute,
        # workflows, A2A, custom layers) are gated.
        for _ in range(200):
            cq.increment_and_check(home, feature="chat_turns_per_day", counter_file="chat_quota.json")


def test_chat_ws_surfaces_call_the_chat_gate():
    routes = _CONSOLE / "corvin_console" / "routes"
    chat_src = (routes / "chat.py").read_text(encoding="utf-8")
    wf_src = (routes / "workflows.py").read_text(encoding="utf-8")
    assistant_src = (routes / "assistant.py").read_text(encoding="utf-8")
    assert "enforce_chat_turns" in chat_src, "chat WS must charge chat_turns_per_day (LIC-WEBCHAT-SPAWN-01)"
    assert "enforce_chat_turns" in wf_src, "design WS must charge chat_turns_per_day (LIC-WFDESIGN-SPAWN-02)"
    # ADR-0150 LIC-ASSISTANT-SPAWN-01: the floating-assistant route is the third
    # interactive claude -p surface and must charge the same chat-turn axis.
    assert "enforce_chat_turns" in assistant_src, (
        "POST /assistant/message must charge chat_turns_per_day (LIC-ASSISTANT-SPAWN-01)"
    )


def test_submit_run_talks_to_worker_with_correct_field_names(monkeypatch, tmp_path):
    """WA-14 regression: POST /compute/runs used to just write a manifest.json
    that no poller ever read (real runs never executed). It must now call the
    worker over its Unix socket with the field names the worker actually
    expects (param_grid/loss_metric/max_wall_clock_s), translated from the
    console request model's params/budget.timeout_s."""
    _compute_pkg = _CONSOLE.parents[0] / "compute"
    if str(_compute_pkg) not in sys.path:
        sys.path.insert(0, str(_compute_pkg))
    import corvin_compute.client as _client_mod
    from corvin_console.routes import _compute_license_gate as G
    from corvin_console.routes import compute as C

    monkeypatch.setattr(G, "_COMPUTE_QUOTA_OK", True)
    monkeypatch.setattr(G, "_cq_increment", lambda *a, **kw: None)

    sock = tmp_path / "worker.sock"
    sock.write_text("")  # just needs to exist for the .exists() check
    monkeypatch.setattr(C, "_socket_path", lambda tid: sock)

    captured: dict = {}

    class _FakeWorkerClient:
        def __init__(self, socket_path):
            captured["socket_path"] = socket_path

        def submit_run(self, **kwargs):
            captured["kwargs"] = kwargs
            return {"compute_handle": "run_fake123", "state": "running"}

    monkeypatch.setattr(_client_mod, "WorkerClient", _FakeWorkerClient)

    class _FakeRec:
        tenant_id = "_default"
        sid_fingerprint = "fp123456"

    body = C.SubmitRunRequest(
        tool_name="spotify_rank_score",
        strategy="grid",
        budget={"max_iterations": 10, "timeout_s": 120},
        objective="minimize_loss",
        params={"w": [0.0, 1.0]},
    )
    result = C.submit_run(body, rec=_FakeRec())

    assert result == {"ok": True, "run_id": "run_fake123", "state": "running"}
    assert captured["kwargs"]["tool_name"] == "spotify_rank_score"
    assert captured["kwargs"]["param_grid"] == {"w": [0.0, 1.0]}
    assert captured["kwargs"]["loss_metric"] == "loss"
    assert captured["kwargs"]["budget"] == {"max_iterations": 10, "max_wall_clock_s": 120}
    assert captured["kwargs"]["minimise"] is True


def test_compute_license_status_reflects_member_tier_without_enterprise_key(monkeypatch):
    """/compute/license must not hardcode tier="free" merely because no Enterprise
    (on-prem) license.jwt is installed — that is the normal case for a Paddle/
    consumer subscriber, who is licensed through the separate operator/license
    system (license.key). Previously this endpoint always reported "Trial · free"
    for such a customer even though compute_units_per_day was already correctly
    unlimited from that same operator/license system on the line above."""
    import corvin_license.verifier as _clv
    from corvin_console.routes import compute as C

    def _raise_missing():
        raise _clv.LicenseFileMissing("no enterprise license installed")

    monkeypatch.setattr(_clv, "load_license_from_disk", _raise_missing)
    monkeypatch.setattr(C, "_lic_active_tier", lambda: "member")
    monkeypatch.setattr(C, "_lic_get_limit", lambda *_a, **_kw: None)  # unlimited
    monkeypatch.setattr(C, "_cq_today", lambda *_a, **_kw: 3)
    monkeypatch.setattr(C, "_runs_today_count", lambda *_a, **_kw: 3)

    class _FakeRec:
        tenant_id = "_default"

    result = C.compute_license_status(rec=_FakeRec())
    assert result["tier"] == "member"
    assert result["mode"] == "licensed"
    assert result["daily_limit"] is None


def test_acs_chokepoint_charges_daily_quota():
    """ADR-0149 WF-CLI-ACS-01: run_acs_workflow charges the daily counter at the
    single chokepoint, so the CLI and scheduler paths cannot bypass it."""
    shared = Path("/home/shumway/projects/CorvinOS/operator/bridges/shared")
    src = (shared / "acs_engine_adapter.py").read_text(encoding="utf-8")
    assert "_enforce_acs_compute_quota" in src and "increment_and_check" in src, (
        "run_acs_workflow must charge compute_units_per_day at the ACS chokepoint"
    )
    assert "charge_quota" in src, "run_acs_workflow must expose charge_quota to avoid double-counting"

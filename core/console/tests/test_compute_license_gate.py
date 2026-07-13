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


def test_cross_tenant_compute_quota_is_not_isolated_bug(monkeypatch, tmp_path):
    """BLIND-SPOT / BUG (ADR-0007 multi-tenant isolation, ADR-0094 M2 spec):
    enforce_compute_quota() takes a tenant_id and threads it into the audit
    chat_key, but the storage path it hands to license.compute_quota is the
    GLOBAL install root (`_forge_paths.corvin_home()`), never
    `tenant_home(tenant_id)`. ADR-0094 M2 specifies the counter must live at
    `<corvin_home>/tenants/<tid>/global/license/compute_quota.json`; the
    shipped code writes `<corvin_home>/global/license/compute_quota.json` —
    ONE file shared by every tenant.

    This test pins `_forge_paths.corvin_home` to a tmp dir, exhausts the
    free-tier 1/day cap for tenant 'acme', and shows a completely unrelated
    tenant 'beta' is ALSO rejected — a cross-tenant quota exhaustion / DoS.
    If this is ever intentionally fixed to be per-tenant, this test's second
    assertion must flip to "beta is NOT blocked" and the docstring above
    must be updated to say so explicitly.
    """
    import license.validator as _v

    _v._set_active_license(None)  # free tier: compute_units_per_day == 1

    home = tmp_path / "corvin_home"
    monkeypatch.setattr(G._forge_paths, "corvin_home", lambda: home)

    # tenant 'acme' consumes the ONLY unit of the shared daily cap.
    G.enforce_compute_quota("acme", "fpAAAAAA", audit_action="acs.run_submit")

    # tenant 'beta' is a totally different tenant_id -> must be independent,
    # but currently is NOT: it hits the same global counter file and is
    # rejected with 402, even though it never spent any quota of its own.
    with pytest.raises(HTTPException) as ei:
        G.enforce_compute_quota("beta", "fpBBBBBB", audit_action="acs.run_submit")
    assert ei.value.status_code == 402
    assert ei.value.detail["feature"] == "compute_units_per_day"

    # Confirm root cause: only ONE quota file exists on disk, with no
    # tenant-scoped path component anywhere under it — 'acme' and 'beta'
    # both wrote/read the exact same file.
    quota_files = list(home.rglob("compute_quota.json"))
    assert len(quota_files) == 1, (
        "expected exactly one global compute_quota.json shared across "
        f"tenants (found {quota_files}) — proves the counter is NOT "
        "tenant-scoped"
    )
    assert quota_files[0] == home / "global" / "license" / "compute_quota.json"
    assert "tenants" not in quota_files[0].parts, (
        "compute_quota.json must not live under a tenant-scoped directory "
        "today (this is the bug) — per ADR-0094 M2 it SHOULD, at "
        "<corvin_home>/tenants/<tid>/global/license/compute_quota.json"
    )


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


def test_license_status_reflects_member_tier_without_enterprise_key(monkeypatch, tmp_path):
    """WA-17 regression: GET /license/status (consumed by the Dashboard) had
    the identical conflation bug as /compute/license — falling back to a
    hardcoded tier="free" whenever no Enterprise on-prem license.jwt exists,
    even though /license/info (the dedicated License page) correctly showed
    "member" for the exact same operator/license license.key. Two pages
    disagreeing about the same customer's tier is precisely what read as
    "the license gets lost sometimes"."""
    from corvin_console.routes import license as L

    missing_path = tmp_path / "no-such-license.jwt"
    monkeypatch.setattr(L._verifier, "license_file_path", lambda: missing_path)
    monkeypatch.setattr(L, "_lic_active_tier", lambda: "member")

    result = L._compute_license_status()
    assert result.tier == "member"
    assert result.mode == "active"


def test_pipeline_detail_derives_stage_state_from_pipeline_summary(tmp_path):
    """WA-16 regression: PipelineCoordinator only ever writes the rolling
    pipeline_summary.json (per PipelineStore's own documented layout) — a
    per-stage stage_summary.json was never part of the write-side contract,
    so every stage card showed "waiting for prev stage…"/"no data" even for
    a fully converged pipeline with real best_losses. pipeline_detail must
    derive state/best_loss from pipeline_summary.json when no richer
    stage_summary.json exists."""
    import json

    from corvin_console.routes import compute as C

    tid = "_default"
    home = tmp_path / "corvin_home"
    pdir = home / "tenants" / tid / "compute" / "pipelines" / "pipeline_test123"
    (pdir / "stages" / "s1").mkdir(parents=True)
    (pdir / "stages" / "s2").mkdir(parents=True)
    (pdir / "manifest.json").write_text(json.dumps({
        "stages": [
            {"stage_id": "s1", "tool_name": "t1"},
            {"stage_id": "s2", "tool_name": "t2"},
        ],
    }), encoding="utf-8")
    (pdir / "pipeline_summary.json").write_text(json.dumps({
        "state": "converged",
        "current_stage_id": None,
        "completed_stages": ["s1", "s2"],
        "best_losses": {"s1": 0.5038, "s2": 0.1042},
    }), encoding="utf-8")

    class _FakeRec:
        tenant_id = tid

    import corvin_console.routes.compute as _compute_mod
    orig = _compute_mod._pipelines_dir
    _compute_mod._pipelines_dir = lambda t: home / "tenants" / t / "compute" / "pipelines"
    try:
        result = C.pipeline_detail("pipeline_test123", rec=_FakeRec())
    finally:
        _compute_mod._pipelines_dir = orig

    stages = {s["stage_id"]: s for s in result["stages"]}
    assert stages["s1"]["state"] == "complete"
    assert stages["s1"]["best_loss"] == 0.5038
    assert stages["s2"]["state"] == "complete"
    assert stages["s2"]["best_loss"] == 0.1042


def test_enforce_compute_quota_leaks_across_tenants_cross_tenant_dos(monkeypatch, tmp_path):
    """BLIND SPOT (ADR-0007 tenant isolation, documented in ADR-0094 M2 as
    ``<corvin_home>/tenants/<tid>/global/license/compute_quota.json``):
    enforce_compute_quota() takes a tenant_id argument and threads it into the
    audit chat_key, but the actual counter storage path passed to
    _cq_increment is the GLOBAL install root (_forge_paths.corvin_home()),
    never a tenant-scoped path. This test proves the counter is presently
    SHARED across tenants: exhausting tenant 'acme's free-tier 1/day cap also
    blocks a totally unrelated tenant 'beta' — a cross-tenant quota-exhaustion
    DoS. This is a KNOWN BUG (spec/implementation drift vs. ADR-0094); the
    assertions below pin the CURRENT (buggy) behaviour so a future fix that
    makes the storage path tenant-scoped will make this test fail and must be
    updated together with the fix.
    """
    import sys as _s
    _s.path.insert(0, str(_CONSOLE.parents[1] / "operator"))
    import license.validator as _v

    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    _v._set_active_license(None)  # free tier: compute_units_per_day == 1
    monkeypatch.setattr(G, "_COMPUTE_QUOTA_OK", True)

    # tenant 'acme': first call is under quota (1/day) → must not raise.
    G.enforce_compute_quota("acme", "fpacme000", audit_action="test.compute")

    # tenant 'acme': second call exceeds its own 1/day cap → 402.
    with pytest.raises(HTTPException) as ei_acme:
        G.enforce_compute_quota("acme", "fpacme000", audit_action="test.compute")
    assert ei_acme.value.status_code == 402

    # A completely different tenant, 'beta', has never called compute before.
    # If the quota were correctly tenant-scoped this call would succeed (beta's
    # own daily budget is untouched). Instead the shared global counter file
    # is already at its cap, so 'beta' is ALSO refused — proving the leak.
    with pytest.raises(HTTPException) as ei_beta:
        G.enforce_compute_quota("beta", "fpbeta0000", audit_action="test.compute")
    assert ei_beta.value.status_code == 402

    # Only ONE counter file exists — the tenant-blind global path — confirming
    # there is no per-tenant counter anywhere on disk.
    global_quota = tmp_path / "global" / "license" / "compute_quota.json"
    assert global_quota.exists(), "quota is written to the global (not tenant-scoped) path"
    tenant_quota_acme = tmp_path / "tenants" / "acme" / "global" / "license" / "compute_quota.json"
    tenant_quota_beta = tmp_path / "tenants" / "beta" / "global" / "license" / "compute_quota.json"
    assert not tenant_quota_acme.exists(), "no per-tenant counter file was ever created for acme"
    assert not tenant_quota_beta.exists(), "no per-tenant counter file was ever created for beta"


def test_enforce_chat_turns_leaks_across_tenants_cross_tenant_dos(monkeypatch, tmp_path):
    """Same underlying storage bug as enforce_compute_quota, exercised via
    enforce_chat_turns()'s counter path (chat_quota.json). chat_turns_per_day
    resolves to None (unlimited) on every shipped tier, so this axis never
    actually blocks in production today — but the code path exists and shares
    the identical tenant-blind ``_forge_paths.corvin_home()`` storage call.
    We force a finite limit via monkeypatch to exercise that path directly and
    confirm the SAME cross-tenant leak class applies here too.
    """
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setattr(G, "_COMPUTE_QUOTA_OK", True)
    monkeypatch.setattr(G, "_lic_get_limit", lambda feature: 1 if feature == "chat_turns_per_day" else None)

    import sys as _s
    _s.path.insert(0, str(_CONSOLE.parents[1] / "operator"))
    import license.validator as _v
    _v._set_active_license(None)
    # compute_quota._do_increment_and_check calls `.validator.get_limit` directly
    # (not the G._lic_get_limit re-export), so it must be patched too to force a
    # finite chat_turns_per_day limit for this test — every shipped tier leaves
    # it None (unlimited), which is why chat is not gated in production today.
    monkeypatch.setattr(
        _v, "get_limit",
        lambda feature: 1 if feature == "chat_turns_per_day" else None,
    )

    # tenant 'acme': first chat turn is under the (forced) 1/day cap.
    G.enforce_chat_turns("acme", "fpacme000", audit_action="test.chat")

    # tenant 'acme': second turn exceeds its own cap → 402.
    with pytest.raises(HTTPException):
        G.enforce_chat_turns("acme", "fpacme000", audit_action="test.chat")

    # tenant 'beta' has never chatted, but the shared global chat_quota.json
    # counter is already exhausted → also refused, proving the same leak.
    with pytest.raises(HTTPException):
        G.enforce_chat_turns("beta", "fpbeta0000", audit_action="test.chat")

    global_chat_quota = tmp_path / "global" / "license" / "chat_quota.json"
    assert global_chat_quota.exists(), "chat quota is written to the global (not tenant-scoped) path"


def test_acs_chokepoint_charges_daily_quota():
    """ADR-0149 WF-CLI-ACS-01: run_acs_workflow charges the daily counter at the
    single chokepoint, so the CLI and scheduler paths cannot bypass it."""
    shared = Path("/home/shumway/projects/CorvinOS/operator/bridges/shared")
    src = (shared / "acs_engine_adapter.py").read_text(encoding="utf-8")
    assert "_enforce_acs_compute_quota" in src and "increment_and_check" in src, (
        "run_acs_workflow must charge compute_units_per_day at the ACS chokepoint"
    )
    assert "charge_quota" in src, "run_acs_workflow must expose charge_quota to avoid double-counting"

"""ACS daily-quota fallback at the run_acs_workflow chokepoint (2026-07-20).

Maintainer decision: "no ACS turn available because the day limit is spent"
must ALWAYS degrade to the normal Claude Code delegation (one direct engine
turn, which does its own built-in Task-tool delegation) — never to a hard
failure. The web-chat path already does this (ADR-0150 graceful degradation,
test_acs_quota_fallback.py); this file covers the chokepoint every OTHER ACS
caller funnels through (workflow CLI, scheduler, orchestration MCP, console
ACS route).

Load-bearing invariants verified here:
  * quota_exhausted  → single claude_code run via run_delegate, marked
    quota_fallback=True, manifest persisted in the normal runs index;
  * enforcement_unavailable (license module removed/shadowed) → stays a HARD
    fail-closed deny, the fallback must NOT fire (else deleting the license
    package buys unmetered fallback compute);
  * the fallback path enforces L44 itself, fail-closed — it bypasses
    ACSRuntime.run, where the gate normally lives;
  * a workflow without any goal/description text cannot fall back (nothing to
    hand a single engine turn) and surfaces the original quota stop.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_SHARED = Path(__file__).resolve().parent
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

import acs_engine_adapter as _adapter  # noqa: E402


def _quota_block() -> dict:
    return {
        "run_id": "r1", "status": "failed", "reason": "quota_exhausted",
        "error": "compute_units_per_day exceeded: limit 1/day",
        "engine": "acs", "duration_s": 0.0,
    }


def _failclosed_block() -> dict:
    return {
        "run_id": "r1", "status": "failed",
        "error": "compute quota enforcement unavailable (fail-closed)",
        "engine": "acs", "duration_s": 0.0,
        "reason": "enforcement_unavailable",
    }


def _spec(description: str = "review the auth module and fix the bugs",
          budget: dict | None = None) -> dict:
    return {
        "awp": "1.0.0",
        "workflow": {"name": "wf-test", "description": description,
                     "version": "1.0.0"},
        "orchestration": {"engine": "delegation_loop",
                          "delegation_loop": {"budget": dict(budget or {})}},
        "state": {"initial": {}},
    }


@pytest.fixture
def fake_delegate():
    """Inject a fake corvin_delegate.delegation.run_delegate; records calls."""
    calls: list[dict] = []

    def _run_delegate(**kw):
        calls.append(kw)
        return SimpleNamespace(ok=True, final_text="fallback answer",
                               error=None, duration_ms=5)

    pkg = types.ModuleType("corvin_delegate")
    mod = types.ModuleType("corvin_delegate.delegation")
    mod.run_delegate = _run_delegate  # type: ignore[attr-defined]
    # The fallback imports BUDGET_FALLBACK_MAX_S from this module (review F7).
    mod.BUDGET_FALLBACK_MAX_S = 86400  # type: ignore[attr-defined]
    pkg.delegation = mod  # type: ignore[attr-defined]
    saved = {k: sys.modules.get(k) for k in ("corvin_delegate",
                                             "corvin_delegate.delegation")}
    sys.modules["corvin_delegate"] = pkg
    sys.modules["corvin_delegate.delegation"] = mod
    try:
        yield calls
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


@pytest.fixture
def fake_l44_allow():
    with _fake_l44(lambda *a, **kw: None) as m:
        yield m


class _fake_l44:
    """Context manager injecting a fake spawn_gates.check_l44."""

    def __init__(self, fn):
        self._fn = fn

    def __enter__(self):
        self._saved = sys.modules.get("spawn_gates")
        mod = types.ModuleType("spawn_gates")
        mod.check_l44 = self._fn  # type: ignore[attr-defined]
        sys.modules["spawn_gates"] = mod
        return mod

    def __exit__(self, *exc):
        if self._saved is None:
            sys.modules.pop("spawn_gates", None)
        else:
            sys.modules["spawn_gates"] = self._saved
        return False


def test_quota_exhausted_falls_back_to_single_claude_code_turn(
        tmp_path, fake_delegate, fake_l44_allow):
    with (
        patch.object(_adapter, "_enforce_acs_compute_quota",
                     return_value=_quota_block()),
        patch.object(_adapter, "_acs_runs_dir", return_value=tmp_path),
    ):
        out = _adapter.run_acs_workflow(_spec(), tenant_id="_default",
                                        run_id="acs-fb-t1")

    assert out["quota_fallback"] is True
    assert out["status"] == "success"
    assert out["engine"] == "claude_code"
    assert out["final_output"] == "fallback answer"
    assert out["workers_spawned"] == 0
    # exactly one direct engine turn, carrying the workflow goal
    assert len(fake_delegate) == 1
    call = fake_delegate[0]
    assert call["engine"] == "claude_code"
    assert "review the auth module" in call["prompt"]
    assert call["allow_write"] is True
    # manifest persisted in the normal runs index (console list/get sees it)
    manifest_dir = tmp_path / "acs-fb-t1"
    assert (manifest_dir / "manifest.json").exists()
    assert (manifest_dir / "result.json").exists()


def test_run_id_is_sanitized_before_path_join(
        tmp_path, fake_delegate, fake_l44_allow):
    """Adversarial review F3: a traversal-shaped run_id must not escape the
    tenant runs dir. The sanitized id addresses a child of tmp_path, and the
    directory run_delegate writes into stays under it."""
    with (
        patch.object(_adapter, "_enforce_acs_compute_quota",
                     return_value=_quota_block()),
        patch.object(_adapter, "_acs_runs_dir", return_value=tmp_path),
    ):
        out = _adapter.run_acs_workflow(
            _spec(), tenant_id="_default", run_id="../../etc/evil")

    assert out["quota_fallback"] is True
    # run_delegate's working_dir must resolve UNDER the runs dir, never outside.
    wd = Path(fake_delegate[0]["working_dir"]).resolve()
    assert str(wd).startswith(str(tmp_path.resolve())), (
        f"working_dir {wd} escaped the runs dir {tmp_path}")
    # No sibling of tmp_path named after the traversal was created.
    assert not (tmp_path.parent / "etc").exists()


def test_enforcement_unavailable_stays_hard_fail_closed(
        tmp_path, fake_delegate, fake_l44_allow):
    with (
        patch.object(_adapter, "_enforce_acs_compute_quota",
                     return_value=_failclosed_block()),
        patch.object(_adapter, "_acs_runs_dir", return_value=tmp_path),
    ):
        out = _adapter.run_acs_workflow(_spec(), tenant_id="_default")

    assert out.get("quota_fallback") is not True
    assert out["status"] == "failed"
    assert "fail-closed" in out["error"]
    assert not fake_delegate, (
        "a removed/shadowed license module must NOT buy unmetered fallback "
        "compute — the fallback fired on enforcement_unavailable"
    )


def test_fallback_enforces_l44_fail_closed_on_refusal(tmp_path, fake_delegate):
    refusal = "[house-rules] Refused: acceptable-use policy."
    with (
        _fake_l44(lambda *a, **kw: refusal),
        patch.object(_adapter, "_enforce_acs_compute_quota",
                     return_value=_quota_block()),
        patch.object(_adapter, "_acs_runs_dir", return_value=tmp_path),
    ):
        out = _adapter.run_acs_workflow(_spec(), tenant_id="_default")

    assert out["status"] == "failed"
    assert out["error"] == refusal
    assert not fake_delegate, "L44 refusal must block the fallback spawn"


def test_fallback_denies_when_l44_gate_unavailable(tmp_path, fake_delegate):
    # spawn_gates importable but WITHOUT check_l44 → ImportError inside the
    # fallback → fail-closed deny (mandatory layer absent must never silently
    # lose the acceptable-use guarantee).
    broken = types.ModuleType("spawn_gates")
    saved = sys.modules.get("spawn_gates")
    sys.modules["spawn_gates"] = broken
    try:
        with (
            patch.object(_adapter, "_enforce_acs_compute_quota",
                         return_value=_quota_block()),
            patch.object(_adapter, "_acs_runs_dir", return_value=tmp_path),
        ):
            out = _adapter.run_acs_workflow(_spec(), tenant_id="_default")
    finally:
        if saved is None:
            sys.modules.pop("spawn_gates", None)
        else:
            sys.modules["spawn_gates"] = saved

    assert out["status"] == "failed"
    assert "house-rules" in out["error"]
    assert not fake_delegate


def test_fallback_skipped_when_workflow_has_no_goal_text(
        tmp_path, fake_delegate, fake_l44_allow):
    spec = {"awp": "1.0.0", "workflow": {},
            "orchestration": {"engine": "delegation_loop",
                              "delegation_loop": {"budget": {}}},
            "state": {"initial": {}}}
    with (
        patch.object(_adapter, "_enforce_acs_compute_quota",
                     return_value=_quota_block()),
        patch.object(_adapter, "_acs_runs_dir", return_value=tmp_path),
    ):
        out = _adapter.run_acs_workflow(spec, tenant_id="_default")

    assert out["status"] == "failed"
    assert "compute_units_per_day exceeded" in out["error"]
    assert "quota fallback skipped" in out["error"]
    assert not fake_delegate


def test_fallback_honours_spec_wall_time_budget(
        tmp_path, fake_delegate, fake_l44_allow):
    with (
        patch.object(_adapter, "_enforce_acs_compute_quota",
                     return_value=_quota_block()),
        patch.object(_adapter, "_acs_runs_dir", return_value=tmp_path),
    ):
        _adapter.run_acs_workflow(_spec(budget={"max_wall_time": 7200}),
                                  tenant_id="_default")

    assert fake_delegate[0]["budget_s"] == 7200
    # F7: the fallback passes the elevated ceiling so a legitimate long goal
    # is not clamped to the 600 s interactive cap.
    from corvin_delegate.delegation import BUDGET_FALLBACK_MAX_S
    assert fake_delegate[0]["budget_ceiling_s"] == BUDGET_FALLBACK_MAX_S


def test_fallback_honours_narrower_budget_override(
        tmp_path, fake_delegate, fake_l44_allow):
    """F8: an explicit budget_override.max_wall_time must not be dropped — the
    narrower of spec and override wins."""
    with (
        patch.object(_adapter, "_enforce_acs_compute_quota",
                     return_value=_quota_block()),
        patch.object(_adapter, "_acs_runs_dir", return_value=tmp_path),
    ):
        _adapter.run_acs_quota_fallback(
            _spec(budget={"max_wall_time": 7200}), tenant_id="_default",
            budget_override={"max_wall_time": 300})

    assert fake_delegate[0]["budget_s"] == 300


def test_chokepoint_threads_budget_override_into_fallback(
        tmp_path, fake_delegate, fake_l44_allow):
    """D1: the run_acs_workflow chokepoint (workflow CLI, scheduler,
    orchestration MCP) must not DROP budget_override when it degrades to the
    quota fallback — F8 fixed only the console route's direct call. A caller
    who bounded the run to 300 s must not get a BUDGET_FALLBACK-length turn."""
    with (
        patch.object(_adapter, "_enforce_acs_compute_quota",
                     return_value=_quota_block()),
        patch.object(_adapter, "_acs_runs_dir", return_value=tmp_path),
    ):
        out = _adapter.run_acs_workflow(
            _spec(), tenant_id="_default",
            budget_override={"max_wall_time": 300})

    assert out["quota_fallback"] is True
    assert fake_delegate[0]["budget_s"] == 300


def test_fallback_is_bounded_per_day(tmp_path, fake_delegate, fake_l44_allow):
    """F6/Sec-F1: after _FALLBACK_MAX_PER_DAY runs the fallback stops degrading
    (bounds the scriptable un-metered surface) rather than looping unbounded."""
    cap = _adapter._FALLBACK_MAX_PER_DAY
    with (
        patch.object(_adapter, "_enforce_acs_compute_quota",
                     return_value=_quota_block()),
        patch.object(_adapter, "_acs_runs_dir", return_value=tmp_path),
    ):
        for i in range(cap):
            out = _adapter.run_acs_quota_fallback(_spec(), tenant_id="_default",
                                                  run_id=f"r{i}")
            assert out["quota_fallback"] is True, f"run {i} should still degrade"
        # the (cap+1)-th is refused, no further delegate spawn
        n_before = len(fake_delegate)
        out = _adapter.run_acs_quota_fallback(_spec(), tenant_id="_default",
                                              run_id="over")
        assert out["status"] == "failed"
        assert "fallback limit reached" in out["error"]
        assert len(fake_delegate) == n_before, "no spawn past the daily cap"


def test_fallback_daily_counter_is_race_safe(tmp_path):
    """D3: _fallback_quota_ok was an unlocked read-modify-write — N parallel
    submissions could each read the same count and overshoot the daily cap
    arbitrarily. The counter must serialize (LIC-1 flock pattern from
    operator/license/compute_quota.py) so concurrent callers never exceed
    _FALLBACK_MAX_PER_DAY. The patched slow write widens the read→write
    window; with a correct lock the write happens INSIDE the critical
    section, so the cap still holds."""
    import threading
    import time as _time

    if os.name != "nt":
        pytest.importorskip("fcntl")

    cap = _adapter._FALLBACK_MAX_PER_DAY
    real_write = _adapter._write_json_atomic

    def _slow_write(path, obj):
        _time.sleep(0.002)
        return real_write(path, obj)

    n_threads = cap + 30
    allowed: list[int] = []
    guard = threading.Lock()
    barrier = threading.Barrier(n_threads)

    def _worker():
        barrier.wait()
        ok, _count = _adapter._fallback_quota_ok("_default")
        if ok:
            with guard:
                allowed.append(1)

    with (
        # runs dir one level DOWN so the day counter (written to
        # runs_dir.parent) lands inside this test's own tmp_path — the bare
        # tmp_path would place it in the pytest basetemp shared by every test
        # of the run (cross-test pollution).
        patch.object(_adapter, "_acs_runs_dir",
                     return_value=tmp_path / "runs"),
        patch.object(_adapter, "_write_json_atomic", side_effect=_slow_write),
    ):
        threads = [threading.Thread(target=_worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert len(allowed) <= cap, (
        f"{len(allowed)} concurrent fallbacks allowed — daily cap is {cap}; "
        "the counter's read-modify-write is not atomic")


def test_fallback_run_ids_do_not_collide_within_one_second(
        tmp_path, fake_delegate, fake_l44_allow):
    """D8: the generated fallback run_id had second resolution
    (`acs-fb-<int(t0)>`) — two fallbacks starting in the same second shared
    one run_dir and silently overwrote each other's manifest/result. The id
    must be unique even at identical timestamps."""
    with (
        patch.object(_adapter, "_enforce_acs_compute_quota",
                     return_value=_quota_block()),
        # runs dir one level DOWN: keeps the day counter (runs_dir.parent)
        # inside this test's tmp_path instead of the shared pytest basetemp.
        patch.object(_adapter, "_acs_runs_dir",
                     return_value=tmp_path / "runs"),
        patch.object(_adapter.time, "time", return_value=1234567890.5),
    ):
        out1 = _adapter.run_acs_quota_fallback(_spec(), tenant_id="_default")
        out2 = _adapter.run_acs_quota_fallback(_spec(), tenant_id="_default")

    assert out1["status"] == "success" and out2["status"] == "success"
    assert out1["run_id"] != out2["run_id"], (
        "two same-second fallbacks must not share a run_id/run_dir")
    assert (tmp_path / "runs" / out1["run_id"] / "manifest.json").exists()
    assert (tmp_path / "runs" / out2["run_id"] / "manifest.json").exists()


def test_dry_run_never_charges_or_falls_back(tmp_path, fake_delegate):
    with (
        patch.object(_adapter, "_enforce_acs_compute_quota",
                     return_value=_quota_block()) as enforce,
        patch.object(_adapter, "_acs_runs_dir", return_value=tmp_path),
    ):
        _adapter.run_acs_workflow(_spec(), tenant_id="_default", dry_run=True)

    enforce.assert_not_called()
    assert not fake_delegate

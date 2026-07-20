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


def test_dry_run_never_charges_or_falls_back(tmp_path, fake_delegate):
    with (
        patch.object(_adapter, "_enforce_acs_compute_quota",
                     return_value=_quota_block()) as enforce,
        patch.object(_adapter, "_acs_runs_dir", return_value=tmp_path),
    ):
        _adapter.run_acs_workflow(_spec(), tenant_id="_default", dry_run=True)

    enforce.assert_not_called()
    assert not fake_delegate

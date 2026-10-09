"""ADR-0149 WF-CLI-ACS-01: the ACS chokepoint charges compute_units_per_day so
the CLI/scheduler paths cannot bypass the daily quota the console route enforces.
"""
import os
import sys
import tempfile
from pathlib import Path

_SHARED = Path(__file__).resolve().parent
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))
_OPERATOR = _SHARED.parents[1]
if str(_OPERATOR) not in sys.path:
    sys.path.insert(0, str(_OPERATOR))

import acs_engine_adapter as A


def test_acs_chokepoint_blocks_second_free_tier_run(monkeypatch):
    # Free tier (no active license) allows FREE_TIER["compute_units_per_day"] (10 since the
    # maintainer decision of 2026-07-24) — one shared pool, so the next run is refused.
    import license.validator as _v
    from license.limits import FREE_TIER
    _v._set_active_license(None)
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("CORVIN_HOME", td)
        limit = int(FREE_TIER["compute_units_per_day"])
        for n in range(limit):
            ok = A._enforce_acs_compute_quota("_default", f"run-{n}")
            assert ok is None, f"ACS run {n + 1}/{limit} must pass the daily quota, got {ok!r}"
        # One over the pool, same day: fail-closed failed dict.
        over = A._enforce_acs_compute_quota("_default", "run-over")
        assert over is not None, "the run past compute_units_per_day must be blocked"
        assert over.get("status") == "failed"
        assert "compute_units_per_day" in over.get("error", "")


def test_acs_chokepoint_dry_run_not_charged():
    # run_acs_workflow exempts dry_run (no workers spawned) — charge_quota path
    # is guarded by `not dry_run`; a dry run must never consume quota.
    src = (Path(__file__).resolve().parent / "acs_engine_adapter.py").read_text(encoding="utf-8")
    assert "if charge_quota and not dry_run:" in src


def test_acs_quota_uses_forge_paths_when_corvin_home_unset(monkeypatch, tmp_path):
    """LIC-ACS-HOME-01: when CORVIN_HOME is unset, the quota counter must resolve
    via forge.paths.corvin_home() (repo-root .corvin) and NOT fall back to the
    user's ~/.corvin — otherwise pinned-deployment and dev-run counters diverge."""
    import license.validator as _v
    _v._set_active_license(None)
    # Remove CORVIN_HOME so the fallback path triggers.
    monkeypatch.delenv("CORVIN_HOME", raising=False)
    # Patch forge.paths.corvin_home to return a controlled tmpdir.
    import importlib, forge.paths as _fp
    monkeypatch.setattr(_fp, "corvin_home", lambda: tmp_path)
    from license.limits import FREE_TIER
    limit = int(FREE_TIER["compute_units_per_day"])
    # Runs up to the pool must pass — the counter lands in tmp_path, not ~/.corvin.
    for n in range(limit):
        result = A._enforce_acs_compute_quota("_default", f"lic-acs-home-01-{n}")
        assert result is None, f"run {n + 1}/{limit} must proceed, got {result!r}"
    # The next run same day must be blocked by the counter written in tmp_path.
    result2 = A._enforce_acs_compute_quota("_default", "lic-acs-home-01-over")
    assert result2 is not None, "the run past the pool must be blocked; counter in tmp_path"
    assert result2.get("status") == "failed"

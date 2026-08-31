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
    # Free tier (no active license) allows compute_units_per_day=1.
    import license.validator as _v
    _v._set_active_license(None)
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("CORVIN_HOME", td)
        # First run: under quota -> proceeds (None).
        first = A._enforce_acs_compute_quota("_default", "run-1")
        assert first is None, f"first ACS run must pass the daily quota, got {first!r}"
        # Second run same day: over quota -> fail-closed failed dict.
        second = A._enforce_acs_compute_quota("_default", "run-2")
        assert second is not None, "second ACS run must be blocked by compute_units_per_day"
        assert second.get("status") == "failed"
        assert "compute_units_per_day" in second.get("error", "")


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
    # First run must pass (None) — counter lands in tmp_path, not ~/.corvin.
    result = A._enforce_acs_compute_quota("_default", "lic-acs-home-01")
    assert result is None, f"first run must proceed, got {result!r}"
    # Second run same day must be blocked by the counter written in tmp_path.
    result2 = A._enforce_acs_compute_quota("_default", "lic-acs-home-01-b")
    assert result2 is not None, "second run must be blocked; counter in tmp_path"
    assert result2.get("status") == "failed"

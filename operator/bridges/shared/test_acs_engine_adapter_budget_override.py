"""Regression: a non-numeric budget_override field must not crash
run_acs_workflow AFTER the real run has already executed (adversarial
review finding).

acs_engine_adapter.py::run_acs_workflow re-parses `budget_override` a
SECOND time (purely to populate display fields in the run manifest) after
`asyncio.run(rt.run(...))` has already completed — real workers spawned,
quota charged, audit events written. That second parse used a bare
`int(v)` with no exception handling: a non-numeric value for one of the
int-typed fields (max_loops, max_workers_per_iteration, max_wall_time,
max_total_workers, max_rejected_completions, max_depth) raised uncaught,
propagating out of run_acs_workflow entirely — so the manifest/result.json
for the run that ALREADY HAPPENED never got persisted, leaving a dangling
audit trail with no corresponding run-list entry.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

_SHARED = Path(__file__).resolve().parent
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

import acs_engine_adapter as _adapter  # noqa: E402
import acs_runtime as _rt  # noqa: E402


def _fake_result(run_id: str) -> _rt.ACSResult:
    return _rt.ACSResult(
        run_id=run_id, workflow_id="wf-1", status="success",
        summary="done", iterations=1, workers_spawned=2,
    )


def _minimal_spec() -> dict:
    return {
        "awp": "1.0.0",
        "workflow": {"name": "test", "description": "d", "version": "1.0.0"},
        "orchestration": {"engine": "delegation_loop", "delegation_loop": {"budget": {}}},
        "state": {"initial": {}},
    }


class _FakeACSRuntime:
    def __init__(self, tenant_id: str = "_default") -> None:
        self.tenant_id = tenant_id

    async def run(self, spec, inputs=None, dry_run=False, run_id=None, budget_override=None):
        return _fake_result(run_id or "run-fake-1")


def test_non_numeric_budget_override_field_does_not_crash_after_real_run(tmp_path):
    with (
        patch.object(_adapter, "_enforce_acs_compute_quota", return_value=None),
        patch.object(_adapter, "_acs_runs_dir", return_value=tmp_path),
        patch("acs_runtime.ACSRuntime", _FakeACSRuntime),
    ):
        out = _adapter.run_acs_workflow(
            _minimal_spec(),
            dry_run=False,
            run_id="run-fake-1",
            budget_override={"max_loops": "not-a-number"},
            charge_quota=False,
        )
    # Must complete successfully — reflecting that the real run (mocked
    # above) genuinely succeeded — not crash on the display-only re-parse.
    assert out["status"] == "success"
    assert out["run_id"] == "run-fake-1"
    # The manifest for the run that already happened must actually be
    # written, not silently dropped because of the crash this regresses.
    manifest_path = tmp_path / "run-fake-1" / "manifest.json"
    assert manifest_path.exists(), "manifest.json must be persisted even with a malformed override field"


def test_valid_numeric_budget_override_still_populates_manifest_fields(tmp_path):
    with (
        patch.object(_adapter, "_enforce_acs_compute_quota", return_value=None),
        patch.object(_adapter, "_acs_runs_dir", return_value=tmp_path),
        patch("acs_runtime.ACSRuntime", _FakeACSRuntime),
    ):
        out = _adapter.run_acs_workflow(
            _minimal_spec(),
            dry_run=False,
            run_id="run-fake-2",
            budget_override={"max_loops": 42},
            charge_quota=False,
        )
    assert out["status"] == "success"
    import json
    manifest = json.loads((tmp_path / "run-fake-2" / "manifest.json").read_text())
    assert manifest["max_loops"] == 42


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

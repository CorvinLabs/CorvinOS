"""TDE joins the shared daily agentic-compute pool (compute_units_per_day).

Maintainer decision 2026-07-24: the free tier gets 10 agentic turns/day
SUMMED across all compute engines — ACS workflows, TDE runs and compute
(grid-search) runs all charge the SAME counter file
(<corvin_home>/global/license/compute_quota.json). TDE was the only
unmetered engine; these tests pin its chokepoint gate in
TieredDelegationEngine.execute().
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
for _p in (_REPO / "operator" / "orchestration",
           _REPO / "operator"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from tde.tde_engine import TieredDelegationEngine  # noqa: E402
from tde.worker_ipc import MockWorkerIPC  # noqa: E402
from initial_analysis import (  # noqa: E402
    Classification,
    Entities,
    GlobalPlan,
    InitialAnalysisRequest,
    Step,
)


def _analysis() -> InitialAnalysisRequest:
    return InitialAnalysisRequest(
        classification=Classification("analysis", "simple", "claude", 0.9),
        entities=Entities(),
        global_plan=GlobalPlan(
            steps=[Step(step=1, action="analyze_data")],
            estimated_duration_s=10,
            estimated_tokens=100,
        ),
    )


@pytest.fixture()
def quota_env(monkeypatch, tmp_path):
    """Isolated corvin_home + free tier, license loading neutralised."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    import license.validator as _v  # noqa: PLC0415
    monkeypatch.setattr(_v, "load_license_from_env", lambda *a, **k: None)
    _v._set_active_license(None)  # free tier
    yield tmp_path
    _v._set_active_license(None)


def _counter_file(home: Path) -> Path:
    return home / "global" / "license" / "compute_quota.json"


def _today() -> str:
    from datetime import datetime, timezone  # noqa: PLC0415
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class ExplodingIPC:
    """Any use proves the gate ran too late."""

    def __getattr__(self, name):  # pragma: no cover - not reached
        raise AssertionError("worker must not run when quota is exhausted")


def test_free_tier_agentic_pool_is_10(quota_env):
    """The shared agentic pool is 10/day on the free tier (was 1)."""
    import license.validator as _v  # noqa: PLC0415
    assert _v.get_limit("compute_units_per_day") == 10


def test_tde_denies_when_pool_exhausted(quota_env):
    """11th agentic turn of the day is refused before any worker spawns."""
    cf = _counter_file(quota_env)
    cf.parent.mkdir(parents=True, exist_ok=True)
    cf.write_text(json.dumps({_today(): 10}), encoding="utf-8")

    engine = TieredDelegationEngine(real_ipc=True)
    result = asyncio.run(engine.execute(
        _analysis(), {"statement": {}}, worker_ipc=ExplodingIPC(),
    ))
    assert result["success"] is False
    assert result.get("reason") == "quota_exhausted"
    # Counter must NOT have been raised past the cap by the denied attempt.
    data = json.loads(cf.read_text(encoding="utf-8"))
    assert data[_today()] == 10


def test_tde_charges_one_unit_per_run(quota_env):
    """A metered TDE run (real_ipc=True) charges exactly one pool unit."""
    async def exec_fn(step, statement, **kw):
        return f"local-{step.step}"

    engine = TieredDelegationEngine(real_ipc=True, local_step_executor=exec_fn)
    result = asyncio.run(engine.execute(
        _analysis(), {"statement": {}}, worker_ipc=MockWorkerIPC(),
        use_semantic_judge=False,
    ))
    assert result["success"] is True
    data = json.loads(_counter_file(quota_env).read_text(encoding="utf-8"))
    assert data[_today()] == 1


def test_tde_unmetered_with_injected_stub_executor(quota_env):
    """Stub executor + fake IPC (unit-test config) consumes no real compute
    and therefore charges nothing."""
    async def exec_fn(step, statement, **kw):
        return f"local-{step.step}"

    engine = TieredDelegationEngine(local_step_executor=exec_fn)
    result = asyncio.run(engine.execute(
        _analysis(), {"statement": {}}, worker_ipc=MockWorkerIPC(),
    ))
    assert result["success"] is True
    assert not _counter_file(quota_env).exists()


def test_invalid_plan_rejected_before_charge(quota_env):
    """Malformed input is rejected without consuming a pool unit."""
    engine = TieredDelegationEngine(real_ipc=True)
    result = asyncio.run(engine.execute({}, {}))
    assert result["success"] is False
    assert not _counter_file(quota_env).exists()

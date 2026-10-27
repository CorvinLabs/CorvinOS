"""ADR-0214 LIVE E2E — real LM calls (claude CLI). Skipped when unavailable.

Covers, with REAL data and REAL LLM invocations:
1. Task classification (ADR-0210 InitialAnalysis one-shot) on a fictional
   coding task — asserts a plausible classification + executable plan.
2. TDE delegation: /use-engine override → SubprocessWorkerIPC → each
   delegated step is a separate LLM invocation; exploration forces a
   measured (semantic-judge) loss entry.
3. Audit: tde.* events land on a sandboxed hash chain and the chain verifies.

Run explicitly:  pytest tests/test_tde_e2e_live.py -m live -q
"""
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "corvin_operator" / "orchestration"))
sys.path.insert(0, str(Path(__file__).parent.parent / "corvin_operator" / "bridges" / "shared"))

# Opt-in (repo convention, see test_adr0213_context_sync_live.py):
# CLAUDE_LIVE_E2E=1 enables; additionally the claude CLI must exist.
# Keeps the default CI run fast and credential-free.
live = pytest.mark.skipif(
    os.environ.get("CLAUDE_LIVE_E2E", "") != "1"
    or (
        shutil.which("claude") is None
        and not os.path.isfile(os.path.expanduser("~/.local/bin/claude"))
    ),
    reason="live TDE E2E needs CLAUDE_LIVE_E2E=1 and the claude CLI",
)

pytestmark = [live, pytest.mark.live]

TASK = (
    "Review these two Python functions for bugs, then produce a short "
    "combined review report. They are independent — analyze them "
    "separately, then synthesize."
)
STATEMENT = {
    "function_a": "def add_item(items, item):\n    items = items + [item]\n    return item\n",
    "function_b": "def find_max(nums):\n    m = 0\n    for n in nums:\n        if n > m:\n            m = n\n    return m\n",
}


@pytest.fixture(autouse=True)
def _temp_cwd(tmp_path, monkeypatch):
    """Real `claude -p` workers write relative to cwd — never the repo."""
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)


@pytest.fixture()
def audit_sandbox(tmp_path, monkeypatch):
    """The sandboxed hash chain this test's tde.* events land on.

    Yields the chain FILE. It used to set FORGE_ROOT and read
    ``tmp_path/audit.jsonl`` — but the repo conftest's autouse
    VOICE_AUDIT_PATH (under the per-test CORVIN_HOME) takes precedence over
    FORGE_ROOT in the writer, so the events were written elsewhere and the
    test failed "tde.* events were not persisted" on a working chain.
    """
    import os as _os

    chain = Path(_os.environ["VOICE_AUDIT_PATH"])
    assert str(chain).startswith(str(tmp_path)), chain  # never the live chain
    from tde import tde_audit
    tde_audit.reset_for_tests()
    yield chain
    tde_audit.reset_for_tests()


def test_live_initial_analysis_classification():
    """REAL LM call: fictional coding task → classification + plan."""
    from tde.analysis_runner import run_initial_analysis_sync

    analysis = run_initial_analysis_sync(
        "Write Python code for a small inventory service: a dataclass model, "
        "an in-memory CRUD repository, and unit tests. Keep parts independent.",
        {"statement": {"language": "python"}},
        timeout_s=240,
    )
    assert analysis.classification.task_type in (
        "code_generation", "transformation", "tool_call", "reasoning",
        "data_analysis", "retrieval", "delegation",
    )
    assert analysis.classification.complexity in ("simple", "moderate", "complex")
    assert 0.0 <= analysis.classification.confidence <= 1.0
    assert len(analysis.global_plan.steps) >= 1
    # F32 regression: steps must carry an executable description
    assert any(s.description for s in analysis.global_plan.steps)


def test_live_tde_delegation_with_audit(audit_sandbox):
    """REAL delegation: override → TDE → SubprocessWorkerIPC → audit chain."""
    from tde.analysis_runner import run_initial_analysis_sync
    from tde.engine_registry import EngineRegistry
    from tde.send_integration import SendIntegration

    context = {"statement": dict(STATEMENT), "task_text": TASK}
    analysis = run_initial_analysis_sync(TASK, context, timeout_s=240)

    integration = SendIntegration(registry=EngineRegistry(real_ipc=True))
    engine_name, result = asyncio.run(
        integration.select_engine_and_execute(
            "/use-engine tiered_delegation\n" + TASK, context, analysis,
        )
    )

    assert engine_name == "tiered_delegation"
    assert result["engine_selection"]["override"] == "tiered_delegation"
    assert result["success"] is True
    results = result["results"]
    assert len(results) == len(analysis.global_plan.steps)
    # At least the side-effect-free steps must have really delegated
    assert any(r.was_delegated for r in results)
    for r in results:
        assert r.success, f"step {r.step_num} failed: {r.error}"
        assert r.output

    # Audit: tde.* events on a verifying hash chain
    audit_file = audit_sandbox
    assert audit_file.exists(), "tde.* events were not persisted"
    events = [json.loads(l) for l in audit_file.read_text().splitlines() if l.strip()]
    etypes = [e.get("event_type", "") for e in events]
    assert "tde.engine_selected" in etypes
    assert "tde.delegation_decision" in etypes
    assert "tde.step_delegated" in etypes
    assert "tde.plan_executed" in etypes

    # CONTENT-FREE check: no statement content leaks into audit details
    blob = json.dumps(events)
    assert "add_item" not in blob
    assert "find_max" not in blob

    sys.path.insert(0, str(Path(__file__).parent.parent / "corvin_operator" / "forge"))
    from forge import security_events
    ok, errors = security_events.verify_chain(audit_file)
    assert ok, f"audit chain broken: {errors}"


def test_live_l34_prescan_forces_local():
    """Secrets in context → L34 prescan forces claude_code even with override."""
    from tde.engine_registry import EngineRegistry
    from tde.send_integration import SendIntegration
    from initial_analysis import (
        Classification, Entities, GlobalPlan, InitialAnalysisRequest, Step,
    )

    class _Recorder:
        name = "any"
        def __init__(self, name):
            self.name = name
        async def execute(self, plan, context, **kwargs):
            return {"engine": self.name, "success": True, "results": []}

    registry = EngineRegistry.__new__(EngineRegistry)
    registry.engines = {n: _Recorder(n) for n in ("tiered_delegation", "acs", "claude_code")}

    analysis = InitialAnalysisRequest(
        classification=Classification("code_generation", "moderate", "claude", 0.9),
        entities=Entities(),
        global_plan=GlobalPlan(
            steps=[Step(step=1, action="generate_code", description="x")],
            estimated_duration_s=5, estimated_tokens=2000,
        ),
    )
    integration = SendIntegration(registry=registry)
    engine_name, result = asyncio.run(
        integration.select_engine_and_execute(
            "/use-engine tiered_delegation\nGenerate code",
            {"api_key": "sk_live_abcdef123456"},
            analysis,
        )
    )
    assert engine_name == "claude_code"
    assert result["engine_selection"]["l34_forced"] is True


def test_live_loss_profile_carries_tokens(audit_sandbox):
    """T-0319 / ADR-2306 decision 3, REAL calls: real delegated workers and a real shadow run
    (no reference model configured, so the shadow re-runs the worker model) must leave
    MEASURED loss entries that carry the usage of BOTH sides in four components.

    Fictional, synthetic steps only. The semantic judge is switched off (lexical fallback) so
    the only LLM calls are the worker and the shadow of each step."""
    from initial_analysis import GlobalPlan, Step
    from tde.loss_profile_tracker import USAGE_KEYS, get_session_tracker
    from tde.tde_engine import TieredDelegationEngine

    plan = GlobalPlan(
        steps=[
            Step(step=1, action="analyze_data",
                 description="State in one sentence whether `def add_item(items, item): items = items + [item]; return item` returns the list."),
            Step(step=2, action="analyze_data",
                 description="State in one sentence what `find_max([-3, -1])` returns for `m = 0; for n in nums: if n > m: m = n; return m`."),
            Step(step=3, action="synthesize", depends_on=[1, 2],
                 description="Combine the two findings into one two-sentence review."),
        ],
        estimated_duration_s=60, estimated_tokens=6000,
    )
    key = "t0319-live"
    engine = TieredDelegationEngine(real_ipc=True)
    result = asyncio.run(engine.execute(
        plan, {"statement": dict(STATEMENT)},
        session_key=key, use_semantic_judge=False, budget_tokens=200_000))
    assert result["success"] is True, result.get("error")

    tracker = get_session_tracker(session_key=key)
    measured = [e for e in tracker.history if e.measured]
    assert measured, "forced exploration must have produced at least one measured entry"
    for e in measured:
        for side in ("usage_delegated", "usage_local"):
            u = getattr(e, side)
            assert u, f"{e.task_type}: {side} missing, a real call must report usage"
            assert all(isinstance(u[k], int) and u[k] >= 0 for k in USAGE_KEYS)
            assert u["total_tokens"] > 0 and u["input_tokens"] + u["output_tokens"] + u["cache_read_input_tokens"] + u["cache_creation_input_tokens"] == u["total_tokens"]
            assert isinstance(u["model"], str) and u["model"].startswith("claude-")
        assert e.tokens_delegated == e.usage_delegated["total_tokens"]
        assert e.tokens_local == e.usage_local["total_tokens"]
        print(f"LIVE {e.task_type}: loss={e.loss_pct:.1f}% delegated={e.usage_delegated} local={e.usage_local}")


def test_live_planner_reports_usage():
    """T-0319 / ADR-2306, REAL call: the InitialAnalysis (planner) call now runs with --output-format json and
    reports its measured tokens, so a TDE cost figure can include them. One Haiku call on a fictional task."""
    from tde.analysis_runner import run_initial_analysis_sync
    from tde.loss_profile_tracker import USAGE_KEYS

    context = {"statement": dict(STATEMENT), "task_text": TASK}
    analysis = run_initial_analysis_sync(TASK, context, timeout_s=240)
    assert analysis.global_plan.steps, "the planner must still produce a plan from the envelope's result text"
    u = analysis.planner_usage
    assert u, "a real planner call must report usage"
    assert all(isinstance(u[k], int) and u[k] >= 0 for k in USAGE_KEYS)
    assert u["total_tokens"] > 0 and isinstance(u["model"], str) and u["model"].startswith("claude-")
    print(f"LIVE planner usage: {u} steps={len(analysis.global_plan.steps)}")

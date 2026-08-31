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

sys.path.insert(0, str(Path(__file__).parent.parent / "operator" / "orchestration"))
sys.path.insert(0, str(Path(__file__).parent.parent / "operator" / "bridges" / "shared"))

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


@pytest.fixture()
def audit_sandbox(tmp_path, monkeypatch):
    """Route the hash chain to a sandbox and reset the TDE audit resolver."""
    monkeypatch.setenv("FORGE_ROOT", str(tmp_path))
    from tde import tde_audit
    tde_audit.reset_for_tests()
    yield tmp_path
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
    audit_file = audit_sandbox / "audit.jsonl"
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

    sys.path.insert(0, str(Path(__file__).parent.parent / "operator" / "forge"))
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

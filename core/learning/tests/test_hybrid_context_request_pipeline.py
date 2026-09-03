"""L-11: HybridContextRequestPipeline — no dummy Tier 1, per-request Tier 2,
real core-chain audit, per-tenant registry.

Runs against the REAL core writer redirected to a temp chain
(``VOICE_AUDIT_PATH``) with the matching tenant context; the ContextSelector's
own skill audit lands under a temp ``CORVIN_HOME``.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

import pytest

from core.learning import hybrid_context_request_pipeline as hcrp
from core.learning.hybrid_context import HybridContextModel
from core.learning.hybrid_context_request_pipeline import (
    AttentionTrackerAdapter,
    DecisionHistoryAdapter,
    HybridContextRequestPipeline,
    OutcomeFeedbackAdapter,
    UserProfileAdapter,
    get_request_pipeline,
    reset_request_pipelines,
)

TENANT = "tenant_a"


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch):
    chain = tmp_path / "chain" / "audit.jsonl"
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(chain))
    monkeypatch.setenv("CORVIN_TENANT_ID", TENANT)
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    reset_request_pipelines()
    yield chain
    reset_request_pipelines()


def _chain(chain: Path) -> list[dict]:
    if not chain.exists():
        return []
    return [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]


class _Selection:
    def __init__(self, adrs, mems):
        from core.skills.os_skills.context_selector import QualityMode

        self.quality_mode = QualityMode.BALANCED
        self.selected_adr_ids = adrs
        self.selected_memory_ids = mems
        self.confidence = 0.6
        self.reasoning = "fake"
        self.execution_time_ms = 0.1


class FakeSelector:
    """Deterministic selector: returns the ids it was built with."""

    def __init__(self, adrs=None, mems=None):
        self.adrs = adrs or []
        self.mems = mems or []
        self.calls = 0

    def execute(self, task_type, user_id, time_budget_ms=1000, system_load_p99_ms=None, user_override=None):
        self.calls += 1
        return _Selection(list(self.adrs), list(self.mems))


class FakeDecisions:
    def get_recent_decisions(self, user_id, tenant_id, limit=10):
        return [{"decision_id": "d1", "chosen": "a"}, {"decision_id": "d2", "chosen": "b"}][:limit]


class FakeOutcomes:
    def get_success_rate(self, user_id, tenant_id):
        return 0.9


class FakeProfile:
    def get_profile(self, user_id, tenant_id):
        return {"decision_style": "pragmatic"}


class FakeAttention:
    def get_remaining_budget(self, user_id, tenant_id):
        return 1234


def _run(pipeline, user="u1", session="s1", task="general"):
    return asyncio.run(pipeline.enrich_request(
        {"messages": [], "metadata": {"task_type": task}, "system_prompt": "SYS"},
        user_id=user, session_id=session,
    ))


class TestNoDummyTier1:
    def test_no_adapters_means_no_tier1_fields_in_prompt(self, sandbox):
        p = HybridContextRequestPipeline(TENANT, context_selector=FakeSelector(["ADR-1"]))
        out = _run(p)
        assert out.tier1_sources == []
        assert out.context_metadata["tier1_present"] is True  # base exists (identity + chain)
        base = p.context_model.base_snapshots["u1:s1"]
        assert base.recent_decisions == []  # never the old [{"decision_id": "d1", "choice": "a"}]
        assert base.user_profile == {}
        # ... and nothing unsourced is rendered into the LLM prompt
        assert "Recent decisions" not in out.system_prompt
        assert "Success rate" not in out.system_prompt
        assert "Attention budget" not in out.system_prompt
        assert "User profile" not in out.system_prompt
        assert "5000" not in out.system_prompt and "70.0%" not in out.system_prompt
        assert out.system_prompt.startswith("SYS")
        assert "adr_references" in out.system_prompt

    def test_adapters_source_every_tier1_field(self, sandbox):
        p = HybridContextRequestPipeline(
            TENANT, context_selector=FakeSelector(),
            decision_adapter=FakeDecisions(), outcome_adapter=FakeOutcomes(),
            profile_adapter=FakeProfile(), attention_adapter=FakeAttention(),
        )
        out = _run(p)
        assert set(out.tier1_sources) == {"recent_decisions", "user_profile", "success_rate", "attention_budget_remaining"}
        base = p.context_model.base_snapshots["u1:s1"]
        assert [d["decision_id"] for d in base.recent_decisions] == ["d1", "d2"]
        assert base.success_rate == 0.9
        assert base.attention_budget_remaining == 1234
        assert base.user_profile == {"decision_style": "pragmatic"}
        assert "Recent decisions: 2 decision(s)" in out.system_prompt
        assert "Success rate: 90.0%" in out.system_prompt
        assert "Attention budget: 1234 tokens" in out.system_prompt
        assert "pragmatic" in out.system_prompt

    def test_failing_adapter_leaves_field_unsourced(self, sandbox):
        class Broken:
            def get_success_rate(self, user_id, tenant_id):
                raise RuntimeError("db down")

        p = HybridContextRequestPipeline(TENANT, context_selector=FakeSelector(),
                                         outcome_adapter=Broken(), profile_adapter=FakeProfile())
        out = _run(p)
        assert out.tier1_sources == ["user_profile"]
        assert "Success rate" not in out.system_prompt

    def test_base_is_snapshotted_once_per_session(self, sandbox):
        p = HybridContextRequestPipeline(TENANT, context_selector=FakeSelector(), outcome_adapter=FakeOutcomes())
        _run(p); _run(p)
        assert sum(1 for r in _chain(sandbox) if r["event_type"] == "tier1_base_snapshotted") == 1
        _run(p, session="s2")
        assert sum(1 for r in _chain(sandbox) if r["event_type"] == "tier1_base_snapshotted") == 2


class TestPerRequestTier2:
    def test_only_this_requests_layers_are_returned(self, sandbox):
        sel = FakeSelector(["ADR-1"], ["m1"])
        p = HybridContextRequestPipeline(TENANT, context_selector=sel)
        first = _run(p)
        assert first.layers_injected == 2
        sel.adrs, sel.mems = ["ADR-2"], []
        second = _run(p)
        assert second.layers_injected == 1  # not 3 (the user's whole history)
        assert second.context_metadata["tier2_layer_names"] == ["adr_references"]
        # history is still the verified chain (2 + 1 layers) …
        assert len(p.context_model.injected_layers["u1"]) == 3
        # … but the prompt carries ONE adr_references entry — the latest — and no duplicates
        assert second.system_prompt.count("adr_references:") == 1
        assert "ADR-2" in second.system_prompt and "ADR-1" not in second.system_prompt
        assert second.system_prompt.count("user_memory:") == 1  # superseded value still current

    def test_direct_build_returns_only_new_layers(self, sandbox):
        p = HybridContextRequestPipeline(TENANT, context_selector=FakeSelector())
        p._build_tier2_layers("u1", ["A"], ["m"])
        layers = p._build_tier2_layers("u1", ["B"], [])
        assert [l.layer_name for l in layers] == ["adr_references"]
        assert layers[0].data["adr_ids"] == ["B"]
        assert p._build_tier2_layers("u1", [], []) == []


class TestAudit:
    def test_enrichment_is_on_the_core_chain_content_free(self, sandbox):
        p = HybridContextRequestPipeline(TENANT, context_selector=FakeSelector(["ADR-9"]),
                                         profile_adapter=FakeProfile())
        out = _run(p, user="alice", session="sess7")
        recs = [r for r in _chain(sandbox) if r["event_type"] == "hybrid_context_request_enriched"]
        assert len(recs) == 1
        rec = recs[0]
        assert "hash" in rec
        d = rec["details"]
        assert d["audit_ref"] == out.audit_ref and out.audit_ref
        assert d["tenant_id"] == TENANT
        assert d["user"] == "alice"
        assert d["session_id"] == "sess7"
        assert d["layers_injected"] == 1
        assert d["layer_names"] == ["adr_references"]
        assert d["tier1_sources"] == ["user_profile"]
        assert d["selected_adr_count"] == 1
        assert d["lom"].endswith("enrich_request")
        assert "pragmatic" not in sandbox.read_text()  # profile CONTENT never on the chain
        assert "ADR-9" not in json.dumps(rec)

    def test_unauditable_enrichment_raises(self, sandbox, monkeypatch):
        from core.learning import event_persistence

        p = HybridContextRequestPipeline(TENANT, context_selector=FakeSelector())
        monkeypatch.setattr(event_persistence, "_resolve_core_audit",
                            lambda: (_ for _ in ()).throw(RuntimeError("core audit writer unavailable")))
        with pytest.raises(RuntimeError):
            _run(p)


class TestPerTenantRegistry:
    def test_pipelines_are_keyed_per_tenant(self, sandbox):
        a = get_request_pipeline("tenant_a")
        b = get_request_pipeline("tenant_b")
        assert a is not b
        assert a.tenant_id == "tenant_a" and b.tenant_id == "tenant_b"
        assert a.context_model.tenant_id == "tenant_a" and b.context_model.tenant_id == "tenant_b"
        assert a.context_selector is not b.context_selector
        assert get_request_pipeline("tenant_a") is a

    def test_invalid_tenant_rejected(self, sandbox):
        with pytest.raises(ValueError):
            get_request_pipeline("tenant-A")
        with pytest.raises(ValueError):
            HybridContextRequestPipeline("Bad Tenant")

    def test_model_of_other_tenant_rejected(self, sandbox):
        with pytest.raises(ValueError):
            HybridContextRequestPipeline("tenant_a", context_model=HybridContextModel("tenant_b"))


class TestRealSelectorWiring:
    def test_real_context_selector_runs_and_is_audited(self, sandbox, tmp_path):
        p = get_request_pipeline(TENANT)  # real ContextSelectorSkill(tenant)
        out = _run(p, task="compliance")
        assert out.quality_mode in {"QUALITY_MAX", "BALANCED", "EFFICIENCY_MAX"}
        assert [r for r in _chain(sandbox) if r["event_type"] == "hybrid_context_request_enriched"]
        skill_chain = tmp_path / "home" / "tenants" / TENANT / "global" / "forge" / "audit.jsonl"
        assert skill_chain.exists(), "ContextSelectorSkill audit lands on the tenant chain under CORVIN_HOME"


class TestPhase3Adapters:
    def test_decision_and_outcome_adapters_over_real_stores(self, sandbox, tmp_path):
        from core.learning.decision_history import DecisionHistoryStore, DecisionRecord
        from core.learning.outcome_feedback import OutcomeFeedbackStore

        dstore = DecisionHistoryStore(tmp_path / "decisions.db")
        for i in range(3):
            dstore.record_decision(DecisionRecord(
                decision_id=f"d{i}", choice_type="routing", candidates=["a", "b"], chosen="a",
                timestamp_utc=datetime.utcnow(), session_id="s1", tenant_id=TENANT,
                confidence_score=0.8, user_id="u1" if i < 2 else "other",
            ))
        dec = DecisionHistoryAdapter(dstore)
        got = dec.get_recent_decisions("u1", TENANT, limit=10)
        assert [d["decision_id"] for d in got] == ["d0", "d1"]  # user-scoped, oldest→newest
        assert dec.get_recent_decisions("nobody", TENANT) == []

        ostore = OutcomeFeedbackStore(tmp_path / "outcomes.db")
        out = OutcomeFeedbackAdapter(ostore, dec)
        assert out.get_success_rate("u1", TENANT) == 0.5  # N < 10 → ADR-0317 suppression

    def test_profile_and_attention_adapters(self, sandbox, tmp_path):
        from core.learning.attention_budget import AttentionBudget, AttentionTracker
        from core.learning.user_profile import UserProfileManager

        prof = UserProfileAdapter(UserProfileManager(profiles_dir=tmp_path / "profiles"))
        data = prof.get_profile("u1", TENANT)
        assert set(data) >= {"decision_style", "conciseness_preference"}
        assert "user_id" not in data

        att = AttentionTrackerAdapter()
        with pytest.raises(LookupError):
            att.get_remaining_budget("u1", TENANT)
        tracker = AttentionTracker(AttentionBudget(user_id="u1", tenant_id=TENANT, max_context_tokens=1000))
        tracker.record_context_usage(250)
        att.register(tracker)
        assert att.get_remaining_budget("u1", TENANT) == 750

"""
T3.1 Model Selection Skill — learning loop tests (rebuilt 2026-09-18, ADR-0885).

Until 2026-09-18 this file did not parse: a commit trailer had been pasted
after its last line, so the "24 tests" it advertised never ran. It also
imported ``model_selector_learning_enhancement`` — an in-memory second Beta
learner with its own model-id table (two of four ids did not exist on any
rate card) and its own price table. That module is gone; the multi-model
ranking now lives on the ONE persisted, audited learner,
``ConfidenceOptimizer.rank_models`` (ADR-0644 + ADR-0885), and reads the
published rate card the cost panels bill against.

Phase 1 (skill instantiation/execution) is kept as it was. Phases 2–4 are
rewritten against the real API. The HTTP wiring proof for the ranking lives
in ``core/console/tests/test_model_ranking_route.py``.

ADRs: 0845 (Architecture), 0644 (Confidence Optimizer), 0885 (Models console)
"""

import pytest

from core.learning.model_selection_learner import model_price_per_1k
from core.learning.model_selection_optimizer import ConfidenceOptimizer, RankedModel
from core.skills.os_skills.model_selector_skill_integration import ModelSelectorSkill

# Real ids on the published rate card (positive control below).
CHEAP = "claude-haiku-4-5-20251001"
MID = "claude-sonnet-5"
DEAR = "claude-opus-5"
UNPRICED = "llama-3.3-70b"


@pytest.fixture
def optimizer(tmp_path, monkeypatch):
    """In-memory store, no audit backend, and a temp CORVIN_HOME — the
    optimizer persists its confidence history under CORVIN_HOME even with a
    dict store, and a unit test must never touch the operator's ~/.corvin."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    return ConfidenceOptimizer(store={}, audit_backend=None)


def _feed(opt: ConfidenceOptimizer, task_type: str, model: str, qualities, tenant="_default"):
    last = None
    for q in qualities:
        last = opt.process_feedback(task_type, model, q, tenant)
    return last


class TestPhase1SkillInstantiation:
    """Phase 1: Basic skill instantiation and execution."""

    def test_skill_instantiation(self):
        skill = ModelSelectorSkill(tenant_id="_default", variant="variant_c")
        assert skill.tenant_id == "_default"
        assert skill.variant_name == "variant_c"

    def test_skill_execution_recommends_a_priced_model(self):
        skill = ModelSelectorSkill(tenant_id="_default")
        decision = skill.execute(task_input="Classify this code review", task_type="code_review")

        assert decision.recommended_model in [CHEAP, MID, DEAR]
        # Every id the skill can recommend must be on the rate card — the
        # deleted module recommended ids that were on nobody's.
        assert model_price_per_1k(decision.recommended_model) is not None
        assert decision.recommended_provider is not None
        assert 0.0 <= decision.confidence <= 1.0

    def test_feedback_recording(self):
        skill = ModelSelectorSkill(tenant_id="_default")
        decision = skill.execute(task_input="Test", task_type="test")
        skill.record_outcome(
            model=decision.recommended_model,
            task_type="test",
            success=True,
            cost_usd=1.50,
        )


class TestPhase2RankingPayload:
    """Phase 2: the payload the console's learning tab reads."""

    def test_rate_card_positive_control(self):
        for m in (CHEAP, MID, DEAR):
            assert model_price_per_1k(m) is not None, m
        assert model_price_per_1k(UNPRICED) is None

    def test_ranked_row_shape(self, optimizer):
        _feed(optimizer, "code_review", MID, [0.8] * 5)
        rows = optimizer.rank_models("code_review", [MID])
        assert len(rows) == 1
        row = rows[0]
        assert isinstance(row, RankedModel)
        d = row.to_dict()
        for key in ("model", "task_type", "tenant_id", "confidence", "posterior_mean",
                    "n_samples", "is_converged", "input_usd_per_1k", "output_usd_per_1k",
                    "priced"):
            assert key in d, key
        assert d["n_samples"] == 5
        assert d["priced"] is True
        assert (d["input_usd_per_1k"], d["output_usd_per_1k"]) == model_price_per_1k(MID)

    def test_unseen_candidate_reports_uninformed_prior(self, optimizer):
        (row,) = optimizer.rank_models("code_review", [CHEAP])
        assert row.n_samples == 0
        assert row.confidence == 0.5
        assert row.is_converged is False

    def test_unpriced_model_is_null_never_zero(self, optimizer):
        (row,) = optimizer.rank_models("code_review", [UNPRICED])
        assert row.priced is False
        assert row.input_usd_per_1k is None
        assert row.output_usd_per_1k is None


class TestPhase3MultiModelLearning:
    """Phase 3: multi-model comparison and budget on the real learner."""

    def test_confidence_moves_with_feedback(self, optimizer):
        (before,) = optimizer.rank_models("code_review", [MID])
        after_success, _ = _feed(optimizer, "code_review", MID, [1.0])
        assert after_success > before.confidence
        after_failure, _ = _feed(optimizer, "code_review", MID, [0.0])
        assert after_failure < after_success

    def test_better_model_ranks_first(self, optimizer):
        _feed(optimizer, "code_review", CHEAP, [1.0] * 10)
        _feed(optimizer, "code_review", MID, [1.0] * 5 + [0.2] * 5)
        ranked = optimizer.rank_models("code_review", [MID, CHEAP])
        assert [r.model for r in ranked] == [CHEAP, MID]
        assert optimizer.select_model("code_review", [MID, CHEAP]).model == CHEAP

    def test_candidates_default_to_what_the_tenant_learned(self, optimizer):
        # Persisted history is what list_entries enumerates; dict store keys
        # are not on disk, so the fallback ranks the process cache instead.
        _feed(optimizer, "coding", DEAR, [0.9] * 3)
        _feed(optimizer, "other", CHEAP, [0.9] * 3)
        ranked = optimizer.rank_models("coding")
        assert [r.model for r in ranked] == [DEAR]

    def test_budget_filters_by_output_rate_and_drops_unpriced(self, optimizer):
        _feed(optimizer, "coding", UNPRICED, [1.0] * 6)  # would win on confidence
        _feed(optimizer, "coding", DEAR, [0.9] * 6)
        _feed(optimizer, "coding", MID, [0.6] * 6)
        cap = model_price_per_1k(MID)[1]
        ranked = optimizer.rank_models(
            "coding", [UNPRICED, DEAR, MID, CHEAP], max_output_usd_per_1k=cap,
        )
        assert [r.model for r in ranked] == [MID, CHEAP]

    def test_no_hard_coded_fallback(self, optimizer):
        assert optimizer.select_model("coding", [DEAR], max_output_usd_per_1k=0.0) is None

    def test_price_source_is_injectable(self, optimizer):
        table = {CHEAP: (0.001, 0.002), MID: (0.001, 0.003)}
        ranked = optimizer.rank_models(
            "coding", [MID, CHEAP], max_output_usd_per_1k=0.0025, price_of=table.get,
        )
        assert [r.model for r in ranked] == [CHEAP]

    def test_tie_breaks_are_stable(self, optimizer):
        a = optimizer.rank_models("coding", [DEAR, MID, CHEAP])
        b = optimizer.rank_models("coding", [CHEAP, DEAR, MID])
        # Equal confidence and samples → cheaper output rate first, then id.
        assert [r.model for r in a] == [r.model for r in b] == [CHEAP, MID, DEAR]

    def test_tenants_do_not_share_learning(self, optimizer):
        _feed(optimizer, "coding", DEAR, [1.0] * 6, tenant="tenant_a")
        (row,) = optimizer.rank_models("coding", [DEAR], "tenant_b")
        assert row.n_samples == 0


class TestPhase4Convergence:
    """Phase 4: convergence on the real learner (window=50, variance<0.05)."""

    def test_converges_after_window_of_consistent_feedback(self, optimizer):
        _feed(optimizer, "coding", MID, [0.9] * 60)
        (row,) = optimizer.rank_models("coding", [MID])
        assert row.n_samples == 60
        assert row.is_converged is True
        assert row.posterior_mean == pytest.approx(0.9, abs=0.05)

    def test_not_converged_below_window(self, optimizer):
        _feed(optimizer, "coding", MID, [0.9] * 20)
        (row,) = optimizer.rank_models("coding", [MID])
        assert row.is_converged is False

    def test_ranking_is_a_pure_read(self, optimizer):
        _feed(optimizer, "coding", MID, [0.9] * 3)
        before = optimizer.get_stats("coding", MID).to_dict()
        optimizer.rank_models("coding", [MID, CHEAP])
        optimizer.select_model("coding", [MID, CHEAP])
        assert optimizer.get_stats("coding", MID).to_dict() == before


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

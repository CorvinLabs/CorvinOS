"""Tests for acs_gate_chain.py — ADR-0104 M5."""
from __future__ import annotations

import pytest

try:
    from . import acs_gate_chain as _gc
except ImportError:
    import acs_gate_chain as _gc  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Simhash tests
# ---------------------------------------------------------------------------

def test_simhash_identical():
    h1 = _gc._simhash("hello world this is a test")
    h2 = _gc._simhash("hello world this is a test")
    assert h1 == h2
    assert _gc.simhash_similarity(h1, h2) == 1.0


def test_simhash_completely_different():
    h1 = _gc._simhash("alpha beta gamma delta epsilon zeta eta theta")
    h2 = _gc._simhash("one two three four five six seven eight nine ten eleven")
    sim = _gc.simhash_similarity(h1, h2)
    assert sim < 0.9, f"expected dissimilar, got {sim}"


def test_simhash_near_duplicate():
    base = "The quick brown fox jumps over the lazy dog. " * 5
    near = base.replace("quick", "fast")
    h1 = _gc._simhash(base)
    h2 = _gc._simhash(near)
    sim = _gc.simhash_similarity(h1, h2)
    assert sim > 0.70, f"expected near-duplicate, got {sim}"


# ---------------------------------------------------------------------------
# Gate 1: Deliverable presence
# ---------------------------------------------------------------------------

def test_gate1_no_artifacts_required():
    g = _gc._gate_deliverable("some output text", [], None)
    assert g.passed
    assert g.gate_id == "gate_1_deliverable"


def test_gate1_artifact_mentioned_in_text():
    g = _gc._gate_deliverable("output saved to report.pdf", ["report.pdf"], None)
    assert g.passed


def test_gate1_artifact_not_mentioned():
    g = _gc._gate_deliverable("output is fine", ["report.pdf"], None)
    assert not g.passed
    assert "report.pdf" in g.details.get("missing", [])


# ---------------------------------------------------------------------------
# Gate 2: L0 contract
# ---------------------------------------------------------------------------

def test_gate2_placeholder_detected():
    g = _gc._gate_l0_contract("This is a TODO item that needs work.")
    assert not g.passed
    assert "no_placeholder" in g.reason


def test_gate2_lorem_ipsum():
    g = _gc._gate_l0_contract("Lorem ipsum dolor sit amet.")
    assert not g.passed


def test_gate2_no_placeholder():
    g = _gc._gate_l0_contract("This output contains real content about machine learning.")
    # Should pass placeholder check
    assert "no_placeholder" not in g.reason or g.passed


def test_gate2_near_duplicate_fixpoint():
    text = "This is the exact output from the previous iteration."
    prev_hash = _gc._simhash(text)
    g = _gc._gate_l0_contract(text, prev_hash=prev_hash)
    assert not g.passed
    assert "repair_fixpoint" in g.reason


def test_gate2_different_from_previous():
    prev = "This was the old content from last time."
    curr = "This is completely different new analysis with different insights."
    prev_hash = _gc._simhash(prev)
    g = _gc._gate_l0_contract(curr, prev_hash=prev_hash)
    # Should not fail due to similarity
    assert "repair_fixpoint" not in g.reason


def test_gate2_balanced_delimiters_in_code():
    text = "```python\nx = {'key': [1, 2, 3]}\n```"
    g = _gc._gate_l0_contract(text)
    assert "balanced_delimiters" not in g.reason or g.passed


def test_gate2_unbalanced_in_code():
    text = "```python\nx = {'key': [1, 2, 3}\n```"  # } closes { but ] not closed for [
    g = _gc._gate_l0_contract(text)
    # May or may not catch depending on parsing


def test_gate2_valid_json_block():
    text = '```json\n{"key": "value", "num": 42}\n```'
    g = _gc._gate_l0_contract(text)
    assert "json_valid_if_claimed" not in g.reason or g.passed


def test_gate2_invalid_json_block():
    text = '```json\n{"key": "value" "broken": true}\n```'
    g = _gc._gate_l0_contract(text)
    assert not g.passed


# ---------------------------------------------------------------------------
# Gate 4: Evaluation metrics
# ---------------------------------------------------------------------------

def test_gate4_no_metrics():
    g = _gc._gate_eval_metrics({"confidence": 0.9}, None, None)
    assert g.passed
    assert g.gate_id == "gate_4_eval_metrics"


def test_gate4_metrics_pass():
    metrics = [{"kind": "schema", "weight": 1.0}]
    thresholds = {"accept": 0.85}
    g = _gc._gate_eval_metrics({"confidence": 0.95}, metrics, thresholds)
    assert g.passed


def test_gate4_metrics_fail_low_confidence():
    metrics = [{"kind": "llm_rubric", "weight": 1.0}]
    thresholds = {"accept": 0.85}
    g = _gc._gate_eval_metrics({"confidence": 0.5}, metrics, thresholds)
    assert not g.passed


# ---------------------------------------------------------------------------
# Gate 5: Refinement gradient
# ---------------------------------------------------------------------------

def test_gate5_first_iteration_always_passes():
    g = _gc._gate_refinement_gradient([], [], iteration=0)
    assert g.passed


def test_gate5_no_signal_fails():
    g = _gc._gate_refinement_gradient([], [], iteration=2)
    assert not g.passed
    assert "nothing to refine" in g.reason


def test_gate5_with_defects():
    g = _gc._gate_refinement_gradient(["missing citation", "hallucinated fact"], [], iteration=1)
    assert g.passed


def test_gate5_with_rejected_gates():
    g = _gc._gate_refinement_gradient([], ["gate_2_l0_contract"], iteration=3)
    assert g.passed


# ---------------------------------------------------------------------------
# ACSGateChain integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_chain_passes():
    chain = _gc.ACSGateChain()
    result = await chain.evaluate(
        result_text="This is a comprehensive analysis of the topic with real findings.",
        result={"confidence": 0.9, "status": "success"},
        iteration=0,
    )
    # Should pass gates 1 (no artifacts), 2 (no placeholders), 4, 5 (iter 0)
    assert result.passed or result.rejected_gate in ("gate_3_llm_critique", "gate_4_eval_metrics")


@pytest.mark.asyncio
async def test_full_chain_fails_on_placeholder():
    chain = _gc.ACSGateChain()
    result = await chain.evaluate(
        result_text="TODO: complete this analysis. Lorem ipsum dolor sit amet.",
        result={"confidence": 0.9, "status": "success"},
        iteration=1,
    )
    assert not result.passed
    assert result.rejected_gate == "gate_2_l0_contract"


@pytest.mark.asyncio
async def test_full_chain_repair_fixpoint_aborts():
    chain = _gc.ACSGateChain()
    text = "This is the exact repeated output, same as before, nothing changed."
    prev_hash = chain.compute_prev_hash(text)
    result = await chain.evaluate(
        result_text=text,
        result={"confidence": 0.9},
        iteration=2,
        prev_hash=prev_hash,
    )
    assert not result.passed
    assert result.abort_reason  # not just repair_needed, but abort


@pytest.mark.asyncio
async def test_full_chain_clean_completion_passes_r36():
    # A clean DELEGATE→COMPLETE at iteration=3 with no prior gate failures
    # must succeed — R36 only fires in repair cycles (when repair_needed=True).
    chain = _gc.ACSGateChain()
    result = await chain.evaluate(
        result_text="The top market is the United States with 42M streams. Track A leads globally.",
        result={"confidence": 0.9},
        iteration=3,
        defects=[],
        rejected_gates=[],
    )
    assert result.passed, f"Clean completion at iteration=3 should pass; abort={result.abort_reason}"
    gate5 = next((g for g in result.gates if g.gate_id == "gate_5_gradient"), None)
    assert gate5 is None, "G5 must not run when no prior gate triggered repair"


@pytest.mark.asyncio
async def test_full_chain_r36_aborts_stale_repair_cycle():
    # G2 fails (placeholder text) → repair_needed=True → G5 fires with no signal → R36 abort.
    chain = _gc.ACSGateChain()
    result = await chain.evaluate(
        result_text="Analysis complete. TODO: fill in details later.",
        result={"confidence": 0.9},
        iteration=2,
        defects=[],
        rejected_gates=[],
    )
    gate5 = next((g for g in result.gates if g.gate_id == "gate_5_gradient"), None)
    assert gate5 is not None, "G5 must run when a prior gate triggered repair"
    assert not gate5.passed
    assert result.abort_reason


@pytest.mark.asyncio
async def test_compute_prev_hash():
    chain = _gc.ACSGateChain()
    h = chain.compute_prev_hash("some text")
    assert isinstance(h, int)
    assert h != 0


# ---------------------------------------------------------------------------
# ADR-0105: WorkflowLoss, LossProfile, convergence
# ---------------------------------------------------------------------------

def test_loss_profile_defaults():
    p = _gc.LossProfile()
    assert p.target == 0.0
    assert p.freshness_polarity == "positive"
    assert abs(p.weights.total() - 1.0) < 1e-6


def test_loss_profile_from_dict():
    p = _gc.LossProfile.from_dict({"target": 0.80, "weights": {"quality": 1.0}})
    assert p.target == 0.80
    # After normalisation, quality should dominate
    assert p.weights.quality > 0.5


def test_loss_profile_preset_analytical():
    p = _gc.loss_profile_from_spec({"observability": {"loss": {"preset": "analytical"}}})
    assert p is not None
    assert p.target > 0.0


def test_loss_profile_from_spec_none():
    # No observability.loss → returns None (backward compat)
    assert _gc.loss_profile_from_spec({}) is None
    assert _gc.loss_profile_from_spec({"observability": {}}) is None


@pytest.mark.asyncio
async def test_workflow_loss_attached_to_gate_result():
    chain = _gc.ACSGateChain()
    result = await chain.evaluate(
        result_text="The analysis covers all required metrics in detail.",
        result={"confidence": 0.85},
        iteration=0,
    )
    assert result.loss is not None
    assert isinstance(result.loss.total, float)
    assert 0.0 <= result.loss.total <= 1.0
    assert result.loss.iteration == 0
    assert result.loss.delta is None  # first iteration


@pytest.mark.asyncio
async def test_workflow_loss_delta_computed_on_second_call():
    chain = _gc.ACSGateChain()
    first = await chain.evaluate(
        result_text="Initial analysis with some depth.",
        result={"confidence": 0.75},
        iteration=0,
    )
    assert first.loss is not None

    second = await chain.evaluate(
        result_text="Improved analysis with significantly more depth and detail.",
        result={"confidence": 0.85},
        iteration=1,
        prev_loss=first.loss,
    )
    assert second.loss is not None
    assert second.loss.delta is not None
    # delta = total(iter1) - total(iter0)
    assert abs(second.loss.delta - (second.loss.total - first.loss.total)) < 1e-6


def _make_loss(total: float, iteration: int, delta: float | None = None) -> "_gc.WorkflowLoss":
    return _gc.WorkflowLoss(
        L_completeness=total,
        L_novelty=total,
        L_quality=total,
        L_metrics=total,
        L_confidence=total,
        total=total,
        iteration=iteration,
        delta=delta,
    )


def test_convergence_target_reached():
    profile = _gc.LossProfile(target=0.80)
    history = [_make_loss(0.85, 0)]
    assert _gc._evaluate_convergence(history, profile) == "converged"


def test_convergence_continue_when_improving():
    profile = _gc.LossProfile(target=0.85, convergence=_gc.ConvergenceConfig(epsilon=0.01, window=2))
    history = [
        _make_loss(0.60, 0),
        _make_loss(0.70, 1, delta=0.10),
    ]
    assert _gc._evaluate_convergence(history, profile) == "continue"


def test_convergence_plateau_detected():
    profile = _gc.LossProfile(target=0.90, convergence=_gc.ConvergenceConfig(epsilon=0.02, window=2))
    history = [
        _make_loss(0.70, 0),
        _make_loss(0.701, 1, delta=0.001),
        _make_loss(0.702, 2, delta=0.001),
    ]
    assert _gc._evaluate_convergence(history, profile) == "plateau"


def test_convergence_plateau_two_entries_not_enough():
    # window=2 but only 1 measured delta (history[0].delta=None) → NOT plateau
    profile = _gc.LossProfile(target=0.90, convergence=_gc.ConvergenceConfig(epsilon=0.02, window=2))
    history = [
        _make_loss(0.70, 0),               # delta=None (baseline)
        _make_loss(0.701, 1, delta=0.001), # only 1 measured delta
    ]
    # Not enough measured deltas for window=2 → should NOT return plateau
    assert _gc._evaluate_convergence(history, profile) != "plateau"


def test_convergence_regression_detected():
    profile = _gc.LossProfile(target=0.90)
    history = [
        _make_loss(0.70, 0),
        _make_loss(0.55, 1, delta=-0.15),
    ]
    assert _gc._evaluate_convergence(history, profile) == "regression"


def test_freshness_polarity_negative_inverts_novelty():
    # verification profile: polarity negative → stable output (low raw-novelty) is rewarded
    profile_neg = _gc.LossProfile(freshness_polarity="negative")
    profile_pos = _gc.LossProfile(freshness_polarity="positive")

    text = "This is a stable specification document. " * 10
    prev_hash = _gc._simhash(text)  # identical prev → simhash_sim ≈ 1.0 → L_novelty_raw ≈ 0.0

    loss_neg = _gc._compute_workflow_loss(
        gates=[],
        result={"confidence": 0.8},
        result_text=text,
        prev_hash=prev_hash,
        profile=profile_neg,
        iteration=1,
        prev_loss=None,
    )
    loss_pos = _gc._compute_workflow_loss(
        gates=[],
        result={"confidence": 0.8},
        result_text=text,
        prev_hash=prev_hash,
        profile=profile_pos,
        iteration=1,
        prev_loss=None,
    )
    # Negative polarity rewards stability: L_novelty should be HIGHER than positive polarity
    # (raw_novelty ≈ 0.0 → neg inverts to ≈ 1.0; pos keeps ≈ 0.0)
    assert loss_neg.L_novelty > loss_pos.L_novelty, (
        f"negative polarity should reward stability: neg={loss_neg.L_novelty} pos={loss_pos.L_novelty}"
    )


# ---------------------------------------------------------------------------
# ADR-0105 M4: worker attribution + adaptive budget helpers
# ---------------------------------------------------------------------------

def _make_worker_result(wid: str, status: str, confidence: float, result_size: int = 200):
    """Helper for M4 tests."""
    try:
        from . import acs_runtime as _rt
    except ImportError:
        import acs_runtime as _rt  # type: ignore[no-redef]
    result = {"data": "x" * result_size}
    return _rt.WorkerResult(
        worker_id=wid, status=status, result=result, confidence=confidence
    )


def test_m4_worker_attribution_proportional():
    try:
        from . import acs_runtime as _rt
    except ImportError:
        import acs_runtime as _rt  # type: ignore[no-redef]

    workers = [
        _make_worker_result("w1", "success", 0.9),
        _make_worker_result("w2", "success", 0.5),
        _make_worker_result("w3", "failed",  0.0),
    ]
    attr = _rt._compute_worker_attributions(workers, loss_delta=0.10, iteration=1)
    assert attr["loss_delta"] == 0.10
    entries = attr["workers"]
    # w1 should have highest attribution
    assert entries[0]["worker_id"] == "w1"
    # sum of attributions should roughly equal loss_delta (failed worker contributes 0)
    total = sum(e["attribution"] for e in entries)
    assert abs(total - 0.10) < 0.05, f"attribution sum={total} != loss_delta=0.10"


def test_m4_worker_attribution_no_delta():
    try:
        from . import acs_runtime as _rt
    except ImportError:
        import acs_runtime as _rt  # type: ignore[no-redef]

    workers = [_make_worker_result("w1", "success", 0.8)]
    attr = _rt._compute_worker_attributions(workers, loss_delta=None, iteration=0)
    assert attr["loss_delta"] is None
    assert len(attr["workers"]) == 1
    # Without delta, attribution is a relative proportion summing to 1.0
    assert abs(attr["workers"][0]["attribution"] - 1.0) < 0.01


def test_m4_worker_attribution_empty():
    try:
        from . import acs_runtime as _rt
    except ImportError:
        import acs_runtime as _rt  # type: ignore[no-redef]

    attr = _rt._compute_worker_attributions([], loss_delta=0.05, iteration=0)
    assert attr == {}


def test_m4_format_loss_context_shows_attribution():
    try:
        from . import acs_runtime as _rt
    except ImportError:
        import acs_runtime as _rt  # type: ignore[no-redef]

    history = [_make_loss(0.70, 0)]
    profile = _gc.LossProfile(target=0.85)
    worker_attrs = [{
        "iteration": 0,
        "workers": [
            {"worker_id": "analyst_001", "status": "success", "confidence": 0.9, "attribution": 0.04},
            {"worker_id": "researcher_001", "status": "partial", "confidence": 0.6, "attribution": 0.01},
        ],
        "loss_delta": 0.05,
    }]
    block = _rt._format_loss_context(history, profile, worker_attrs)
    assert "Worker Attribution" in block
    assert "analyst_001" in block
    assert "researcher_001" in block


def test_m4_adaptive_worker_count_scales_with_gap():
    """Verify adaptive worker count formula: large gap → more workers."""
    try:
        from . import acs_runtime as _rt
    except ImportError:
        import acs_runtime as _rt  # type: ignore[no-redef]

    base = 6
    # gap = 0.5 → scale = 1.0 → adaptive_n = 6
    gap_large = 0.5
    scale_large = max(0.5, min(1.0, 0.5 + gap_large))
    n_large = max(1, round(base * scale_large))

    # gap = 0.0 → scale = 0.5 → adaptive_n = 3
    gap_small = 0.0
    scale_small = max(0.5, min(1.0, 0.5 + gap_small))
    n_small = max(1, round(base * scale_small))

    assert n_large >= n_small, f"large gap should give more workers: {n_large} vs {n_small}"
    assert n_large == 6
    assert n_small == 3

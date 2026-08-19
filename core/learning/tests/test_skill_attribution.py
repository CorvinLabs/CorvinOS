"""Tests for Skill Attribution Engine (Gap 3, ADR-0323).

Tests:
1. EQUAL attribution fairness
2. Single skill strategy
3. Multi-skill strategy
4. Chained skills
5. Failed skill attribution
6. WEIGHTED model deferred (documented)
7. Audit trail integration
8. Tenant isolation
9. Credit validation & normalization
10. Grade conversion
11. Chained strategy phases
12. Invalid inputs & edge cases
"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from core.learning.skill_attribution import (
    AttributionModel,
    AttributionPayload,
    SkillAttributionEngine,
)


class MockEventStore:
    """Mock EventStore for testing."""

    def __init__(self):
        self.events = []
        self.write_error = None

    async def write_event(self, event):
        """Record event; simulate write_error if set."""
        if self.write_error:
            raise RuntimeError(self.write_error)
        self.events.append(event)


@pytest.fixture
def mock_event_store():
    """Create mock event store."""
    return MockEventStore()


@pytest.fixture
def attribution_engine_equal(mock_event_store):
    """Create EQUAL attribution engine."""
    return SkillAttributionEngine(
        tenant_id="_default",
        event_store=mock_event_store,
        model=AttributionModel.EQUAL,
        emit_events=True,
    )


@pytest.fixture
def attribution_engine_no_emit(mock_event_store):
    """Create engine with event emission disabled."""
    return SkillAttributionEngine(
        tenant_id="_default",
        event_store=mock_event_store,
        model=AttributionModel.EQUAL,
        emit_events=False,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: EQUAL Attribution Fairness (MVP)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_equal_attribution_two_skills_success(attribution_engine_equal):
    """Two skills in strategy, success: each gets +0.5 credit."""
    payload = await attribution_engine_equal.attribute_outcome(
        strategy_id="strategy_1",
        decision_id="decision_1",
        skills=["skill_a", "skill_b"],
        outcome="success",
    )

    assert payload.outcome == "success"
    assert payload.model == AttributionModel.EQUAL
    assert payload.credits["skill_a"] == pytest.approx(0.5)
    assert payload.credits["skill_b"] == pytest.approx(0.5)
    assert sum(payload.credits.values()) == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_equal_attribution_three_skills_success(attribution_engine_equal):
    """Three skills in strategy, success: each gets +0.333 credit."""
    payload = await attribution_engine_equal.attribute_outcome(
        strategy_id="strategy_2",
        decision_id="decision_2",
        skills=["skill_a", "skill_b", "skill_c"],
        outcome="success",
    )

    assert payload.outcome == "success"
    expected_share = 1.0 / 3.0
    assert payload.credits["skill_a"] == pytest.approx(expected_share)
    assert payload.credits["skill_b"] == pytest.approx(expected_share)
    assert payload.credits["skill_c"] == pytest.approx(expected_share)
    assert sum(payload.credits.values()) == pytest.approx(1.0)


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Single Skill Strategy
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_skill_strategy_success(attribution_engine_equal):
    """Single skill: gets full +1.0 credit on success."""
    payload = await attribution_engine_equal.attribute_outcome(
        strategy_id="strategy_single",
        decision_id="decision_single",
        skills=["skill_only"],
        outcome="success",
    )

    assert payload.credits["skill_only"] == pytest.approx(1.0)
    assert sum(payload.credits.values()) == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_single_skill_strategy_failure(attribution_engine_equal):
    """Single skill: gets 0.0 credit on failure."""
    payload = await attribution_engine_equal.attribute_outcome(
        strategy_id="strategy_single_fail",
        decision_id="decision_single_fail",
        skills=["skill_only"],
        outcome="failure",
    )

    assert payload.credits["skill_only"] == pytest.approx(0.0)


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Multi-Skill Strategy
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_multi_skill_strategy_success(attribution_engine_equal):
    """Multiple skills in pipeline: equal split."""
    skills = ["skill_a", "skill_b", "skill_c", "skill_d"]
    payload = await attribution_engine_equal.attribute_outcome(
        strategy_id="strategy_multi",
        decision_id="decision_multi",
        skills=skills,
        outcome="success",
    )

    expected_share = 0.25  # 4 skills
    for skill in skills:
        assert payload.credits[skill] == pytest.approx(expected_share)


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Chained Skills
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chained_skills_in_order(attribution_engine_equal):
    """Chained skills (diagnosis -> fix -> verify) get equal credit."""
    # Simulate a three-step chain where all succeed
    chain = ["diagnosis_skill", "fix_skill", "verification_skill"]
    payload = await attribution_engine_equal.attribute_outcome(
        strategy_id="strategy_chain",
        decision_id="decision_chain",
        skills=chain,
        outcome="success",
    )

    # All should get 1/3 credit
    expected_share = 1.0 / 3.0
    for skill in chain:
        assert payload.credits[skill] == pytest.approx(expected_share)


@pytest.mark.asyncio
async def test_chained_skills_with_failure_in_middle(attribution_engine_equal):
    """Chained skills where chain fails: no skill gets credit."""
    # Even though diagnosis and fix were part of the chain,
    # if the overall outcome is failure, nobody gets credit
    chain = ["diagnosis_skill", "fix_skill", "verification_skill"]
    payload = await attribution_engine_equal.attribute_outcome(
        strategy_id="strategy_chain_fail",
        decision_id="decision_chain_fail",
        skills=chain,
        outcome="failure",
    )

    for skill in chain:
        assert payload.credits[skill] == pytest.approx(0.0)


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Failed Skill Attribution
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_failure_outcome_all_skills_zero(attribution_engine_equal):
    """Failure outcome: all skills get 0 credit (uniform demotion)."""
    payload = await attribution_engine_equal.attribute_outcome(
        strategy_id="strategy_fail",
        decision_id="decision_fail",
        skills=["skill_a", "skill_b", "skill_c"],
        outcome="failure",
    )

    assert payload.outcome == "failure"
    for skill, credit in payload.credits.items():
        assert credit == pytest.approx(0.0)

    # Total is still normalized
    assert sum(payload.credits.values()) == pytest.approx(0.0)


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: Partial Outcome (MVP)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_partial_outcome_reduced_credit(attribution_engine_equal):
    """Partial outcome: skills get reduced credit (0.5 / num_skills)."""
    payload = await attribution_engine_equal.attribute_outcome(
        strategy_id="strategy_partial",
        decision_id="decision_partial",
        skills=["skill_a", "skill_b"],
        outcome="partial",
    )

    assert payload.outcome == "partial"
    # 0.5 / 2 = 0.25 each
    assert payload.credits["skill_a"] == pytest.approx(0.25)
    assert payload.credits["skill_b"] == pytest.approx(0.25)
    assert sum(payload.credits.values()) == pytest.approx(0.5)


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: WEIGHTED Model (Deferred, Documented)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_weighted_model_deferred_falls_back_to_equal(mock_event_store):
    """WEIGHTED model: currently deferred, falls back to EQUAL."""
    engine = SkillAttributionEngine(
        tenant_id="_default",
        event_store=mock_event_store,
        model=AttributionModel.WEIGHTED,
        emit_events=False,
    )

    payload = await engine.attribute_outcome(
        strategy_id="strategy_weighted",
        decision_id="decision_weighted",
        skills=["skill_a", "skill_b"],
        outcome="success",
    )

    # Should fallback to EQUAL: 0.5 each
    assert payload.credits["skill_a"] == pytest.approx(0.5)
    assert payload.credits["skill_b"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_first_model_deferred_falls_back_to_equal(mock_event_store):
    """FIRST model: currently deferred, falls back to EQUAL."""
    engine = SkillAttributionEngine(
        tenant_id="_default",
        event_store=mock_event_store,
        model=AttributionModel.FIRST,
        emit_events=False,
    )

    payload = await engine.attribute_outcome(
        strategy_id="strategy_first",
        decision_id="decision_first",
        skills=["skill_a", "skill_b"],
        outcome="success",
    )

    # Should fallback to EQUAL: 0.5 each (not just first)
    assert payload.credits["skill_a"] == pytest.approx(0.5)
    assert payload.credits["skill_b"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_last_model_deferred_falls_back_to_equal(mock_event_store):
    """LAST model: currently deferred, falls back to EQUAL."""
    engine = SkillAttributionEngine(
        tenant_id="_default",
        event_store=mock_event_store,
        model=AttributionModel.LAST,
        emit_events=False,
    )

    payload = await engine.attribute_outcome(
        strategy_id="strategy_last",
        decision_id="decision_last",
        skills=["skill_a", "skill_b"],
        outcome="success",
    )

    # Should fallback to EQUAL: 0.5 each (not just last)
    assert payload.credits["skill_a"] == pytest.approx(0.5)
    assert payload.credits["skill_b"] == pytest.approx(0.5)


# ─────────────────────────────────────────────────────────────────────────────
# Test 8: Audit Trail Integration
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_attribution_emits_event_to_store(attribution_engine_equal, mock_event_store):
    """Attribution outcome is emitted as learning event."""
    payload = await attribution_engine_equal.attribute_outcome(
        strategy_id="strategy_audit",
        decision_id="decision_audit",
        skills=["skill_a", "skill_b"],
        outcome="success",
    )

    # Should have emitted an event
    assert len(mock_event_store.events) == 1
    event = mock_event_store.events[0]

    assert event.payload["attribution_id"] == payload.attribution_id
    assert event.payload["strategy_id"] == "strategy_audit"
    assert event.payload["outcome"] == "success"
    assert event.payload["skills"] == ["skill_a", "skill_b"]
    assert event.payload["credits"] == {"skill_a": 0.5, "skill_b": 0.5}


@pytest.mark.asyncio
async def test_attribution_no_emit_when_disabled(attribution_engine_no_emit, mock_event_store):
    """When emit_events=False, no event is emitted."""
    payload = await attribution_engine_no_emit.attribute_outcome(
        strategy_id="strategy_no_emit",
        decision_id="decision_no_emit",
        skills=["skill_a", "skill_b"],
        outcome="success",
    )

    # Should NOT have emitted
    assert len(mock_event_store.events) == 0


@pytest.mark.asyncio
async def test_attribution_event_includes_rating(attribution_engine_equal, mock_event_store):
    """Attribution event includes rating if provided."""
    payload = await attribution_engine_equal.attribute_outcome(
        strategy_id="strategy_rated",
        decision_id="decision_rated",
        skills=["skill_a"],
        outcome="success",
        rating=5,
    )

    event = mock_event_store.events[0]
    assert event.payload["rating"] == 5


@pytest.mark.asyncio
async def test_attribution_event_includes_reasoning(attribution_engine_equal, mock_event_store):
    """Attribution event includes reasoning if provided."""
    reason = "User rated this outcome as highly successful"
    payload = await attribution_engine_equal.attribute_outcome(
        strategy_id="strategy_reasoned",
        decision_id="decision_reasoned",
        skills=["skill_a"],
        outcome="success",
        reasoning=reason,
    )

    event = mock_event_store.events[0]
    assert event.payload["reasoning"] == reason


@pytest.mark.asyncio
async def test_attribution_event_emission_failure_logged(attribution_engine_equal, mock_event_store):
    """If event emission fails, it is logged but does not raise."""
    mock_event_store.write_error = "Simulated write failure"

    # Should not raise even though emission fails
    payload = await attribution_engine_equal.attribute_outcome(
        strategy_id="strategy_error",
        decision_id="decision_error",
        skills=["skill_a", "skill_b"],
        outcome="success",
    )

    # Payload should still be valid
    assert payload.credits["skill_a"] == pytest.approx(0.5)


# ─────────────────────────────────────────────────────────────────────────────
# Test 9: Tenant Isolation
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tenant_isolation_in_event(mock_event_store):
    """Events are tagged with tenant_id."""
    engine = SkillAttributionEngine(
        tenant_id="tenant_acme",
        event_store=mock_event_store,
        model=AttributionModel.EQUAL,
        emit_events=True,
    )

    payload = await engine.attribute_outcome(
        strategy_id="strategy_tenant",
        decision_id="decision_tenant",
        skills=["skill_a"],
        outcome="success",
    )

    event = mock_event_store.events[0]
    assert event.tenant_id == "tenant_acme"


# ─────────────────────────────────────────────────────────────────────────────
# Test 10: Grade Conversion (Convert Outcome to Grade Deltas)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_grade_skills_from_outcome_success(attribution_engine_equal):
    """Success outcome: grades are positive credits."""
    grades = await attribution_engine_equal.grade_skills_from_outcome(
        strategy_id="strategy_grade",
        decision_id="decision_grade",
        skills=["skill_a", "skill_b"],
        outcome="success",
        session_id="session_1",
    )

    assert grades["skill_a"] == pytest.approx(0.5)
    assert grades["skill_b"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_grade_skills_from_outcome_failure(attribution_engine_equal):
    """Failure outcome: all grades are -1.0 (uniform demotion)."""
    grades = await attribution_engine_equal.grade_skills_from_outcome(
        strategy_id="strategy_grade_fail",
        decision_id="decision_grade_fail",
        skills=["skill_a", "skill_b", "skill_c"],
        outcome="failure",
        session_id="session_1",
    )

    assert grades["skill_a"] == pytest.approx(-1.0)
    assert grades["skill_b"] == pytest.approx(-1.0)
    assert grades["skill_c"] == pytest.approx(-1.0)


@pytest.mark.asyncio
async def test_grade_skills_from_outcome_partial(attribution_engine_equal):
    """Partial outcome: grades are positive but reduced."""
    grades = await attribution_engine_equal.grade_skills_from_outcome(
        strategy_id="strategy_grade_partial",
        decision_id="decision_grade_partial",
        skills=["skill_a", "skill_b"],
        outcome="partial",
        session_id="session_1",
    )

    # Partial: 0.5 / 2 = 0.25 each
    assert grades["skill_a"] == pytest.approx(0.25)
    assert grades["skill_b"] == pytest.approx(0.25)


# ─────────────────────────────────────────────────────────────────────────────
# Test 11: Chained Strategy Phases
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_attribute_strategy_chain_single_phase(attribution_engine_equal):
    """Strategy chain with single phase: attributes all skills equally."""
    results = await attribution_engine_equal.attribute_strategy_chain(
        strategy_id="strategy_chain_full",
        decision_id="decision_chain_full",
        skills_by_phase={
            "diagnosis": ["skill_a", "skill_b"],
        },
        outcome="success",
        session_id="session_1",
    )

    assert "diagnosis" in results
    payload = results["diagnosis"]
    assert payload.credits["skill_a"] == pytest.approx(0.5)
    assert payload.credits["skill_b"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_attribute_strategy_chain_multiple_phases(attribution_engine_equal):
    """Strategy chain with multiple phases: attributes each phase."""
    results = await attribution_engine_equal.attribute_strategy_chain(
        strategy_id="strategy_multi_phase",
        decision_id="decision_multi_phase",
        skills_by_phase={
            "diagnosis": ["skill_a"],
            "fix": ["skill_b"],
            "verify": ["skill_c"],
        },
        outcome="success",
        session_id="session_1",
    )

    # Each phase has its own attribution
    assert len(results) == 3

    # Diagnosis: 1 skill gets 1.0
    assert results["diagnosis"].credits["skill_a"] == pytest.approx(1.0)

    # Fix: 1 skill gets 1.0
    assert results["fix"].credits["skill_b"] == pytest.approx(1.0)

    # Verify: 1 skill gets 1.0
    assert results["verify"].credits["skill_c"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_attribute_strategy_chain_multi_skill_phase(attribution_engine_equal):
    """Strategy chain: phase with multiple skills attributes equally within phase."""
    results = await attribution_engine_equal.attribute_strategy_chain(
        strategy_id="strategy_multi_skill_phase",
        decision_id="decision_multi_skill_phase",
        skills_by_phase={
            "diagnosis": ["skill_a", "skill_b", "skill_c"],
            "fix": ["skill_d"],
        },
        outcome="success",
        session_id="session_1",
    )

    # Diagnosis: 3 skills, each gets 1/3
    expected_diagnosis = 1.0 / 3.0
    assert results["diagnosis"].credits["skill_a"] == pytest.approx(expected_diagnosis)
    assert results["diagnosis"].credits["skill_b"] == pytest.approx(expected_diagnosis)
    assert results["diagnosis"].credits["skill_c"] == pytest.approx(expected_diagnosis)

    # Fix: 1 skill gets 1.0
    assert results["fix"].credits["skill_d"] == pytest.approx(1.0)


# ─────────────────────────────────────────────────────────────────────────────
# Test 12: Invalid Inputs & Edge Cases
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_skills_list_raises(attribution_engine_equal):
    """Empty skills list raises ValueError."""
    with pytest.raises(ValueError, match="at least one skill"):
        await attribution_engine_equal.attribute_outcome(
            strategy_id="strategy_empty",
            decision_id="decision_empty",
            skills=[],
            outcome="success",
        )


@pytest.mark.asyncio
async def test_invalid_outcome_raises(attribution_engine_equal):
    """Invalid outcome value raises ValueError."""
    with pytest.raises(ValueError, match="Invalid outcome"):
        await attribution_engine_equal.attribute_outcome(
            strategy_id="strategy_invalid",
            decision_id="decision_invalid",
            skills=["skill_a"],
            outcome="unknown_outcome",
        )


@pytest.mark.asyncio
async def test_attribution_payload_frozen(attribution_engine_equal):
    """AttributionPayload is immutable (frozen dataclass)."""
    payload = await attribution_engine_equal.attribute_outcome(
        strategy_id="strategy_frozen",
        decision_id="decision_frozen",
        skills=["skill_a"],
        outcome="success",
    )

    # Should not be able to modify
    with pytest.raises((AttributeError, TypeError)):
        payload.outcome = "failure"


@pytest.mark.asyncio
async def test_large_skill_count(attribution_engine_equal):
    """Large number of skills: credit is still normalized."""
    skills = [f"skill_{i}" for i in range(100)]
    payload = await attribution_engine_equal.attribute_outcome(
        strategy_id="strategy_large",
        decision_id="decision_large",
        skills=skills,
        outcome="success",
    )

    # Each gets 1/100 credit
    expected = 1.0 / 100.0
    for skill in skills:
        assert payload.credits[skill] == pytest.approx(expected)

    # Total is 1.0
    assert sum(payload.credits.values()) == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_empty_skills_chain_raises(attribution_engine_equal):
    """Empty skills_by_phase raises ValueError."""
    with pytest.raises(ValueError, match="at least one phase"):
        await attribution_engine_equal.attribute_strategy_chain(
            strategy_id="strategy_empty_chain",
            decision_id="decision_empty_chain",
            skills_by_phase={},
            outcome="success",
            session_id="session_1",
        )


@pytest.mark.asyncio
async def test_chain_with_empty_phases_raises(attribution_engine_equal):
    """skills_by_phase with empty phases raises ValueError."""
    with pytest.raises(ValueError, match="at least one skill"):
        await attribution_engine_equal.attribute_strategy_chain(
            strategy_id="strategy_empty_phases",
            decision_id="decision_empty_phases",
            skills_by_phase={
                "phase_1": [],
                "phase_2": [],
            },
            outcome="success",
            session_id="session_1",
        )


@pytest.mark.asyncio
async def test_attribution_id_is_unique(attribution_engine_equal):
    """Each attribution gets a unique attribution_id."""
    payload1 = await attribution_engine_equal.attribute_outcome(
        strategy_id="strategy_unique_1",
        decision_id="decision_unique_1",
        skills=["skill_a"],
        outcome="success",
    )

    payload2 = await attribution_engine_equal.attribute_outcome(
        strategy_id="strategy_unique_2",
        decision_id="decision_unique_2",
        skills=["skill_a"],
        outcome="success",
    )

    assert payload1.attribution_id != payload2.attribution_id


@pytest.mark.asyncio
async def test_attribution_timestamp_set(attribution_engine_equal):
    """Attribution payload has timestamp set."""
    payload = await attribution_engine_equal.attribute_outcome(
        strategy_id="strategy_timestamp",
        decision_id="decision_timestamp",
        skills=["skill_a"],
        outcome="success",
    )

    assert payload.timestamp_utc is not None
    assert isinstance(payload.timestamp_utc, datetime)

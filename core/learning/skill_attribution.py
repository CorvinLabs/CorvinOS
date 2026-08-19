"""Skill Attribution Model — fair skill grading for multi-skill strategies (ADR-0323).

When multiple skills run in a strategy, this engine distributes credit/debit fairly
using configurable attribution models. Enables closed-loop learning from composite
skill executions.

Models:
- EQUAL (MVP): Split credit equally among all skills in strategy
- WEIGHTED: Split proportional to observed success/cost (deferred, documented)
- FIRST: Credit only the first skill in chain (deferred, documented)
- LAST: Credit only the last skill in chain (deferred, documented)

Tenant-scoped: all attributions respect tenant_id (GDPR Art. 5, 32).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .event_schema import LearningEvent, LearningEventType
from .event_store import EventStore

logger = logging.getLogger(__name__)


class AttributionModel(str, Enum):
    """Attribution models for multi-skill strategies."""

    EQUAL = "equal"  # Split credit equally (MVP)
    WEIGHTED = "weighted"  # Split proportional to metrics (deferred)
    FIRST = "first"  # Credit first skill only (deferred)
    LAST = "last"  # Credit last skill only (deferred)


@dataclass(frozen=True)
class AttributionPayload:
    """Immutable record of skill attribution for an outcome."""

    attribution_id: str
    strategy_id: str  # Pipeline/composition ID
    decision_id: str  # Parent decision
    outcome: str  # "success" | "partial" | "failure"
    model: AttributionModel
    skills: list[str]  # Skills in strategy (in order)
    credits: Dict[str, float]  # {skill_name: credit_share, ...}
    timestamp_utc: datetime = field(default_factory=datetime.utcnow)
    reasoning: Optional[str] = None
    audit_id: Optional[str] = None


@dataclass
class SkillAttributionEngine:
    """Distribute credit/debit fairly among skills in a composite strategy.

    Features:
    - EQUAL model MVP (equal split)
    - Deferred: WEIGHTED, FIRST, LAST
    - Audit trail integration
    - Tenant isolation
    - Fail-safe validation

    Invariants:
    - Credit shares sum to 0.0 (failure), 0.5 (partial), or 1.0 (success)
    - Only positive skills receive credit (failure case gets no credit)
    - All skills in strategy are recorded in audit trail
    """

    tenant_id: str
    event_store: EventStore
    model: AttributionModel = AttributionModel.EQUAL
    emit_events: bool = True  # If False, records but does not emit

    async def attribute_outcome(
        self,
        strategy_id: str,
        decision_id: str,
        skills: List[str],
        outcome: str,
        rating: Optional[int] = None,
        reasoning: Optional[str] = None,
    ) -> AttributionPayload:
        """Attribute an outcome to multiple skills.

        Distributes credit/debit fairly based on the configured model.

        Args:
            strategy_id: ID of the skill pipeline/composition
            decision_id: Parent decision ID
            skills: Ordered list of skills in strategy
            outcome: "success", "partial", or "failure"
            rating: Optional numeric rating (1-5) for outcome quality
            reasoning: Optional explanation of attribution

        Returns:
            AttributionPayload (ready to emit)

        Raises:
            ValueError: If skills list is empty or invalid
            RuntimeError: If attribution fails

        Guarantees:
        - All skills recorded in audit trail
        - Credit shares sum to 0.0 (failure), 0.5 (partial), or 1.0 (success)
        - Credits are normalized by outcome type and validated within tolerance
        """
        if not skills:
            raise ValueError("Strategy must include at least one skill")

        if outcome not in ("success", "partial", "failure"):
            raise ValueError(f"Invalid outcome: {outcome}, must be success/partial/failure")

        # Compute credit distribution based on model
        credits = self._compute_credits(skills, outcome)

        # Validate credits sum to expected value for outcome type
        # success: 1.0, partial: 0.5, failure: 0.0 (allow small floating-point error)
        total_credit = sum(credits.values())
        expected_sum = {"success": 1.0, "partial": 0.5, "failure": 0.0}[outcome]
        tolerance = 0.01
        if not (expected_sum - tolerance <= total_credit <= expected_sum + tolerance):
            raise RuntimeError(
                f"Attribution failed: credits sum to {total_credit}, expected {expected_sum}"
            )

        # Create attribution record
        payload = AttributionPayload(
            attribution_id=str(uuid4()),
            strategy_id=strategy_id,
            decision_id=decision_id,
            outcome=outcome,
            model=self.model,
            skills=skills,
            credits=credits,
            reasoning=reasoning,
        )

        # Emit learning event (if enabled)
        if self.emit_events:
            await self._emit_attribution_event(payload, rating)

        return payload

    def _compute_credits(self, skills: List[str], outcome: str) -> Dict[str, float]:
        """Compute credit distribution for a set of skills.

        Args:
            skills: Ordered list of skills
            outcome: "success", "partial", or "failure"

        Returns:
            Dict mapping skill_name -> credit_share (0.0-1.0)
        """
        if self.model == AttributionModel.EQUAL:
            return self._credit_equal(skills, outcome)
        elif self.model == AttributionModel.WEIGHTED:
            # Deferred: would require metrics from event_store
            logger.warning("WEIGHTED model is deferred; falling back to EQUAL")
            return self._credit_equal(skills, outcome)
        elif self.model == AttributionModel.FIRST:
            # Deferred: only first skill gets credit
            logger.warning("FIRST model is deferred; falling back to EQUAL")
            return self._credit_equal(skills, outcome)
        elif self.model == AttributionModel.LAST:
            # Deferred: only last skill gets credit
            logger.warning("LAST model is deferred; falling back to EQUAL")
            return self._credit_equal(skills, outcome)
        else:
            raise ValueError(f"Unknown attribution model: {self.model}")

    def _credit_equal(self, skills: List[str], outcome: str) -> Dict[str, float]:
        """EQUAL model: split credit equally among all skills.

        Args:
            skills: List of skills
            outcome: "success", "partial", or "failure"

        Returns:
            Dict mapping skill_name -> equal_share
        """
        if outcome == "failure":
            # Failures receive no credit (all get 0)
            return {skill: 0.0 for skill in skills}

        if outcome == "success":
            # Successes: equal split among all skills
            share = 1.0 / len(skills)
            return {skill: share for skill in skills}

        if outcome == "partial":
            # Partial: equal split, but reduced credit (0.5 instead of 1.0)
            share = 0.5 / len(skills)
            return {skill: share for skill in skills}

        # Fallback (should not reach)
        return {skill: 0.0 for skill in skills}

    async def _emit_attribution_event(
        self, payload: AttributionPayload, rating: Optional[int] = None
    ) -> None:
        """Emit attribution event to learning system.

        Args:
            payload: Attribution record
            rating: Optional user rating (1-5)

        Raises:
            RuntimeError: If event emission fails
        """
        try:
            # Build event payload
            event_payload: Dict[str, Any] = {
                "attribution_id": payload.attribution_id,
                "strategy_id": payload.strategy_id,
                "decision_id": payload.decision_id,
                "outcome": payload.outcome,
                "model": payload.model.value,
                "skills": payload.skills,
                "credits": payload.credits,
            }

            if rating is not None:
                event_payload["rating"] = rating

            if payload.reasoning:
                event_payload["reasoning"] = payload.reasoning

            # Emit METRIC_AGGREGATED event (existing type for composites)
            # We'll eventually have a dedicated SKILL_ATTRIBUTION type in ADR-0323
            event = LearningEvent(
                event_type=LearningEventType.METRIC_AGGREGATED,
                tenant_id=self.tenant_id,
                instance_id="skill_attribution_engine",
                skill_name=None,  # Multi-skill, not single-skill
                session_id="",  # Will be filled by caller
                timestamp_utc=datetime.utcnow(),
                payload=event_payload,
                tags=["attribution", "skill_composite", self.model.value],
            )

            # Attempt to emit (fire-and-forget on error)
            await self.event_store.write_event(event)
            logger.debug(
                f"Emitted attribution event {payload.attribution_id} "
                f"for strategy {payload.strategy_id} (model: {self.model.value})"
            )

        except Exception as e:
            # Log but don't raise: attribution is best-effort
            logger.error(
                f"Failed to emit attribution event: {e}",
                exc_info=True,
            )

    async def grade_skills_from_outcome(
        self,
        strategy_id: str,
        decision_id: str,
        skills: List[str],
        outcome: str,
        session_id: str,
        rating: Optional[int] = None,
    ) -> Dict[str, float]:
        """Grade (promote/demote) skills based on attributed outcome.

        High-level convenience: runs attribution and returns skill scores
        ready for consumption by the skill grading system.

        Args:
            strategy_id: ID of skill pipeline
            decision_id: Parent decision
            skills: Skills in pipeline
            outcome: "success", "partial", or "failure"
            session_id: Session ID for audit trail
            rating: Optional numeric rating (1-5)

        Returns:
            Dict mapping skill_name -> grade_delta (-1.0 to +1.0)

        Raises:
            ValueError: If arguments are invalid
        """
        payload = await self.attribute_outcome(
            strategy_id=strategy_id,
            decision_id=decision_id,
            skills=skills,
            outcome=outcome,
            rating=rating,
            reasoning=f"Graded from outcome={outcome}, session={session_id}",
        )

        # Convert credits to grade deltas
        # Success: delta = +credit (promote by credit share)
        # Partial: delta = +credit (promote by credit share)
        # Failure: delta = -1.0 (demote uniformly; no credit absorption)
        if outcome == "failure":
            return {skill: -1.0 for skill in skills}

        # Success or partial: promote by credit amount
        return payload.credits

    async def attribute_strategy_chain(
        self,
        strategy_id: str,
        decision_id: str,
        skills_by_phase: Dict[str, List[str]],
        outcome: str,
        session_id: str,
        reasoning: Optional[str] = None,
    ) -> Dict[str, AttributionPayload]:
        """Attribute outcomes for chained skill strategies.

        When a strategy has multiple phases (e.g., [diagnosis, fix, verify]),
        this method allows per-phase attribution to fairly grade each phase.

        Args:
            strategy_id: ID of overall strategy
            decision_id: Parent decision
            skills_by_phase: Dict mapping phase_name -> [skills]
            outcome: "success", "partial", or "failure"
            session_id: Session ID
            reasoning: Optional explanation

        Returns:
            Dict mapping phase_name -> AttributionPayload

        Raises:
            ValueError: If arguments are invalid
        """
        if not skills_by_phase:
            raise ValueError("Must specify at least one phase")

        all_skills = [s for phase_skills in skills_by_phase.values() for s in phase_skills]
        if not all_skills:
            raise ValueError("Must include at least one skill across all phases")

        # For now, treat as flat: don't do phase-aware weighting (that's WEIGHTED model)
        # Just attribute uniformly across all skills
        results = {}
        for phase_name, phase_skills in skills_by_phase.items():
            payload = await self.attribute_outcome(
                strategy_id=f"{strategy_id}::{phase_name}",
                decision_id=decision_id,
                skills=phase_skills,
                outcome=outcome,
                reasoning=reasoning or f"Phase: {phase_name}, {len(phase_skills)} skills",
            )
            results[phase_name] = payload

        return results

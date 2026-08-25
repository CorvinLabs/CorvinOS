"""ConsistencyValidator: Detect contradictions in task decisions.

Monitors:
- Task state from CheckpointManager (carries key decisions)
- Extraction: 5-7 key decisions per phase (structured parse)
- Detection: check for logical contradictions

Alert: If conflicts detected → "entropy_detected"

Implementation:
- Extract decision statements from phase output
- Check for semantic contradictions (word-based heuristics)
- Report conflicting decisions

ADR-0407: Session Manager Phase 2.2
Depends on: base.MonitorBase
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .base import MonitorBase, MonitorAlert, AlertType, MonitorConfig, MonitorState

logger = logging.getLogger(__name__)


@dataclass
class DecisionStatement:
    """Represents a key decision in a task."""

    text: str
    phase: str
    iteration: int
    confidence: float = 0.8


@dataclass
class ConsistencyValidatorState(MonitorState):
    """Extended state for ConsistencyValidator."""

    decisions: List[DecisionStatement] = field(default_factory=list)
    conflicting_pairs: List[Tuple[str, str]] = field(default_factory=list)
    phase: str = ""


class ConsistencyValidator(MonitorBase):
    """Detect contradictions in task decisions.

    Extracts key decisions from phase output and detects logical contradictions.
    Alert: If conflicts detected → "entropy_detected"

    Configuration:
    - decisions_per_phase: Number of decisions to track per phase, default 7
    - contradiction_threshold: Confidence threshold for contradiction detection, default 0.6
    """

    def __init__(self, config: Optional[MonitorConfig] = None):
        """Initialize ConsistencyValidator.

        Args:
            config: Optional configuration
        """
        super().__init__("consistency_validator", config)
        self.decisions_per_phase = 7
        self.contradiction_threshold = 0.6

    def add_decision(
        self, session_id: str, task_id: str, tenant_id: str,
        decision_text: str, phase: str, iteration: int
    ) -> None:
        """Add a decision to track.

        Args:
            session_id: Session ID
            task_id: Task ID
            tenant_id: Tenant ID
            decision_text: Decision statement
            phase: Current phase name
            iteration: Iteration number
        """
        state = self.create_or_get_consistency_state(session_id, task_id, tenant_id)
        state.phase = phase

        decision = DecisionStatement(
            text=decision_text,
            phase=phase,
            iteration=iteration,
        )
        state.decisions.append(decision)

        # Keep only last N decisions per phase
        decisions_this_phase = [d for d in state.decisions if d.phase == phase]
        if len(decisions_this_phase) > self.decisions_per_phase:
            # Remove oldest decision from this phase
            for i, d in enumerate(state.decisions):
                if d.phase == phase:
                    state.decisions.pop(i)
                    break

    def check(self, state: MonitorState) -> Optional[MonitorAlert]:
        """Check for contradictions.

        Args:
            state: MonitorState for the session

        Returns:
            MonitorAlert if contradictions detected, None otherwise
        """
        if not isinstance(state, ConsistencyValidatorState):
            return None

        if len(state.decisions) < 2:
            return None

        # Find contradictions
        contradictions = self._find_contradictions(state.decisions)

        if contradictions:
            state.conflicting_pairs.extend(contradictions)

            # Build conflict summary
            conflict_summary = []
            for decision1, decision2 in contradictions:
                conflict_summary.append(
                    f"'{decision1.text[:80]}...' conflicts with '{decision2.text[:80]}...'"
                )

            alert = MonitorAlert(
                alert_type=AlertType.ENTROPY_DETECTED,
                session_id=state.session_id,
                task_id=state.task_id,
                tenant_id=state.tenant_id,
                severity="critical",
                reason=f"Detected {len(contradictions)} contradictory decision(s)",
                metadata={
                    "contradiction_count": len(contradictions),
                    "conflicts": conflict_summary[:5],  # Top 5 conflicts
                    "total_decisions_tracked": len(state.decisions),
                    "phase": state.phase,
                },
            )

            logger.warning(
                f"{self.name}: {state.session_id} detected "
                f"{len(contradictions)} contradiction(s)"
            )

            return alert

        return None

    def _find_contradictions(
        self, decisions: List[DecisionStatement]
    ) -> List[Tuple[DecisionStatement, DecisionStatement]]:
        """Find contradictory decisions.

        Uses simple word-based heuristics to detect common contradiction patterns:
        - "We decided to X" vs "We decided NOT to X"
        - "X is required" vs "X is optional"
        - "X will happen" vs "X won't happen"

        Args:
            decisions: List of decisions to analyze

        Returns:
            List of (decision1, decision2) tuples that contradict
        """
        contradictions = []

        for i, decision1 in enumerate(decisions):
            for decision2 in decisions[i + 1 :]:
                if self._are_contradictory(decision1.text, decision2.text):
                    contradictions.append((decision1, decision2))

        return contradictions

    def _are_contradictory(self, text1: str, text2: str) -> bool:
        """Check if two decisions are contradictory.

        Uses word-based heuristics:
        1. Extract key subject (first 5 words)
        2. Check for negation words (not, no, never, won't, can't, shouldn't)
        3. Compare polarity

        Args:
            text1: First decision text
            text2: Second decision text

        Returns:
            True if contradictory, False otherwise
        """
        # Normalize
        text1_lower = text1.lower()
        text2_lower = text2.lower()

        # Extract key subject (first few words)
        words1 = text1_lower.split()[:5]
        words2 = text2_lower.split()[:5]

        # Check if they talk about the same subject
        subject_overlap = len(set(words1) & set(words2))
        if subject_overlap < 2:
            return False

        # Check for negation polarity
        negation_words = {"not", "no", "never", "won't", "can't", "shouldn't", "don't"}
        has_negation1 = any(word in text1_lower for word in negation_words)
        has_negation2 = any(word in text2_lower for word in negation_words)

        # Contradiction if one is negated and the other isn't (same subject)
        return has_negation1 != has_negation2

    def create_or_get_consistency_state(
        self, session_id: str, task_id: str, tenant_id: str
    ) -> ConsistencyValidatorState:
        """Create or get ConsistencyValidatorState for a session.

        Args:
            session_id: Session ID
            task_id: Task ID
            tenant_id: Tenant ID

        Returns:
            ConsistencyValidatorState for the session
        """
        if session_id not in self.session_states:
            self.session_states[session_id] = ConsistencyValidatorState(
                session_id=session_id,
                task_id=task_id,
                tenant_id=tenant_id,
            )
        return self.session_states[session_id]

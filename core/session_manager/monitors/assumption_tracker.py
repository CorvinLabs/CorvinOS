"""AssumptionTracker: Validate unvalidated assumptions.

Monitors:
- Phase output (text/transcript)
- Patterns: "Assuming that...", "We expect...", "Based on X, we infer...", "It's likely that..."
- Detection: parse assumptions, check for follow-up validation in same phase

Alert: If unvalidated assumption found → "assumption_unvalidated"

Implementation:
- Pattern matching for assumption-introducing phrases
- Track validation status within phase
- Alert if assumption still unvalidated after N iterations

ADR-0407: Session Manager Phase 2.2
Depends on: base.MonitorBase
"""

import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional, Set

from .base import MonitorBase, MonitorAlert, AlertType, MonitorConfig, MonitorState

logger = logging.getLogger(__name__)


@dataclass
class Assumption:
    """Represents an assumption made in a task."""

    text: str
    iteration_found: int
    phase: str
    validated: bool = False
    validation_iteration: Optional[int] = None


@dataclass
class AssumptionTrackerState(MonitorState):
    """Extended state for AssumptionTracker."""

    assumptions: List[Assumption] = field(default_factory=list)
    phase: str = ""
    iteration: int = 0


class AssumptionTracker(MonitorBase):
    """Validate unvalidated assumptions.

    Extracts assumptions from phase output and checks if they're validated.
    Alert: If unvalidated assumption found → "assumption_unvalidated"

    Configuration:
    - assumption_patterns: List of regex patterns for assumption detection
    - validation_keywords: Keywords indicating validation
    - max_iterations_to_validate: Iterations allowed before alert, default 10
    """

    # Regex patterns for assumptions
    ASSUMPTION_PATTERNS = [
        r"assuming\s+that\s+([^.!?]*[.!?])",
        r"we\s+expect\s+([^.!?]*[.!?])",
        r"based\s+on\s+\w+,?\s+(?:we\s+)?(?:infer|assume|believe)\s+([^.!?]*[.!?])",
        r"it'?s\s+(?:likely|probable)\s+that\s+([^.!?]*[.!?])",
        r"we\s+assume\s+([^.!?]*[.!?])",
        r"assumption:\s+([^.!?]*[.!?])",
        r"(?:let's|lets)\s+assume\s+([^.!?]*[.!?])",
    ]

    # Keywords indicating validation
    VALIDATION_KEYWORDS = {
        "verified",
        "validated",
        "confirmed",
        "tested",
        "checked",
        "proven",
        "demonstrated",
        "observed",
        "found",
        "correct",
        "valid",
        "accurate",
        "correct",
    }

    def __init__(self, config: Optional[MonitorConfig] = None):
        """Initialize AssumptionTracker.

        Args:
            config: Optional configuration
        """
        super().__init__("assumption_tracker", config)
        self.max_iterations_to_validate = 10

    def process_iteration(
        self,
        session_id: str,
        task_id: str,
        tenant_id: str,
        iteration_text: str,
        phase: str,
        iteration: int,
    ) -> None:
        """Process an iteration of work.

        Extracts assumptions and checks for validations.

        Args:
            session_id: Session ID
            task_id: Task ID
            tenant_id: Tenant ID
            iteration_text: Text output from the iteration
            phase: Current phase name
            iteration: Iteration number
        """
        state = self.create_or_get_assumption_state(session_id, task_id, tenant_id)
        state.phase = phase
        state.iteration = iteration

        # Extract new assumptions
        new_assumptions = self._extract_assumptions(iteration_text, phase, iteration)
        state.assumptions.extend(new_assumptions)

        # Check for validations
        self._update_validations(state, iteration_text, iteration)

    def check(self, state: MonitorState) -> Optional[MonitorAlert]:
        """Check for unvalidated assumptions.

        Args:
            state: MonitorState for the session

        Returns:
            MonitorAlert if unvalidated assumption found, None otherwise
        """
        if not isinstance(state, AssumptionTrackerState):
            return None

        # Find unvalidated assumptions that have been waiting too long
        unvalidated = [
            a for a in state.assumptions
            if not a.validated
            and (state.iteration - a.iteration_found) >= self.max_iterations_to_validate
        ]

        if not unvalidated:
            return None

        # Build alert
        unvalidated_texts = [a.text[:100] for a in unvalidated[:5]]
        alert = MonitorAlert(
            alert_type=AlertType.ASSUMPTION_UNVALIDATED,
            session_id=state.session_id,
            task_id=state.task_id,
            tenant_id=state.tenant_id,
            severity="warning",
            reason=f"Found {len(unvalidated)} unvalidated assumption(s) after "
            f"{self.max_iterations_to_validate}+ iterations",
            metadata={
                "unvalidated_count": len(unvalidated),
                "unvalidated_assumptions": unvalidated_texts,
                "phase": state.phase,
                "iteration": state.iteration,
            },
        )

        logger.warning(
            f"{self.name}: {state.session_id} found {len(unvalidated)} "
            f"unvalidated assumption(s)"
        )

        return alert

    def _extract_assumptions(
        self, text: str, phase: str, iteration: int
    ) -> List[Assumption]:
        """Extract assumptions from text.

        Args:
            text: Text to analyze
            phase: Current phase name
            iteration: Iteration number

        Returns:
            List of Assumption objects found
        """
        assumptions = []
        text_lower = text.lower()

        for pattern in self.ASSUMPTION_PATTERNS:
            for match in re.finditer(pattern, text_lower, re.IGNORECASE):
                assumption_text = match.group(1) if match.groups() else match.group(0)
                # Clean up whitespace
                assumption_text = " ".join(assumption_text.split())

                assumption = Assumption(
                    text=assumption_text,
                    iteration_found=iteration,
                    phase=phase,
                )
                assumptions.append(assumption)

        return assumptions

    def _update_validations(
        self, state: AssumptionTrackerState, iteration_text: str, iteration: int
    ) -> None:
        """Update validation status of assumptions.

        Args:
            state: AssumptionTrackerState
            iteration_text: Text from current iteration
            iteration: Iteration number
        """
        text_lower = iteration_text.lower()

        # Check if any validation keywords appear
        has_validation = any(kw in text_lower for kw in self.VALIDATION_KEYWORDS)

        if not has_validation:
            return

        # Mark unvalidated assumptions from current phase as validated
        for assumption in state.assumptions:
            if (
                not assumption.validated
                and assumption.phase == state.phase
                and iteration - assumption.iteration_found < self.max_iterations_to_validate
            ):
                # Simple heuristic: if validation keywords appear, mark as validated
                assumption.validated = True
                assumption.validation_iteration = iteration
                logger.debug(
                    f"Marked assumption as validated in iteration {iteration}: "
                    f"{assumption.text[:50]}..."
                )

    def create_or_get_assumption_state(
        self, session_id: str, task_id: str, tenant_id: str
    ) -> AssumptionTrackerState:
        """Create or get AssumptionTrackerState for a session.

        Args:
            session_id: Session ID
            task_id: Task ID
            tenant_id: Tenant ID

        Returns:
            AssumptionTrackerState for the session
        """
        if session_id not in self.session_states:
            self.session_states[session_id] = AssumptionTrackerState(
                session_id=session_id,
                task_id=task_id,
                tenant_id=tenant_id,
            )
        return self.session_states[session_id]

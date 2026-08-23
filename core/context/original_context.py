"""Original Context Layer — Immutable user-stated goal + constraints.

Captured at session start, never modified by pipeline.
Provides the foundation for context preservation.

ADR-0399: Context-Pipeline v2 — Preservation+Additive Model
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from enum import Enum

logger = logging.getLogger(__name__)


class ContextScope(Enum):
    """Where this context applies."""
    SESSION = "session"
    PROJECT = "project"
    USER = "user"


@dataclass(frozen=True)
class OriginalContext:
    """Immutable snapshot of user's original goal + constraints.

    Captured from user's first message and explicit session directives.
    Never modified after creation; protected against pipeline overwrites.
    """

    user_prompt: str
    """What user literally asked for."""

    goal: str
    """Inferred primary goal (derived from user_prompt)."""

    constraints: list[str]
    """Explicit constraints: 'Don't do X', 'Always do Y'."""

    project_scope: str
    """Where this applies: file path, module name, project area."""

    user_preferences: dict
    """From user memory: tone, language, style, decision-making."""

    task_directives: dict
    """Explicit directives: 'focus': X, 'ignore': Y."""

    timestamp: datetime
    """Session start time."""

    session_id: str
    """Unique session identifier."""

    def is_valid(self) -> bool:
        """Validate that all required fields are set."""
        return all([
            self.user_prompt,
            self.goal,
            self.project_scope,
            self.session_id,
        ])

    def summary(self) -> str:
        """Human-readable summary of original context."""
        constraints_str = ", ".join(self.constraints) if self.constraints else "(none)"
        return f"""ORIGINAL CONTEXT
Goal: {self.goal}
Scope: {self.project_scope}
Constraints: {constraints_str}
User Preferences: {self.user_preferences}
Task Directives: {self.task_directives}"""

    def to_system_prompt_section(self) -> str:
        """Render as system prompt section (immutable, labeled clearly)."""
        return f"""## ORIGINAL CONTEXT [IMMUTABLE — User's Stated Goal]
Goal: {self.goal}
Scope: {self.project_scope}
Constraints: {', '.join(self.constraints) if self.constraints else '(none)'}
User Preferences: {self.user_preferences}
Task Directives: {self.task_directives}

[This layer is protected. Pipeline context only ADDS to this, never replaces.]
"""


def capture_original_context(
    user_prompt: str,
    session_id: str,
    project_scope: str = "default",
    user_preferences: Optional[dict] = None,
    task_directives: Optional[dict] = None,
) -> OriginalContext:
    """Capture original context from user's first message.

    Args:
        user_prompt: User's literal first message
        session_id: Unique session identifier
        project_scope: Where this applies (file path, module, etc.)
        user_preferences: Tone, language, style (from memory if available)
        task_directives: Explicit 'focus', 'ignore' directives

    Returns:
        Frozen OriginalContext snapshot
    """
    # Simple goal inference: take first sentence or clause
    goal = user_prompt.split('\n')[0].strip()
    if len(goal) > 200:
        goal = goal[:197] + "..."

    context = OriginalContext(
        user_prompt=user_prompt,
        goal=goal,
        constraints=[],  # Parse from prompt if contains "don't", "never", "always"
        project_scope=project_scope,
        user_preferences=user_preferences or {},
        task_directives=task_directives or {},
        timestamp=datetime.utcnow(),
        session_id=session_id,
    )

    if not context.is_valid():
        logger.error(f"Invalid original context: {context}")
        raise ValueError("Failed to capture valid original context")

    logger.info(f"Captured original context for session {session_id}: {goal}")
    return context

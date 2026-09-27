"""Deterministic error-recovery heuristic for the VibeEngine.

This is the heuristic that used to live in ``hermes_bridge.py`` as the
"Hermes unavailable" fallback. The Hermes client it bridged to was never
wired and local-model inference was removed per ADR-2091, so the heuristic
is now the only diagnosis path: no model call, no network.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class RecoveryDiagnosis:
    """A recovery suggestion derived from an error."""
    primary_strategy: str  # "retry", "decompose", "fallback", "escalate"
    confidence: float  # 0-1
    reason: str
    fallback_skill: Optional[str] = None
    recommendations: Optional[list] = None


# Diagnosis strategy → Vibe Recovery strategy.
_STRATEGY_MAP = {
    "retry": "retry",
    "decompose_task": "decompose",
    "fallback": "fallback",
    "timeout_increase": "retry",
    "escalate": "escalate",
}

# Substring sets for the heuristic. Kept as data next to the exception types
# so the two stay in step.
#
# The original heuristic matched only the exact tokens "timeout" /
# "complexity" / "too large", and therefore missed the phrasings Python and
# this codebase actually produce — "timed out", "too complex", "deadline
# exceeded". Those all fell through to `escalate`, so a long autonomous run
# that hit its wall clock asked a human for help instead of retrying, which
# is exactly the behaviour that stops a long task from finishing on its own.
_RETRY_HINTS = ("timeout", "timed out", "timed-out", "deadline", "network",
                "connection", "temporarily unavailable", "rate limit",
                "try again")
_DECOMPOSE_HINTS = ("complexity", "too complex", "too large", "too big",
                    "context length", "token limit", "exceeds", "too long")


def diagnose_error(error: Exception, context: Dict[str, Any]) -> RecoveryDiagnosis:
    """Heuristic diagnosis of *error* (never raises, never calls a model)."""
    try:
        return _diagnose(error, context or {})
    except Exception as e:  # noqa: BLE001 — diagnosis must never break recovery
        logger.error(f"Recovery diagnosis failed: {e}")
        return RecoveryDiagnosis(primary_strategy="escalate", confidence=0.0,
                                 reason="diagnosis failed (escalate)")


def _diagnose(error: Exception, context: Dict[str, Any]) -> RecoveryDiagnosis:
    error_msg = str(error).lower()
    fallback_skills = context.get("fallback_skills", [])

    # Classify on the exception TYPE first: it is unambiguous where a
    # message substring is guesswork, and it cannot drift with wording.
    if isinstance(error, (TimeoutError, ConnectionError, OSError)) and \
            not isinstance(error, (NotADirectoryError, IsADirectoryError,
                                   FileNotFoundError, PermissionError)):
        return RecoveryDiagnosis(
            primary_strategy="retry",
            confidence=0.7,
            reason=f"Transient {type(error).__name__} (fallback heuristic)"
        )
    if isinstance(error, MemoryError):
        return RecoveryDiagnosis(
            primary_strategy="decompose",
            confidence=0.7,
            reason="Ran out of memory — split the work (fallback heuristic)"
        )

    if any(h in error_msg for h in _RETRY_HINTS):
        return RecoveryDiagnosis(
            primary_strategy="retry",
            confidence=0.6,
            reason="Transient network/timeout error (fallback heuristic)"
        )
    if any(h in error_msg for h in _DECOMPOSE_HINTS):
        return RecoveryDiagnosis(
            primary_strategy="decompose",
            confidence=0.65,
            reason="Task complexity exceeds skill capacity (fallback heuristic)"
        )
    if "not found" in error_msg and fallback_skills:
        return RecoveryDiagnosis(
            primary_strategy="fallback",
            confidence=0.7,
            reason="Skill not found, fallback available (fallback heuristic)",
            fallback_skill=fallback_skills[0]
        )
    return RecoveryDiagnosis(
        primary_strategy="escalate",
        confidence=0.5,
        reason=f"Unable to diagnose: {error_msg} (fallback: escalate)"
    )


def map_to_recovery_strategy(diagnosis: RecoveryDiagnosis) -> str:
    """Map a diagnosis to a Vibe Recovery strategy."""
    return _STRATEGY_MAP.get(diagnosis.primary_strategy, diagnosis.primary_strategy)

"""Phase 3d: Hermes-Healing Integration for AI-Driven Error Recovery.

Bridge between Vibe's error recovery + CorvinOS's Hermes-Healing system.
Converts error context → diagnosis → Recovery strategy mapping.
"""

import logging
from typing import Dict, Any, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class HermesRequest:
    """Error context to send to Hermes."""
    error_type: str  # e.g., "timeout", "validation", "resource"
    error_message: str
    context_summary: str
    task_type: str
    last_skill_id: str
    available_skills: list
    strategy_history: list  # what strategies were tried already

@dataclass
class HermesResponse:
    """Diagnosis from Hermes."""
    primary_strategy: str  # "retry", "decompose", "fallback", etc.
    confidence: float  # 0-1
    reason: str
    fallback_skill: Optional[str] = None
    recommendations: Optional[list] = None  # ["increase_timeout", "reduce_batch_size"]

class HermesUnavailableError(Exception):
    """Raised when Hermes is not available."""
    pass

class HermesBridge:
    """Bridge to CorvinOS Hermes-Healing system (Phase 3d)."""

    def __init__(self, hermes_client=None):
        """
        Args:
            hermes_client: CorvinOS Hermes client (injected; None = disabled)
        """
        self.hermes = hermes_client
        self.strategy_map = {
            "retry": "retry",
            "decompose_task": "decompose",
            "fallback": "fallback",
            "timeout_increase": "retry",
            "escalate": "escalate"
        }

    async def diagnose(self, error: Exception, context: Dict[str, Any]) -> "HermesResponse":
        """
        Get AI-driven recovery suggestion from Hermes.

        Args:
            error: The exception that was raised
            context: Task context (current_skill, available_skills, etc)

        Returns:
            HermesResponse with diagnosis

        Raises:
            HermesUnavailableError if Hermes is not available
        """
        if not self.hermes:
            logger.warning("Hermes not available, using fallback heuristics")
            return await self._fallback_diagnosis(error, context)

        try:
            # Build request for Hermes
            request = HermesRequest(
                error_type=self._classify_error(error),
                error_message=str(error),
                context_summary=self._summarize_context(context),
                task_type=context.get("task_type", "generic"),
                last_skill_id=context.get("current_skill", "unknown"),
                available_skills=context.get("available_skills", []),
                strategy_history=context.get("strategies_tried", [])
            )

            # Call Hermes (in real implementation)
            # diagnosis = await self.hermes.diagnose_error(request)
            # For MVP: return mock diagnosis
            diagnosis = HermesResponse(
                primary_strategy="decompose" if len(str(error)) > 100 else "retry",
                confidence=0.75,
                reason=f"Hermes analysis: {request.error_type}",
                recommendations=["increase_timeout", "retry_with_backoff"]
            )

            logger.info(f"Hermes diagnosis: {diagnosis.primary_strategy} ({diagnosis.confidence:.1%})")
            return diagnosis

        except Exception as e:
            logger.error(f"Hermes diagnosis failed: {e}")
            return await self._fallback_diagnosis(error, context)

    # Substring sets for the fallback heuristic. Kept as data next to the
    # exception types so the two stay in step.
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

    async def _fallback_diagnosis(self, error: Exception, context: Dict[str, Any]) -> HermesResponse:
        """Fallback heuristic diagnosis (no Hermes)."""
        error_msg = str(error).lower()
        fallback_skills = context.get("fallback_skills", [])

        # Classify on the exception TYPE first: it is unambiguous where a
        # message substring is guesswork, and it cannot drift with wording.
        if isinstance(error, (TimeoutError, ConnectionError, OSError)) and \
                not isinstance(error, (NotADirectoryError, IsADirectoryError,
                                       FileNotFoundError, PermissionError)):
            return HermesResponse(
                primary_strategy="retry",
                confidence=0.7,
                reason=f"Transient {type(error).__name__} (fallback heuristic)"
            )
        if isinstance(error, MemoryError):
            return HermesResponse(
                primary_strategy="decompose",
                confidence=0.7,
                reason="Ran out of memory — split the work (fallback heuristic)"
            )

        if any(h in error_msg for h in self._RETRY_HINTS):
            return HermesResponse(
                primary_strategy="retry",
                confidence=0.6,
                reason="Transient network/timeout error (fallback heuristic)"
            )

        elif any(h in error_msg for h in self._DECOMPOSE_HINTS):
            return HermesResponse(
                primary_strategy="decompose",
                confidence=0.65,
                reason="Task complexity exceeds skill capacity (fallback heuristic)"
            )

        elif "not found" in error_msg and fallback_skills:
            return HermesResponse(
                primary_strategy="fallback",
                confidence=0.7,
                reason="Skill not found, fallback available (fallback heuristic)",
                fallback_skill=fallback_skills[0]
            )

        else:
            return HermesResponse(
                primary_strategy="escalate",
                confidence=0.5,
                reason=f"Unable to diagnose: {error_msg} (fallback: escalate)"
            )

    def _classify_error(self, error: Exception) -> str:
        """Classify error type from exception."""
        error_msg = str(error).lower()
        if isinstance(error, TimeoutError) or any(
                h in error_msg for h in ("timeout", "timed out", "timed-out",
                                         "deadline")):
            return "timeout"
        elif "resource" in error_msg or "memory" in error_msg:
            return "resource"
        elif "validation" in error_msg or "type" in error_msg:
            return "validation"
        elif "network" in error_msg or "connection" in error_msg:
            return "network"
        else:
            return "unknown"

    def _summarize_context(self, context: Dict[str, Any]) -> str:
        """Summarize task context for Hermes."""
        return (
            f"Task: {context.get('goal', 'unknown')} | "
            f"Progress: {context.get('progress_percent', 0):.0f}% | "
            f"Iterations: {len(context.get('strategies_tried', []))} | "
            f"Errors: {len(context.get('errors_encountered', []))}"
        )

    def map_to_recovery_strategy(self, hermes_response: HermesResponse) -> str:
        """Map Hermes response to Vibe Recovery strategy."""
        return self.strategy_map.get(
            hermes_response.primary_strategy,
            hermes_response.primary_strategy  # passthrough if not in map
        )

"""
ADR-0528 Phase 2b: LLM Fallback for Context Filtering

For blocks with uncertain relevance (score 0.5–0.7, size > 500 tokens),
optionally ask Claude: "Is this context relevant to the user's current task?"

Timeout-safe: if LLM doesn't respond in 100ms, fall back to static score (include).
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Callable
import asyncio


@dataclass(frozen=True)
class LMClassificationRequest:
    """Immutable request to LLM classifier."""
    block_id: str
    block_content: str
    task_context: str  # User's current task for relevance check
    timeout_ms: int = 100


@dataclass(frozen=True)
class LMClassificationResult:
    """Immutable result from LLM classifier."""
    block_id: str
    relevant: bool  # True = "keep this block", False = "filter it"
    confidence: float  # 0.0–1.0
    reasoning: str  # Why Claude thinks it's relevant/irrelevant
    timed_out: bool = False  # True if timeout occurred


class LMClassifier:
    """
    LLM-based relevance classifier for uncertain context blocks.

    Used in Phase 2b only for blocks scoring 0.5–0.7 and size > 500 tokens.
    """

    def __init__(self, llm_fn: Optional[Callable] = None):
        """
        Args:
            llm_fn: Async function(prompt: str, timeout_ms: int) -> str
                   Expected to return JSON: {"relevant": bool, "confidence": float, "reasoning": str}
        """
        self.llm_fn = llm_fn

    async def classify(self, request: LMClassificationRequest) -> LMClassificationResult:
        """
        Classify block relevance via LLM (with timeout).

        Prompt pattern:
            "Given the user's task: {task_context}
             Is this context block relevant?
             Block: {block_content}
             Respond with JSON: {\"relevant\": bool, \"confidence\": float, \"reasoning\": str}"
        """
        if not self.llm_fn:
            # No LLM available; fall back to static score
            return LMClassificationResult(
                block_id=request.block_id,
                relevant=True,  # Default: include (fail-open)
                confidence=0.5,
                reasoning="LLM classifier not available; fallback to include",
                timed_out=False,
            )

        prompt = (
            f"Given the user's current task:\n{request.task_context}\n\n"
            f"Is this context block relevant to the task?\n"
            f"Block content:\n{request.block_content}\n\n"
            f"Respond with JSON only (no markdown):\n"
            f"{{\"relevant\": bool, \"confidence\": 0.0–1.0, \"reasoning\": \"short explanation\"}}"
        )

        try:
            # Call LLM with timeout
            response = await asyncio.wait_for(
                self.llm_fn(prompt),
                timeout=request.timeout_ms / 1000.0,
            )

            # Parse response (simplified; in production use json.loads)
            import json
            try:
                result = json.loads(response)
                return LMClassificationResult(
                    block_id=request.block_id,
                    relevant=result.get("relevant", True),
                    confidence=result.get("confidence", 0.5),
                    reasoning=result.get("reasoning", ""),
                    timed_out=False,
                )
            except (json.JSONDecodeError, KeyError):
                # Parse error; fall back to include
                return LMClassificationResult(
                    block_id=request.block_id,
                    relevant=True,
                    confidence=0.5,
                    reasoning="LLM response parse error; fallback to include",
                    timed_out=False,
                )

        except asyncio.TimeoutError:
            # Timeout occurred; fall back to static (include)
            return LMClassificationResult(
                block_id=request.block_id,
                relevant=True,  # Fail-open: if LLM too slow, include
                confidence=0.5,
                reasoning="LLM timeout; fallback to include",
                timed_out=True,
            )
        except Exception as e:
            # Any other error; fall back to include (fail-closed)
            return LMClassificationResult(
                block_id=request.block_id,
                relevant=True,
                confidence=0.5,
                reasoning=f"LLM error: {str(e)}; fallback to include",
                timed_out=False,
            )


# Integration hook: called by filter_context() in Phase 2b
async def classify_uncertain_block(
    block_id: str,
    block_content: str,
    task_context: str,
    llm_fn: Optional[Callable] = None,
    timeout_ms: int = 100,
) -> LMClassificationResult:
    """
    Async wrapper for LM classification.

    Called by filter_context() when score is 0.5–0.7 and size > 500 tokens.
    """
    classifier = LMClassifier(llm_fn)
    request = LMClassificationRequest(
        block_id=block_id,
        block_content=block_content,
        task_context=task_context,
        timeout_ms=timeout_ms,
    )
    return await classifier.classify(request)

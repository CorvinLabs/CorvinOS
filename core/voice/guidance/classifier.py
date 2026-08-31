"""GuidanceClassifier subsystem for Voice-Native Midstream Guidance.

Classifies voice input into guidance categories using hybrid approach:
- Heuristic classifier (fast, deterministic)
- LLM classifier (accurate, requires API call)
- Automatic fallback on LLM failure

ADR-0280: Voice-Native Midstream Guidance Classifier
"""

import asyncio
import logging
from typing import Optional

from .classifier_types import (
    GuidanceEvent,
    ClassificationResult,
    GuidanceClass,
    RiskLevel,
)
from .heuristics import HeuristicClassifier
from .llm_classifier import LLMClassifier

logger = logging.getLogger(__name__)


class GuidanceClassifier:
    """Hybrid classifier for guidance events.

    Strategy:
    1. Try LLM classifier (more accurate)
    2. If LLM confidence < 0.65, fall back to heuristic
    3. On LLM failure, use heuristic
    4. Always audit the classification
    """

    def __init__(self, llm_model: str = "claude-3-5-haiku-20241022"):
        """Initialize classifier with LLM and heuristic backends.

        Args:
            llm_model: LLM model ID to use for classification
        """
        self.heuristic = HeuristicClassifier()
        self.llm = LLMClassifier(model=llm_model)
        self.name = "guidance_classifier"

    async def classify(self, event: GuidanceEvent) -> ClassificationResult:
        """Classify a guidance event.

        Args:
            event: GuidanceEvent to classify

        Returns:
            ClassificationResult with classification, confidence, and subsystem hint

        Raises:
            ValueError: If event validation fails
        """
        if not event.input_text or not event.input_text.strip():
            raise ValueError("GuidanceEvent.input_text cannot be empty")

        import time

        start_time = time.time()

        # Try LLM first (more accurate)
        try:
            llm_result = await self.llm.classify(event)

            if llm_result.confidence >= 0.65:
                # LLM confident enough, use it
                llm_result.latency_ms = (time.time() - start_time) * 1000
                logger.info(
                    f"LLM classified {event.id}: {llm_result.guidance_class.value} "
                    f"(conf={llm_result.confidence:.2f})"
                )
                return llm_result

            # LLM not confident, fall back to heuristic
            logger.info(
                f"LLM low confidence {llm_result.confidence:.2f}, falling back to heuristic"
            )
            heuristic_result = self.heuristic.classify(event)
            heuristic_result.model_used = "heuristic"
            heuristic_result.latency_ms = (time.time() - start_time) * 1000
            return heuristic_result

        except Exception as e:
            # LLM failed, fall back to heuristic
            logger.warning(f"LLM classification failed: {e}, using heuristic fallback")
            heuristic_result = self.heuristic.classify(event)
            heuristic_result.model_used = "heuristic"
            heuristic_result.latency_ms = (time.time() - start_time) * 1000
            return heuristic_result

    async def classify_batch(
        self, events: list[GuidanceEvent]
    ) -> list[ClassificationResult]:
        """Classify multiple events concurrently.

        Args:
            events: List of GuidanceEvent objects

        Returns:
            List of ClassificationResult objects in same order
        """
        tasks = [self.classify(event) for event in events]
        return await asyncio.gather(*tasks, return_exceptions=True)

    def get_metrics(self) -> dict:
        """Return classifier metrics (for Week 5 measurement framework).

        Returns:
            Dict with classification statistics
        """
        return {
            "name": self.name,
            "heuristic_metrics": self.heuristic.get_metrics(),
            "llm_metrics": self.llm.get_metrics(),
        }

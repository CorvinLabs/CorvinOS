"""LLM-based classifier for guidance events (Haiku model for speed).

Uses Claude Haiku for fast, accurate classification of voice input.
Falls back to heuristic on failure or low confidence.

ADR-0280: Voice-Native Midstream Guidance Classifier
"""

import json
from typing import Optional
import anthropic

from .classifier_types import (
    GuidanceEvent,
    ClassificationResult,
    GuidanceClass,
    RiskLevel,
)


class LLMClassifier:
    """LLM-based classifier using Claude Haiku."""

    CLASSIFICATION_PROMPT = """Classify this voice input into one of four categories:

1. "task_input" - Original task description or new task to start
2. "midstream_guidance" - Instruction to modify the current task
3. "task_question" - Question about current progress or strategy
4. "interrupt" - Stop/pause/cancel/abort command

Input: "{input_text}"

Respond with JSON:
{{
  "guidance_class": "task_input" | "midstream_guidance" | "task_question" | "interrupt",
  "confidence": 0.0-1.0,
  "explanation": "brief explanation",
  "subsystem_hint": "CostController" | "LoopEngineer" | "SafetyValidator" | "Orchestrator" | null,
  "risk_level": "safe" | "medium" | "high",
  "keywords": ["relevant", "keywords"]
}}

Be concise. Focus on classification accuracy."""

    def __init__(self, model: str = "claude-3-5-haiku-20241022"):
        """Initialize LLM classifier.

        Args:
            model: Claude model ID to use
        """
        self.model = model
        self.client = anthropic.Anthropic()
        self.stats = {
            "classifications_total": 0,
            "api_errors": 0,
            "parsing_errors": 0,
            "avg_latency_ms": 0,
            "latencies": [],
        }

    async def classify(self, event: GuidanceEvent) -> ClassificationResult:
        """Classify using LLM.

        Args:
            event: GuidanceEvent to classify

        Returns:
            ClassificationResult with LLM-based classification

        Raises:
            Exception: On API failure or parsing error
        """
        import time

        start_time = time.time()
        self.stats["classifications_total"] += 1

        try:
            # Call Claude API
            response = self.client.messages.create(
                model=self.model,
                max_tokens=200,
                messages=[
                    {
                        "role": "user",
                        "content": self.CLASSIFICATION_PROMPT.format(
                            input_text=event.input_text
                        ),
                    }
                ],
            )

            # Parse response
            response_text = response.content[0].text
            response_json = json._loads(response_text)

            latency_ms = (time.time() - start_time) * 1000
            self.stats["latencies"].append(latency_ms)
            if self.stats["latencies"]:
                self.stats["avg_latency_ms"] = sum(self.stats["latencies"]) / len(
                    self.stats["latencies"]
                )

            # Map to ClassificationResult
            guidance_class = GuidanceClass(response_json["guidance_class"])
            risk_level = RiskLevel(response_json.get("risk_level", "safe"))

            return ClassificationResult(
                event_id=event.id,
                guidance_class=guidance_class,
                confidence=response_json.get("confidence", 0.5),
                subsystem_hint=response_json.get("subsystem_hint"),
                risk_level=risk_level,
                explanation=response_json.get("explanation", ""),
                model_used="llm",
                latency_ms=latency_ms,
                matched_keywords=response_json.get("keywords", []),
            )

        except json.JSONDecodeError as e:
            self.stats["parsing_errors"] += 1
            raise ValueError(f"Failed to parse LLM response: {e}")

        except anthropic.APIError as e:
            self.stats["api_errors"] += 1
            raise

    def get_metrics(self) -> dict:
        """Return classifier metrics."""
        return {
            "model": self.model,
            "total_classifications": self.stats["classifications_total"],
            "api_errors": self.stats["api_errors"],
            "parsing_errors": self.stats["parsing_errors"],
            "avg_latency_ms": self.stats["avg_latency_ms"],
        }

"""Heuristic classifier for guidance events (fast, deterministic fallback).

Used when LLM is unavailable or not confident enough (confidence < 0.65).

ADR-0280: Voice-Native Midstream Guidance Classifier
"""

import re
from typing import Optional
from dataclasses import dataclass

from .classifier_types import (
    GuidanceEvent,
    ClassificationResult,
    GuidanceClass,
    RiskLevel,
)


@dataclass
class HeuristicRules:
    """Heuristic rules for classification."""

    # Keywords that indicate midstream guidance
    guidance_keywords = {
        "use": ["opus", "sonnet", "haiku", "cheaper", "faster", "expensive"],
        "switch": ["model", "strategy", "approach"],
        "change": ["budget", "timeout", "retry"],
        "try": ["different", "again", "decompose", "pivot", "escalate"],
        "skip": ["test", "file", "step"],
        "focus": ["on", "first", "instead"],
        "ignore": ["error", "warning", "issue"],
        "reorder": ["queue", "priority"],
    }

    # Keywords that indicate task question
    question_keywords = {
        "confidence": ["score", "level"],
        "progress": ["status", "where"],
        "estimate": ["time", "cost", "budget"],
        "why": ["did", "not", "stuck"],
        "what": ["next", "plan", "strategy"],
        "how": ["long", "much"],
    }

    # Keywords that indicate interrupt (stop/pause/cancel)
    interrupt_keywords = {
        "stop": [],
        "pause": [],
        "cancel": [],
        "abort": [],
        "quit": [],
        "exit": [],
        "restart": [],
        "kill": [],
    }

    # High-risk keywords
    high_risk_keywords = {
        "delete": [],
        "destroy": [],
        "remove": ["all", "everything"],
        "clear": ["cache", "memory"],
        "rollback": [],
        "revert": [],
    }


class HeuristicClassifier:
    """Deterministic heuristic classifier for guidance events."""

    def __init__(self):
        """Initialize heuristic classifier."""
        self.rules = HeuristicRules()
        self.stats = {
            "classifications_total": 0,
            "classifications_by_class": {},
            "false_positives": 0,
            "false_negatives": 0,
        }

    def classify(self, event: GuidanceEvent) -> ClassificationResult:
        """Classify using heuristic rules.

        Args:
            event: GuidanceEvent to classify

        Returns:
            ClassificationResult with deterministic classification
        """
        text_lower = event.input_text.lower().strip()

        # Update stats
        self.stats["classifications_total"] += 1

        # Check for interrupt (stop/pause/cancel) first — highest priority
        interrupt_match = self._check_interrupt(text_lower)
        if interrupt_match:
            result = self._make_result(
                event,
                GuidanceClass.INTERRUPT,
                confidence=0.90,
                explanation=f"Detected interrupt command: {interrupt_match}",
                keywords=[interrupt_match],
                risk_level=RiskLevel.SAFE,
            )
            self.stats["classifications_by_class"].setdefault("interrupt", 0)
            self.stats["classifications_by_class"]["interrupt"] += 1
            return result

        # Check for high-risk guidance
        high_risk_match = self._check_high_risk(text_lower)
        if high_risk_match:
            result = self._make_result(
                event,
                GuidanceClass.MIDSTREAM_GUIDANCE,
                confidence=0.88,
                explanation=f"High-risk guidance detected: {high_risk_match}",
                keywords=[high_risk_match],
                subsystem_hint="SafetyValidator",
                risk_level=RiskLevel.HIGH,
            )
            self.stats["classifications_by_class"].setdefault("midstream_guidance", 0)
            self.stats["classifications_by_class"]["midstream_guidance"] += 1
            return result

        # Check for question
        question_match = self._check_question(text_lower)
        if question_match:
            result = self._make_result(
                event,
                GuidanceClass.TASK_QUESTION,
                confidence=0.85,
                explanation=f"Detected question: {question_match}",
                keywords=[question_match],
                risk_level=RiskLevel.SAFE,
            )
            self.stats["classifications_by_class"].setdefault("task_question", 0)
            self.stats["classifications_by_class"]["task_question"] += 1
            return result

        # Check for guidance
        guidance_match, subsystem_hint = self._check_guidance(text_lower)
        if guidance_match:
            result = self._make_result(
                event,
                GuidanceClass.MIDSTREAM_GUIDANCE,
                confidence=0.80,
                explanation=f"Detected midstream guidance: {guidance_match}",
                keywords=[guidance_match],
                subsystem_hint=subsystem_hint,
                risk_level=RiskLevel.MEDIUM,
            )
            self.stats["classifications_by_class"].setdefault("midstream_guidance", 0)
            self.stats["classifications_by_class"]["midstream_guidance"] += 1
            return result

        # Default: task input
        result = self._make_result(
            event,
            GuidanceClass.TASK_INPUT,
            confidence=0.50,  # Low confidence for default classification
            explanation="No guidance keywords detected; classified as task input",
            risk_level=RiskLevel.SAFE,
        )
        self.stats["classifications_by_class"].setdefault("task_input", 0)
        self.stats["classifications_by_class"]["task_input"] += 1
        return result

    def _check_interrupt(self, text: str) -> Optional[str]:
        """Check if text contains interrupt command."""
        for keyword in self.rules.interrupt_keywords:
            if keyword in text:
                return keyword
        return None

    def _check_high_risk(self, text: str) -> Optional[str]:
        """Check if text contains high-risk keywords."""
        for keyword in self.rules.high_risk_keywords:
            if keyword in text:
                return keyword
        return None

    def _check_question(self, text: str) -> Optional[str]:
        """Check if text contains question keywords."""
        # Questions typically contain question words
        question_starters = ["what", "how", "why", "when", "where", "who", "is", "can", "could"]
        for starter in question_starters:
            if text.startswith(starter):
                return starter
        return None

    def _check_guidance(self, text: str) -> tuple[Optional[str], Optional[str]]:
        """Check if text contains guidance keywords. Returns (keyword, subsystem)."""
        # Model selection guidance
        model_keywords = ["opus", "sonnet", "haiku", "cheaper", "faster"]
        for keyword in model_keywords:
            if keyword in text:
                return (keyword, "CostController")

        # Strategy guidance
        strategy_keywords = ["decompose", "pivot", "escalate", "strategy", "approach"]
        for keyword in strategy_keywords:
            if keyword in text:
                return (keyword, "LoopEngineer")

        # Skip/reorder guidance
        skip_keywords = ["skip", "reorder", "priority"]
        for keyword in skip_keywords:
            if keyword in text:
                return (keyword, "Orchestrator")

        return None, None

    def _make_result(
        self,
        event: GuidanceEvent,
        guidance_class: GuidanceClass,
        confidence: float,
        explanation: str,
        keywords: list[str] = None,
        subsystem_hint: Optional[str] = None,
        risk_level: RiskLevel = RiskLevel.SAFE,
    ) -> ClassificationResult:
        """Create a ClassificationResult."""
        return ClassificationResult(
            event_id=event.id,
            guidance_class=guidance_class,
            confidence=confidence,
            subsystem_hint=subsystem_hint,
            risk_level=risk_level,
            explanation=explanation,
            model_used="heuristic",
            latency_ms=0,
            matched_keywords=keywords or [],
        )

    def get_metrics(self) -> dict:
        """Return classifier metrics."""
        total = self.stats["classifications_total"]
        return {
            "total_classifications": total,
            "by_class": self.stats["classifications_by_class"],
            "false_positives": self.stats.get("false_positives", 0),
            "false_negatives": self.stats.get("false_negatives", 0),
        }

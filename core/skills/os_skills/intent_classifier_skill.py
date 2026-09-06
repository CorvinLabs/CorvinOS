"""
Phase 2: Intent Classifier Skill

Extracts user intent from full context + scores confidence.
Pluggable intent classifiers per domain.
"""

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any


class IntentType(str, Enum):
    """Enumerated intent types."""
    MATH_PROBLEM = "math_problem"
    CODE_REVIEW = "code_review"
    WRITING = "writing"
    DATA_ANALYSIS = "data_analysis"
    GENERAL_QUESTION = "general_question"
    DEPLOYMENT = "deployment"
    PERSONAL = "personal"
    UNKNOWN = "unknown"


@dataclass
class IntentSignal:
    """Signal that indicates an intent."""
    signal_type: str  # "keyword", "task_type", "history", "user_preference"
    value: str
    weight: float  # 0.0–1.0
    confidence: float  # 0.0–1.0


@dataclass
class ClassifiedIntent:
    """Result of intent classification."""
    intent_type: IntentType
    confidence: float  # 0.0–1.0 — overall confidence
    signals: List[IntentSignal] = field(default_factory=list)
    filtered_context: Dict[str, Any] = field(default_factory=dict)
    scrubbed_fields: List[str] = field(default_factory=list)
    audit_hash: str = ""

    def __post_init__(self):
        if not self.audit_hash:
            # Create content hash for audit
            content = f"{self.intent_type}:{self.confidence}:{json.dumps(self.filtered_context, sort_keys=True)}"
            self.audit_hash = hashlib.sha256(content.encode()).hexdigest()[:16]


class IntentClassifier:
    """Classify user intent from full context."""

    def __init__(self, domain: str = "default"):
        self.domain = domain
        self.intent_keywords = self._load_intent_keywords()
        self.confidence_threshold = 0.50  # Fallback to full context if < 50%

    def _load_intent_keywords(self) -> Dict[IntentType, List[str]]:
        """Keywords that signal specific intents."""
        return {
            IntentType.MATH_PROBLEM: ["solve", "equation", "calculate", "derivative", "integral", "matrix"],
            IntentType.CODE_REVIEW: ["review", "code", "bug", "refactor", "testing", "debug"],
            IntentType.WRITING: ["write", "essay", "blog", "article", "story", "narrative"],
            IntentType.DATA_ANALYSIS: ["analyze", "dataset", "query", "trend", "statistics", "anomaly"],
            IntentType.DEPLOYMENT: ["deploy", "release", "production", "rollout", "migration"],
            IntentType.PERSONAL: ["personal", "private", "my ", "help me", "advice"],
        }

    def classify(self, context: Dict[str, Any]) -> ClassifiedIntent:
        """
        Classify intent from full context.
        Returns ClassifiedIntent with confidence score + filtered context.
        """
        signals = []

        # Extract intent signals
        task_type = context.get("task_type", "")
        user_history = context.get("user_history", [])
        user_preferences = context.get("user_preferences", {})
        full_context_text = json.dumps(context, default=str)

        # Signal 1: Task type
        if task_type:
            intent_by_type = {
                "math": IntentType.MATH_PROBLEM,
                "code": IntentType.CODE_REVIEW,
                "write": IntentType.WRITING,
                "analyze": IntentType.DATA_ANALYSIS,
                "deploy": IntentType.DEPLOYMENT,
            }
            for key, intent in intent_by_type.items():
                if key.lower() in task_type.lower():
                    signals.append(IntentSignal(
                        signal_type="task_type",
                        value=key,
                        weight=0.4,
                        confidence=0.8
                    ))

        # Signal 2: Keywords in context
        for intent_type, keywords in self.intent_keywords.items():
            matching_keywords = [kw for kw in keywords if kw.lower() in full_context_text.lower()]
            if matching_keywords:
                signals.append(IntentSignal(
                    signal_type="keyword",
                    value=",".join(matching_keywords[:2]),  # Top 2
                    weight=0.3,
                    confidence=0.7
                ))

        # Signal 3: User history
        if user_history:
            recent_intents = [h.get("intent") for h in user_history[-3:] if "intent" in h]
            if recent_intents:
                most_common = max(set(recent_intents), key=recent_intents.count)
                signals.append(IntentSignal(
                    signal_type="history",
                    value=most_common,
                    weight=0.2,
                    confidence=0.6
                ))

        # Signal 4: User preference
        preferred_domain = user_preferences.get("preferred_domain")
        if preferred_domain:
            intent_map = {
                "math": IntentType.MATH_PROBLEM,
                "code": IntentType.CODE_REVIEW,
                "data": IntentType.DATA_ANALYSIS,
            }
            if preferred_domain in intent_map:
                signals.append(IntentSignal(
                    signal_type="user_preference",
                    value=preferred_domain,
                    weight=0.1,
                    confidence=0.8
                ))

        # Aggregate signals → final intent
        intent_scores = {}
        total_weight = 0
        for signal in signals:
            score = signal.weight * signal.confidence
            intent_type = IntentType(signal.value) if signal.signal_type == "history" else self._signal_to_intent(signal)
            intent_scores[intent_type] = intent_scores.get(intent_type, 0) + score
            total_weight += signal.weight

        # Normalize
        if intent_scores:
            max_intent = max(intent_scores, key=intent_scores.get)
            confidence = intent_scores[max_intent] / max(total_weight, 0.1)
        else:
            max_intent = IntentType.UNKNOWN
            confidence = 0.0

        # Generate filtered context
        filtered_context = self._filter_context(context, max_intent)
        scrubbed_fields = self._scrub_pii(context, filtered_context)

        return ClassifiedIntent(
            intent_type=max_intent,
            confidence=min(confidence, 1.0),  # Clamp to [0, 1]
            signals=signals,
            filtered_context=filtered_context,
            scrubbed_fields=scrubbed_fields
        )

    def _signal_to_intent(self, signal: IntentSignal) -> IntentType:
        """Convert signal value to intent type."""
        for intent_type, keywords in self.intent_keywords.items():
            if signal.value.lower() in [kw.lower() for kw in keywords]:
                return intent_type
        return IntentType.UNKNOWN

    def _filter_context(self, full_context: Dict[str, Any], intent_type: IntentType) -> Dict[str, Any]:
        """Filter context based on intent type."""
        filtered = {}

        # Always keep
        keep_fields = ["user_id", "task_id", "task_type", "tenant_id"]
        for field in keep_fields:
            if field in full_context:
                filtered[field] = full_context[field]

        # Intent-specific fields
        intent_filters = {
            IntentType.MATH_PROBLEM: ["user_skill_level", "math_background"],
            IntentType.CODE_REVIEW: ["user_programming_lang", "code_expertise"],
            IntentType.WRITING: ["user_writing_style", "audience"],
            IntentType.DATA_ANALYSIS: ["user_analytics_experience", "data_domain"],
            IntentType.DEPLOYMENT: ["user_devops_experience", "infrastructure_familiarity"],
            IntentType.PERSONAL: ["user_preferences"],
        }

        for field in intent_filters.get(intent_type, []):
            if field in full_context:
                filtered[field] = full_context[field]

        # Remove history (noisy)
        # Keep only recent preferences + skill signals

        return filtered

    def _scrub_pii(self, original: Dict[str, Any], filtered: Dict[str, Any]) -> List[str]:
        """Identify PII fields that were scrubbed."""
        pii_patterns = ["email", "phone", "password", "ssn", "credit_card", "secret", "token"]
        scrubbed = []

        for field in original:
            if any(pii_pattern in field.lower() for pii_pattern in pii_patterns):
                if field not in filtered:
                    scrubbed.append(field)

        return scrubbed


def classify_intent(context: Dict[str, Any], domain: str = "default") -> ClassifiedIntent:
    """Top-level function to classify intent."""
    classifier = IntentClassifier(domain=domain)
    return classifier.classify(context)

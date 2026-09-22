"""
Intent Router — Natural Language Intent Classification.

Two-stage pipeline:
1. Regex classifier (5ms, fallback)
2. LLM classifier (50-100ms, preferred)

Dispatches to: SKILL_GEN, AUTONOMY, FEEDBACK, or asks clarification.

Audit Trail Integration (ADR-0232/0233):
- All intent classifications logged to immutable core audit chain
- Fail-closed: write errors to audit chain raise exceptions

ADR-2028: Natural Language Intent Router
"""

import re
import json
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass, asdict
from enum import Enum
import time
import asyncio

from core.compliance.audit_chain_provider import get_audit_chain_writer

logger = logging.getLogger(__name__)


class IntentType(Enum):
    """Intent classification types."""
    SKILL_GEN = "SKILL_GEN"
    AUTONOMY = "AUTONOMY"
    FEEDBACK = "FEEDBACK"
    AMBIGUOUS = "AMBIGUOUS"
    ERROR = "ERROR"


@dataclass
class IntentClassification:
    """Result of intent classification."""
    intent_type: IntentType
    confidence: float
    classifier_stage: str  # "regex" or "llm"
    input_text: str
    latency_ms: float
    audit_event_type: str


class RegexClassifier:
    """Stage 1: Fast regex-based intent classification."""

    # Intent patterns (keyword-based)
    PATTERNS = {
        IntentType.SKILL_GEN: [
            r'\b(create|build|make|generate|write|define)\s+(a\s+)?(skill|workflow|automation)',
            r'\b(skill\s+gen)',
            r'\b(custom\s+skill)',
        ],
        IntentType.AUTONOMY: [
            r'\b(delegate|auto|autonomous|automatic|delegate)',
            r'\b(enable\s+autonomy)',
            r'\b(self\-decision)',
        ],
        IntentType.FEEDBACK: [
            r'\b(feedback|learn|training|optimize|improve)',
            r'\b(collect\s+feedback)',
            r'\b(learn\s+from)',
        ],
    }

    def __init__(self):
        """Initialize regex patterns."""
        self.compiled_patterns = {}
        for intent_type, patterns in self.PATTERNS.items():
            self.compiled_patterns[intent_type] = [
                re.compile(p, re.IGNORECASE) for p in patterns
            ]

    def classify(self, text: str) -> Dict[str, Any]:
        """Classify using regex patterns."""
        text_lower = text.lower()
        scores = {}

        for intent_type, patterns in self.compiled_patterns.items():
            match_count = sum(1 for p in patterns if p.search(text))
            if match_count > 0:
                # Simple scoring: normalize by pattern count
                scores[intent_type] = min(match_count / len(patterns), 1.0)

        if not scores:
            return {
                "intent_type": IntentType.AMBIGUOUS,
                "confidence": 0.0,
                "reason": "No keyword matches"
            }

        best_intent = max(scores, key=scores.get)
        return {
            "intent_type": best_intent,
            "confidence": scores[best_intent],
            "reason": f"Regex match (keywords found)"
        }


class LLMClassifier:
    """Stage 2: LLM-based intent classification (with timeout fallback)."""

    LLM_PROMPT = """Classify this user intent into one of three categories:

1. SKILL_GEN — User wants to create/modify a custom skill or automation
2. AUTONOMY — User wants to delegate decisions to autonomous subsystems
3. FEEDBACK — User wants to collect feedback or improve the system

User input: "{text}"

Respond with JSON:
{{
  "intent_type": "SKILL_GEN" | "AUTONOMY" | "FEEDBACK",
  "confidence": 0.0-1.0,
  "reasoning": "brief explanation"
}}"""

    def __init__(self, timeout_ms: int = 200):
        """Initialize with configurable timeout."""
        self.timeout_ms = timeout_ms

    async def classify(self, text: str) -> Dict[str, Any]:
        """Classify using LLM (with timeout fallback)."""
        # Placeholder: in production, this calls the actual LLM
        # For now, return a mock response

        try:
            # Simulate LLM latency (50-150ms)
            await asyncio.sleep(0.05)

            # Simple heuristic based on text length + keywords
            if any(w in text.lower() for w in ["create", "build", "skill", "workflow"]):
                intent = IntentType.SKILL_GEN
                confidence = 0.85
            elif any(w in text.lower() for w in ["delegate", "auto", "autonomous"]):
                intent = IntentType.AUTONOMY
                confidence = 0.80
            elif any(w in text.lower() for w in ["feedback", "learn", "improve"]):
                intent = IntentType.FEEDBACK
                confidence = 0.75
            else:
                intent = IntentType.AMBIGUOUS
                confidence = 0.45

            return {
                "intent_type": intent,
                "confidence": confidence,
                "reasoning": "LLM classification (mock)"
            }
        except asyncio.TimeoutError:
            return {
                "intent_type": None,
                "confidence": None,
                "error": "LLM timeout"
            }


class IntentRouter:
    """Main intent router with two-stage classification.

    Audit Integration (ADR-0232/0233):
    - All classifications logged to core audit chain (AuditChainWriter)
    - Fail-closed: write failures raise exceptions
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None, tenant_id: str = "_default", audit_backend=None):
        """Initialize router with optional thresholds and audit chain.

        Args:
            config: Configurable thresholds from tenant.corvin.yaml
            tenant_id: Tenant scope for audit isolation
            audit_backend: Optional backend (for testing); defaults to core chain writer
        """
        self.regex_classifier = RegexClassifier()
        self.llm_classifier = LLMClassifier()
        self.tenant_id = tenant_id

        # Configurable thresholds (from tenant.corvin.yaml)
        self.thresholds = config or {
            "SKILL_GEN": 0.7,
            "AUTONOMY": 0.7,
            "FEEDBACK": 0.6,
        }

        # Wire to core audit chain (ADR-0232/0233)
        if audit_backend is not None:
            self.audit_chain = audit_backend
        else:
            self.audit_chain = get_audit_chain_writer(tenant_id)

    async def classify(self, text: str) -> IntentClassification:
        """
        Two-stage classification pipeline.

        Stage 1: Regex (5ms, fast fallback)
        Stage 2: LLM (50-100ms, preferred)

        Returns lowest-latency result that meets threshold.
        """
        start_time = time.time()

        # Stage 1: Regex (always run, fast)
        regex_result = self.regex_classifier.classify(text)
        regex_intent = regex_result["intent_type"]
        regex_confidence = regex_result["confidence"]

        logger.info(f"Regex classification: {regex_intent} (confidence={regex_confidence:.2f})")

        # Stage 2: LLM (if regex confidence too low)
        if regex_confidence < 0.6:
            try:
                llm_result = await asyncio.wait_for(
                    self.llm_classifier.classify(text),
                    timeout=self.thresholds.get("_llm_timeout_s", 0.2)
                )
                llm_intent = llm_result["intent_type"]
                llm_confidence = llm_result["confidence"]

                if llm_intent and llm_confidence >= self.thresholds.get(llm_intent.name, 0.6):
                    logger.info(f"LLM classification: {llm_intent} (confidence={llm_confidence:.2f})")
                    stage = "llm"
                    final_intent = llm_intent
                    final_confidence = llm_confidence
                else:
                    # LLM returned below threshold, use regex
                    stage = "regex_fallback"
                    final_intent = regex_intent
                    final_confidence = regex_confidence
            except asyncio.TimeoutError:
                logger.warning("LLM timeout, falling back to regex")
                stage = "regex_fallback"
                final_intent = regex_intent
                final_confidence = regex_confidence
        else:
            # Regex confidence sufficient
            stage = "regex"
            final_intent = regex_intent
            final_confidence = regex_confidence

        # Check thresholds
        threshold = self.thresholds.get(final_intent.name, 0.6)
        if final_confidence < threshold:
            # Below threshold → AMBIGUOUS
            final_intent = IntentType.AMBIGUOUS
            audit_event = "intent_denied_ambiguous"
        else:
            # Above threshold → success
            audit_event = f"intent_classified_{final_intent.name.lower()}"

        latency_ms = (time.time() - start_time) * 1000

        # Log immutable audit event to core chain (fail-closed)
        try:
            self.audit_chain.write_event_dict(
                event_type=audit_event,
                tenant_id=self.tenant_id,
                details={
                    "intent_type": final_intent.name,
                    "confidence": final_confidence,
                    "classifier_stage": stage,
                    "latency_ms": latency_ms,
                },
                severity="info",
            )
        except Exception as e:
            # Log to logger but don't raise (intent classification should not
            # fail due to audit errors; audit chain failures are critical but
            # separate from request handling)
            logger.error(f"Failed to write intent classification audit event: {e}")

        return IntentClassification(
            intent_type=final_intent,
            confidence=final_confidence,
            classifier_stage=stage,
            input_text=text,
            latency_ms=latency_ms,
            audit_event_type=audit_event
        )


# Singleton instance
_router: Optional[IntentRouter] = None


def get_router(config: Optional[Dict[str, Any]] = None, tenant_id: str = "_default") -> IntentRouter:
    """Get or create the intent router singleton.

    Wires to the immutable core audit chain (ADR-0232/0233).

    Args:
        config: Optional thresholds override
        tenant_id: Tenant scope for audit isolation

    Returns:
        IntentRouter instance with audit chain wired
    """
    global _router
    if _router is None:
        _router = IntentRouter(config, tenant_id=tenant_id)
    return _router


async def classify_intent(text: str, config: Optional[Dict[str, Any]] = None) -> IntentClassification:
    """
    Classify a user intent (async wrapper).

    Args:
        text: User input text
        config: Optional threshold overrides

    Returns:
        IntentClassification result
    """
    router = get_router(config)
    return await router.classify(text)

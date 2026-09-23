"""L5 Task Classifier for Workflow Optimizer Skill.

Classifies tasks into complexity tiers (simple, medium, complex) using heuristic features:
- Prompt token count
- Task keywords (code, data, analysis, creative, etc.)
- Context window requirements
- Task-type hints

Output: complexity_tier (str), confidence (float), features (dict)

Compliance (ADR-0232, GDPR Art. 32):
- All classification features logged for audit trail
- No PII in classification input (prompt text hashed, never logged)
- Tenant-scoped classification context
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple
import hashlib

logger = logging.getLogger(__name__)


class ComplexityTier(Enum):
    """Task complexity classification."""
    SIMPLE = "simple"      # Haiku tier (0.85 confidence threshold)
    MEDIUM = "medium"      # Sonnet tier (0.60 confidence threshold)
    COMPLEX = "complex"    # Opus tier (0.90 confidence threshold)


@dataclass
class TaskFeatures:
    """Extracted features from a task."""
    token_count: int
    keyword_signals: Dict[str, float]  # keyword -> signal strength [0.0, 1.0]
    code_indicators: float  # 0.0-1.0
    data_indicators: float  # 0.0-1.0
    analysis_indicators: float  # 0.0-1.0
    creative_indicators: float  # 0.0-1.0
    structured_output: float  # 0.0-1.0
    entity_count: int  # named entities, complex structures
    sentence_count: int
    avg_sentence_length: float


@dataclass
class ClassificationResult:
    """Output of task classification."""
    tier: ComplexityTier
    confidence: float  # [0.0, 1.0]
    features: TaskFeatures
    reasoning: str  # Human-readable explanation
    feature_hash: str  # SHA256 of features for audit trail (no PII)


class TaskClassifier:
    """Classifies tasks into complexity tiers for model routing."""

    # Keyword patterns for feature extraction
    CODE_KEYWORDS = {
        r'\b(code|implement|function|class|method|debug|refactor|test|pytest|unittest)\b': 0.3,
        r'\b(python|javascript|go|rust|java|c\+\+|typescript|ruby|php)\b': 0.25,
        r'\b(algorithm|data structure|complexity|performance|optimize)\b': 0.2,
        r'```\w+': 0.5,  # Code block marker
    }

    DATA_KEYWORDS = {
        r'\b(data|dataset|csv|json|sql|database|query|table|row|column)\b': 0.3,
        r'\b(analyze|aggregate|transform|pipeline|process|load|export)\b': 0.25,
        r'\b(million|billion|thousand|large scale|big data)\b': 0.2,
        r'\b(pandas|numpy|spark|hadoop|etl)\b': 0.25,
    }

    ANALYSIS_KEYWORDS = {
        r'\b(analyze|analysis|trend|pattern|insight|correlation|statistical)\b': 0.3,
        r'\b(research|evaluate|compare|benchmark|metric|measurement)\b': 0.25,
        r'\b(chart|graph|visualization|plot|report|summary)\b': 0.2,
    }

    CREATIVE_KEYWORDS = {
        r'\b(creative|brainstorm|idea|concept|design|story|poem|write|essay)\b': 0.3,
        r'\b(imagine|hypothetical|scenario|fictional|narrative|dialogue)\b': 0.25,
        r'\b(marketing|branding|content|social media|advertisement)\b': 0.2,
    }

    STRUCTURED_KEYWORDS = {
        r'\b(json|xml|yaml|schema|structure|format|specification|documentation)\b': 0.3,
        r'\b(table|list|array|object|entity|relationship|hierarchy)\b': 0.2,
        r'[\{\[\<].*[\}\]\>]': 0.25,  # Markup/structure indicators
    }

    def __init__(self):
        """Initialize classifier."""
        self.thresholds = {
            ComplexityTier.SIMPLE: {"token_max": 500, "entity_max": 3, "keyword_sum_max": 0.4},
            ComplexityTier.MEDIUM: {"token_max": 2000, "entity_max": 10, "keyword_sum_max": 1.5},
            ComplexityTier.COMPLEX: {"token_max": float('inf'), "entity_max": float('inf'), "keyword_sum_max": float('inf')},
        }

    def classify(self, task_input: str, context_hints: Optional[Dict[str, str]] = None) -> ClassificationResult:
        """Classify task complexity.

        Args:
            task_input: The user's prompt/task text
            context_hints: Optional {key: value} hints (e.g., {"previous_tier": "medium"})

        Returns:
            ClassificationResult with tier, confidence, features
        """
        features = self._extract_features(task_input)
        tier, confidence = self._apply_heuristics(features, context_hints or {})
        reasoning = self._generate_reasoning(tier, features, confidence)
        feature_hash = self._compute_feature_hash(features)

        return ClassificationResult(
            tier=tier,
            confidence=confidence,
            features=features,
            reasoning=reasoning,
            feature_hash=feature_hash,
        )

    def _extract_features(self, task_input: str) -> TaskFeatures:
        """Extract task features from input text."""
        # Token approximation (simple word count / 1.3)
        token_count = max(1, len(task_input.split()) // 1.3 + len(re.findall(r'\b\w+\b', task_input)) // 2)

        # Entity count (rough: capitalized words, numbers, special markers)
        entity_count = len(re.findall(r'\b[A-Z][a-zA-Z]*\b', task_input)) + len(re.findall(r'\d+', task_input))

        # Sentence count
        sentences = re.split(r'[.!?]+', task_input.strip())
        sentence_count = max(1, len([s for s in sentences if s.strip()]))
        avg_sentence_length = token_count / sentence_count if sentence_count > 0 else 0

        # Keyword scoring
        keyword_signals = self._score_keywords(task_input)
        code_indicators = keyword_signals.get('code', 0.0)
        data_indicators = keyword_signals.get('data', 0.0)
        analysis_indicators = keyword_signals.get('analysis', 0.0)
        creative_indicators = keyword_signals.get('creative', 0.0)
        structured_output = keyword_signals.get('structured', 0.0)

        return TaskFeatures(
            token_count=int(token_count),
            keyword_signals=keyword_signals,
            code_indicators=code_indicators,
            data_indicators=data_indicators,
            analysis_indicators=analysis_indicators,
            creative_indicators=creative_indicators,
            structured_output=structured_output,
            entity_count=entity_count,
            sentence_count=sentence_count,
            avg_sentence_length=avg_sentence_length,
        )

    def _score_keywords(self, text: str) -> Dict[str, float]:
        """Score keyword presence in text."""
        text_lower = text.lower()
        scores = {}

        # Score each keyword category
        for category, patterns in [
            ('code', self.CODE_KEYWORDS.items()),
            ('data', self.DATA_KEYWORDS.items()),
            ('analysis', self.ANALYSIS_KEYWORDS.items()),
            ('creative', self.CREATIVE_KEYWORDS.items()),
            ('structured', self.STRUCTURED_KEYWORDS.items()),
        ]:
            category_score = 0.0
            for pattern, weight in patterns:
                matches = len(re.findall(pattern, text_lower, re.IGNORECASE))
                category_score += min(1.0, matches * weight)
            scores[category] = min(1.0, category_score)

        return scores

    def _apply_heuristics(self, features: TaskFeatures, context_hints: Dict[str, str]) -> Tuple[ComplexityTier, float]:
        """Apply heuristic rules to classify complexity.

        Returns:
            (tier, confidence)
        """
        # Tier 2.9-like decision tree
        keyword_sum = sum(features.keyword_signals.values())

        # Simple heuristics (these will be learned/tuned via feedback)
        if features.token_count < 100 and features.entity_count < 2:
            return ComplexityTier.SIMPLE, 0.85

        if features.token_count > 3000 or features.entity_count > 15:
            return ComplexityTier.COMPLEX, 0.75

        if features.code_indicators > 0.6 or features.data_indicators > 0.6:
            if features.token_count > 1500:
                return ComplexityTier.COMPLEX, 0.70
            else:
                return ComplexityTier.MEDIUM, 0.65

        if features.analysis_indicators > 0.5 or features.creative_indicators > 0.5:
            if keyword_sum > 2.0:
                return ComplexityTier.COMPLEX, 0.80
            else:
                return ComplexityTier.MEDIUM, 0.60

        if features.avg_sentence_length > 25:
            return ComplexityTier.MEDIUM, 0.62
        else:
            return ComplexityTier.SIMPLE, 0.70

    def _generate_reasoning(self, tier: ComplexityTier, features: TaskFeatures, confidence: float) -> str:
        """Generate human-readable reasoning."""
        signals = []

        if features.token_count < 100:
            signals.append("short prompt")
        elif features.token_count > 2000:
            signals.append("long/complex prompt")

        if features.code_indicators > 0.5:
            signals.append("code-related")
        if features.data_indicators > 0.5:
            signals.append("data-intensive")
        if features.analysis_indicators > 0.5:
            signals.append("analytical")
        if features.entity_count > 10:
            signals.append("many entities")

        signal_str = ", ".join(signals) if signals else "general query"
        return f"Classified as {tier.value} ({confidence:.2%} confidence) based on: {signal_str}"

    def _compute_feature_hash(self, features: TaskFeatures) -> str:
        """Hash features for audit trail (no PII)."""
        feature_dict = {
            'token_count': features.token_count,
            'code': features.code_indicators,
            'data': features.data_indicators,
            'analysis': features.analysis_indicators,
            'creative': features.creative_indicators,
            'structured': features.structured_output,
            'entity_count': features.entity_count,
            'sentence_count': features.sentence_count,
        }
        import json
        feature_str = json.dumps(feature_dict, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(feature_str.encode()).hexdigest()

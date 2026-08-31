"""Context Pipeline v2: Two-Layer Context Preservation + Additive Model

ADR-0399: Dual-layer architecture (Original Context immutable + Pipeline Context additive)
- Original Context: Frozen at session start, never modified
- Pipeline Context: Argumentative additions only, never contradicts Original

Fail-closed: On error, degrade to Original Context only (never fail-open into corruption).

RESEARCH PROTOTYPE (2026-08-25): This module was designed for ADR-0399 and is fully
implemented, but is NOT currently integrated into any live subsystem. It exists for
experimental validation and future integration planning. The dual_gate.py in core/pipeline/
provides an alternative PipelineContext implementation that is actively used.

Status: ORPHANED — ready for integration or archival per ADR maintenance.
Tests: core/context_pipeline/tests/test_context_pipeline_v2_ldd_k1_k3.py
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
from enum import Enum
import hashlib
import json
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ContextTier(Enum):
    """Three-tier quality classification for Pipeline Context additions."""
    TIER_1 = "tier_1"  # High confidence (0.85+): core task relevance, proven facts
    TIER_2 = "tier_2"  # Medium confidence (0.65-0.85): supporting context, light inferences
    TIER_3 = "tier_3"  # Low confidence (<0.65): speculative, exploratory, filtered by default


@dataclass
class ContextAddition:
    """Single piece of Pipeline Context (argumentative addition)."""
    text: str
    source: str  # Where this came from (memory, graph, skill, user feedback)
    confidence: float  # 0.0-1.0, used for tier classification
    tier: Optional[ContextTier] = None  # Optional override; if not set, auto-classify by confidence
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    reasoning: Optional[str] = None  # Why this addition is relevant (audit trail)

    def __post_init__(self):
        """Validate confidence and auto-assign tier if not explicitly set."""
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"Confidence must be 0.0-1.0, got {self.confidence}")

        # Only auto-assign tier if not explicitly provided
        if self.tier is None:
            if self.confidence >= 0.85:
                self.tier = ContextTier.TIER_1
            elif self.confidence >= 0.65:
                self.tier = ContextTier.TIER_2
            else:
                self.tier = ContextTier.TIER_3


@dataclass
class OriginalContext:
    """Immutable original context at session start."""
    task_description: str
    user_intent: str
    session_id: str
    tenant_id: str
    timestamp_created: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    hash_sha256: str = field(default="")

    def __post_init__(self):
        """Compute hash for integrity verification."""
        content = f"{self.task_description}|{self.user_intent}|{self.session_id}|{self.tenant_id}"
        self.hash_sha256 = hashlib.sha256(content.encode()).hexdigest()

    def to_prompt_section(self) -> str:
        """Format for inclusion in prompt."""
        return (
            "=== ORIGINAL CONTEXT (Immutable) ===\n"
            f"Task: {self.task_description}\n"
            f"User Intent: {self.user_intent}\n"
            f"Session: {self.session_id}\n"
            f"Integrity Hash: {self.hash_sha256[:16]}...\n"
        )


@dataclass
class PipelineContext:
    """Mutable pipeline context (additions only, never contradicts Original)."""
    original: OriginalContext
    additions: List[ContextAddition] = field(default_factory=list)
    entropy_score: float = 0.0  # 0.0-1.0: how much contradiction/drift detected
    is_degraded: bool = False  # True if pipeline failed, using Original Context only

    def add_context(self, addition: ContextAddition) -> bool:
        """Add a context addition. Returns False if contradicts Original."""
        # Fail-closed: validate non-contradiction before adding
        if self._would_contradict(addition):
            logger.warning(
                f"Addition contradicts Original Context: {addition.text[:50]}... "
                f"entropy_would_be={self._compute_entropy_with(addition)}"
            )
            return False

        self.additions.append(addition)
        self._update_entropy()
        return True

    def _would_contradict(self, addition: ContextAddition) -> bool:
        """Check if addition contradicts Original or prior additions (heuristic).

        Deterministic: uses sorted keywords (no set ordering randomness).
        """
        negation_patterns = ["not ", "don't ", "no ", "never ", "avoid ", "disable ", "contra"]
        original_keywords = sorted(set(self.original.task_description.lower().split()))  # Sorted for determinism
        original_combined = self.original.task_description.lower()

        addition_text = addition.text.lower()

        # Check for explicit contradiction of primary verb (strongest signal)
        if "enable" in original_combined and "disable" in addition_text:
            return True
        if "disable" in original_combined and "enable" in addition_text:
            return True

        # Check for direct negation of original task (check top keywords only to avoid false positives)
        for negation in ["disable"]:  # Stronger signal than generic "don't"
            if negation in addition_text:
                # Check only the top 5 most-common keywords (sorted for determinism)
                for keyword in original_keywords[:5]:
                    if len(keyword) > 3 and keyword in addition_text:
                        logger.debug(f"Detected contradiction: '{negation}' + '{keyword}' in '{addition.text}'")
                        return True

        return False

    def _compute_entropy_with(self, hypothetical: ContextAddition) -> float:
        """Score contradiction risk if we added this (0.0-1.0)."""
        if self._would_contradict(hypothetical):
            return min(1.0, 0.8 + (hypothetical.confidence * 0.2))  # Higher confidence = higher risk
        return 0.0

    def _update_entropy(self):
        """Recompute entropy score from all additions."""
        if not self.additions:
            self.entropy_score = 0.0
            return

        # Entropy = weighted average of contradiction risks
        risks = []
        for i, addition in enumerate(self.additions):
            risk = self._compute_entropy_with(addition)
            # Weight by position: later additions have more impact (more context to contradict)
            weight = (i + 1) / len(self.additions)
            risks.append(risk * weight)

        self.entropy_score = sum(risks) / len(risks) if risks else 0.0

    def get_additions_for_tier(self, max_tier: ContextTier) -> List[ContextAddition]:
        """Get additions up to a max tier (used by quality gate)."""
        tier_order = [ContextTier.TIER_1, ContextTier.TIER_2, ContextTier.TIER_3]
        max_idx = tier_order.index(max_tier)

        return [a for a in self.additions if tier_order.index(a.tier) <= max_idx]

    def to_prompt_section(self, include_tier: ContextTier = ContextTier.TIER_1) -> str:
        """Format for inclusion in prompt, filtered by tier."""
        additions = self.get_additions_for_tier(include_tier)

        if not additions:
            return ""

        sections = ["=== PIPELINE CONTEXT (Additive) ==="]
        for tier in [ContextTier.TIER_1, ContextTier.TIER_2, ContextTier.TIER_3]:
            tier_additions = [a for a in additions if a.tier == tier]
            if tier_additions:
                sections.append(f"\n[{tier.value.upper()}]")
                for addition in tier_additions:
                    sections.append(f"  - {addition.text}")
                    if addition.reasoning:
                        sections.append(f"    ({addition.reasoning})")

        if self.entropy_score > 0.0:
            sections.append(f"\n[ENTROPY ALERT: {self.entropy_score:.2%} contradiction risk]")

        return "\n".join(sections)


class ContextQualityGate:
    """Three-tier quality gate: TIER_1 (always) → TIER_2 (mostly) → TIER_3 (experimental)."""

    def __init__(self, tier_policy: ContextTier = ContextTier.TIER_1):
        """Initialize with default max tier to include."""
        self.tier_policy = tier_policy
        self.stats = {
            "tier_1_accepted": 0,
            "tier_2_accepted": 0,
            "tier_3_filtered": 0,
        }

    def classify_addition(self, text: str, confidence: float) -> ContextTier:
        """Classify an addition by confidence."""
        if confidence >= 0.85:
            return ContextTier.TIER_1
        elif confidence >= 0.65:
            return ContextTier.TIER_2
        else:
            return ContextTier.TIER_3

    def should_include(self, addition: ContextAddition) -> bool:
        """Determine if addition passes the tier gate."""
        tier_order = [ContextTier.TIER_1, ContextTier.TIER_2, ContextTier.TIER_3]
        include = tier_order.index(addition.tier) <= tier_order.index(self.tier_policy)

        # Record for metrics — consistent counting (accept vs. filter)
        if include:
            if addition.tier == ContextTier.TIER_1:
                self.stats["tier_1_accepted"] += 1
            elif addition.tier == ContextTier.TIER_2:
                self.stats["tier_2_accepted"] += 1
            elif addition.tier == ContextTier.TIER_3:
                self.stats["tier_3_filtered"] += 1  # "Filtered" = accepted at policy level
        else:
            # Rejected by tier policy
            self.stats[f"tier_{tier_order.index(addition.tier) + 1}_rejected"] = \
                self.stats.get(f"tier_{tier_order.index(addition.tier) + 1}_rejected", 0) + 1

        return include

    def filter_additions(self, additions: List[ContextAddition]) -> List[ContextAddition]:
        """Filter additions by tier gate."""
        return [a for a in additions if self.should_include(a)]

    def get_stats(self) -> Dict[str, int]:
        """Return classification statistics."""
        return self.stats.copy()


class EntropyDetector:
    """Detects contradictions in Pipeline Context (entropy detection)."""

    def __init__(self, threshold: float = 0.6):
        """Initialize with entropy threshold (0.0-1.0)."""
        self.threshold = threshold
        self.detections: List[Tuple[int, str]] = []  # (iteration, reason)

    def detect(self, pipeline: PipelineContext) -> bool:
        """Check if entropy exceeds threshold. Returns True if contradiction detected."""
        if pipeline.entropy_score >= self.threshold:
            self.detections.append((
                len(pipeline.additions),
                f"Entropy {pipeline.entropy_score:.2%} >= {self.threshold:.2%}"
            ))
            return True
        return False

    def report(self) -> str:
        """Generate a report of detections."""
        if not self.detections:
            return "No contradictions detected."

        report_lines = [f"Contradiction Detection Report ({len(self.detections)} total):\n"]
        for iteration, reason in self.detections:
            report_lines.append(f"  Iteration {iteration}: {reason}")

        return "\n".join(report_lines)


def build_dual_layer_prompt(
    original: OriginalContext,
    pipeline: PipelineContext,
    quality_gate: ContextQualityGate,
) -> str:
    """Build prompt with both Original (immutable) and Pipeline (filtered) contexts.

    This is the core k=1 checkpoint: both layers visible in output.
    """
    prompt_parts = [
        original.to_prompt_section(),
        "\n",
        pipeline.to_prompt_section(include_tier=quality_gate.tier_policy),
    ]

    return "\n".join(p for p in prompt_parts if p)


# ============================================================================
# Production-Ready Helpers
# ============================================================================

def degrade_to_original(original: OriginalContext, reason: str) -> PipelineContext:
    """Create a degraded PipelineContext (Original only, no additions).

    Used when pipeline fails (fail-closed pattern).
    """
    logger.warning(f"Degrading to Original Context only: {reason}")
    pipeline = PipelineContext(original=original, is_degraded=True)
    return pipeline


def validate_context_fidelity(original: OriginalContext, pipeline: PipelineContext) -> bool:
    """Validate that Original Context hash is still intact (no corruption).

    Fail-closed: returns False if integrity check fails.
    Computes current hash from fields and compares to stored hash.
    """
    content = f"{original.task_description}|{original.user_intent}|{original.session_id}|{original.tenant_id}"
    current_hash = hashlib.sha256(content.encode()).hexdigest()

    if current_hash != original.hash_sha256:
        logger.error(
            f"Original Context integrity check FAILED: "
            f"stored={original.hash_sha256[:16]}... current={current_hash[:16]}..."
        )
        return False
    return True

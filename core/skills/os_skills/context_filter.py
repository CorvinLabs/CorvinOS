"""
ADR-0528: Context Filter Architecture (L10 Refinement) — Phase 2a Static Filtering

Filters context blocks by relevance → reduces noise → improves routing accuracy.
Load-bearing rule: Audit-first (every decision logged).

Pattern:
  1. Score context block via static rules (task_history=0.9, user_profile=0.7, ...)
  2. If score >= threshold (0.7): INCLUDE
  3. If score < threshold & block_size > 500 tokens: Ask LLM (Phase 2b)
  4. If all blocks filtered: fallback to largest block
  5. Log every decision to audit chain
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List, Tuple
import json


class ContextCategory(str, Enum):
    """Context block categories with static relevance scores."""
    TASK_HISTORY = "task_history"
    USER_PROFILE = "user_profile"
    SESSION_STATE = "session_state"
    CONVERSATION_RECALL = "conversation_recall"
    SYSTEM_MESSAGES = "system_messages"


@dataclass(frozen=True)
class ContextBlock:
    """Immutable context block with metadata."""
    id: str                                # "task_history" | "user_profile" | etc.
    content: str                           # actual context text
    size_tokens: int                       # approximate token count
    category: ContextCategory              # enum: TASK_HISTORY, USER_PROFILE, etc.
    timestamp: datetime                    # when captured


@dataclass(frozen=True)
class FilterDecision:
    """Immutable filter decision (audit-ready)."""
    block_id: str
    score: float                           # static score [0.0, 1.0]
    threshold: float                       # typically 0.7
    action: str                            # "include" | "filter" | "lm_ask" | "fallback"
    lm_response: Optional[str] = None      # LLM result if asked (Phase 2b)
    reason: str = ""                       # human-readable explanation
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True)
class FilterConfig:
    """Immutable filter configuration."""
    static_scores: Dict[str, float] = field(default_factory=lambda: {
        "task_history": 0.9,
        "user_profile": 0.7,
        "session_state": 0.5,
        "conversation_recall": 0.8,
        "system_messages": 0.95,
    })
    threshold: float = 0.7
    lm_timeout_ms: int = 100               # Phase 2b: LLM timeout
    min_block_size_for_lm: int = 500       # Phase 2b: only ask LLM for large blocks
    include_fallback: bool = True          # if all filtered, include largest


class StaticScorer:
    """Score context blocks via static rules (deterministic, auditable)."""

    def __init__(self, config: Optional[FilterConfig] = None):
        self.config = config or FilterConfig()

    def score(self, block: ContextBlock) -> float:
        """Get static score for a context block."""
        # Look up score by category name
        category_key = block.category.value
        return self.config.static_scores.get(category_key, 0.5)  # default: neutral


class ContextFilterAuditEvent:
    """Audit event schema for context filtering decisions."""

    @staticmethod
    def from_decision(
        decision: FilterDecision,
        block_id: str,
        tenant_id: str = "_default",
    ) -> Dict[str, Any]:
        """Create audit event from filter decision."""
        return {
            "event_type": "context_filtered",
            "timestamp": decision.timestamp.isoformat(),
            "tenant_id": tenant_id,
            "block_id": block_id,
            "score": decision.score,
            "threshold": decision.threshold,
            "action": decision.action,
            "reason": decision.reason,
            "lm_response": decision.lm_response,
            "audit_hash": "",  # Will be filled by audit chain
        }


def filter_context(
    blocks: List[ContextBlock],
    config: Optional[FilterConfig] = None,
    lm_classify_fn=None,  # Phase 2b: optional LLM classifier
) -> Tuple[List[ContextBlock], List[FilterDecision]]:
    """
    Filter context blocks by relevance; return included blocks + decisions.

    Algorithm (ADR-0528):
      1. Score each block via static rules
      2. If score >= threshold: INCLUDE
      3. If score < threshold & size > min_block_size_for_lm: Ask LLM (if available)
      4. Fallback: if all filtered, include largest block
      5. Log all decisions to audit chain

    Args:
        blocks: List of context blocks to filter
        config: Filter configuration (defaults to ADR-0528 standard)
        lm_classify_fn: Optional LLM classifier for Phase 2b (not wired yet)

    Returns:
        (included_blocks, filter_decisions) — decisions logged for audit trail
    """
    config = config or FilterConfig()
    scorer = StaticScorer(config)

    decisions: List[FilterDecision] = []
    included: List[ContextBlock] = []

    # Score and decide for each block
    for block in blocks:
        score = scorer.score(block)

        if score >= config.threshold:
            # High-scoring block → INCLUDE
            decision = FilterDecision(
                block_id=block.id,
                score=score,
                threshold=config.threshold,
                action="include",
                reason=f"score {score:.2f} >= threshold {config.threshold}",
            )
            included.append(block)
            decisions.append(decision)
        elif block.size_tokens > config.min_block_size_for_lm and lm_classify_fn:
            # Large block + LLM available (Phase 2b) → Ask LLM
            decision = FilterDecision(
                block_id=block.id,
                score=score,
                threshold=config.threshold,
                action="lm_ask",
                reason=f"size {block.size_tokens} > {config.min_block_size_for_lm}, LLM available",
            )
            # TODO: Wire LLM classifier (Phase 2b)
            decisions.append(decision)
        else:
            # Small block → INCLUDE (low risk)
            decision = FilterDecision(
                block_id=block.id,
                score=score,
                threshold=config.threshold,
                action="include",
                reason=f"small block ({block.size_tokens} tokens), safe to include",
            )
            included.append(block)
            decisions.append(decision)

    # Fallback: if all filtered, include largest block
    if not included and config.include_fallback and blocks:
        largest = max(blocks, key=lambda b: b.size_tokens)
        included.append(largest)
        # Update last decision to reflect fallback
        for i, d in enumerate(decisions):
            if d.block_id == largest.id:
                decisions[i] = FilterDecision(
                    block_id=largest.id,
                    score=d.score,
                    threshold=d.threshold,
                    action="fallback",
                    reason="all blocks filtered; fallback: include largest",
                )
                break

    return included, decisions


def validate_no_pii(filtered_context: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Validate that filtered context has no PII.

    Returns:
        (is_safe, found_pii_fields)
    """
    pii_patterns = ["email", "phone", "password", "ssn", "credit_card", "secret", "token", "api_key"]
    found_pii = []

    for field, value in filtered_context.items():
        field_lower = field.lower()
        for pii_pattern in pii_patterns:
            if pii_pattern in field_lower:
                found_pii.append(field)
                break

    return len(found_pii) == 0, found_pii

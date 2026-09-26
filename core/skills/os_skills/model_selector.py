"""Tier 1 Router: Complexity-based model selection (Haiku/Sonnet/Opus).

k=2 Implementation: Stratified sampling per complexity level.
- simple: 30–40% Haiku, 60–70% Sonnet, 0% Opus
- medium: 10% Haiku, 50% Sonnet, 40% Opus
- complex: 0% Haiku, 20% Sonnet, 80% Opus

Audit-safe: no PII, no prompts, only task_id/complexity/model (ADR-0297).
"""
from dataclasses import dataclass
from enum import Enum
import hashlib


class Complexity(str, Enum):
    """Task complexity levels."""
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"


class ModelTier(str, Enum):
    """Available model tiers."""
    HAIKU = "haiku"
    SONNET = "sonnet"
    OPUS = "opus"


@dataclass(frozen=True)
class ModelSelectionDecision:
    """Immutable, audit-safe model selection decision.
    
    Fields: task_id, complexity, selected_model, stratification_bucket.
    No PII, no prompts. Safe for audit_backend.emit() (ADR-0297).
    """
    task_id: str
    complexity: str
    selected_model: str
    stratification_bucket: float  # [0.0, 1.0], deterministic per task_id


class Tier1Router:
    """Complexity-based model selection with stratified sampling."""
    
    STRATIFICATION = {
        Complexity.SIMPLE: {
            ModelTier.HAIKU: 0.35,    # 30–40%
            ModelTier.SONNET: 0.65,   # 60–70%
            ModelTier.OPUS: 0.0,      # 0%
        },
        Complexity.MEDIUM: {
            ModelTier.HAIKU: 0.10,    # 10%
            ModelTier.SONNET: 0.50,   # 50%
            ModelTier.OPUS: 0.40,     # 40%
        },
        Complexity.COMPLEX: {
            ModelTier.HAIKU: 0.0,     # 0%
            ModelTier.SONNET: 0.20,   # 20%
            ModelTier.OPUS: 0.80,     # 80%
        },
    }
    
    MODEL_NAMES = {
        ModelTier.HAIKU: "claude-haiku-4-5-20251001",
        ModelTier.SONNET: "claude-sonnet-5",
        ModelTier.OPUS: "claude-opus-5-5",
    }
    
    def route(self, task_id: str, complexity: str) -> ModelSelectionDecision:
        """Select model based on complexity."""
        if complexity not in [c.value for c in Complexity]:
            raise ValueError(f"Unknown complexity: {complexity}")
        
        bucket = self._hash_to_bucket(task_id)
        complexity_enum = Complexity(complexity)
        model_tier = self._select_tier(complexity_enum, bucket)
        model_name = self.MODEL_NAMES[model_tier]
        
        return ModelSelectionDecision(
            task_id=task_id,
            complexity=complexity,
            selected_model=model_name,
            stratification_bucket=bucket,
        )
    
    def _hash_to_bucket(self, task_id: str) -> float:
        """Convert task_id to deterministic bucket [0.0, 1.0]."""
        hash_bytes = hashlib.sha256(task_id.encode("utf-8")).digest()
        hash_int = int.from_bytes(hash_bytes[:8], byteorder="big")
        return (hash_int % 10000) / 10000.0
    
    def _select_tier(self, complexity: Complexity, bucket: float) -> ModelTier:
        """Select model tier based on complexity + bucket."""
        rates = self.STRATIFICATION[complexity]
        
        # Cumulative selection
        haiku_threshold = rates[ModelTier.HAIKU]
        sonnet_threshold = haiku_threshold + rates[ModelTier.SONNET]
        
        if bucket < haiku_threshold:
            return ModelTier.HAIKU
        elif bucket < sonnet_threshold:
            return ModelTier.SONNET
        else:
            return ModelTier.OPUS

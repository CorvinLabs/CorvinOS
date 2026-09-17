#!/usr/bin/env python3
"""
Extensible Task Classification Interface for CorvinOS

Design principles:
1. Multi-dimensional classification (Model, Complexity, Context, etc.)
2. Typed dimensions (not arbitrary dicts)
3. Composable & plugin-extendable
4. Backward-compatible with ADR-0845 (Model Selector Tier 1-3)
5. Constraint validation (no impossible combinations)

Usage:
  classifier = TaskClassifier()
  classification = classifier.classify(instruction)
  # Returns: TaskClassification(
  #   model_class=ModelClass.SONNET,
  #   complexity=ComplexityLevel.MEDIUM,
  #   context_need=ContextWindow.MEDIUM,
  #   reasoning_depth=ReasoningDepth.DEEP,
  # )

Author: Claude Haiku 4.5
Date: 2026-09-16
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Protocol, Optional, Dict, List, Any, Callable
from abc import ABC, abstractmethod
import json


# ============================================================================
# DIMENSION DEFINITIONS (Typed Enums)
# ============================================================================

class ModelClass(Enum):
    """Model performance class (independent of actual model names)."""
    SMALL = auto()      # Haiku: 4B params, <8k context, fast, cheap
    MEDIUM = auto()     # Sonnet: 100B params, 200k context, balanced
    LARGE = auto()      # Opus: 200B+ params, deep reasoning, expensive
    # Future: XLARGE, SPECIALIST, etc. (can add without breaking)

class ComplexityLevel(Enum):
    """Task complexity (reasoning depth required)."""
    TRIVIAL = 1         # E.g., "list files", "format JSON"
    SIMPLE = 2          # E.g., "fix typo", "add docstring"
    MEDIUM = 3          # E.g., "code review", "write test", "refactor"
    COMPLEX = 4         # E.g., "design API", "write ADR", "extract concept"
    EXPERT = 5          # E.g., "novel research", "deep debugging", "architecture"

class ContextWindow(Enum):
    """Required context window size."""
    SHORT = "short"          # < 4k tokens (documents, snippets)
    MEDIUM = "medium"        # 8-32k tokens (files, small projects)
    LONG = "long"            # 100k+ tokens (entire repos, large context)
    UNRESTRICTED = "unrestricted"  # No window limit (future)

class ReasoningDepth(Enum):
    """Type of reasoning needed."""
    NONE = 0                 # Pure execution (rote)
    SHALLOW = 1              # Basic reasoning (alternatives, simple trade-offs)
    DEEP = 2                 # Complex reasoning (design, implications)
    EXPERT = 3               # Expert-level reasoning (research, novel insights)

class Domain(Enum):
    """Problem domain (for future routing policies)."""
    CODE = "code"            # Programming, review, refactoring
    TEXT = "text"            # Writing, editing, documentation
    MATH = "math"            # Calculations, proofs, analysis
    REASONING = "reasoning"  # Logic, planning, design
    DATA = "data"            # Analysis, transformation, visualization
    # Future: RESEARCH, CREATIVITY, etc.

class DocumentType(Enum):
    """Document class (signals strategic vs. execution)."""
    ADR = "adr"              # Architectural Decision Record
    CONCEPT = "concept"      # Generalized working method
    IMPLEMENTATION = "impl"  # Code, tests, scripts
    GUIDE = "guide"          # How-to, tutorial, reference
    UNKNOWN = "unknown"      # Unable to classify


# ============================================================================
# CLASSIFICATION RESULT (Central Data Structure)
# ============================================================================

@dataclass(frozen=True)
class TaskClassification:
    """Immutable classification result for a task."""

    # Core dimensions (always present)
    model_class: ModelClass
    complexity: ComplexityLevel
    context_window: ContextWindow
    reasoning_depth: ReasoningDepth

    # Optional dimensions (can be None; composable)
    domain: Optional[Domain] = None
    document_type: Optional[DocumentType] = None

    # Metadata
    confidence: float = 1.0  # 0-1 score
    source: str = "default"  # Which classifier produced this?
    raw_scores: Dict[str, float] = field(default_factory=dict)  # Debug info
    constraints_violated: List[str] = field(default_factory=list)  # Validation warnings

    def is_valid(self) -> bool:
        """Check if classification passes all constraints."""
        return len(self.constraints_violated) == 0

    def to_dict(self) -> dict:
        """Serialize for storage/logging."""
        return {
            "model_class": self.model_class.name,
            "complexity": self.complexity.name,
            "context_window": self.context_window.value,
            "reasoning_depth": self.reasoning_depth.name,
            "domain": self.domain.value if self.domain else None,
            "document_type": self.document_type.value if self.document_type else None,
            "confidence": self.confidence,
            "source": self.source,
            "is_valid": self.is_valid()
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TaskClassification":
        """Deserialize from dict (backward-compatible)."""
        return cls(
            model_class=ModelClass[data["model_class"]],
            complexity=ComplexityLevel[data["complexity"]],
            context_window=ContextWindow(data["context_window"]),
            reasoning_depth=ReasoningDepth[data["reasoning_depth"]],
            domain=Domain(data["domain"]) if data.get("domain") else None,
            document_type=DocumentType(data["document_type"]) if data.get("document_type") else None,
            confidence=data.get("confidence", 1.0),
            source=data.get("source", "unknown")
        )


# ============================================================================
# CONSTRAINT SYSTEM (Validation Rules)
# ============================================================================

class ConstraintValidator:
    """Validate classification combinations."""

    # Define impossible/undesirable combinations
    CONSTRAINTS = [
        # (condition, violation_message)
        (
            lambda c: c.model_class == ModelClass.SMALL and c.context_window == ContextWindow.LONG,
            "SMALL model cannot handle LONG context (max ~8k tokens)"
        ),
        (
            lambda c: c.model_class == ModelClass.SMALL and c.complexity >= ComplexityLevel.COMPLEX,
            "SMALL model not suitable for COMPLEX tasks (lack reasoning depth)"
        ),
        (
            lambda c: c.reasoning_depth == ReasoningDepth.EXPERT and c.model_class == ModelClass.SMALL,
            "EXPERT reasoning requires LARGE model"
        ),
    ]

    @classmethod
    def validate(cls, classification: TaskClassification) -> List[str]:
        """Return list of constraint violations (empty = valid)."""
        violations = []
        for condition, message in cls.CONSTRAINTS:
            if condition(classification):
                violations.append(message)
        return violations


# ============================================================================
# CLASSIFIER PROTOCOL (Pluggable Architecture)
# ============================================================================

class TaskClassifierPlugin(ABC):
    """Base class for pluggable classifiers (ADR-0262 Plugin Builder pattern)."""

    @property
    @abstractmethod
    def plugin_id(self) -> str:
        """Unique identifier (e.g., 'classifier:keyword_heuristic')."""
        pass

    @property
    @abstractmethod
    def priority(self) -> int:
        """Priority in classifier chain (higher = runs first). 0-100."""
        pass

    @abstractmethod
    def can_classify(self, instruction: str, context: Dict[str, Any]) -> bool:
        """Quick check: can this classifier handle this task?"""
        pass

    @abstractmethod
    def classify(self, instruction: str, context: Dict[str, Any]) -> Optional[TaskClassification]:
        """Classify or return None (fallthrough to next)."""
        pass


# ============================================================================
# CENTRAL CLASSIFIER (Composition + Routing)
# ============================================================================

class TaskClassifier:
    """
    Central task classifier for CorvinOS.

    Uses plugin chain pattern (ADR-0262/0263):
    1. Try each classifier in priority order
    2. First classifier that can_classify() → classify() and return
    3. If none match, apply default heuristics
    4. Validate result against constraints
    5. Return TaskClassification (immutable)
    """

    def __init__(self):
        self.plugins: List[TaskClassifierPlugin] = []
        self._register_builtin_classifiers()

    def register_plugin(self, plugin: TaskClassifierPlugin):
        """Register a new classifier plugin."""
        self.plugins.append(plugin)
        self.plugins.sort(key=lambda p: p.priority, reverse=True)  # Higher priority first

    def _register_builtin_classifiers(self):
        """Register built-in classifiers."""
        self.register_plugin(DocumentTypeClassifier())
        self.register_plugin(KeywordHeuristicClassifier())
        self.register_plugin(ComplexityInferenceClassifier())

    def classify(self,
                instruction: str,
                file_path: Optional[str] = None,
                user_context: Optional[Dict[str, Any]] = None) -> TaskClassification:
        """
        Classify a task instruction.

        Args:
            instruction: Task description or code snippet
            file_path: Optional file being edited (for document-type detection)
            user_context: Optional additional context (user profile, preferences)

        Returns:
            TaskClassification (immutable, validated)
        """

        context = {
            "instruction": instruction,
            "file_path": file_path,
            "user_context": user_context or {}
        }

        # Try each classifier in priority order
        for plugin in self.plugins:
            if plugin.can_classify(instruction, context):
                classification = plugin.classify(instruction, context)
                if classification:
                    # Validate and mark violations
                    violations = ConstraintValidator.validate(classification)
                    if violations:
                        classification = TaskClassification(
                            model_class=classification.model_class,
                            complexity=classification.complexity,
                            context_window=classification.context_window,
                            reasoning_depth=classification.reasoning_depth,
                            domain=classification.domain,
                            document_type=classification.document_type,
                            confidence=classification.confidence * 0.8,  # Reduce confidence if violated
                            source=f"{plugin.plugin_id}:with_violations",
                            constraints_violated=violations
                        )
                    return classification

        # Fallback: Default conservative classification
        return TaskClassification(
            model_class=ModelClass.MEDIUM,
            complexity=ComplexityLevel.MEDIUM,
            context_window=ContextWindow.MEDIUM,
            reasoning_depth=ReasoningDepth.SHALLOW,
            confidence=0.5,
            source="fallback:default"
        )


# ============================================================================
# BUILTIN CLASSIFIERS (Examples)
# ============================================================================

class DocumentTypeClassifier(TaskClassifierPlugin):
    """Detect document type from file path → infer model class."""

    @property
    def plugin_id(self) -> str:
        return "classifier:document_type"

    @property
    def priority(self) -> int:
        return 100  # Highest priority

    def can_classify(self, instruction: str, context: Dict[str, Any]) -> bool:
        return context.get("file_path") is not None

    def classify(self, instruction: str, context: Dict[str, Any]) -> Optional[TaskClassification]:
        file_path = context["file_path"]

        # Strategic documents → Large model
        if "ADR" in file_path and ".md" in file_path:
            return TaskClassification(
                model_class=ModelClass.LARGE,  # Opus: deep reasoning
                complexity=ComplexityLevel.COMPLEX,
                context_window=ContextWindow.MEDIUM,
                reasoning_depth=ReasoningDepth.DEEP,
                document_type=DocumentType.ADR,
                source="document_type:adr",
                confidence=0.95
            )

        if "CONCEPT" in file_path and ".md" in file_path:
            return TaskClassification(
                model_class=ModelClass.LARGE,
                complexity=ComplexityLevel.COMPLEX,
                context_window=ContextWindow.MEDIUM,
                reasoning_depth=ReasoningDepth.DEEP,
                document_type=DocumentType.CONCEPT,
                source="document_type:concept",
                confidence=0.95
            )

        # Implementation files → Medium to Small
        if any(ext in file_path for ext in [".py", ".js", ".go", ".rs"]):
            return TaskClassification(
                model_class=ModelClass.MEDIUM,  # Sonnet: balanced
                complexity=ComplexityLevel.MEDIUM,
                context_window=ContextWindow.LONG,
                reasoning_depth=ReasoningDepth.SHALLOW,
                document_type=DocumentType.IMPLEMENTATION,
                domain=Domain.CODE,
                source="document_type:code",
                confidence=0.80
            )

        if "test" in file_path:
            return TaskClassification(
                model_class=ModelClass.SMALL,  # Haiku: straightforward
                complexity=ComplexityLevel.SIMPLE,
                context_window=ContextWindow.MEDIUM,
                reasoning_depth=ReasoningDepth.NONE,
                document_type=DocumentType.IMPLEMENTATION,
                domain=Domain.CODE,
                source="document_type:test",
                confidence=0.85
            )

        return None


class KeywordHeuristicClassifier(TaskClassifierPlugin):
    """Classify based on keywords in instruction."""

    @property
    def plugin_id(self) -> str:
        return "classifier:keyword_heuristic"

    @property
    def priority(self) -> int:
        return 80  # Second priority

    def can_classify(self, instruction: str, context: Dict[str, Any]) -> bool:
        return len(instruction) > 0

    def classify(self, instruction: str, context: Dict[str, Any]) -> Optional[TaskClassification]:
        lower = instruction.lower()

        # Strategic keywords
        strategic_keywords = ["design", "architecture", "why", "trade-off", "alternative", "concept"]
        if any(kw in lower for kw in strategic_keywords):
            return TaskClassification(
                model_class=ModelClass.LARGE,
                complexity=ComplexityLevel.COMPLEX,
                context_window=ContextWindow.MEDIUM,
                reasoning_depth=ReasoningDepth.DEEP,
                source="keyword:strategic",
                confidence=0.70
            )

        # Medium complexity keywords
        medium_keywords = ["review", "analyze", "refactor", "improve", "test case"]
        if any(kw in lower for kw in medium_keywords):
            return TaskClassification(
                model_class=ModelClass.MEDIUM,
                complexity=ComplexityLevel.MEDIUM,
                context_window=ContextWindow.MEDIUM,
                reasoning_depth=ReasoningDepth.SHALLOW,
                source="keyword:medium",
                confidence=0.65
            )

        # Simple keywords
        simple_keywords = ["fix", "add", "format", "simplify", "typo", "rename"]
        if any(kw in lower for kw in simple_keywords):
            return TaskClassification(
                model_class=ModelClass.SMALL,
                complexity=ComplexityLevel.SIMPLE,
                context_window=ContextWindow.SHORT,
                reasoning_depth=ReasoningDepth.NONE,
                source="keyword:simple",
                confidence=0.60
            )

        return None


class ComplexityInferenceClassifier(TaskClassifierPlugin):
    """Infer complexity from instruction length + structure."""

    @property
    def plugin_id(self) -> str:
        return "classifier:complexity_inference"

    @property
    def priority(self) -> int:
        return 50  # Low priority (fallback)

    def can_classify(self, instruction: str, context: Dict[str, Any]) -> bool:
        return True  # Always can fallback

    def classify(self, instruction: str, context: Dict[str, Any]) -> Optional[TaskClassification]:
        # Simple heuristic: instruction length ≈ complexity
        length = len(instruction)

        if length < 100:
            complexity = ComplexityLevel.SIMPLE
            model = ModelClass.SMALL
        elif length < 500:
            complexity = ComplexityLevel.MEDIUM
            model = ModelClass.MEDIUM
        else:
            complexity = ComplexityLevel.COMPLEX
            model = ModelClass.LARGE

        return TaskClassification(
            model_class=model,
            complexity=complexity,
            context_window=ContextWindow.MEDIUM,
            reasoning_depth=ReasoningDepth.SHALLOW,
            source="complexity_inference:length",
            confidence=0.40  # Low confidence for fallback
        )


# ============================================================================
# ROUTER INTERFACE (Translate Classification → Model + Strategy)
# ============================================================================

@dataclass
class RoutingDecision:
    """Result of routing based on classification."""
    model_id: str  # "claude-3-5-haiku", "claude-3-5-sonnet", "claude-opus-5"
    strategy: str  # "direct", "decomposed", "cached", etc.
    confidence: float
    reason: str

class RoutingPolicy(ABC):
    """Policy for translating classification → routing decision."""

    @abstractmethod
    def route(self, classification: TaskClassification) -> RoutingDecision:
        pass

class DefaultRoutingPolicy(RoutingPolicy):
    """Simple mapping: ModelClass → model_id."""

    MODEL_MAP = {
        ModelClass.SMALL: "claude-3-5-haiku-20241022",
        ModelClass.MEDIUM: "claude-3-5-sonnet-20241022",
        ModelClass.LARGE: "claude-opus-5",
    }

    def route(self, classification: TaskClassification) -> RoutingDecision:
        model_id = self.MODEL_MAP[classification.model_class]

        # Choose strategy based on classification
        if classification.context_window == ContextWindow.LONG:
            strategy = "batched"  # Split long context
        elif classification.reasoning_depth == ReasoningDepth.EXPERT:
            strategy = "recursive"  # May need follow-up calls
        else:
            strategy = "direct"

        return RoutingDecision(
            model_id=model_id,
            strategy=strategy,
            confidence=classification.confidence,
            reason=f"Classification {classification.source}: {classification.complexity.name}"
        )


# ============================================================================
# USAGE EXAMPLE
# ============================================================================

def example_usage():
    """Show how to use the task classifier."""

    classifier = TaskClassifier()

    # Example 1: ADR writing
    classification = classifier.classify(
        instruction="Write an ADR for the new model routing system, including alternatives and trade-offs",
        file_path="Corvin-ADR/decisions/ADR-0XXX.md"
    )
    print(f"ADR Classification: {classification.to_dict()}")
    # Expected: model_class=LARGE, complexity=COMPLEX, reasoning_depth=DEEP

    # Example 2: Test writing
    classification = classifier.classify(
        instruction="Write unit tests for the task classifier",
        file_path="tests/test_classifier.py"
    )
    print(f"Test Classification: {classification.to_dict()}")
    # Expected: model_class=SMALL, complexity=SIMPLE

    # Example 3: Code review (medium)
    classification = classifier.classify(
        instruction="Review this code for bugs and performance issues"
    )
    print(f"Code Review Classification: {classification.to_dict()}")
    # Expected: model_class=MEDIUM, complexity=MEDIUM

    # Routing
    policy = DefaultRoutingPolicy()
    for classification_ex in [
        classifier.classify("Fix typo in README"),
        classifier.classify("Design new caching layer", file_path="docs/design/caching.md"),
        classifier.classify("Write test case"),
    ]:
        routing = policy.route(classification_ex)
        print(f"Routing: {routing.model_id} via {routing.strategy} (confidence={routing.confidence})")


if __name__ == "__main__":
    example_usage()

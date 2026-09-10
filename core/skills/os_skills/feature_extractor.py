"""
Phase 2: Feature Extraction for Task Classification (ADR-0642)

Extracts deterministic features from task input to classify complexity level.
No LLM calls — pure Python logic.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional
import re


@dataclass(frozen=True)
class ExtractedFeatures:
    """Immutable feature set (audit-trail safe)."""
    token_estimate: int  # Estimated tokens in input
    keyword_complexity: str  # "simple" | "medium" | "complex"
    code_blocks: int  # Number of code blocks detected
    dependency_count: int  # Imports/dependencies mentioned
    reasoning_depth: int  # Estimated steps needed (1-5)
    has_system_prompt: bool  # Whether system context is present
    has_pseudocode: bool  # Whether pseudocode/logic flow is present
    intent_clarity: float  # 0.0-1.0, how clear the intent is

    def to_dict(self) -> Dict[str, any]:
        return {
            "token_estimate": self.token_estimate,
            "keyword_complexity": self.keyword_complexity,
            "code_blocks": self.code_blocks,
            "dependency_count": self.dependency_count,
            "reasoning_depth": self.reasoning_depth,
            "has_system_prompt": self.has_system_prompt,
            "has_pseudocode": self.has_pseudocode,
            "intent_clarity": self.intent_clarity,
        }


class FeatureExtractor:
    """Extract features from task input for classification."""

    # Keyword patterns by complexity (low → high)
    _SIMPLE_KEYWORDS = {
        "list", "explain", "translate", "summarize", "format", "rewrite",
        "fix typo", "spell check", "capitalize", "lowercase", "sort",
        "count", "find", "replace", "template"
    }

    _COMPLEX_KEYWORDS = {
        "design", "architect", "refactor", "optimize", "debug", "analyze",
        "prove", "derive", "implement", "algorithm", "system", "protocol",
        "framework", "distributed", "concurrent", "async", "performance",
        "security", "cryptography", "machine learning", "neural"
    }

    # Dependency keywords
    _DEPENDENCY_KEYWORDS = {
        "import", "require", "package", "library", "module", "dependency",
        "npm", "pip", "cargo", "maven", "gradle", "setup.py",
        "requirements.txt", "pyproject.toml", "Gemfile", "Dockerfile"
    }

    def extract(self, task_input: str, tenant_id: Optional[str] = None) -> ExtractedFeatures:
        """
        Extract features from task input.

        Args:
            task_input: The full task/prompt text
            tenant_id: Optional tenant scoping (audit trail)

        Returns:
            ExtractedFeatures with deterministic values
        """
        # Estimate tokens (rough: 1 token ≈ 4 chars)
        token_estimate = max(1, len(task_input) // 4)

        # Extract code blocks
        code_blocks = len(re.findall(r'```[\s\S]*?```', task_input))

        # Detect dependencies
        dependency_count = 0
        for keyword in self._DEPENDENCY_KEYWORDS:
            dependency_count += task_input.lower().count(keyword)

        # Determine keyword-based complexity
        task_lower = task_input.lower()
        complex_score = sum(1 for kw in self._COMPLEX_KEYWORDS if kw in task_lower)
        simple_score = sum(1 for kw in self._SIMPLE_KEYWORDS if kw in task_lower)

        if complex_score > simple_score:
            keyword_complexity = "complex"
        elif simple_score > 0:
            keyword_complexity = "simple"
        else:
            keyword_complexity = "medium"

        # Detect system prompt / context
        has_system_prompt = "system:" in task_lower or "context:" in task_lower or \
                           "background:" in task_lower or "you are" in task_lower[:100]

        # Detect pseudocode / logic flow
        has_pseudocode = bool(re.search(r'(if|for|while|function|return|step|then|else)', task_lower))

        # Estimate reasoning depth (1-5 based on task length and complexity)
        reasoning_depth = min(5, max(1, token_estimate // 100 + (1 if complex_score > 0 else 0)))

        # Intent clarity (heuristic based on punctuation and structure)
        intent_clarity = self._estimate_intent_clarity(task_input)

        return ExtractedFeatures(
            token_estimate=token_estimate,
            keyword_complexity=keyword_complexity,
            code_blocks=code_blocks,
            dependency_count=dependency_count,
            reasoning_depth=reasoning_depth,
            has_system_prompt=has_system_prompt,
            has_pseudocode=has_pseudocode,
            intent_clarity=intent_clarity,
        )

    def _estimate_intent_clarity(self, task_input: str) -> float:
        """
        Estimate how clear the intent is (0.0-1.0).

        Heuristics:
        - Question marks: +0.2
        - Exclamation marks: +0.1
        - Multiple sentences: +0.15
        - Bullet points: +0.2
        - Capitalization at start: +0.1
        """
        clarity = 0.0

        if task_input and task_input[0].isupper():
            clarity += 0.1

        if '?' in task_input:
            clarity += 0.2

        if '!' in task_input:
            clarity += 0.1

        sentence_count = len(re.split(r'[.!?]+', task_input)) - 1
        if sentence_count > 1:
            clarity += min(0.15, sentence_count * 0.05)

        if re.search(r'^\s*[-•*]\s', task_input, re.MULTILINE):
            clarity += 0.2

        return min(1.0, clarity)

    @staticmethod
    def batch_extract(
        tasks: List[str],
        tenant_id: Optional[str] = None
    ) -> List[ExtractedFeatures]:
        """Extract features from a batch of tasks."""
        extractor = FeatureExtractor()
        return [extractor.extract(task, tenant_id) for task in tasks]

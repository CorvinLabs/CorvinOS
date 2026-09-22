"""
Task Complexity Classifier for Workflow Optimizer (Phase 10 Stream 1)

Deterministic feature extraction for task complexity classification.
Used by WorkflowOptimizer.classify_complexity() to score tasks as
simple/medium/complex without requiring LLM calls.

**Design:**
- All extraction deterministic (no randomness, no LLM)
- Features: token count, code blocks, keyword density, nesting, APIs, multi-file
- Scoring: weighted sum (0–1)
- Thresholds: 0.3 → simple, 0.3–0.7 → medium, >0.7 → complex

**Compliance:**
- GDPR: No PII extraction or retention
- Audit-safe: Feature extraction is stateless + pure

**Testing:**
- Unit tests: 10+ tests for each feature extractor
- Integration tests: 5 end-to-end classification tests
"""

import re
from dataclasses import dataclass
from typing import Dict, List

# Complexity markers by type
CODE_COMPLEXITY_KEYWORDS = [
    "algorithm", "recursion", "graph", "tree", "sorting", "searching",
    "dynamic programming", "memoization", "optimization",
    "binary search", "hash table", "linked list", "queue", "stack"
]

SYSTEM_DESIGN_KEYWORDS = [
    "system design", "architecture", "scalability", "performance",
    "bottleneck", "throughput", "latency", "load balancing",
    "caching", "replication", "sharding", "consistency",
    "distributed", "concurrent", "parallel", "asynchronous"
]

SECURITY_KEYWORDS = [
    "security", "encryption", "authentication", "authorization",
    "vulnerability", "CVE", "attack", "defense", "intrusion",
    "SSL", "TLS", "HTTPS", "HTTPS", "certificate", "PKI",
    "cryptography", "hash", "signature", "compliance", "GDPR"
]

INFRASTRUCTURE_KEYWORDS = [
    "deployment", "Docker", "Kubernetes", "cloud", "AWS", "GCP", "Azure",
    "infrastructure", "DevOps", "CI/CD", "monitoring", "observability",
    "logging", "tracing", "metrics", "alerting", "database", "SQL",
    "NoSQL", "migration", "terraform", "Ansible"
]

# All complexity keywords combined (for keyword density)
ALL_COMPLEXITY_KEYWORDS = (
    CODE_COMPLEXITY_KEYWORDS +
    SYSTEM_DESIGN_KEYWORDS +
    SECURITY_KEYWORDS +
    INFRASTRUCTURE_KEYWORDS
)


@dataclass
class TaskFeatures:
    """Extracted features from a task."""
    token_count: int
    code_block_count: int
    keyword_density: float  # 0–1
    max_nesting_depth: int
    external_api_refs: int
    multi_file_indicator: float  # 0–1
    structured_data_complexity: float  # 0–1 (JSON/XML nesting)
    language_complexity_score: float  # 0–1 (vocabulary diversity)


class TaskComplexityClassifier:
    """Deterministic task complexity classifier.

    Uses rule-based feature extraction (no ML, no LLM) to classify
    tasks as simple/medium/complex.

    **Features Extracted:**
    1. Token count: raw text length (normalized 0–1)
    2. Code blocks: markdown ``` blocks (0–1 normalized)
    3. Keyword density: complexity keywords per total keywords (0–1)
    4. Nesting depth: max indentation level (0–1 normalized)
    5. External API refs: URLs, curl commands, API calls (0–1)
    6. Multi-file indicator: mentions of multiple files (0–1)
    7. Structured data: JSON/XML nesting complexity (0–1)
    8. Language complexity: vocabulary diversity (0–1)

    **Scoring:**
    score = weighted sum of features (0–1)
    if score < 0.3: SIMPLE
    if 0.3 ≤ score < 0.7: MEDIUM
    if score ≥ 0.7: COMPLEX
    """

    def extract_features(self, task_content: str) -> TaskFeatures:
        """Extract all features from task content.

        Args:
            task_content: Task description or prompt

        Returns:
            TaskFeatures dataclass with all extracted features
        """
        # Individual feature extractors (all deterministic)
        token_count = self._count_tokens(task_content)
        code_blocks = self._count_code_blocks(task_content)
        keyword_density = self._compute_keyword_density(task_content)
        nesting_depth = self._compute_nesting_depth(task_content)
        api_refs = self._count_external_api_refs(task_content)
        multi_file = self._detect_multi_file(task_content)
        structured_data_complexity = self._compute_structured_data_complexity(task_content)
        language_complexity = self._compute_language_complexity(task_content)

        return TaskFeatures(
            token_count=token_count,
            code_block_count=code_blocks,
            keyword_density=keyword_density,
            max_nesting_depth=nesting_depth,
            external_api_refs=api_refs,
            multi_file_indicator=multi_file,
            structured_data_complexity=structured_data_complexity,
            language_complexity_score=language_complexity,
        )

    def score_features(self, features: TaskFeatures) -> float:
        """Score extracted features (0–1 range).

        Uses weighted sum of normalized features.

        Args:
            features: Extracted TaskFeatures

        Returns:
            Score 0–1 (0=simple, 1=complex)
        """
        # Normalize each feature to 0–1
        token_score = min(features.token_count / 5000.0, 1.0)
        code_score = min(features.code_block_count / 5.0, 1.0)
        keyword_score = features.keyword_density
        nesting_score = min(features.max_nesting_depth / 20.0, 1.0)
        api_score = min(features.external_api_refs / 10.0, 1.0)
        multi_file_score = features.multi_file_indicator
        struct_score = features.structured_data_complexity
        lang_score = features.language_complexity_score

        # Weighted sum (weights must sum to 1.0)
        score = (
            token_score * 0.15 +           # 15% text length
            code_score * 0.2 +             # 20% code content
            keyword_score * 0.25 +         # 25% complexity keywords
            nesting_score * 0.1 +          # 10% code structure
            api_score * 0.1 +              # 10% external integration
            multi_file_score * 0.05 +      # 5% multi-file scope
            struct_score * 0.1 +           # 10% data structure complexity
            lang_score * 0.05               # 5% vocabulary diversity
        )

        return min(max(score, 0.0), 1.0)  # Clamp to [0, 1]

    # ========================================================================
    # Feature Extractors (all deterministic, stateless)
    # ========================================================================

    def _count_tokens(self, text: str) -> int:
        """Count tokens in text (simple split-based approximation).

        Note: This is a rough approximation. For production, integrate
        with actual tokenizer (e.g., tiktoken for Claude models).
        """
        return len(text.split())

    def _count_code_blocks(self, text: str) -> int:
        """Count markdown code blocks (```...```)."""
        return text.count("```") // 2  # Each block has opening + closing

    def _compute_keyword_density(self, text: str) -> float:
        """Compute density of complexity keywords in text.

        Returns:
            Float 0–1 (ratio of complexity keywords to total words)
        """
        text_lower = text.lower()
        total_words = len(text.split())

        if total_words == 0:
            return 0.0

        # Count keyword occurrences
        keyword_count = sum(
            text_lower.count(kw.lower()) for kw in ALL_COMPLEXITY_KEYWORDS
        )

        # Density: keywords per word, capped at 1.0
        density = min(keyword_count / max(total_words, 1), 1.0)
        return density

    def _compute_nesting_depth(self, text: str) -> int:
        """Compute max indentation/nesting depth.

        Useful for code-heavy tasks that may have nested structures,
        functions, classes, etc.
        """
        lines = text.split("\n")
        max_indent = 0

        for line in lines:
            # Count leading spaces (tabs = 4 spaces)
            indent = len(line) - len(line.lstrip())
            indent = indent // 4 if indent > 0 else 0  # Normalize tabs
            max_indent = max(max_indent, indent)

        return max_indent

    def _count_external_api_refs(self, text: str) -> int:
        """Count references to external APIs, URLs, commands, etc."""
        count = 0
        count += len(re.findall(r"https?://", text))  # URLs
        count += text.count("curl ")  # curl commands
        count += text.count("API")  # API keyword
        count += text.count("endpoint")  # REST endpoint
        count += text.count("webhook")  # Webhook
        count += text.count("socket")  # Socket connections
        count += text.count("TCP")  # Network protocols
        count += text.count("UDP")
        return count

    def _detect_multi_file(self, text: str) -> float:
        """Detect multi-file scope (0=single file, 1=multi-file).

        Returns:
            0.0 for single-file tasks, 1.0 for multi-file
        """
        text_lower = text.lower()

        # Strong indicators of multi-file scope
        multi_file_keywords = [
            "multiple files", "across", "modules", "packages",
            "monorepo", "repository", "codebase", "refactor",
            "integration", "migration", "database schema",
        ]

        has_multi_file = any(kw in text_lower for kw in multi_file_keywords)

        return 1.0 if has_multi_file else 0.0

    def _compute_structured_data_complexity(self, text: str) -> float:
        """Compute complexity of structured data (JSON/XML nesting).

        Counts brackets/braces to estimate data structure depth.

        Returns:
            Float 0–1 (estimated complexity of nested data structures)
        """
        # Count bracket types
        json_brackets = text.count("{") + text.count("}")
        xml_brackets = text.count("<") + text.count(">")
        array_brackets = text.count("[") + text.count("]")

        # Simple metric: more brackets = more structure = more complex
        total_brackets = json_brackets + xml_brackets + array_brackets
        complexity = min(total_brackets / 100.0, 1.0)  # Normalize to 0–1

        return complexity

    def _compute_language_complexity(self, text: str) -> float:
        """Estimate language complexity via vocabulary diversity.

        Higher vocabulary diversity (more unique words) often indicates
        more complex domain or task.

        Returns:
            Float 0–1 (estimated vocabulary complexity)
        """
        words = text.lower().split()
        if not words:
            return 0.0

        unique_words = len(set(words))
        diversity = unique_words / len(words)  # Type-token ratio

        # Scale to 0–1 range (typical diversity 0.4–0.8)
        normalized = min(max((diversity - 0.3) / 0.5, 0.0), 1.0)

        return normalized


# Singleton instance for module-level use
_classifier = TaskComplexityClassifier()


def extract_features(task_content: str) -> TaskFeatures:
    """Module-level convenience function."""
    return _classifier.extract_features(task_content)


def score_task(task_content: str) -> float:
    """Score task complexity (0–1 range).

    Args:
        task_content: Task description

    Returns:
        Score 0–1 (0=simple, 1=complex)
    """
    features = _classifier.extract_features(task_content)
    return _classifier.score_features(features)


def classify_task(task_content: str) -> str:
    """Classify task as simple/medium/complex.

    Args:
        task_content: Task description

    Returns:
        "simple", "medium", or "complex"
    """
    score = score_task(task_content)

    if score < 0.3:
        return "simple"
    elif score < 0.7:
        return "medium"
    else:
        return "complex"

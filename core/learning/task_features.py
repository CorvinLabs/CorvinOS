"""Feature Extraction for Learned Task Complexity Classifier (ADR-0393).

Extracts normalized feature vectors from task text, context, and user history
for use in ML classification model.

Feature Types:
  1. Text Features: TF-IDF vectors from task description
  2. Context Features: domain keywords, prior complexity, task type
  3. User Features: historical complexity distribution, success rate
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer


@dataclass
class FeatureVector:
    """Normalized feature vector for ML inference."""
    text_features: np.ndarray  # TF-IDF (50 dims)
    context_features: np.ndarray  # Context (32 dims)
    user_features: np.ndarray  # User (32 dims)
    raw_text: str
    task_id: Optional[str] = None

    def to_array(self) -> np.ndarray:
        """Concatenate all features into single vector (114 dims)."""
        return np.concatenate([
            self.text_features,
            self.context_features,
            self.user_features,
        ])


class TaskFeatureExtractor:
    """Extract normalized feature vectors from tasks, context, and user history."""

    # Domain-specific keywords for context features
    _DOMAIN_KEYWORDS_BY_COMPLEXITY = {
        "simple": {
            "rename", "delete", "remove", "format", "comment", "typo",
            "fix", "syntax", "lint", "sort", "reorder", "cleanup", "clean",
            "trim", "strip", "pad", "align", "case", "replace", "substitute",
        },
        "complex": {
            "refactor", "design", "architecture", "optimize", "implement",
            "integrate", "build", "create", "feature", "framework", "pattern",
            "algorithm", "performance", "security", "scale", "new", "rewrite",
            "restructure", "migrate", "transform", "generalize", "abstract",
            "architect", "complex", "sophisticated",
        },
    }

    def __init__(self, tfidf_max_features: int = 50):
        """Initialize feature extractor with TF-IDF vectorizer.

        Args:
            tfidf_max_features: Maximum number of TF-IDF features to extract
        """
        self.tfidf_max_features = tfidf_max_features
        self.tfidf_vectorizer = TfidfVectorizer(
            max_features=tfidf_max_features,
            lowercase=True,
            stop_words="english",
            min_df=1,
            max_df=0.95,
        )
        self._tfidf_fitted = False

    def fit(self, texts: list[str]) -> None:
        """Fit TF-IDF vectorizer on training texts.

        Args:
            texts: List of task descriptions to fit
        """
        if not texts:
            raise ValueError("Cannot fit TF-IDF on empty text list")
        self.tfidf_vectorizer.fit(texts)
        self._tfidf_fitted = True

    def extract_text_features(self, task_text: str) -> np.ndarray:
        """Extract TF-IDF text features from task description.

        Args:
            task_text: Task description text

        Returns:
            Normalized vector (50 dims)
        """
        if not self._tfidf_fitted:
            # Return zero vector if not fitted yet
            return np.zeros(self.tfidf_max_features)

        if not task_text or not isinstance(task_text, str):
            return np.zeros(self.tfidf_max_features)

        vector = self.tfidf_vectorizer.transform([task_text]).toarray()[0]
        # Pad/truncate to expected size
        if len(vector) < self.tfidf_max_features:
            vector = np.pad(vector, (0, self.tfidf_max_features - len(vector)))
        return vector[:self.tfidf_max_features]

    def extract_context_features(
        self,
        task_text: str,
        task_type: Optional[str] = None,
        prior_complexity: Optional[str] = None,
        domain_tags: Optional[list[str]] = None,
    ) -> np.ndarray:
        """Extract context features from task metadata.

        Features (32 dims):
          - Domain keyword matches (8 dims: simple=0-3, complex=4-7)
          - Task type encoding (8 dims)
          - Prior complexity encoding (8 dims)
          - Text length features (8 dims: length, caps, numbers, punctuation ratios)

        Args:
            task_text: Task description
            task_type: Type of task (optional)
            prior_complexity: Prior complexity label (simple/moderate/complex)
            domain_tags: Domain-specific tags

        Returns:
            Normalized vector (32 dims)
        """
        features = np.zeros(32)

        # 1. Domain keyword matches (8 dims)
        lower_text = task_text.lower()
        words = set(re.findall(r"\b\w+\b", lower_text))

        simple_matches = len(words & self._DOMAIN_KEYWORDS_BY_COMPLEXITY["simple"])
        complex_matches = len(words & self._DOMAIN_KEYWORDS_BY_COMPLEXITY["complex"])

        features[0] = min(1.0, simple_matches / 10)  # norm to 0-1
        features[1] = min(1.0, complex_matches / 10)
        features[2] = simple_matches - complex_matches  # net bias
        features[3] = len(domain_tags or []) / 10

        # 2. Task type encoding (8 dims, one-hot-like)
        task_type_map = {
            "bug_fix": 0, "feature": 1, "refactor": 2, "doc": 3,
            "test": 4, "perf": 5, "security": 6, "other": 7
        }
        if task_type and task_type in task_type_map:
            features[8 + task_type_map[task_type]] = 1.0

        # 3. Prior complexity encoding (8 dims)
        complexity_map = {"simple": 0, "moderate": 1, "complex": 2}
        if prior_complexity and prior_complexity in complexity_map:
            features[16 + complexity_map[prior_complexity]] = 1.0

        # 4. Text length features (8 dims)
        text_len = len(task_text)
        features[24] = min(1.0, text_len / 500)  # normalize to ~500 char max
        features[25] = len([c for c in task_text if c.isupper()]) / max(1, text_len)
        features[26] = len([c for c in task_text if c.isdigit()]) / max(1, text_len)
        features[27] = len([c for c in task_text if not c.isalnum()]) / max(1, text_len)
        features[28] = len(task_text.split()) / 20  # word count, norm to ~20 words
        features[29] = task_text.count(".") / 5  # sentence count
        features[30] = task_text.count("(") / 3  # code indicator

        return features / np.linalg.norm(features + 1e-8)  # L2 norm

    def extract_user_features(
        self,
        user_id: Optional[str] = None,
        user_complexity_history: Optional[dict[str, int]] = None,
        user_success_rate: float = 0.5,
        tasks_completed: int = 0,
    ) -> np.ndarray:
        """Extract user history features.

        Features (32 dims):
          - Complexity distribution (8 dims: simple/moderate/complex counts and ratios)
          - Task success metrics (8 dims)
          - User skill level indicators (8 dims)
          - Activity metrics (8 dims)

        Args:
            user_id: User identifier (optional)
            user_complexity_history: Dict of complexity→count
            user_success_rate: Task success rate (0.0-1.0)
            tasks_completed: Total tasks completed

        Returns:
            Normalized vector (32 dims)
        """
        features = np.zeros(32)

        if user_complexity_history is None:
            user_complexity_history = {}

        # 1. Complexity distribution (8 dims)
        total_tasks = sum(user_complexity_history.values()) or 1
        features[0] = user_complexity_history.get("simple", 0) / total_tasks
        features[1] = user_complexity_history.get("moderate", 0) / total_tasks
        features[2] = user_complexity_history.get("complex", 0) / total_tasks
        features[3] = (
            (user_complexity_history.get("complex", 0) +
             user_complexity_history.get("moderate", 0)) / total_tasks
        )

        # 2. Task success metrics (8 dims)
        features[8] = user_success_rate
        features[9] = 1.0 - user_success_rate
        features[10] = min(1.0, tasks_completed / 100)  # experience level
        features[11] = (
            (user_complexity_history.get("complex", 0) / total_tasks)
            if total_tasks > 0 else 0.0
        )

        # 3. User skill level (8 dims) - inferred from history
        if tasks_completed > 50:
            features[16] = min(1.0, tasks_completed / 200)  # experienced
        if user_success_rate > 0.8:
            features[17] = 1.0  # high performer

        # 4. Activity metrics (8 dims)
        features[24] = min(1.0, tasks_completed / 1000)  # overall activity
        features[25] = user_success_rate * (tasks_completed / 100)  # weighted score

        return features / np.linalg.norm(features + 1e-8)

    def extract_all_features(
        self,
        task_text: str,
        task_type: Optional[str] = None,
        prior_complexity: Optional[str] = None,
        domain_tags: Optional[list[str]] = None,
        user_id: Optional[str] = None,
        user_complexity_history: Optional[dict[str, int]] = None,
        user_success_rate: float = 0.5,
        tasks_completed: int = 0,
        task_id: Optional[str] = None,
    ) -> FeatureVector:
        """Extract all features for a task.

        Args:
            task_text: Task description
            task_type: Type of task
            prior_complexity: Prior complexity estimate
            domain_tags: Domain tags
            user_id: User ID
            user_complexity_history: Complexity history
            user_success_rate: User success rate
            tasks_completed: Tasks completed by user
            task_id: Task identifier

        Returns:
            FeatureVector with all features concatenated (114 dims total)
        """
        text_features = self.extract_text_features(task_text)
        context_features = self.extract_context_features(
            task_text, task_type, prior_complexity, domain_tags
        )
        user_features = self.extract_user_features(
            user_id, user_complexity_history, user_success_rate, tasks_completed
        )

        return FeatureVector(
            text_features=text_features,
            context_features=context_features,
            user_features=user_features,
            raw_text=task_text,
            task_id=task_id,
        )

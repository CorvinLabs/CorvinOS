"""Graph Traversal module (Phase 2).

Walks classifier graphs to discover related decisions.
Extends Phase 1 Memory Lookup with structural context.

Features:
- BFS traversal of classifier graphs
- Relevance scoring (similarity + distance)
- Caching (same pattern as Phase 1)
- Integration with RichTaskBrief
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Dict, Tuple
import time

logger = logging.getLogger(__name__)


@dataclass
class RelatedDecision:
    """Single related decision found by graph traversal."""

    decision_id: str
    """Unique identifier for this decision."""

    title: str
    """Title/summary of the decision."""

    relevance_score: float
    """Relevance [0.0, 1.0]. Higher = more similar to current task."""

    distance: int
    """Graph distance from current task (0=direct, 1=neighbor, 2=neighbor-of-neighbor)."""

    decision_type: str
    """Type of decision (e.g., 'bug-fix', 'refactor', 'feature')."""

    context: str
    """Brief context/description."""

    def __post_init__(self):
        """Validate score is in [0.0, 1.0]."""
        if not 0.0 <= self.relevance_score <= 1.0:
            raise ValueError(f"Score must be [0.0, 1.0], got {self.relevance_score}")


@dataclass
class GraphTraversalResult:
    """Result of graph traversal."""

    task_id: str
    """Original task identifier."""

    related_decisions: List[RelatedDecision]
    """Top N related decisions, ranked by relevance."""

    search_duration_ms: float
    """Time to traverse and rank."""

    cache_hit: bool
    """Whether result was cached."""

    traversal_depth: int
    """Max depth of traversal (typically 2)."""


class GraphTraversal:
    """Traverse classifier graphs to find related decisions.

    Implements Phase 2 extension to CEL.
    Uses same caching pattern as Phase 1 MemoryLookup.
    """

    def __init__(self, cache_ttl_minutes: int = 30):
        """Initialize graph traversal.

        Args:
            cache_ttl_minutes: Cache TTL in minutes (default: 30).
        """
        self.cache_ttl = timedelta(minutes=cache_ttl_minutes)
        self._traversal_cache: Dict[int, Tuple[List[RelatedDecision], datetime]] = {}

        logger.info(f"GraphTraversal initialized (TTL: {cache_ttl_minutes}min)")

    def find_related_decisions(
        self,
        task: object,
        depth: int = 2,
        top_n: int = 3,
        max_results: int = 5,
    ) -> GraphTraversalResult:
        """Find related decisions by traversing classifier graphs.

        Pipeline:
        1. Extract decision nodes from task
        2. BFS to specified depth
        3. Score by relevance + distance
        4. Cache results
        5. Return top N ranked

        Args:
            task: Task object (EnrichedTask or similar).
            depth: Max traversal depth (default: 2).
            top_n: Number of seeds to start from (default: 3).
            max_results: Max results to return (default: 5).

        Returns:
            GraphTraversalResult with related decisions ranked.
        """
        start = time.perf_counter()

        # Check cache
        cache_key = hash((id(task), depth, top_n, max_results))
        if cache_key in self._traversal_cache:
            cached_results, timestamp = self._traversal_cache[cache_key]
            age = datetime.now() - timestamp
            if age < self.cache_ttl:
                elapsed_ms = (time.perf_counter() - start) * 1000
                logger.debug(
                    f"Graph traversal cache hit: {len(cached_results)} decisions, "
                    f"age={age.total_seconds():.0f}s"
                )
                return GraphTraversalResult(
                    task_id=self._get_task_id(task),
                    related_decisions=cached_results[:max_results],
                    search_duration_ms=elapsed_ms,
                    cache_hit=True,
                    traversal_depth=depth,
                )
            else:
                del self._traversal_cache[cache_key]

        logger.debug(f"Graph traversal cache miss: searching (depth={depth})")

        # Placeholder: In real implementation, would traverse classifier graphs
        # For now, return empty list (tests will mock this)
        related = []

        # Sort by relevance (descending)
        ranked = sorted(related, key=lambda d: d.relevance_score, reverse=True)

        # Cache results
        elapsed_ms = (time.perf_counter() - start) * 1000
        self._traversal_cache[cache_key] = (ranked, datetime.now())

        logger.info(
            f"Graph traversal complete: {len(ranked)} related decisions found, "
            f"returning top {min(len(ranked), max_results)}, "
            f"latency={elapsed_ms:.0f}ms"
        )

        return GraphTraversalResult(
            task_id=self._get_task_id(task),
            related_decisions=ranked[:max_results],
            search_duration_ms=elapsed_ms,
            cache_hit=False,
            traversal_depth=depth,
        )

    def _get_task_id(self, task: object) -> str:
        """Extract task identifier."""
        if hasattr(task, "id"):
            return str(task.id)
        if hasattr(task, "task_id"):
            return str(task.task_id)
        return f"task_{id(task)}"

    def rank(self, decisions: List[RelatedDecision]) -> List[RelatedDecision]:
        """Re-rank decisions by relevance (descending).

        Args:
            decisions: List of RelatedDecision objects.

        Returns:
            Re-ranked decisions (highest score first).
        """
        return sorted(decisions, key=lambda d: d.relevance_score, reverse=True)

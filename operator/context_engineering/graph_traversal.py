"""Graph Traversal module (Phase 2 + Phase 3 ADR Integration).

Walks classifier graphs to discover related decisions.
Phase 2: Generic decision discovery
Phase 3: ADR-based decision discovery via Corvin-ADR repo

Features:
- BFS traversal of classifier graphs
- ADR-based decision discovery with dependency graph
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

# Try to import ADR support (Phase 3)
try:
    from .adr_loader import ADRLoader
    from .adr_classifier import ADRClassifier
    ADR_AVAILABLE = True
except ImportError:
    ADR_AVAILABLE = False


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

    Implements Phase 2 + Phase 3 (ADR integration).
    Uses same caching pattern as Phase 1 MemoryLookup.

    Phase 2: Generic decision discovery (placeholder)
    Phase 3: ADR-based decision discovery from Corvin-ADR repo
    """

    def __init__(self, cache_ttl_minutes: int = 30, enable_adr: bool = True):
        """Initialize graph traversal.

        Args:
            cache_ttl_minutes: Cache TTL in minutes (default: 30).
            enable_adr: Enable ADR-based discovery (Phase 3, default: True).
        """
        self.cache_ttl = timedelta(minutes=cache_ttl_minutes)
        self._traversal_cache: Dict[int, Tuple[List[RelatedDecision], datetime]] = {}

        # Phase 3: ADR integration
        self.adr_classifier: Optional[ADRClassifier] = None
        if enable_adr and ADR_AVAILABLE:
            try:
                adr_loader = ADRLoader()
                self.adr_classifier = ADRClassifier(adr_loader)
                logger.info(f"GraphTraversal initialized with ADR support ({len(adr_loader.adrs)} ADRs loaded)")
            except Exception as e:
                logger.warning(f"Failed to initialize ADR support: {e}")

        if not self.adr_classifier:
            logger.info(f"GraphTraversal initialized without ADR (Phase 2 placeholder mode)")

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

        # Check cache (use task_id not object identity to avoid GC reuse collisions)
        task_id = self._get_task_id(task)
        cache_key = hash((task_id, depth, top_n, max_results))
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

        # Phase 3: Try ADR-based discovery first (if available)
        all_decisions = []

        if self.adr_classifier:
            try:
                adr_results = self.adr_classifier.find_relevant_adrs(task, top_n=top_n, max_results=max_results)
                logger.debug(f"ADR classifier found {len(adr_results)} relevant ADRs")

                # Convert ADR metadata to decision dicts
                for adr_metadata in adr_results:
                    all_decisions.append(self._adr_metadata_to_decision(adr_metadata, distance=1))
            except Exception as e:
                logger.warning(f"ADR discovery failed: {e}, falling back to Phase 2")

        # Phase 2: Fallback to generic decision discovery (placeholder)
        if not all_decisions:
            seed_nodes = self._extract_seed_nodes(task, top_n)
            logger.debug(f"Extracted {len(seed_nodes)} seed nodes (Phase 2 fallback)")

            # BFS traversal
            visited = set()
            for seed in seed_nodes:
                decisions = self._bfs_traverse(seed, depth, visited)
                all_decisions.extend(decisions)

        # Score by relevance + distance
        scored = self._score_decisions(all_decisions, task)

        # Sort by relevance (descending)
        ranked = sorted(scored, key=lambda d: d.relevance_score, reverse=True)

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

    def _extract_seed_nodes(self, task: object, top_n: int) -> List[str]:
        """Extract top N decision nodes from task as BFS seeds.

        In real implementation, would extract from classifier output.
        For now, returns empty list (tests will provide mocks).

        Args:
            task: Task object.
            top_n: Number of seeds to extract.

        Returns:
            List of decision node IDs.
        """
        # Placeholder: In real implementation, would extract from classifier
        return []

    def _bfs_traverse(self, seed_node: str, depth: int, visited: set) -> List[Dict]:
        """BFS traverse from seed node to specified depth.

        Args:
            seed_node: Starting node ID.
            depth: Max depth to traverse.
            visited: Set of already-visited nodes (for deduplication).

        Returns:
            List of discovered decisions (dicts with id, title, type, etc.).
        """
        # Placeholder: In real implementation, would traverse graph edges
        # Returns empty list for now
        return []

    def _score_decisions(self, decisions: List[Dict], task: object) -> List[RelatedDecision]:
        """Score decisions by relevance and distance.

        Scoring: relevance = (task_similarity * 0.7) + (distance_factor * 0.3)
        - task_similarity: how similar decision context is to current task (keyword overlap)
        - distance_factor: inverse of graph distance (1.0 at distance 0, 0.5 at distance 2)

        Args:
            decisions: List of raw decision dicts from BFS or ADR metadata.
            task: Current task for similarity scoring.

        Returns:
            List of RelatedDecision objects with scores.
        """
        if not decisions:
            return []

        related = []
        for decision in decisions:
            # Placeholder scoring: for now, all scores are 0.5
            # Real implementation would calculate task similarity
            score = 0.5
            distance = decision.get("distance", 2)

            try:
                related.append(
                    RelatedDecision(
                        decision_id=decision.get("id", "unknown"),
                        title=decision.get("title", "Unknown"),
                        relevance_score=score,
                        distance=distance,
                        decision_type=decision.get("type", "adr"),  # Phase 3: type = "adr"
                        context=decision.get("context", ""),
                    )
                )
            except ValueError:
                # Skip invalid scores
                logger.warning(f"Invalid score for decision {decision.get('id')}")
                continue

        return related

    def _adr_metadata_to_decision(self, adr_metadata: object, distance: int = 1) -> Dict:
        """Convert ADR metadata to decision dict for scoring.

        Args:
            adr_metadata: ADRMetadata object from ADRClassifier.
            distance: Graph distance from seed ADR.

        Returns:
            Dictionary representation suitable for RelatedDecision.
        """
        if not adr_metadata:
            return {}

        return {
            "id": getattr(adr_metadata, "id", "unknown"),
            "title": getattr(adr_metadata, "title", "Unknown"),
            "type": "adr",
            "distance": distance,
            "context": getattr(adr_metadata, "content_preview", "")[:200],
            "status": getattr(adr_metadata, "status", "unknown"),
        }

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

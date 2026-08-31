"""ADR Classifier: Match tasks to relevant ADRs."""

import logging
from typing import List, Optional
from .adr_loader import ADRLoader, ADRMetadata

logger = logging.getLogger(__name__)


class ADRClassifier:
    """Classify tasks and find relevant ADRs."""

    def __init__(self, adr_loader: Optional[ADRLoader] = None):
        """Initialize ADR classifier.

        Args:
            adr_loader: ADRLoader instance (creates new one if None).
        """
        self.loader = adr_loader or ADRLoader()
        logger.info("ADRClassifier initialized")

    def find_relevant_adrs(
        self,
        task: object,
        top_n: int = 3,
        max_results: int = 5,
    ) -> List[ADRMetadata]:
        """Find relevant ADRs for a task.

        Pipeline:
        1. Extract keywords from task
        2. Search ADRs by keywords
        3. For each match, traverse dependency graph
        4. Rank by relevance + distance
        5. Return top N

        Args:
            task: Task object (EnrichedTask or similar).
            top_n: Number of seed ADRs to start traversal from.
            max_results: Max ADRs to return.

        Returns:
            List of relevant ADRMetadata objects ranked by relevance.
        """
        # Extract keywords from task
        keywords = self._extract_keywords(task)
        if not keywords:
            return []

        # Search ADRs by keywords
        seed_adr_ids = self.loader.search_by_keywords(keywords, max_results=top_n)
        if not seed_adr_ids:
            return []

        # Traverse graph from each seed, collect related ADRs
        related_adr_ids = set()
        for seed_id in seed_adr_ids:
            related = self.loader.find_related_adr_ids(seed_id, depth=2, max_results=max_results)
            related_adr_ids.update(related)

        # Add seed ADRs themselves
        related_adr_ids.update(seed_adr_ids)

        # Collect metadata, limit to max_results
        results = []
        for adr_id in list(related_adr_ids)[:max_results]:
            metadata = self.loader.get_adr(adr_id)
            if metadata and metadata.id:
                results.append(metadata)

        logger.info(f"Found {len(results)} relevant ADRs for task")
        return results

    def _extract_keywords(self, task: object) -> List[str]:
        """Extract searchable keywords from task.

        Args:
            task: Task object.

        Returns:
            List of keywords (max 10).
        """
        keywords = []

        # Try to extract from task.normalized.summary or task.raw_input
        if hasattr(task, "normalized") and hasattr(task.normalized, "summary"):
            summary = task.normalized.summary
        elif hasattr(task, "raw_input"):
            summary = task.raw_input
        elif hasattr(task, "summary"):
            summary = task.summary
        else:
            summary = str(task)[:500]

        if not summary:
            return []

        # Simple keyword extraction: split by spaces, filter short words, deduplicate
        words = summary.lower().split()
        keywords = [
            w.strip(".,!?;:")
            for w in words
            if len(w) >= 4 and w not in {"the", "that", "this", "from", "with", "have"}
        ]

        # Deduplicate and limit to 10
        return list(dict.fromkeys(keywords))[:10]

"""ADR Classifier: Match tasks to relevant ADRs."""

import dataclasses
import logging
from typing import Dict, List, Optional
from .adr_loader import ADRLoader, ADRMetadata, MIN_RELEVANCE, get_loader, tokenize

logger = logging.getLogger(__name__)


class ADRClassifier:
    """Classify tasks and find relevant ADRs."""

    def __init__(self, adr_loader: Optional[ADRLoader] = None):
        """Initialize ADR classifier.

        Args:
            adr_loader: ADRLoader instance (creates new one if None).
        """
        self.loader = adr_loader or get_loader()
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
        keywords = self._extract_keywords(task)
        if not keywords:
            return []

        # Every returned ADR carries its OWN lexical score against the task.
        # The top_n direct matches seed the graph walk. A graph neighbour (depends_on /
        # related / supersedes) is only added when it is itself relevant: it
        # inherits a decayed share of its seed's score, blended with its own,
        # and must still clear MIN_RELEVANCE. The old code added every 2-hop
        # neighbour unscored and then cut ``list(set)[:max_results]`` — an
        # arbitrary subset, which could drop the best seed for a stranger.
        scored = self.loader.score_query(keywords, min_relevance=0.0)
        own: Dict[str, float] = dict(scored)
        direct = [(i, s) for i, s in scored if s >= MIN_RELEVANCE][:max_results]
        if not direct:
            return []
        final: Dict[str, float] = dict(direct)
        for seed_id, seed_score in direct[:top_n]:
            for n_id in self.loader.find_related_adr_ids(seed_id, depth=2, max_results=max_results * 2):
                if n_id in final:
                    continue
                blended = 0.6 * own.get(n_id, 0.0) + 0.4 * seed_score * 0.5
                if blended >= MIN_RELEVANCE:
                    final[n_id] = round(blended, 4)

        results = []
        for adr_id, score in sorted(final.items(), key=lambda x: (-x[1], x[0]))[:max_results]:
            metadata = self.loader.get_adr(adr_id)
            if metadata and metadata.id and metadata.id not in {r.id for r in results}:
                results.append(dataclasses.replace(metadata, relevance=score))

        logger.info(f"Found {len(results)} relevant ADRs for task")
        return results

    def _extract_keywords(self, task: object) -> List[str]:
        """Extract searchable keywords from task.

        Args:
            task: Task object.

        Returns:
            List of keywords (max 24).
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

        # Whole-word, stop-word-free, stemmed tokens; the first 24 carry the
        # task (a long brief's tail is usually boilerplate).
        return tokenize(summary)[:24]

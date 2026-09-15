"""Phase 2: Graph Filtering & Deduplication (ADR-0267).

Simple, stable filtering pipeline:
1. Deduplicate: Find files appearing in multiple graphs
2. Rank: Score graphs by confidence + file-count
3. Filter: Remove low-relevance, keep >= 1 graph
"""

from dataclasses import dataclass, field
from typing import Any
from .classifier import ClassifiedTask


@dataclass
class FilterConfig:
    """Configuration for graph filtering."""

    relevance_threshold: float = 0.3
    """Minimum score to keep a graph (0.0–1.0)."""

    min_graphs: int = 1
    """Always keep at least this many graphs (fallback)."""

    max_graphs: int = 5
    """Never recommend more than this many graphs."""

    def validate(self) -> None:
        """Validate configuration bounds."""
        if not 0.0 <= self.relevance_threshold <= 1.0:
            raise ValueError("relevance_threshold must be in [0.0, 1.0]")
        if self.min_graphs < 1:
            raise ValueError("min_graphs must be >= 1")
        if self.max_graphs < self.min_graphs:
            raise ValueError("max_graphs must be >= min_graphs")


@dataclass
class FilteredGraphs:
    """Output of Phase 2 filtering."""

    normalized: Any  # NormalizedTask
    classified: ClassifiedTask
    filtered_graphs: list[str]
    """Final recommended graphs, sorted by relevance."""

    deduplicated_files: dict[str, list[str]] = field(default_factory=dict)
    """Mapping {file: [source_graphs]}."""

    ranked_graphs: list[tuple[str, float]] = field(default_factory=list)
    """All graphs with scores, sorted descending."""

    redundancy_ratio: float = 0.0
    """Proportion of duplicate entries (0.0–1.0)."""


class GraphFilteringPipeline:
    """Orchestrates deduplication, ranking, and filtering."""

    def process(
        self,
        classified: ClassifiedTask,
        normalized: Any = None,
        config: FilterConfig | None = None,
    ) -> FilteredGraphs:
        """Filter and rank graphs from Phase 1.

        Args:
            classified: ClassifiedTask from Phase 1.
            normalized: NormalizedTask (optional, for context).
            config: FilterConfig (uses defaults if None).

        Returns:
            FilteredGraphs with final recommendations.
        """
        if config is None:
            config = FilterConfig()
        config.validate()

        # Step 1: Deduplicate files across graphs
        dedup = self._deduplicate(classified)

        # Step 2: Rank graphs by relevance
        ranked = self._rank_graphs(classified, dedup)

        # Step 3: Filter to final recommendations
        filtered = self._filter_graphs(ranked, config)

        return FilteredGraphs(
            normalized=normalized,
            classified=classified,
            filtered_graphs=filtered,
            deduplicated_files=dedup,
            ranked_graphs=ranked,
            redundancy_ratio=self._calculate_redundancy(classified, dedup),
        )

    def _deduplicate(self, classified: ClassifiedTask) -> dict[str, list[str]]:
        """Find files appearing in multiple graphs."""
        dedup = {}

        if not hasattr(classified, "classification") or not classified.classification:
            return dedup

        for graph_name, (score, metadata) in classified.classification.items():
            if not isinstance(metadata, dict):
                continue

            # Extract files from metadata
            files = metadata.get("files", [])
            if isinstance(files, str):
                files = [files]

            for file_entry in files:
                if file_entry not in dedup:
                    dedup[file_entry] = []
                if graph_name not in dedup[file_entry]:
                    dedup[file_entry].append(graph_name)

        return dedup

    def _rank_graphs(
        self, classified: ClassifiedTask, dedup: dict
    ) -> list[tuple[str, float]]:
        """Score each graph and return sorted ranking."""
        ranked = []

        if not hasattr(classified, "classification"):
            return ranked

        for graph_name, (conf_score, metadata) in classified.classification.items():
            # Confidence from Phase 1
            score = float(conf_score) if isinstance(conf_score, (int, float)) else 0.5
            score = min(1.0, max(0.0, score))

            # Bonus for file count (more files = more comprehensive)
            if isinstance(metadata, dict):
                file_count = len(metadata.get("files", []))
                file_bonus = min(0.2, file_count * 0.02)
                score = min(1.0, score + file_bonus)

            ranked.append((graph_name, score))

        # Sort by score (descending)
        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked

    def _filter_graphs(
        self, ranked: list[tuple[str, float]], config: FilterConfig
    ) -> list[str]:
        """Apply filtering thresholds and limits."""
        if not ranked:
            return []

        # Apply relevance threshold
        above_threshold = [g for g, s in ranked if s >= config.relevance_threshold]

        # Fallback: keep best graph if no graphs pass threshold
        if not above_threshold:
            above_threshold = [ranked[0][0]]

        # Apply max_graphs limit
        result = above_threshold[: config.max_graphs]

        # Apply min_graphs fallback (include lower-scoring graphs if needed)
        while len(result) < config.min_graphs and len(result) < len(ranked):
            next_graph = ranked[len(result)][0]
            if next_graph not in result:
                result.append(next_graph)

        return result

    def _calculate_redundancy(
        self, classified: ClassifiedTask, dedup: dict
    ) -> float:
        """Calculate redundancy ratio (duplicates / total)."""
        if not dedup:
            return 0.0

        total_entries = sum(len(graphs) for graphs in dedup.values())
        unique_entries = len(dedup)

        if total_entries == 0:
            return 0.0

        # Redundancy = (total - unique) / total
        return (total_entries - unique_entries) / total_entries

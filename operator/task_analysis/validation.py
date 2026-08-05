"""Phase 3: Graph Validation & Re-Ranking (ADR-0267).

Validate router completeness and adjust confidence scores based on:
- Which routers provided data
- Quality of discovered graphs
- File/ADR existence spot-checks
"""

from dataclasses import dataclass, field
from typing import Any
from .filtering import FilteredGraphs


@dataclass
class ValidatedGraphs:
    """Output of Phase 3 validation & re-ranking."""

    filtered: FilteredGraphs
    """Original filtered graphs from Phase 2."""

    graphs_complete: dict[str, bool]
    """{graph_name: had_data} — which routers returned results."""

    confidence_adjusted: dict[str, float]
    """{graph_name: new_score} — scores after validation."""

    validation_notes: list[str] = field(default_factory=list)
    """Human-readable validation feedback."""

    final_confidence: float = 0.0
    """Global confidence after validation (0.0–1.0)."""


class GraphValidator:
    """Validates and re-ranks graphs from Phase 2."""

    def validate(self, filtered: FilteredGraphs) -> ValidatedGraphs:
        """Validate filtered graphs and adjust confidence.

        Args:
            filtered: FilteredGraphs from Phase 2.

        Returns:
            ValidatedGraphs with adjusted scores.
        """
        graphs_complete = self._check_router_completeness(filtered)
        confidence_adjusted = self._rerank_by_completeness(
            filtered, graphs_complete
        )
        validation_notes = self._generate_notes(filtered, graphs_complete)
        final_confidence = self._calculate_final_confidence(confidence_adjusted)

        return ValidatedGraphs(
            filtered=filtered,
            graphs_complete=graphs_complete,
            confidence_adjusted=confidence_adjusted,
            validation_notes=validation_notes,
            final_confidence=final_confidence,
        )

    def _check_router_completeness(self, filtered: FilteredGraphs) -> dict[str, bool]:
        """Check which routers returned data."""
        completeness = {}

        if not filtered.classified.classification:
            return completeness

        for graph_name, (score, metadata) in filtered.classified.classification.items():
            # A graph is "complete" if it has score > 0.0 and metadata
            has_data = (
                isinstance(score, (int, float))
                and score > 0.0
                and isinstance(metadata, dict)
                and len(metadata) > 0
            )
            completeness[graph_name] = has_data

        return completeness

    def _rerank_by_completeness(
        self, filtered: FilteredGraphs, completeness: dict[str, bool]
    ) -> dict[str, float]:
        """Adjust scores based on router completeness."""
        adjusted = {}

        for graph_name, (orig_score, _) in filtered.classified.classification.items():
            score = float(orig_score) if isinstance(orig_score, (int, float)) else 0.5
            score = min(1.0, max(0.0, score))

            # Penalty if router had no data
            if not completeness.get(graph_name, False):
                score *= 0.5  # Halve score for incomplete routers

            adjusted[graph_name] = score

        return adjusted

    def _generate_notes(
        self, filtered: FilteredGraphs, completeness: dict[str, bool]
    ) -> list[str]:
        """Generate human-readable validation feedback."""
        notes = []

        total_graphs = len(completeness)
        complete_graphs = sum(1 for c in completeness.values() if c)

        if total_graphs == 0:
            notes.append("No graphs to validate")
            return notes

        notes.append(
            f"Router completeness: {complete_graphs}/{total_graphs} "
            f"({100*complete_graphs//total_graphs}%)"
        )

        # Flag incomplete routers
        for graph_name, is_complete in completeness.items():
            if not is_complete:
                notes.append(f"⚠️ {graph_name} returned no data")

        # Flag low-confidence graphs
        for graph_name, (score, _) in filtered.classified.classification.items():
            if isinstance(score, (int, float)) and score < 0.3:
                notes.append(f"⚠️ {graph_name} has low confidence ({score:.2f})")

        return notes

    def _calculate_final_confidence(self, adjusted: dict[str, float]) -> float:
        """Calculate global confidence after validation."""
        if not adjusted:
            return 0.0

        return sum(adjusted.values()) / len(adjusted)

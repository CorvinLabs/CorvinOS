"""Phase contracts: Verify data flow between pipeline stages.

Ensures that each phase outputs exactly what the next phase expects.
Catches drift early (e.g., Phase 1 adds new field that Phase 2 ignores).
"""

from typing import Any
from .normalizer import NormalizedTask
from .classifier import ClassifiedTask
from .filtering import FilteredGraphs
from .validation import ValidatedGraphs
from .enrichment import EnrichedTask
from .delegation import DelegationDecision


class ContractViolation(Exception):
    """Data contract between phases is broken."""

    pass


class PhaseContracts:
    """Verify contracts between phases."""

    @staticmethod
    def validate_phase0_output(normalized: Any) -> None:
        """Phase 0 (Normalizer) must return NormalizedTask with required fields.

        Args:
            normalized: Output from Normalizer.

        Raises:
            ContractViolation: If required fields are missing.
        """
        if not isinstance(normalized, NormalizedTask):
            raise ContractViolation(
                f"Phase 0 output must be NormalizedTask, got {type(normalized)}"
            )

        required_fields = [
            "summary",
            "type",
            "severity",
            "components",
            "affected_layers",
        ]
        for field in required_fields:
            if not hasattr(normalized, field):
                raise ContractViolation(
                    f"Phase 0 output missing required field: {field}"
                )

    @staticmethod
    def validate_phase1_output(classified: Any) -> None:
        """Phase 1 (Classifier) must return ClassifiedTask.

        Args:
            classified: Output from Classifier.

        Raises:
            ContractViolation: If required fields are missing.
        """
        if not isinstance(classified, ClassifiedTask):
            raise ContractViolation(
                f"Phase 1 output must be ClassifiedTask, got {type(classified)}"
            )

        required_fields = ["normalized", "classification", "confidence"]
        for field in required_fields:
            if not hasattr(classified, field):
                raise ContractViolation(
                    f"Phase 1 output missing required field: {field}"
                )

        # Validate classification structure
        if not isinstance(classified.classification, dict):
            raise ContractViolation(
                "Phase 1 classification must be dict[str, tuple[float, dict]]"
            )

        for graph_name, graph_data in classified.classification.items():
            if not isinstance(graph_data, tuple) or len(graph_data) != 2:
                raise ContractViolation(
                    f"Phase 1 classification['{graph_name}'] must be (score, metadata) tuple"
                )
            score, metadata = graph_data
            if not isinstance(score, (int, float)):
                raise ContractViolation(
                    f"Phase 1 score for '{graph_name}' must be numeric, got {type(score)}"
                )
            if metadata is not None and not isinstance(metadata, dict):
                raise ContractViolation(
                    f"Phase 1 metadata for '{graph_name}' must be dict or None"
                )

    @staticmethod
    def validate_phase2_output(filtered: Any) -> None:
        """Phase 2 (Filter) must return FilteredGraphs.

        Args:
            filtered: Output from Filter.

        Raises:
            ContractViolation: If required fields are missing.
        """
        if not isinstance(filtered, FilteredGraphs):
            raise ContractViolation(
                f"Phase 2 output must be FilteredGraphs, got {type(filtered)}"
            )

        required_fields = [
            "classified",
            "filtered_graphs",
            "deduplicated_files",
            "ranked_graphs",
            "redundancy_ratio",
        ]
        for field in required_fields:
            if not hasattr(filtered, field):
                raise ContractViolation(
                    f"Phase 2 output missing required field: {field}"
                )

        # Validate redundancy_ratio
        if not 0.0 <= filtered.redundancy_ratio <= 1.0:
            raise ContractViolation(
                f"Phase 2 redundancy_ratio must be in [0.0, 1.0], got {filtered.redundancy_ratio}"
            )

    @staticmethod
    def validate_phase3_output(validated: Any) -> None:
        """Phase 3 (Validator) must return ValidatedGraphs.

        Args:
            validated: Output from Validator.

        Raises:
            ContractViolation: If required fields are missing.
        """
        if not isinstance(validated, ValidatedGraphs):
            raise ContractViolation(
                f"Phase 3 output must be ValidatedGraphs, got {type(validated)}"
            )

        required_fields = [
            "filtered",
            "graphs_complete",
            "confidence_adjusted",
            "final_confidence",
        ]
        for field in required_fields:
            if not hasattr(validated, field):
                raise ContractViolation(
                    f"Phase 3 output missing required field: {field}"
                )

        # Validate confidence bounds
        if not 0.0 <= validated.final_confidence <= 1.0:
            raise ContractViolation(
                f"Phase 3 final_confidence must be in [0.0, 1.0], got {validated.final_confidence}"
            )

    @staticmethod
    def validate_phase4_output(enriched: Any) -> None:
        """Phase 4 (Enricher) must return EnrichedTask.

        Args:
            enriched: Output from Enricher.

        Raises:
            ContractViolation: If required fields are missing.
        """
        if not isinstance(enriched, EnrichedTask):
            raise ContractViolation(
                f"Phase 4 output must be EnrichedTask, got {type(enriched)}"
            )

        required_fields = [
            "validated",
            "task_complexity",
            "model_recommendation",
            "estimated_tokens",
            "estimated_cost_usd",
        ]
        for field in required_fields:
            if not hasattr(enriched, field):
                raise ContractViolation(
                    f"Phase 4 output missing required field: {field}"
                )

        # Validate bounds
        if not 0.0 <= enriched.task_complexity <= 1.0:
            raise ContractViolation(
                f"Phase 4 task_complexity must be in [0.0, 1.0], got {enriched.task_complexity}"
            )

        if enriched.model_recommendation not in ["haiku", "opus"]:
            raise ContractViolation(
                f"Phase 4 model_recommendation must be 'haiku' or 'opus', got '{enriched.model_recommendation}'"
            )

        if enriched.estimated_tokens <= 0:
            raise ContractViolation(
                f"Phase 4 estimated_tokens must be > 0, got {enriched.estimated_tokens}"
            )

        if enriched.estimated_cost_usd < 0.0:
            raise ContractViolation(
                f"Phase 4 estimated_cost_usd must be >= 0.0, got {enriched.estimated_cost_usd}"
            )

    @staticmethod
    def validate_phase5_output(decision: Any) -> None:
        """Phase 5 (Router) must return DelegationDecision.

        Args:
            decision: Output from Router.

        Raises:
            ContractViolation: If required fields are missing.
        """
        if not isinstance(decision, DelegationDecision):
            raise ContractViolation(
                f"Phase 5 output must be DelegationDecision, got {type(decision)}"
            )

        required_fields = [
            "enriched",
            "should_delegate",
            "delegation_target",
            "carve_out_reason",
            "confidence",
        ]
        for field in required_fields:
            if not hasattr(decision, field):
                raise ContractViolation(
                    f"Phase 5 output missing required field: {field}"
                )

        # Validate confidence
        if not 0.0 <= decision.confidence <= 1.0:
            raise ContractViolation(
                f"Phase 5 confidence must be in [0.0, 1.0], got {decision.confidence}"
            )

        # Validate should_delegate consistency
        is_native = str(decision.delegation_target).endswith("native")
        if is_native and decision.should_delegate:
            raise ContractViolation(
                "Phase 5: should_delegate=True but target is NATIVE"
            )


def validate_all_contracts(
    normalized, classified, filtered, validated, enriched, decision
) -> None:
    """Validate entire pipeline output.

    Args:
        normalized: Phase 0 output
        classified: Phase 1 output
        filtered: Phase 2 output
        validated: Phase 3 output
        enriched: Phase 4 output
        decision: Phase 5 output

    Raises:
        ContractViolation: If any phase output is invalid.
    """
    PhaseContracts.validate_phase0_output(normalized)
    PhaseContracts.validate_phase1_output(classified)
    PhaseContracts.validate_phase2_output(filtered)
    PhaseContracts.validate_phase3_output(validated)
    PhaseContracts.validate_phase4_output(enriched)
    PhaseContracts.validate_phase5_output(decision)

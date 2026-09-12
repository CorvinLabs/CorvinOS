"""CLI integration for Quality Gates System (ADR-0688).

Provides command-line interface for running validators and inspecting gate status.
"""

import json
import sys
from typing import Any, Dict, List, Optional
import logging

from .graph import KnowledgeGraph
from .audit import QualityGateAuditLogger
from .validators import BaseValidator
from .models import GateResult
from .config import load_gate_config, list_gates

logger = logging.getLogger(__name__)


class QualityGateCLI:
    """CLI interface for Quality Gates."""

    def __init__(self, db_path: str, tenant_id: str):
        """Initialize CLI.

        Args:
            db_path: Path to DuckDB database
            tenant_id: Tenant ID for queries
        """
        self.db_path = db_path
        self.tenant_id = tenant_id
        self.graph = KnowledgeGraph(db_path, tenant_id)
        self.audit_logger = QualityGateAuditLogger(self.graph.conn)

    def run_validator(
        self,
        gate_name: str,
        artifact: Dict[str, Any],
    ) -> GateResult:
        """Run a single validator.

        Args:
            gate_name: Name of gate validator (e.g., 'IdeaGate')
            artifact: Artifact to validate

        Returns:
            GateResult
        """
        try:
            config = load_gate_config(gate_name)
            validator_class = config["validator_class"]
            validator = validator_class(self.graph, self.tenant_id)
            result = validator.validate(artifact)

            # Log to audit chain
            self.audit_logger.write_gate_event(result)

            return result
        except Exception as e:
            logger.error(f"Validator error: {e}")
            raise

    def run_all_validators(
        self,
        artifacts: Dict[str, Dict[str, Any]],
    ) -> Dict[str, GateResult]:
        """Run all validators on provided artifacts.

        Args:
            artifacts: Dict of gate_name -> artifact

        Returns:
            Dict of gate_name -> GateResult
        """
        results = {}
        for gate_name, artifact in artifacts.items():
            try:
                result = self.run_validator(gate_name, artifact)
                results[gate_name] = result
            except Exception as e:
                logger.error(f"Error running {gate_name}: {e}")

        return results

    def format_result_human(self, result: GateResult) -> str:
        """Format GateResult as human-readable text.

        Args:
            result: GateResult to format

        Returns:
            Formatted string
        """
        lines = [
            f"Gate: {result.gate_name}",
            f"Artifact: {result.artifact_id}",
            f"Verdict: {result.verdict.value.upper()}",
            f"Confidence: {result.confidence:.2f}",
            f"Reason: {result.reason}",
        ]

        if result.findings:
            lines.append("Findings:")
            for finding in result.findings:
                lines.append(f"  - {finding}")

        return "\n".join(lines)

    def format_results_human(self, results: Dict[str, GateResult]) -> str:
        """Format multiple results as human-readable text.

        Args:
            results: Dict of gate_name -> GateResult

        Returns:
            Formatted string
        """
        lines = []
        for gate_name, result in results.items():
            lines.append(self.format_result_human(result))
            lines.append("")

        return "\n".join(lines)

    def format_result_json(self, result: GateResult) -> dict:
        """Convert GateResult to JSON-serializable dict.

        Args:
            result: GateResult

        Returns:
            Dict representation
        """
        return {
            "gate_name": result.gate_name,
            "artifact_id": result.artifact_id,
            "verdict": result.verdict.value,
            "confidence": result.confidence,
            "reason": result.reason,
            "findings": result.findings,
            "timestamp": result.timestamp,
        }

    def format_results_json(self, results: Dict[str, GateResult]) -> dict:
        """Convert multiple results to JSON-serializable dict.

        Args:
            results: Dict of gate_name -> GateResult

        Returns:
            Dict with results array
        """
        return {
            "results": [self.format_result_json(result) for result in results.values()],
            "timestamp": results[list(results.keys())[0]].timestamp if results else None,
        }

    def get_exit_code(self, results: Dict[str, GateResult]) -> int:
        """Determine exit code from results.

        Args:
            results: Dict of gate_name -> GateResult

        Returns:
            0 if all pass, 2 if any warn, 1 if any fail
        """
        has_fail = False
        has_warn = False

        for result in results.values():
            if result.verdict.value == "fail":
                has_fail = True
            elif result.verdict.value == "warn":
                has_warn = True

        if has_fail:
            return 1
        elif has_warn:
            return 2
        else:
            return 0

    def close(self) -> None:
        """Close database connection."""
        self.graph.close()

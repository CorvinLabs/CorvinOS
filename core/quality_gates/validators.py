"""Quality gate validators for Quality Gates System (ADR-0688).

Implements 4 gate validators:
- IdeaGateValidator
- ConceptGateValidator
- ADRGateValidator
- ImplementationPlanGateValidator
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
import logging

from .models import GateResult, VerdictType
from .graph import KnowledgeGraph

logger = logging.getLogger(__name__)


class BaseValidator(ABC):
    """Abstract base class for gate validators."""

    def __init__(self, graph: KnowledgeGraph, tenant_id: str):
        """Initialize validator.

        Args:
            graph: KnowledgeGraph instance
            tenant_id: Tenant ID
        """
        self.graph = graph
        self.tenant_id = tenant_id
        self.gate_name = self.__class__.__name__.replace("Validator", "")

    @abstractmethod
    def validate(self, artifact: Any) -> GateResult:
        """Validate an artifact.

        Args:
            artifact: Artifact to validate (dict, object, etc.)

        Returns:
            GateResult with verdict and reason
        """
        pass

    def fail_closed(self, reason: str) -> GateResult:
        """Return fail verdict (fail-closed on error).

        Args:
            reason: Reason for failure

        Returns:
            GateResult with verdict=fail
        """
        return GateResult(
            gate_name=self.gate_name,
            artifact_id="unknown",
            verdict=VerdictType.FAIL,
            reason=reason,
            confidence=0.0,
            tenant_id=self.tenant_id,
        )


class IdeaGateValidator(BaseValidator):
    """Validator for Idea artifacts (CONCEPT-0040).

    Rule: Evidence (2+ sources) + recurrence (2+ tasks)
    Confidence: Based on evidence strength
    """

    def validate(self, artifact: Dict[str, Any]) -> GateResult:
        """Validate an Idea artifact.

        Args:
            artifact: Idea dict with:
                - id: str
                - description: str
                - evidence_tasks: List[str]
                - evidence_commits: List[str]
                - recurrence_count: int (optional)

        Returns:
            GateResult
        """
        try:
            artifact_id = artifact.get("id", "unknown")

            # Extract evidence
            evidence_tasks = artifact.get("evidence_tasks", [])
            evidence_commits = artifact.get("evidence_commits", [])
            recurrence_count = artifact.get("recurrence_count", 0)

            # Total evidence count
            total_evidence = len(evidence_tasks) + len(evidence_commits)

            # Rule: 2+ evidence sources OR 2+ recurrence tasks
            has_enough_evidence = total_evidence >= 2 or recurrence_count >= 2

            if not has_enough_evidence:
                findings = []
                if total_evidence < 2:
                    findings.append(
                        f"Insufficient evidence: {total_evidence} sources (need 2+)"
                    )
                if recurrence_count < 2:
                    findings.append(
                        f"Low recurrence: {recurrence_count} tasks (need 2+)"
                    )

                return GateResult(
                    gate_name=self.gate_name,
                    artifact_id=artifact_id,
                    verdict=VerdictType.FAIL,
                    reason="Evidence and recurrence do not meet minimum thresholds",
                    confidence=0.2,
                    tenant_id=self.tenant_id,
                    findings=findings,
                )

            # Confidence: based on evidence strength (more evidence = higher confidence)
            confidence = min(0.95, 0.5 + (total_evidence * 0.15) + (recurrence_count * 0.1))

            return GateResult(
                gate_name=self.gate_name,
                artifact_id=artifact_id,
                verdict=VerdictType.PASS,
                reason=f"Idea meets evidence threshold ({total_evidence} sources, {recurrence_count} recurrence)",
                confidence=confidence,
                tenant_id=self.tenant_id,
            )

        except Exception as e:
            logger.error(f"IdeaGateValidator error: {e}")
            return self.fail_closed(f"Validation error: {str(e)}")


class ConceptGateValidator(BaseValidator):
    """Validator for Concept artifacts (CONCEPT-0040).

    Rule: Narrative (>100 words) + boundaries + evidence commits (2+)
    Confidence: Based on completeness
    """

    def validate(self, artifact: Dict[str, Any]) -> GateResult:
        """Validate a Concept artifact.

        Args:
            artifact: Concept dict with:
                - id: str
                - narrative: str
                - boundaries: str (optional)
                - evidence_commits: List[str]
                - skills: List[str] (optional)

        Returns:
            GateResult
        """
        try:
            artifact_id = artifact.get("id", "unknown")
            narrative = artifact.get("narrative", "")
            boundaries = artifact.get("boundaries", "")
            evidence_commits = artifact.get("evidence_commits", [])

            findings = []

            # Check narrative length
            narrative_words = len(narrative.split())
            if narrative_words < 100:
                findings.append(
                    f"Narrative too short: {narrative_words} words (need 100+)"
                )

            # Check boundaries
            if not boundaries or len(boundaries.strip()) == 0:
                findings.append("Boundaries field is missing or empty")

            # Check evidence commits
            if len(evidence_commits) < 2:
                findings.append(
                    f"Insufficient evidence commits: {len(evidence_commits)} (need 2+)"
                )

            if findings:
                return GateResult(
                    gate_name=self.gate_name,
                    artifact_id=artifact_id,
                    verdict=VerdictType.FAIL,
                    reason="Concept does not meet completeness requirements",
                    confidence=0.2,
                    tenant_id=self.tenant_id,
                    findings=findings,
                )

            # Confidence: based on detail level
            completeness = min(1.0, (narrative_words / 200.0) + 0.3 + (len(evidence_commits) * 0.2))
            confidence = min(0.95, 0.6 + (completeness * 0.35))

            return GateResult(
                gate_name=self.gate_name,
                artifact_id=artifact_id,
                verdict=VerdictType.PASS,
                reason=f"Concept meets requirements ({narrative_words} words, {len(evidence_commits)} evidence commits)",
                confidence=confidence,
                tenant_id=self.tenant_id,
            )

        except Exception as e:
            logger.error(f"ConceptGateValidator error: {e}")
            return self.fail_closed(f"Validation error: {str(e)}")


class ADRGateValidator(BaseValidator):
    """Validator for ADR artifacts (CONCEPT-0040).

    Rule: Frontmatter complete (id, status, depends_on, paths, docs, commits all present)
    Confidence: Binary (valid/invalid)
    """

    def validate(self, artifact: Dict[str, Any]) -> GateResult:
        """Validate an ADR artifact.

        Args:
            artifact: ADR dict with:
                - id: str
                - status: str
                - depends_on: List[str]
                - paths: List[str]
                - docs: List[str]
                - commits: List[str]

        Returns:
            GateResult
        """
        try:
            artifact_id = artifact.get("id", "unknown")

            required_fields = ["id", "status", "depends_on", "paths", "docs", "commits"]
            findings = []

            for field in required_fields:
                value = artifact.get(field)
                if value is None:
                    findings.append(f"Missing frontmatter field: {field}")
                elif isinstance(value, list) and len(value) == 0:
                    if field not in ["depends_on"]:  # depends_on can be empty
                        findings.append(f"Empty list field: {field}")

            if findings:
                return GateResult(
                    gate_name=self.gate_name,
                    artifact_id=artifact_id,
                    verdict=VerdictType.FAIL,
                    reason="ADR frontmatter incomplete",
                    confidence=0.0,
                    tenant_id=self.tenant_id,
                    findings=findings,
                )

            return GateResult(
                gate_name=self.gate_name,
                artifact_id=artifact_id,
                verdict=VerdictType.PASS,
                reason="ADR frontmatter valid and complete",
                confidence=0.95,
                tenant_id=self.tenant_id,
            )

        except Exception as e:
            logger.error(f"ADRGateValidator error: {e}")
            return self.fail_closed(f"Validation error: {str(e)}")


class ImplementationPlanGateValidator(BaseValidator):
    """Validator for ImplementationPlan artifacts (CONCEPT-0040).

    Rule: 3 phases + success criteria + resource estimate
    Confidence: Based on detail level
    """

    def validate(self, artifact: Dict[str, Any]) -> GateResult:
        """Validate an ImplementationPlan artifact.

        Args:
            artifact: ImplementationPlan dict with:
                - id: str
                - subsystem: str
                - phases: List[Dict]
                - success_criteria: str (optional)
                - resource_estimate: str (optional)
                - timeline_weeks: int (optional)

        Returns:
            GateResult
        """
        try:
            artifact_id = artifact.get("id", "unknown")
            phases = artifact.get("phases", [])
            success_criteria = artifact.get("success_criteria", "")
            resource_estimate = artifact.get("resource_estimate", "")
            timeline_weeks = artifact.get("timeline_weeks", 0)

            findings = []

            # Check phases
            if len(phases) < 3:
                findings.append(f"Insufficient phases: {len(phases)} (need 3+)")

            # Check success criteria
            if not success_criteria or len(success_criteria.strip()) == 0:
                findings.append("Success criteria missing or empty")

            # Check resource estimate
            if not resource_estimate or len(resource_estimate.strip()) == 0:
                findings.append("Resource estimate missing or empty")

            if findings:
                return GateResult(
                    gate_name=self.gate_name,
                    artifact_id=artifact_id,
                    verdict=VerdictType.FAIL,
                    reason="Implementation plan does not meet requirements",
                    confidence=0.2,
                    tenant_id=self.tenant_id,
                    findings=findings,
                )

            # Confidence: based on detail level
            detail_level = min(1.0, (len(phases) / 5.0) + 0.3 + (timeline_weeks / 20.0))
            confidence = min(0.95, 0.6 + (detail_level * 0.35))

            return GateResult(
                gate_name=self.gate_name,
                artifact_id=artifact_id,
                verdict=VerdictType.PASS,
                reason=f"Implementation plan meets requirements ({len(phases)} phases, {timeline_weeks} weeks)",
                confidence=confidence,
                tenant_id=self.tenant_id,
            )

        except Exception as e:
            logger.error(f"ImplementationPlanGateValidator error: {e}")
            return self.fail_closed(f"Validation error: {str(e)}")

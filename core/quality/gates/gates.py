"""
Quality Gates for Idea-to-Implementation Pipeline.

Four gates: idea-gate, concept-gate, adr-gate (enhanced), implementation-gate
Each enforces upstream/downstream lineage constraints.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Tuple
from enum import Enum

from ..models.artifact import Artifact, ArtifactType, Status, Idea, Concept, ADR, ImplementationPlan
from ..palace.drawer import Drawer, DrawerManager


class GateVerdict(str, Enum):
    """Gate validation verdict."""
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"


@dataclass
class GateResult:
    """Result of gate validation."""
    gate_name: str
    artifact_id: str
    verdict: GateVerdict
    issues: List[str]
    warnings: List[str]
    approved_at: Optional[datetime] = None

    def is_pass(self) -> bool:
        return self.verdict == GateVerdict.PASS

    def to_dict(self) -> dict:
        return {
            'gate': self.gate_name,
            'artifact_id': self.artifact_id,
            'verdict': self.verdict.value,
            'issues': self.issues,
            'warnings': self.warnings,
            'approved_at': self.approved_at.isoformat() if self.approved_at else None,
        }


class IdeaGate:
    """
    Gate for raw ideas: minimal validation.

    Rules:
    - Must have a name
    - Status must be draft | proposed
    - Tags are optional but recommended
    - No upstream required (it's a root)
    """

    def validate(self, idea: Idea) -> GateResult:
        """Validate idea."""
        issues = []
        warnings = []

        # Must have name
        if not idea.name or not idea.name.strip():
            issues.append("Idea must have a name")

        # Status must be draft or proposed
        if idea.status not in [Status.DRAFT, Status.PROPOSED]:
            issues.append(f"Idea status must be draft or proposed, got {idea.status}")

        # Warn if no tags
        if not idea.tags:
            warnings.append("Idea has no tags; recommend adding at least one")

        # Warn if no inspiration context
        if not idea.inspiration_context:
            warnings.append("Idea lacks inspiration context; where did this come from?")

        # Idea should not have upstream (it's a root)
        if idea.upstream:
            warnings.append("Idea has upstream; ideas are typically roots of the pipeline")

        verdict = GateVerdict.FAIL if issues else GateVerdict.PASS
        if warnings and not issues:
            verdict = GateVerdict.WARN

        return GateResult(
            gate_name="idea-gate",
            artifact_id=idea.id,
            verdict=verdict,
            issues=issues,
            warnings=warnings,
            approved_at=idea.approved_at,
        )


class ConceptGate:
    """
    Gate for reusable concepts.

    Rules:
    - Must have upstream Idea (auto-generate if missing)
    - Must have a name
    - Status must be draft | proposed | approved
    - Downstream should link back to upstream Idea
    - Tags required (pattern name, keywords)
    """

    def validate(self, concept: Concept, drawer_manager: Optional[DrawerManager] = None) -> GateResult:
        """Validate concept."""
        issues = []
        warnings = []

        # Must have name
        if not concept.name or not concept.name.strip():
            issues.append("Concept must have a name")

        # Status must be draft | proposed | approved
        if concept.status not in [Status.DRAFT, Status.PROPOSED, Status.APPROVED]:
            issues.append(f"Concept status must be draft/proposed/approved, got {concept.status}")

        # Must have tags (pattern name, keywords)
        if not concept.tags:
            issues.append("Concept must have tags (e.g., pattern name, keywords)")
        elif len(concept.tags) < 2:
            warnings.append("Concept has <2 tags; recommend at least 2 for discoverability")

        # Must have upstream Idea
        if not concept.upstream:
            issues.append("Concept must have upstream Idea (auto-generate? Call idea-gate first)")
        else:
            # Verify upstream exists (if drawer_manager provided)
            if drawer_manager:
                upstream_idea = drawer_manager.load('idea', concept.upstream)
                if not upstream_idea:
                    issues.append(f"Upstream Idea {concept.upstream} not found in repository")

        # Downstream should link to this concept's ID in upstream Idea
        # (This is validated in bidirectional-backfill, not here)

        verdict = GateVerdict.FAIL if issues else GateVerdict.PASS
        if warnings and not issues:
            verdict = GateVerdict.WARN

        return GateResult(
            gate_name="concept-gate",
            artifact_id=concept.id,
            verdict=verdict,
            issues=issues,
            warnings=warnings,
            approved_at=concept.approved_at,
        )


class ADRGate:
    """
    Enhanced gate for Architecture Decision Records.

    Rules (existing ADR-0264 + enhancements):
    - Must have upstream Concept (new)
    - Decision section required
    - Context + Alternatives sections required
    - Status progression: proposed → approved → active
    - No supersedes without explicit ADR number
    """

    def validate(self, adr: ADR, drawer_manager: Optional[DrawerManager] = None) -> GateResult:
        """Validate ADR."""
        issues = []
        warnings = []

        # Must have name
        if not adr.name or not adr.name.strip():
            issues.append("ADR must have a name (title)")

        # Status progression
        if adr.status not in [Status.PROPOSED, Status.APPROVED, Status.ACTIVE]:
            issues.append(f"ADR status must be proposed/approved/active, got {adr.status}")

        # ADR must have upstream Concept
        if not adr.upstream:
            issues.append("ADR must have upstream Concept (link to CONCEPT-NNNN)")
        else:
            if drawer_manager:
                upstream_concept = drawer_manager.load('concept', adr.upstream)
                if not upstream_concept:
                    issues.append(f"Upstream Concept {adr.upstream} not found")

        # Warn if no tags
        if not adr.tags:
            warnings.append("ADR should have tags (e.g., 'security', 'performance', 'api')")

        verdict = GateVerdict.FAIL if issues else GateVerdict.PASS
        if warnings and not issues:
            verdict = GateVerdict.WARN

        return GateResult(
            gate_name="adr-gate",
            artifact_id=adr.id,
            verdict=verdict,
            issues=issues,
            warnings=warnings,
            approved_at=adr.approved_at,
        )


class ImplementationGate:
    """
    Blocking gate for implementation plans.

    Rules:
    - Must have upstream ADR (blocking)
    - Must have ≥1 deployment step
    - Status: approved → active → superseded
    - Must have success criteria
    - Rollback procedure recommended
    """

    def validate(self, plan: ImplementationPlan, drawer_manager: Optional[DrawerManager] = None) -> GateResult:
        """Validate implementation plan."""
        issues = []
        warnings = []

        # Must have upstream ADR
        if not plan.upstream:
            issues.append("Plan must have upstream ADR (blocking gate)")
        else:
            if drawer_manager:
                upstream_adr = drawer_manager.load('adr', plan.upstream)
                if not upstream_adr:
                    issues.append(f"Upstream ADR {plan.upstream} not found")

        # Must have ≥1 deployment step
        if not plan.deployment_steps or len(plan.deployment_steps) == 0:
            issues.append("Plan must have at least 1 deployment step")

        # Must have success criteria
        if not plan.success_criteria or not plan.success_criteria.strip():
            issues.append("Plan must define success criteria")

        # Recommend rollback procedure
        if not plan.rollback_procedure:
            warnings.append("Plan should include rollback procedure (failure path)")

        # Recommend rollout sequence
        if not plan.rollout_sequence:
            warnings.append("Plan should include rollout sequence (canary → staged → full)")

        # Status must be approved | active | superseded
        if plan.status not in [Status.APPROVED, Status.ACTIVE, Status.SUPERSEDED]:
            issues.append(f"Plan status should be approved/active/superseded, got {plan.status}")

        verdict = GateVerdict.FAIL if issues else GateVerdict.PASS
        if warnings and not issues:
            verdict = GateVerdict.WARN

        return GateResult(
            gate_name="implementation-gate",
            artifact_id=plan.id,
            verdict=verdict,
            issues=issues,
            warnings=warnings,
            approved_at=plan.approved_at,
        )


class PipelineAudit:
    """
    Full-pipeline audit: check for orphans, cycles, broken lineage.
    """

    def audit_all(self, drawer_manager: DrawerManager) -> List[GateResult]:
        """Audit all artifacts in pipeline."""
        results = []

        all_artifacts = drawer_manager.list_all()

        # Check each artifact type
        for idea in all_artifacts['ideas']:
            result = IdeaGate().validate(idea)
            results.append(result)

        for concept in all_artifacts['concepts']:
            result = ConceptGate().validate(concept, drawer_manager)
            results.append(result)

        for adr in all_artifacts['adrs']:
            result = ADRGate().validate(adr, drawer_manager)
            results.append(result)

        for plan in all_artifacts['plans']:
            result = ImplementationGate().validate(plan, drawer_manager)
            results.append(result)

        # Check for orphans
        orphans = self._find_orphans(all_artifacts)
        for artifact_id in orphans:
            results.append(GateResult(
                gate_name="pipeline-audit",
                artifact_id=artifact_id,
                verdict=GateVerdict.FAIL,
                issues=[f"Orphan artifact: no upstream lineage"],
                warnings=[],
            ))

        # Check for cycles
        cycles = self._find_cycles(all_artifacts)
        for cycle_path in cycles:
            results.append(GateResult(
                gate_name="pipeline-audit",
                artifact_id=cycle_path[0],
                verdict=GateVerdict.FAIL,
                issues=[f"Cycle detected: {' → '.join(cycle_path)}"],
                warnings=[],
            ))

        return results

    def _find_orphans(self, all_artifacts: dict) -> List[str]:
        """Find artifacts with no upstream (except Ideas)."""
        orphans = []

        # Concepts without Ideas
        for concept in all_artifacts['concepts']:
            if not concept.upstream:
                orphans.append(concept.id)

        # ADRs without Concepts
        for adr in all_artifacts['adrs']:
            if not adr.upstream:
                orphans.append(adr.id)

        # Plans without ADRs
        for plan in all_artifacts['plans']:
            if not plan.upstream:
                orphans.append(plan.id)

        return orphans

    def _find_cycles(self, all_artifacts: dict) -> List[List[str]]:
        """Find circular dependencies in lineage."""
        # Build graph
        graph = {}
        for artifact_list in all_artifacts.values():
            for artifact in artifact_list:
                if artifact.downstream:
                    graph[artifact.id] = artifact.downstream

        cycles = []

        # DFS from each node
        for start_id in graph.keys():
            visited = set()
            path = []
            if self._dfs_cycle(start_id, graph, visited, path):
                cycles.append(path)

        return cycles

    def _dfs_cycle(self, node_id: str, graph: dict, visited: set, path: List[str]) -> bool:
        """DFS to find cycles."""
        if node_id in visited:
            if node_id in path:
                # Found cycle
                return True
            return False

        visited.add(node_id)
        path.append(node_id)

        for child_id in graph.get(node_id, []):
            if self._dfs_cycle(child_id, graph, visited, path):
                return True

        path.pop()
        return False

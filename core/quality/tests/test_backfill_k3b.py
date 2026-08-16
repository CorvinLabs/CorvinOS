"""
K=3b Tests: Bidirectional Upstream Backfill + Integration.

Tests: auto-generate missing upstream Ideas/Concepts/ADRs
Tests: full pipeline create + audit + backfill workflow
"""

import tempfile
from pathlib import Path
from datetime import datetime

from ..cli import IdeaPipelineCLI
from ..backfill import UpstreamBackfill
from ..models.artifact import Concept, ADR, ImplementationPlan, Status
from ..palace import IdeaPalace


class TestBackfillIdea:
    """Test: Auto-generate missing upstream Ideas."""

    def test_generate_idea_for_concept(self):
        """Generate Idea when Concept has no upstream."""
        with tempfile.TemporaryDirectory() as tmpdir:
            palace = IdeaPalace(Path(tmpdir))
            backfill = UpstreamBackfill(palace)

            wing = palace.create_wing('test')
            room = wing.create_room('consensus')

            concept = Concept(
                id='CONCEPT-0001',
                name='raft-algorithm',
                room='consensus',
                wing='test',
                status=Status.DRAFT,
                created_at=datetime.now(),
                tags=['raft'],
                # No upstream
            )

            # Generate upstream
            idea_id = backfill.ensure_idea_upstream(concept.id, concept, auto_generate=True)
            assert idea_id.startswith('IDEA-')
            print(f"✓ Generated: {idea_id} ← {concept.id}")

    def test_dont_generate_if_upstream_exists(self):
        """Don't regenerate if upstream already set."""
        with tempfile.TemporaryDirectory() as tmpdir:
            palace = IdeaPalace(Path(tmpdir))
            backfill = UpstreamBackfill(palace)

            concept = Concept(
                id='CONCEPT-0001',
                name='raft',
                room='test',
                wing='test',
                status=Status.DRAFT,
                created_at=datetime.now(),
                upstream='IDEA-0001',
                tags=['raft'],
            )

            # Should return existing upstream
            idea_id = backfill.ensure_idea_upstream(concept.id, concept, auto_generate=True)
            assert idea_id == 'IDEA-0001'
            print("✓ Returns existing upstream (no regeneration)")

    def test_return_none_if_auto_generate_false(self):
        """Return None if auto_generate is False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            palace = IdeaPalace(Path(tmpdir))
            backfill = UpstreamBackfill(palace)

            concept = Concept(
                id='CONCEPT-0001',
                name='raft',
                room='test',
                wing='test',
                status=Status.DRAFT,
                created_at=datetime.now(),
                # No upstream
            )

            # Should return None
            idea_id = backfill.ensure_idea_upstream(concept.id, concept, auto_generate=False)
            assert idea_id is None
            print("✓ Returns None when auto_generate=False")


class TestBackfillConcept:
    """Test: Auto-generate missing upstream Concepts."""

    def test_generate_concept_for_adr(self):
        """Generate Concept when ADR has no upstream."""
        with tempfile.TemporaryDirectory() as tmpdir:
            palace = IdeaPalace(Path(tmpdir))
            backfill = UpstreamBackfill(palace)

            wing = palace.create_wing('test')
            room = wing.create_room('consensus')

            adr = ADR(
                id='ADR-0001',
                name='use-raft',
                room='consensus',
                wing='test',
                status=Status.PROPOSED,
                created_at=datetime.now(),
                # No upstream
            )

            # Generate upstream
            concept_id = backfill.ensure_concept_upstream(adr.id, adr, auto_generate=True)
            assert concept_id.startswith('CONCEPT-')
            print(f"✓ Generated: {concept_id} ← {adr.id}")


class TestBackfillADR:
    """Test: Auto-generate missing upstream ADRs."""

    def test_generate_adr_for_plan(self):
        """Generate ADR when Plan has no upstream."""
        with tempfile.TemporaryDirectory() as tmpdir:
            palace = IdeaPalace(Path(tmpdir))
            backfill = UpstreamBackfill(palace)

            wing = palace.create_wing('test')
            room = wing.create_room('consensus')

            plan = ImplementationPlan(
                id='IMPL-0001',
                name='raft-rollout',
                room='consensus',
                wing='test',
                status=Status.APPROVED,
                created_at=datetime.now(),
                deployment_steps=['Step 1'],
                success_criteria='Works',
                # No upstream
            )

            # Generate upstream
            adr_id = backfill.ensure_adr_upstream(plan.id, plan, auto_generate=True)
            assert adr_id.startswith('ADR-')
            print(f"✓ Generated: {adr_id} ← {plan.id}")


class TestBackfillLineage:
    """Test: Audit and backfill full lineage."""

    def test_backfill_lineage_report(self):
        """Backfill lineage and report results."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cli = IdeaPipelineCLI(Path(tmpdir))

            # Create artifacts with missing upstreams
            concept_id = cli.create_artifact('concept', 'orphaned-concept', 'test', 'test', upstream=None)
            adr_id = cli.create_artifact('adr', 'orphaned-adr', 'test', 'test', upstream=None)
            plan_id = cli.create_artifact('implementation-plan', 'orphaned-plan', 'test', 'test', upstream=None)

            # Backfill
            palace = IdeaPalace(Path(tmpdir))
            backfill = UpstreamBackfill(palace)
            result = backfill.backfill_lineage('test', auto_generate=True)

            assert result['filled'] >= 3, f"Expected ≥3 filled, got {result['filled']}"
            print(f"✓ Backfilled {result['filled']} upstreams")

    def test_backfill_warning_if_not_auto(self):
        """Report warnings if auto_generate=False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cli = IdeaPipelineCLI(Path(tmpdir))
            cli.create_artifact('concept', 'orphaned', 'test', 'test', upstream=None)

            palace = IdeaPalace(Path(tmpdir))
            backfill = UpstreamBackfill(palace)
            result = backfill.backfill_lineage('test', auto_generate=False)

            assert len(result['warnings']) > 0
            print(f"✓ Got {len(result['warnings'])} warnings")


class TestFullPipelineIntegration:
    """Test: Full create → audit → backfill workflow."""

    def test_create_full_pipeline_with_backfill(self):
        """Full E2E: create artifacts → run audit → backfill missing → re-audit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cli = IdeaPipelineCLI(Path(tmpdir))

            # Create complete pipeline
            idea_id = cli.create_artifact('idea', 'Consensus', 'consensus', 'core')
            concept_id = cli.create_artifact('concept', 'Raft', 'consensus', 'core', upstream=idea_id)
            adr_id = cli.create_artifact('adr', 'Use Raft', 'consensus', 'core', upstream=concept_id)
            plan_id = cli.create_artifact('implementation-plan', 'Rollout', 'consensus', 'core', upstream=adr_id)

            # Audit 1 (all green)
            print("\nAudit 1 (complete pipeline):")
            cli.audit(wing='core')

            # Create orphaned artifacts
            orphan_concept = cli.create_artifact('concept', 'Orphaned Concept', 'orphans', 'core')
            orphan_adr = cli.create_artifact('adr', 'Orphaned ADR', 'orphans', 'core')

            print("\nAudit 2 (with orphans):")
            cli.audit(wing='core')

            # Backfill
            palace = IdeaPalace(Path(tmpdir))
            backfill = UpstreamBackfill(palace)
            result = backfill.backfill_lineage('core', auto_generate=True)
            print(f"\nBackfilled {result['filled']} artifacts")

            # Audit 3 (after backfill)
            print("\nAudit 3 (after backfill):")
            cli.audit(wing='core')

            print(f"\n✓ Full pipeline: {idea_id} → {concept_id} → {adr_id} → {plan_id}")
            print(f"✓ Backfilled orphans: {result['filled']} upstreams generated")


if __name__ == '__main__':
    print("=" * 60)
    print("K=3b Test Suite: Backfill + Integration")
    print("=" * 60)

    print("\n[Backfill Idea]")
    TestBackfillIdea().test_generate_idea_for_concept()
    TestBackfillIdea().test_dont_generate_if_upstream_exists()
    TestBackfillIdea().test_return_none_if_auto_generate_false()

    print("\n[Backfill Concept]")
    TestBackfillConcept().test_generate_concept_for_adr()

    print("\n[Backfill ADR]")
    TestBackfillADR().test_generate_adr_for_plan()

    print("\n[Backfill Lineage]")
    TestBackfillLineage().test_backfill_lineage_report()
    TestBackfillLineage().test_backfill_warning_if_not_auto()

    print("\n[Full Integration]")
    TestFullPipelineIntegration().test_create_full_pipeline_with_backfill()

    print("\n" + "=" * 60)
    print("K=3b: All tests PASS ✓")
    print("=" * 60)

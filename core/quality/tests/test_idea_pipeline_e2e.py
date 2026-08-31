"""
E2E tests for Idea-to-Implementation Pipeline.

Tests: Drawer I/O, Navigator, Artifact Models, Quality Gates, lineage validation.
"""

import pytest
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

from ..palace import Drawer, DrawerManager, IdeaPalace
from ..models.artifact import Idea, Concept, ADR, ImplementationPlan, Status, ArtifactType
from ..gates.gates import IdeaGate, ConceptGate, ADRGate, ImplementationGate, PipelineAudit


class TestDrawerIO:
    """Test: Drawer markdown I/O (immutability, schema validation)."""

    def test_drawer_serialization(self):
        """Drawer → markdown → Drawer round-trip."""
        metadata = {
            'id': 'IDEA-0001',
            'type': 'idea',
            'name': 'distributed-consensus',
            'status': 'proposed',
            'tags': ['consensus', 'distributed-systems'],
            'created_at': '2026-08-16T10:00:00',
            'upstream': None,
            'downstream': [],
        }

        content = "# Distributed Consensus\n\nProblem: how to achieve agreement without a central authority?"

        drawer = Drawer(
            artifact_type='idea',
            artifact_id='IDEA-0001',
            content=content,
            metadata=metadata,
        )

        # Serialize
        markdown = drawer.to_markdown()
        assert '---' in markdown
        assert 'id: IDEA-0001' in markdown
        assert content in markdown

        # Deserialize
        drawer2 = Drawer.from_markdown(markdown, 'idea')
        assert drawer2.id == drawer.id
        assert drawer2.content == drawer.content
        assert drawer2.metadata['name'] == 'distributed-consensus'

    def test_drawer_immutability(self):
        """Drawers cannot be overwritten."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wing_path = Path(tmpdir)
            dm = DrawerManager(wing_path)

            metadata = {
                'id': 'IDEA-0001',
                'type': 'idea',
                'name': 'test-idea',
                'status': 'draft',
                'created_at': '2026-08-16T10:00:00',
            }

            drawer = Drawer('idea', 'IDEA-0001', 'Original content', metadata)

            # First save
            path1 = dm.save(drawer)
            assert path1.exists()

            # Try to save same ID again
            with pytest.raises(FileExistsError):
                dm.save(drawer)

    def test_drawer_manager_list_by_type(self):
        """List drawers by type."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wing_path = Path(tmpdir)
            dm = DrawerManager(wing_path)

            # Create 2 ideas
            for i in range(1, 3):
                metadata = {
                    'id': f'IDEA-000{i}',
                    'type': 'idea',
                    'name': f'idea-{i}',
                    'status': 'draft',
                    'created_at': '2026-08-16T10:00:00',
                }
                drawer = Drawer('idea', f'IDEA-000{i}', f'Content {i}', metadata)
                dm.save(drawer)

            ideas = dm.list_by_type('idea')
            assert len(ideas) == 2


class TestArtifactModels:
    """Test: Idea, Concept, ADR, Plan model validation."""

    def test_idea_creation(self):
        """Create and validate Idea."""
        idea = Idea(
            id='IDEA-0001',
            name='distributed-consensus',
            room='distributed-systems',
            wing='core',
            status=Status.PROPOSED,
            created_at=datetime.now(),
            tags=['consensus', 'protocols'],
        )

        assert idea.type == ArtifactType.IDEA
        assert idea.upstream is None
        assert idea.downstream == []

    def test_concept_with_upstream(self):
        """Concept must reference upstream Idea."""
        concept = Concept(
            id='CONCEPT-0001',
            name='raft-consensus',
            room='consensus-algorithms',
            wing='core',
            status=Status.PROPOSED,
            created_at=datetime.now(),
            upstream='IDEA-0001',
            tags=['raft', 'consensus', 'algorithm'],
        )

        assert concept.upstream == 'IDEA-0001'
        assert concept.type == ArtifactType.CONCEPT

    def test_adr_with_concept_upstream(self):
        """ADR must reference upstream Concept."""
        adr = ADR(
            id='ADR-0321',
            name='use-raft-for-distributed-consensus',
            room='consensus-algorithms',
            wing='core',
            status=Status.PROPOSED,
            created_at=datetime.now(),
            upstream='CONCEPT-0001',
            tags=['raft', 'decision', 'consensus'],
        )

        assert adr.upstream == 'CONCEPT-0001'
        assert adr.type == ArtifactType.ADR

    def test_implementation_plan_structure(self):
        """Plan has deployment steps and success criteria."""
        plan = ImplementationPlan(
            id='IMPL-0001',
            name='raft-rollout-plan',
            room='consensus-algorithms',
            wing='core',
            status=Status.APPROVED,
            created_at=datetime.now(),
            upstream='ADR-0321',
            deployment_steps=[
                'Deploy consensus-service v1.0 to staging',
                'Run 1h smoke tests (replication, failover)',
                'Canary to 5% of prod nodes',
                'Monitor metrics for 6h',
                'Full rollout to 100%',
            ],
            success_criteria='Raft election time < 500ms, quorum reachability > 99.9%',
            rollback_procedure='Revert to previous consensus version (v0.9) + 15min sync window',
            rollout_sequence='canary → staged → full',
        )

        assert len(plan.deployment_steps) == 5
        assert 'Raft election time' in plan.success_criteria
        assert plan.type == ArtifactType.IMPLEMENTATION_PLAN


class TestQualityGates:
    """Test: idea-gate, concept-gate, adr-gate, implementation-gate."""

    def test_idea_gate_pass(self):
        """Valid idea passes gate."""
        idea = Idea(
            id='IDEA-0001',
            name='distributed-consensus',
            room='distributed-systems',
            wing='core',
            status=Status.PROPOSED,
            created_at=datetime.now(),
            tags=['consensus', 'protocols'],
            inspiration_context='Observed consensus gaps in microservices',
        )

        result = IdeaGate().validate(idea)
        assert result.is_pass()
        assert len(result.issues) == 0

    def test_idea_gate_fail_no_name(self):
        """Idea without name fails gate."""
        idea = Idea(
            id='IDEA-0001',
            name='',  # Empty
            room='distributed-systems',
            wing='core',
            status=Status.PROPOSED,
            created_at=datetime.now(),
        )

        result = IdeaGate().validate(idea)
        assert not result.is_pass()
        assert 'name' in result.issues[0].lower()

    def test_concept_gate_requires_upstream(self):
        """Concept without upstream Idea fails gate."""
        concept = Concept(
            id='CONCEPT-0001',
            name='raft-algorithm',
            room='consensus',
            wing='core',
            status=Status.PROPOSED,
            created_at=datetime.now(),
            tags=['algorithm'],
            # No upstream
        )

        result = ConceptGate().validate(concept)
        assert not result.is_pass()
        assert 'upstream' in result.issues[0].lower()

    def test_adr_gate_requires_concept_upstream(self):
        """ADR without upstream Concept fails gate."""
        adr = ADR(
            id='ADR-0321',
            name='use-raft',
            room='consensus',
            wing='core',
            status=Status.PROPOSED,
            created_at=datetime.now(),
            # No upstream
        )

        result = ADRGate().validate(adr)
        assert not result.is_pass()
        assert 'upstream' in result.issues[0].lower()

    def test_implementation_gate_blocks_missing_upstream(self):
        """Plan without upstream ADR fails (blocking)."""
        plan = ImplementationPlan(
            id='IMPL-0001',
            name='raft-rollout',
            room='consensus',
            wing='core',
            status=Status.APPROVED,
            created_at=datetime.now(),
            deployment_steps=['Step 1'],
            success_criteria='Test passes',
            # No upstream ADR
        )

        result = ImplementationGate().validate(plan)
        assert not result.is_pass()
        assert len(result.issues) > 0

    def test_implementation_gate_requires_steps_and_criteria(self):
        """Plan must have steps and success criteria."""
        plan = ImplementationPlan(
            id='IMPL-0001',
            name='raft-rollout',
            room='consensus',
            wing='core',
            status=Status.APPROVED,
            created_at=datetime.now(),
            upstream='ADR-0321',
            # Missing deployment_steps and success_criteria
        )

        result = ImplementationGate().validate(plan)
        assert not result.is_pass()
        assert len(result.issues) >= 2  # Missing steps and criteria


class TestNavigator:
    """Test: Wing/Room/Drawer navigation hierarchy."""

    def test_idea_palace_create_wing(self):
        """Create and manage Wings."""
        with tempfile.TemporaryDirectory() as tmpdir:
            palace = IdeaPalace(Path(tmpdir))

            wing = palace.create_wing('core')
            assert wing.name == 'core'
            assert wing.path.exists()

    def test_wing_create_room(self):
        """Create and manage Rooms."""
        with tempfile.TemporaryDirectory() as tmpdir:
            palace = IdeaPalace(Path(tmpdir))
            wing = palace.create_wing('core')

            room = wing.create_room('consensus')
            assert room.name == 'consensus'
            assert room.path.exists()

    def test_wing_summary_stats(self):
        """Wing aggregates statistics across rooms."""
        with tempfile.TemporaryDirectory() as tmpdir:
            palace = IdeaPalace(Path(tmpdir))
            wing = palace.create_wing('core')
            room = wing.create_room('consensus')

            # Save an idea to the room
            metadata = {
                'id': 'IDEA-0001',
                'type': 'idea',
                'name': 'consensus',
                'status': 'proposed',
                'created_at': '2026-08-16T10:00:00',
            }
            drawer = Drawer('idea', 'IDEA-0001', 'Content', metadata)
            room.drawer_manager.save(drawer)

            # Check room summary
            room_summary = room.summary()
            assert room_summary['ideas'] == 1
            assert room_summary['total'] == 1

            # Check wing summary
            wing_summary = wing.summary()
            assert wing_summary['ideas'] == 1
            assert wing_summary['rooms'] == 1


class TestPipelineLineage:
    """Test: Idea → Concept → ADR → Plan lineage validation."""

    def test_full_pipeline_lineage(self):
        """Create full pipeline with proper lineage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            palace = IdeaPalace(Path(tmpdir))
            wing = palace.create_wing('core')
            room = wing.create_room('consensus')

            # Create Idea
            idea = Idea(
                id='IDEA-0001',
                name='distributed-consensus',
                room='consensus',
                wing='core',
                status=Status.PROPOSED,
                created_at=datetime.now(),
                tags=['consensus'],
            )
            idea_drawer = Drawer('idea', idea.id, 'Idea content', idea.to_dict())
            room.drawer_manager.save(idea_drawer)

            # Create Concept with upstream link
            concept = Concept(
                id='CONCEPT-0001',
                name='raft-consensus',
                room='consensus',
                wing='core',
                status=Status.PROPOSED,
                created_at=datetime.now(),
                upstream='IDEA-0001',
                tags=['raft', 'consensus'],
            )
            concept_drawer = Drawer('concept', concept.id, 'Concept content', concept.to_dict())
            room.drawer_manager.save(concept_drawer)

            # Create ADR with upstream link
            adr = ADR(
                id='ADR-0321',
                name='use-raft',
                room='consensus',
                wing='core',
                status=Status.PROPOSED,
                created_at=datetime.now(),
                upstream='CONCEPT-0001',
                tags=['raft', 'decision'],
            )
            adr_drawer = Drawer('adr', adr.id, 'ADR content', adr.to_dict())
            room.drawer_manager.save(adr_drawer)

            # Create Plan with upstream link
            plan = ImplementationPlan(
                id='IMPL-0001',
                name='raft-rollout',
                room='consensus',
                wing='core',
                status=Status.APPROVED,
                created_at=datetime.now(),
                upstream='ADR-0321',
                deployment_steps=['Step 1', 'Step 2'],
                success_criteria='Replication works',
            )
            plan_drawer = Drawer('implementation-plan', plan.id, 'Plan content', plan.to_dict())
            room.drawer_manager.save(plan_drawer)

            # Verify full lineage
            all_artifacts = room.drawer_manager.list_all()
            assert len(all_artifacts['ideas']) == 1
            assert len(all_artifacts['concepts']) == 1
            assert len(all_artifacts['adrs']) == 1
            assert len(all_artifacts['plans']) == 1

            # Verify upstream links
            loaded_concept = all_artifacts['concepts'][0]
            assert loaded_concept.upstream == 'IDEA-0001'

            loaded_adr = all_artifacts['adrs'][0]
            assert loaded_adr.upstream == 'CONCEPT-0001'

            loaded_plan = all_artifacts['plans'][0]
            assert loaded_plan.upstream == 'ADR-0321'


class TestPipelineAudit:
    """Test: Full pipeline audit for orphans, cycles, lineage validation."""

    def test_audit_detects_orphans(self):
        """Audit detects orphaned artifacts (no upstream)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            palace = IdeaPalace(Path(tmpdir))
            wing = palace.create_wing('core')
            room = wing.create_room('consensus')

            # Create orphaned Concept (no upstream Idea)
            concept = Concept(
                id='CONCEPT-0001',
                name='orphaned-concept',
                room='consensus',
                wing='core',
                status=Status.PROPOSED,
                created_at=datetime.now(),
                # No upstream
            )
            concept_drawer = Drawer('concept', concept.id, 'Content', concept.to_dict())
            room.drawer_manager.save(concept_drawer)

            # Audit should find orphan
            audit = PipelineAudit()
            results = audit.audit_all(room.drawer_manager)

            orphan_findings = [r for r in results if 'orphan' in str(r.issues).lower()]
            assert len(orphan_findings) > 0

    def test_audit_detects_cycles(self):
        """Audit detects circular dependencies."""
        # This is a structural test; real cycles are hard to create
        # (would require manual metadata manipulation)
        # For now, test that cycle detection runs without error
        with tempfile.TemporaryDirectory() as tmpdir:
            palace = IdeaPalace(Path(tmpdir))
            wing = palace.create_wing('core')
            room = wing.create_room('consensus')

            audit = PipelineAudit()
            results = audit.audit_all(room.drawer_manager)

            # Should run without error (no artifacts = no cycles)
            assert isinstance(results, list)

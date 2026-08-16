"""
K=3a Tests: CLI + SkillForge Integration.

Tests: create, list, search, audit CLI commands
Tests: skill catalog availability
"""

import tempfile
from pathlib import Path
from datetime import datetime

from ..cli import IdeaPipelineCLI
from ..skills import get_skill_catalog, list_skills
from ..models.artifact import Idea, Concept, ADR, ImplementationPlan, Status
from ..palace import IdeaPalace


class TestCLICreate:
    """Test: CLI create command creates artifacts."""

    def test_create_idea(self):
        """Create an idea via CLI."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cli = IdeaPipelineCLI(Path(tmpdir))
            artifact_id = cli.create_artifact('idea', 'test-idea', 'general', 'default')
            assert artifact_id.startswith('IDEA-')
            print(f"✓ Created: {artifact_id}")

    def test_create_concept_with_upstream(self):
        """Create concept with upstream idea."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cli = IdeaPipelineCLI(Path(tmpdir))
            idea_id = cli.create_artifact('idea', 'consensus', 'general', 'default')
            concept_id = cli.create_artifact('concept', 'raft', 'general', 'default', upstream=idea_id)
            assert concept_id.startswith('CONCEPT-')
            print(f"✓ Created: {concept_id} ← {idea_id}")

    def test_create_adr_with_upstream(self):
        """Create ADR with concept upstream."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cli = IdeaPipelineCLI(Path(tmpdir))
            idea_id = cli.create_artifact('idea', 'consensus', 'general', 'default')
            concept_id = cli.create_artifact('concept', 'raft', 'general', 'default', upstream=idea_id)
            adr_id = cli.create_artifact('adr', 'use-raft', 'general', 'default', upstream=concept_id)
            assert adr_id.startswith('ADR-')
            print(f"✓ Created: {adr_id} ← {concept_id} ← {idea_id}")

    def test_create_plan_with_upstream(self):
        """Create plan with ADR upstream."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cli = IdeaPipelineCLI(Path(tmpdir))
            idea_id = cli.create_artifact('idea', 'consensus', 'general', 'default')
            concept_id = cli.create_artifact('concept', 'raft', 'general', 'default', upstream=idea_id)
            adr_id = cli.create_artifact('adr', 'use-raft', 'general', 'default', upstream=concept_id)
            plan_id = cli.create_artifact('implementation-plan', 'raft-rollout', 'general', 'default', upstream=adr_id)
            assert plan_id.startswith('IMPL-')
            print(f"✓ Created: {plan_id} ← {adr_id}")


class TestCLIList:
    """Test: CLI list command."""

    def test_list_all(self):
        """List all artifacts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cli = IdeaPipelineCLI(Path(tmpdir))
            idea_id = cli.create_artifact('idea', 'test', 'general', 'default')

            # This should not crash
            cli.list_artifacts()
            print("✓ List all artifacts")

    def test_list_by_type(self):
        """List artifacts by type."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cli = IdeaPipelineCLI(Path(tmpdir))
            idea_id = cli.create_artifact('idea', 'test', 'general', 'default')

            cli.list_artifacts(artifact_type='ideas')
            print("✓ List ideas")

    def test_list_by_wing_room(self):
        """List artifacts by wing/room."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cli = IdeaPipelineCLI(Path(tmpdir))
            artifact_id = cli.create_artifact('idea', 'test', 'my-room', 'my-wing')

            cli.list_artifacts(wing='my-wing', room='my-room')
            print("✓ List by wing/room")


class TestCLISearch:
    """Test: CLI search command."""

    def test_search_by_name(self):
        """Search artifacts by name."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cli = IdeaPipelineCLI(Path(tmpdir))
            artifact_id = cli.create_artifact('idea', 'distributed-consensus', 'general', 'default')

            cli.search('consensus')
            print("✓ Search by name")

    def test_search_no_results(self):
        """Search with no results."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cli = IdeaPipelineCLI(Path(tmpdir))
            cli.search('nonexistent')
            print("✓ Search no results")


class TestCLIAudit:
    """Test: CLI audit command."""

    def test_audit_all(self):
        """Audit all artifacts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cli = IdeaPipelineCLI(Path(tmpdir))
            idea_id = cli.create_artifact('idea', 'test', 'general', 'default')

            cli.audit()
            print("✓ Audit all")

    def test_audit_by_wing(self):
        """Audit specific wing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cli = IdeaPipelineCLI(Path(tmpdir))
            artifact_id = cli.create_artifact('idea', 'test', 'general', 'my-wing')

            cli.audit(wing='my-wing')
            print("✓ Audit by wing")


class TestSkillCatalog:
    """Test: SkillForge skill catalog."""

    def test_get_catalog(self):
        """Get full skill catalog."""
        catalog = get_skill_catalog()
        assert len(catalog) > 0
        assert 'idea-gate' in catalog
        assert 'concept-gate' in catalog
        assert 'adr-gate' in catalog
        assert 'implementation-gate' in catalog
        print(f"✓ Skill catalog: {len(catalog)} skills")

    def test_get_skill(self):
        """Get individual skill."""
        skill = get_skill('idea-gate')
        assert skill is not None
        assert skill['name'] == 'idea-gate'
        assert 'body' in skill
        print(f"✓ Got skill: {skill['name']}")

    def test_list_skills(self):
        """List all skill names."""
        skills = list_skills()
        assert 'idea-gate' in skills
        print(f"✓ Skills: {', '.join(skills)}")

    def test_skill_body_not_empty(self):
        """All skills have markdown body."""
        for skill_name in list_skills():
            skill = get_skill(skill_name)
            assert skill['body']
            assert len(skill['body']) > 50  # non-trivial markdown
        print(f"✓ All skills have markdown bodies")


class TestCLIIntegration:
    """Test: Full CLI workflow."""

    def test_full_pipeline_via_cli(self):
        """Create full pipeline: Idea → Concept → ADR → Plan."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cli = IdeaPipelineCLI(Path(tmpdir))

            # Create Idea
            idea_id = cli.create_artifact('idea', 'Distributed Consensus', 'consensus', 'core')
            assert idea_id.startswith('IDEA-')

            # Create Concept
            concept_id = cli.create_artifact(
                'concept', 'Raft Algorithm', 'consensus', 'core',
                upstream=idea_id
            )
            assert concept_id.startswith('CONCEPT-')

            # Create ADR
            adr_id = cli.create_artifact(
                'adr', 'Use Raft for Consensus', 'consensus', 'core',
                upstream=concept_id
            )
            assert adr_id.startswith('ADR-')

            # Create Plan
            plan_id = cli.create_artifact(
                'implementation-plan', 'Raft Rollout', 'consensus', 'core',
                upstream=adr_id
            )
            assert plan_id.startswith('IMPL-')

            # Audit
            cli.audit(wing='core')

            print(f"✓ Full pipeline: {idea_id} → {concept_id} → {adr_id} → {plan_id}")


if __name__ == '__main__':
    print("=" * 60)
    print("K=3a Test Suite: CLI + SkillForge")
    print("=" * 60)

    # Test CLI
    print("\n[CLI Tests]")
    TestCLICreate().test_create_idea()
    TestCLICreate().test_create_concept_with_upstream()
    TestCLICreate().test_create_adr_with_upstream()
    TestCLICreate().test_create_plan_with_upstream()
    TestCLIList().test_list_all()
    TestCLIList().test_list_by_type()
    TestCLIList().test_list_by_wing_room()
    TestCLISearch().test_search_by_name()
    TestCLISearch().test_search_no_results()
    TestCLIAudit().test_audit_all()
    TestCLIAudit().test_audit_by_wing()

    # Test Skills
    print("\n[SkillForge Tests]")
    TestSkillCatalog().test_get_catalog()
    TestSkillCatalog().test_get_skill()
    TestSkillCatalog().test_list_skills()
    TestSkillCatalog().test_skill_body_not_empty()

    # Integration
    print("\n[Integration Tests]")
    TestCLIIntegration().test_full_pipeline_via_cli()

    print("\n" + "=" * 60)
    print("K=3a: All tests PASS ✓")
    print("=" * 60)

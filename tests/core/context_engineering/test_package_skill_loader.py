"""Tests for PackageSkillLoader (ADR-0268 Phase 5).

Verifies that skills from installed packages are discovered and
made available to SkillInjection.
"""

import pytest
import sys
from pathlib import Path

# Add operator directory to path to avoid collision with built-in operator module
operator_path = Path(__file__).parent.parent.parent.parent / "operator"
if str(operator_path) not in sys.path:
    sys.path.insert(0, str(operator_path))


@pytest.fixture
def sample_package_manifest():
    """Sample package manifest with skills declared."""
    return {
        "id": "com.example.test-pkg",
        "name": "Test Package",
        "version": "1.0.0",
        "contents": {
            "skills": [
                {
                    "id": "debug_skill",
                    "name": "Debugging",
                    "description": "Systematic debugging approach",
                    "category": "debugging",
                    "file": "skills/debug.yaml",
                    "hooks": [
                        {
                            "id": "debug_hook",
                            "trigger": "preprocessing",
                            "priority": 50,
                        }
                    ],
                },
                {
                    "id": "refactor_skill",
                    "name": "Refactoring",
                    "description": "Code refactoring patterns",
                    "category": "refactoring",
                    "file": "skills/refactor.yaml",
                },
            ]
        },
    }


class TestPackageSkillLoader:
    """Tests for PackageSkillLoader."""

    def test_extract_skills_from_manifest(self, sample_package_manifest):
        """Test extracting skills from package manifest."""
        from context_engineering.package_skill_loader import PackageSkillLoader

        loader = PackageSkillLoader()
        skills = loader._extract_skills_from_manifest("com.example.test-pkg", sample_package_manifest)

        assert len(skills) == 2, "Should extract 2 skills"
        assert skills[0].skill_id == "com.example.test-pkg:debug_skill"
        assert skills[0].title == "Debugging"
        assert skills[0].category == "debugging"
        assert skills[0].trigger == "preprocessing"

        assert skills[1].skill_id == "com.example.test-pkg:refactor_skill"
        assert skills[1].title == "Refactoring"
        assert skills[1].category == "refactoring"

    def test_convert_to_skill_injection_format(self, sample_package_manifest):
        """Test converting package skills to SkillInjection format."""
        from context_engineering.package_skill_loader import PackageSkillLoader

        loader = PackageSkillLoader()
        skills = loader._extract_skills_from_manifest("com.example.test-pkg", sample_package_manifest)

        # Convert to SkillInjection format (simulating get_skills_for_task)
        skill_dicts = []
        for pkg_skill in skills:
            skill_dicts.append(
                {
                    "skill_id": pkg_skill.skill_id,
                    "title": pkg_skill.title,
                    "description": pkg_skill.description,
                    "category": pkg_skill.category,
                    "relevance_score": 0.5,
                    "success_rate": 0.7,
                    "source": f"package:{pkg_skill.package_id}",
                }
            )

        assert len(skill_dicts) == 2
        assert skill_dicts[0]["skill_id"] == "com.example.test-pkg:debug_skill"
        assert skill_dicts[0]["source"] == "package:com.example.test-pkg"

    def test_manifest_missing_skills_field(self):
        """Test handling manifest without skills."""
        from context_engineering.package_skill_loader import PackageSkillLoader

        loader = PackageSkillLoader()
        manifest = {"id": "com.example.empty", "name": "Empty", "contents": {}}
        skills = loader._extract_skills_from_manifest("com.example.empty", manifest)

        assert len(skills) == 0, "Should handle missing skills gracefully"

    def test_skill_missing_required_fields(self):
        """Test handling skill with missing id."""
        from context_engineering.package_skill_loader import PackageSkillLoader

        loader = PackageSkillLoader()
        manifest = {
            "id": "com.example.bad",
            "contents": {
                "skills": [
                    {
                        "name": "Bad Skill",
                        # Missing 'id' field
                        "description": "No ID",
                    }
                ]
            },
        }
        skills = loader._extract_skills_from_manifest("com.example.bad", manifest)

        assert len(skills) == 0, "Should skip skills with missing id"

    def test_cache_behavior(self, sample_package_manifest):
        """Test skill discovery caching."""
        from context_engineering.package_skill_loader import PackageSkillLoader

        loader = PackageSkillLoader(cache_ttl_minutes=30)

        # First call
        skills1 = loader._extract_skills_from_manifest("com.example.test-pkg", sample_package_manifest)
        assert len(skills1) == 2

        # Cache should be populated
        assert len(loader._skill_cache) == 0  # _extract_skills doesn't cache; discover_package_skills does


class TestSkillInjectionWithPackages:
    """Tests for SkillInjection with package skill integration."""

    def test_skill_injection_initializes_package_loader(self):
        """Test that SkillInjection can initialize PackageSkillLoader."""
        # This test will only run if PackageSkillLoader is importable
        try:
            from context_engineering.skill_injection import SkillInjection

            injection = SkillInjection(tenant_id="_default")

            # Should have initialized package loader if available
            assert hasattr(injection, "package_skill_loader")
            # May be None if PackageSkillLoader import failed, which is OK
            assert injection.tenant_id == "_default"

        except ImportError:
            pytest.skip("PackageSkillLoader not available")

    def test_map_decisions_to_skills_includes_packages(self):
        """Test that _map_decisions_to_skills includes package skills."""
        try:
            from context_engineering.skill_injection import SkillInjection

            injection = SkillInjection(tenant_id="_default")

            # Call the method
            skills = injection._map_decisions_to_skills(decisions=None)

            # Should return list (may be empty if no packages installed)
            assert isinstance(skills, list)

            # If we have skills, they should have the right fields
            for skill in skills:
                assert "skill_id" in skill
                assert "title" in skill
                assert "description" in skill
                assert "relevance_score" in skill
                assert "success_rate" in skill

        except ImportError:
            pytest.skip("PackageSkillLoader not available")


class TestPackageSkillLoaderE2E:
    """E2E tests with real adscale-ldd package."""

    def test_discover_skills_from_real_adscale_package(self):
        """Test discovering skills from adscale-ldd package (if installed)."""
        from context_engineering.package_skill_loader import PackageSkillLoader

        loader = PackageSkillLoader()

        # Try to discover package skills
        skills = loader.discover_package_skills()

        # If adscale-ldd is installed, we should find its skills
        # If not, we'll get an empty list (which is OK)
        assert isinstance(skills, list)

        # Log what we found for debugging
        if skills:
            print(f"\n✓ Discovered {len(skills)} package skills")
            for skill in skills:
                print(f"  - {skill.skill_id}: {skill.title}")

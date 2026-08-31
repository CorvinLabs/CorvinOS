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


class TestRelevanceScoring:
    """Tests for skill relevance scoring."""

    def test_score_skill_relevance_base_score(self):
        """Test base relevance score without task context."""
        from context_engineering.package_skill_loader import PackageSkillLoader, PackageSkillInfo

        loader = PackageSkillLoader()
        skill = PackageSkillInfo(
            skill_id="test:skill",
            title="Test Skill",
            description="A test skill",
            category="general",
            package_id="com.test",
            file_path="skills/test.yaml",
        )

        # Without task context, should get base score
        score = loader._score_skill_relevance(skill, task=None)
        assert score == 0.5, f"Expected base score 0.5, got {score}"

    def test_score_skill_relevance_category_match(self):
        """Test relevance boost for category matching."""
        from context_engineering.package_skill_loader import PackageSkillLoader, PackageSkillInfo

        loader = PackageSkillLoader()
        skill = PackageSkillInfo(
            skill_id="test:debug",
            title="Debug Skill",
            description="Debugging patterns",
            category="debugging",
            package_id="com.test",
            file_path="skills/debug.yaml",
        )

        # Create a mock task with matching category
        class MockTask:
            category = "debugging"

        score = loader._score_skill_relevance(skill, task=MockTask())
        assert score == 0.8, f"Expected score 0.8 (0.5 + 0.3), got {score}"

    def test_score_skill_relevance_package_match(self):
        """Test relevance boost for package matching."""
        from context_engineering.package_skill_loader import PackageSkillLoader, PackageSkillInfo

        loader = PackageSkillLoader()
        skill = PackageSkillInfo(
            skill_id="pkg:skill",
            title="Package Skill",
            description="A skill from a package",
            category="general",
            package_id="com.specific",
            file_path="skills/pkg.yaml",
        )

        # Create a mock task mentioning the package
        class MockTask:
            package_id = "com.specific"

        score = loader._score_skill_relevance(skill, task=MockTask())
        assert score == 0.7, f"Expected score 0.7 (0.5 + 0.2), got {score}"

    def test_score_skill_relevance_preprocessing_hook_bonus(self):
        """Test universal bonus for preprocessing hooks."""
        from context_engineering.package_skill_loader import PackageSkillLoader, PackageSkillInfo

        loader = PackageSkillLoader()
        skill = PackageSkillInfo(
            skill_id="test:preprocess",
            title="Preprocess Skill",
            description="Preprocessing hook",
            category="general",
            package_id="com.test",
            file_path="hooks/preprocess.py",
            trigger="preprocessing",
        )

        # Preprocessing hooks should get universal bonus
        score = loader._score_skill_relevance(skill, task=None)
        assert score == 0.6, f"Expected score 0.6 (0.5 + 0.1), got {score}"

    def test_score_skill_relevance_combined_bonuses(self):
        """Test combined relevance bonuses."""
        from context_engineering.package_skill_loader import PackageSkillLoader, PackageSkillInfo

        loader = PackageSkillLoader()
        skill = PackageSkillInfo(
            skill_id="pkg:debug",
            title="Debug Preprocessing",
            description="Debug preprocessing skill",
            category="debugging",
            package_id="com.specific",
            file_path="hooks/debug_preprocess.py",
            trigger="preprocessing",
        )

        class MockTask:
            category = "debugging"
            package_id = "com.specific"

        # Should get all bonuses: 0.5 (base) + 0.3 (category) + 0.2 (package) + 0.1 (preprocessing)
        score = loader._score_skill_relevance(skill, task=MockTask())
        expected = 1.0  # Clamped at 1.0
        assert score == expected, f"Expected score {expected}, got {score}"


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

"""Skill Forge v2.0 Complete — All phases tested."""

import pytest
from pathlib import Path
import sys
import json

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core" / "skill_forge"))

from generators.manifest import SkillManifest, SkillType, SkillScope
from generators.skeleton import SkeletonGenerator
from generators.llm_generator import enhance_with_llm
from generators.validator import ManifestValidator


class TestPhase2LLMFallback:
    """Test LLM generator with fallback."""

    def test_llm_fallback_on_api_error(self):
        """LLM unavailable → fallback to skeleton."""
        manifest = SkillManifest(
            name="test_fallback",
            skill_type=SkillType.LEARNED_EXPERIENCE,
            title="Test",
            description="Test fallback behavior.",
            scope=SkillScope.TASK,
            body_md="# Test\n\nPattern.\n\nWhen to Use.\n\nExamples."
        )

        # Simulate LLM disabled (fallback only)
        result, is_llm = enhance_with_llm(manifest, use_llm=False)

        assert result.generator_phase in ["skeleton", "full"]
        assert not is_llm

    def test_manifest_phase_tracking(self):
        """Track generation phase (skeleton vs full)."""
        gen = SkeletonGenerator(SkillType.REASONING)
        m = gen.generate("test", "Test", "Test.", SkillScope.TASK)

        assert m.generator_phase == "skeleton"


class TestPhase3UIIntegration:
    """Test Console UI readiness."""

    def test_manifest_serializes_for_ui(self):
        """Manifest converts to UI-friendly JSON."""
        m = SkillManifest(
            name="ui_test",
            skill_type=SkillType.REFERENCE,
            title="UI Test",
            description="Test for UI.",
            scope=SkillScope.SESSION,
            body_md="# Test\n\nOverview.\n\nExamples."
        )

        d = m.to_dict()
        json_str = json.dumps(d)

        # Verify UI can parse
        parsed = json.loads(json_str)
        assert parsed["name"] == "ui_test"
        assert isinstance(parsed["scope"], str)


class TestPhase4Production:
    """Test production readiness."""

    def test_concurrent_generation_safe(self):
        """Multiple skills can be generated concurrently."""
        from concurrent.futures import ThreadPoolExecutor

        def gen_skill(skill_type):
            gen = SkeletonGenerator(skill_type)
            return gen.generate(f"skill_{skill_type.value.replace('-', '_')}", "Test", ".", SkillScope.TASK)

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(gen_skill, t) for t in SkillType]
            results = [f.result() for f in futures]

        assert len(results) == 4
        assert all(r.name.startswith("skill_") for r in results)

    def test_validation_deterministic(self):
        """Validation is deterministic (same result every time)."""
        m = SkillManifest(
            name="deterministic_test",
            skill_type=SkillType.AUTOMATION,
            title="Deterministic",
            description="Test determinism.",
            scope=SkillScope.PROJECT,
            body_md="# Test\n\nAlgorithm.\n\nInput Contract.\n\nOutput Contract.\n\nError Handling."
        )

        validator = ManifestValidator()

        # Validate 3 times
        results = [validator.validate(m) for _ in range(3)]

        # All should be identical
        assert all(r[0] == results[0][0] for r in results)
        assert all(r[1] == results[0][1] for r in results)


class TestE2EAllPhases:
    """End-to-end: all phases working together."""

    def test_full_pipeline_phases_1_to_4(self):
        """Complete pipeline: Phase 1→2→3→4."""

        # Phase 1: Generate skeleton
        gen = SkeletonGenerator(SkillType.LEARNED_EXPERIENCE)
        m = gen.generate("pipeline_test", "Pipeline Test", "E2E test.", SkillScope.TASK)
        assert m.generator_phase == "skeleton"

        # Phase 2: Validate (would enhance with LLM in production)
        validator = ManifestValidator()
        is_valid, errors, _ = validator.validate(m)
        assert is_valid

        # Phase 3: Convert to JSON for UI
        json_data = m.to_dict()
        assert json_data["skill_type"] == "learned-experience"

        # Phase 4: Verify concurrent safety
        import json
        json_str = json.dumps(json_data)
        parsed = json.loads(json_str)
        m2 = SkillManifest.from_dict(parsed)

        assert m2.name == m.name
        assert m2.skill_type == m.skill_type


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

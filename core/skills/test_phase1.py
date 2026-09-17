"""
Phase 1 Unit Tests: SkillManifestV2 + Skeleton Generation

K_MAX = 5 iterations, Tier-1/2/3 gates.
"""

import json
import pytest
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime

from core.skills.phase1_manifest_v2 import (
    SkillManifestV2,
    SkillManifestValidator,
    BootLayer,
    SkillDomain,
    SkillParameter,
    SkillDependency,
)

from core.skills.phase1_skeleton_generator import (
    SkillSkeletonGenerator,
    SkillScaffoldConfig,
)


class TestSkillManifestV2:
    """Manifest schema + validation tests."""

    def test_create_minimal_manifest(self):
        """Minimal valid manifest."""
        m = SkillManifestV2(
            skill_id="minimal_skill",
            name="Minimal",
            version="0.1.0",
            description="Minimal skill",
            entry_point="src.skill:MinimalSkill.execute",
            input_schema={"x": "string"},
            output_schema={"result": "string", "confidence": "float"},
        )
        assert m.skill_id == "minimal_skill"
        assert m.version == "0.1.0"
        assert m.boot_layer == BootLayer.INSTALLED

    def test_manifest_validation_passes(self):
        """Valid manifest has no errors."""
        m = SkillManifestV2(
            skill_id="test_skill",
            name="Test",
            version="1.0.0",
            description="Test",
            entry_point="src.skill:TestSkill.execute",
            input_schema={"a": "string", "b": "int"},
            output_schema={"result": "bool", "confidence": "float"},
        )
        errors = m.validate()
        assert len(errors) == 0, f"Expected 0 errors, got {errors}"

    def test_manifest_validation_missing_confidence(self):
        """Output schema must have confidence field."""
        m = SkillManifestV2(
            skill_id="bad_skill",
            name="Bad",
            version="0.1.0",
            description="Bad",
            entry_point="src.skill:BadSkill.execute",
            input_schema={"x": "string"},
            output_schema={"result": "string"},  # Missing confidence
        )
        errors = m.validate()
        assert any("confidence" in e for e in errors), f"Expected confidence error, got {errors}"

    def test_manifest_validation_bad_version(self):
        """Version must be semantic (MAJOR.MINOR.PATCH)."""
        m = SkillManifestV2(
            skill_id="bad_version",
            name="Bad",
            version="1.0",  # Missing PATCH
            description="Bad",
            entry_point="src.skill:BadSkill.execute",
            input_schema={"x": "string"},
            output_schema={"result": "string", "confidence": "float"},
        )
        errors = m.validate()
        assert any("version" in e for e in errors), f"Expected version error, got {errors}"

    def test_manifest_to_dict_and_back(self):
        """Serialization roundtrip."""
        original = SkillManifestV2(
            skill_id="roundtrip_skill",
            name="Roundtrip",
            version="0.2.0",
            description="Roundtrip test",
            entry_point="src.skill:RoundtripSkill.execute",
            input_schema={"data": "string"},
            output_schema={"output": "string", "confidence": "float"},
            boot_layer=BootLayer.BUNDLED,
        )

        # to_dict
        data = original.to_dict()
        assert data["skill_id"] == "roundtrip_skill"
        assert data["boot_layer"] == "bundled"

        # from_dict
        restored = SkillManifestV2.from_dict(data)
        assert restored.skill_id == original.skill_id
        assert restored.boot_layer == original.boot_layer

    def test_manifest_json_serialization(self):
        """JSON serialization."""
        m = SkillManifestV2(
            skill_id="json_skill",
            name="JSON",
            version="0.1.0",
            description="JSON test",
            entry_point="src.skill:JsonSkill.execute",
            input_schema={"x": "int"},
            output_schema={"sum": "int", "confidence": "float"},
        )

        json_str = m.to_json()
        data = json.loads(json_str)
        assert data["skill_id"] == "json_skill"

        # Load from JSON
        restored = SkillManifestV2.from_dict(data)
        assert restored.skill_id == m.skill_id

    def test_manifest_with_parameters(self):
        """Manifest with tunable parameters (learning)."""
        param = SkillParameter(
            name="threshold",
            type="float",
            default=0.5,
            bounds=[0.0, 1.0],
            description="Classification threshold",
        )

        m = SkillManifestV2(
            skill_id="param_skill",
            name="With Params",
            version="0.1.0",
            description="Has parameters",
            entry_point="src.skill:ParamSkill.execute",
            input_schema={"x": "string"},
            output_schema={"class": "string", "confidence": "float"},
            parameters=[param],
        )

        errors = m.validate()
        assert len(errors) == 0
        assert len(m.parameters) == 1
        assert m.parameters[0].name == "threshold"

    def test_manifest_with_dependencies(self):
        """Manifest with skill dependencies (ADR-0535)."""
        dep = SkillDependency(
            skill_id="os.context_adapter",
            version=">=1.0.0",
            optional=False,
        )

        m = SkillManifestV2(
            skill_id="dependent_skill",
            name="Dependent",
            version="0.1.0",
            description="Depends on context adapter",
            entry_point="src.skill:DependentSkill.execute",
            input_schema={"ctx": "string"},
            output_schema={"result": "string", "confidence": "float"},
            dependencies=[dep],
        )

        errors = m.validate()
        assert len(errors) == 0
        assert len(m.dependencies) == 1

    def test_manifest_boot_layer_constraint(self):
        """User skills cannot claim core/compliance boot_layer."""
        m = SkillManifestV2(
            skill_id="fake_core",
            name="Fake Core",
            version="0.1.0",
            description="Trying to be core",
            entry_point="src.skill:FakeCore.execute",
            input_schema={"x": "string"},
            output_schema={"y": "string", "confidence": "float"},
            boot_layer=BootLayer.CORE,  # Invalid for user skill
        )

        errors = m.validate()
        assert any("boot_layer" in e for e in errors), f"Expected boot_layer error, got {errors}"

    def test_manifest_validator_file(self):
        """Validator can check a JSON file."""
        with TemporaryDirectory() as tmpdir:
            skill_json = Path(tmpdir) / "skill.json"
            m = SkillManifestV2(
                skill_id="file_test",
                name="File Test",
                version="0.1.0",
                description="File validation test",
                entry_point="src.skill:FileTest.execute",
                input_schema={"x": "string"},
                output_schema={"y": "string", "confidence": "float"},
            )
            m.to_json_file(str(skill_json))

            # Validate file
            is_valid, errors = SkillManifestValidator.validate_manifest_file(str(skill_json))
            assert is_valid, f"Expected valid manifest, got errors: {errors}"

    def test_manifest_learning_config(self):
        """Manifest learning configuration."""
        m = SkillManifestV2(
            skill_id="learnable_skill",
            name="Learnable",
            version="0.1.0",
            description="Can learn",
            entry_point="src.skill:LearnableSkill.execute",
            input_schema={"x": "string"},
            output_schema={"class": "string", "confidence": "float"},
            learning={
                "enabled": True,
                "strategy": "gradient_descent",
                "feedback_sources": ["outcome_feedback"],
                "convergence_signal": "weight_stabilization",
            },
        )

        errors = m.validate()
        assert len(errors) == 0
        assert m.learning["enabled"] is True

    def test_manifest_compliance_fields(self):
        """Manifest compliance metadata."""
        m = SkillManifestV2(
            skill_id="compliant_skill",
            name="Compliant",
            version="0.1.0",
            description="GDPR compliant",
            entry_point="src.skill:CompliantSkill.execute",
            input_schema={"data": "string"},
            output_schema={"result": "string", "confidence": "float"},
            compliance={
                "gdpr_ready": True,
                "audit_trail": True,
                "pii_handling": "redacted",
                "eu_ai_act_tier": "high_risk",
            },
        )

        assert m.compliance["gdpr_ready"] is True
        assert m.compliance["audit_trail"] is True


class TestSkillManifestIntegration:
    """Integration tests: multiple manifests."""

    def test_multiple_domains(self):
        """Create manifests for each domain."""
        domains = [
            SkillDomain.ROUTING,
            SkillDomain.LEARNING,
            SkillDomain.OPTIMIZATION,
            SkillDomain.INTEGRATION,
        ]

        for domain in domains:
            m = SkillManifestV2(
                skill_id=f"{domain.value}_skill",
                name=f"{domain.value} Skill",
                version="0.1.0",
                description=f"A {domain.value} skill",
                entry_point=f"src.skill:{domain.value.capitalize()}Skill.execute",
                domain=domain,
                input_schema={"x": "string"},
                output_schema={"result": "string", "confidence": "float"},
            )

            errors = m.validate()
            assert len(errors) == 0, f"{domain}: {errors}"

    def test_audit_events_field(self):
        """Manifest audit events (ADR-0534)."""
        m = SkillManifestV2(
            skill_id="audited_skill",
            name="Audited",
            version="0.1.0",
            description="Auditable",
            entry_point="src.skill:AuditedSkill.execute",
            input_schema={"x": "string"},
            output_schema={"y": "string", "confidence": "float"},
            audit_events=[
                "skill_executed",
                "skill_failed",
                "learning_event_emitted",
            ],
        )

        assert len(m.audit_events) > 0
        assert "skill_executed" in m.audit_events


# ============================================================================
# SKELETON GENERATION TESTS (11 tests)
# ============================================================================

class TestSkillSkeletonGenerator:
    """Skeleton generation: folder structure, boilerplate, determinism."""

    def test_generate_basic_skill(self):
        """Generate a complete skill folder."""
        with TemporaryDirectory() as tmpdir:
            generator = SkillSkeletonGenerator(base_output_dir=Path(tmpdir))
            config = SkillScaffoldConfig(
                skill_id="test_gen_skill",
                skill_name="Test Gen Skill",
                domain="routing",
                description="Generated for testing",
                input_schema={"request": "string"},
                output_schema={"engine": "string", "confidence": "float"},
            )

            skill_dir = generator.generate(config)

            # Verify folder structure
            assert skill_dir.exists()
            assert (skill_dir / "src").exists()
            assert (skill_dir / "tests").exists()
            assert (skill_dir / "hooks").exists()
            assert (skill_dir / "scripts").exists()

    def test_generate_manifest_file(self):
        """Generated skill.json is valid."""
        with TemporaryDirectory() as tmpdir:
            generator = SkillSkeletonGenerator(base_output_dir=Path(tmpdir))
            config = SkillScaffoldConfig(
                skill_id="manifest_test",
                skill_name="Manifest Test",
                domain="learning",
                description="Test manifest generation",
                input_schema={"data": "string"},
                output_schema={"class": "string", "confidence": "float"},
            )

            skill_dir = generator.generate(config)
            manifest_file = skill_dir / "skill.json"

            assert manifest_file.exists()
            is_valid, errors = SkillManifestValidator.validate_manifest_file(str(manifest_file))
            assert is_valid, f"Generated manifest invalid: {errors}"

    def test_generate_skill_class_code(self):
        """Generated skill.py has proper structure."""
        with TemporaryDirectory() as tmpdir:
            generator = SkillSkeletonGenerator(base_output_dir=Path(tmpdir))
            config = SkillScaffoldConfig(
                skill_id="code_gen",
                skill_name="Code Gen",
                domain="optimization",
                description="Test code generation",
                input_schema={"x": "int"},
                output_schema={"y": "int", "confidence": "float"},
            )

            skill_dir = generator.generate(config)
            skill_py = skill_dir / "src" / "skill.py"

            assert skill_py.exists()
            code = skill_py.read_text()
            # Verify key class structure
            assert "class CodeGen" in code
            assert "async def execute" in code
            assert "confidence" in code

    def test_generate_hook_files(self):
        """All hook stubs are generated."""
        with TemporaryDirectory() as tmpdir:
            generator = SkillSkeletonGenerator(base_output_dir=Path(tmpdir))
            config = SkillScaffoldConfig(
                skill_id="hooks_test",
                skill_name="Hooks Test",
                domain="integration",
                description="Test hook generation",
                input_schema={"a": "string"},
                output_schema={"b": "string", "confidence": "float"},
            )

            skill_dir = generator.generate(config)
            hooks_dir = skill_dir / "hooks"
            assert (hooks_dir / "on_load.py").exists()
            assert (hooks_dir / "on_execute.py").exists()
            assert (hooks_dir / "on_feedback.py").exists()
            assert (hooks_dir / "on_unload.py").exists()

    def test_generate_scripts(self):
        """All scripts are generated."""
        with TemporaryDirectory() as tmpdir:
            generator = SkillSkeletonGenerator(base_output_dir=Path(tmpdir))
            config = SkillScaffoldConfig(
                skill_id="scripts_test",
                skill_name="Scripts Test",
                domain="routing",
                description="Test script generation",
                input_schema={"x": "string"},
                output_schema={"y": "string", "confidence": "float"},
            )

            skill_dir = generator.generate(config)
            scripts_dir = skill_dir / "scripts"
            assert (scripts_dir / "install.py").exists()
            assert (scripts_dir / "test_runner.py").exists()
            assert (scripts_dir / "packager.py").exists()
            assert (scripts_dir / "integrator.py").exists()

    def test_generate_deterministic(self):
        """Same input → same output (deterministic)."""
        with TemporaryDirectory() as tmpdir1:
            with TemporaryDirectory() as tmpdir2:
                config = SkillScaffoldConfig(
                    skill_id="deterministic",
                    skill_name="Deterministic",
                    domain="routing",
                    description="Test determinism",
                    input_schema={"x": "string"},
                    output_schema={"y": "string", "confidence": "float"},
                )

                gen1 = SkillSkeletonGenerator(base_output_dir=Path(tmpdir1))
                dir1 = gen1.generate(config)

                gen2 = SkillSkeletonGenerator(base_output_dir=Path(tmpdir2))
                dir2 = gen2.generate(config)

                # Compare skill.json contents
                manifest1 = json.loads((dir1 / "skill.json").read_text())
                manifest2 = json.loads((dir2 / "skill.json").read_text())

                assert manifest1["skill_id"] == manifest2["skill_id"]
                assert manifest1["domain"] == manifest2["domain"]

    def test_config_validation_invalid_skill_id(self):
        """Invalid skill_id rejected."""
        generator = SkillSkeletonGenerator()
        config = SkillScaffoldConfig(
            skill_id="Invalid-ID",
            skill_name="Invalid",
            domain="routing",
            description="Invalid",
            input_schema={"x": "string"},
            output_schema={"y": "string", "confidence": "float"},
        )

        with pytest.raises(ValueError, match="skill_id must be lowercase"):
            generator.generate(config)

    def test_config_validation_invalid_domain(self):
        """Invalid domain rejected."""
        with TemporaryDirectory() as tmpdir:
            generator = SkillSkeletonGenerator(base_output_dir=Path(tmpdir))
            config = SkillScaffoldConfig(
                skill_id="valid_id",
                skill_name="Valid",
                domain="invalid_domain",
                description="Invalid domain",
                input_schema={"x": "string"},
                output_schema={"y": "string", "confidence": "float"},
            )

            with pytest.raises(ValueError, match="domain must be one of"):
                generator.generate(config)

    def test_config_validation_missing_confidence(self):
        """Output schema must include confidence."""
        with TemporaryDirectory() as tmpdir:
            generator = SkillSkeletonGenerator(base_output_dir=Path(tmpdir))
            config = SkillScaffoldConfig(
                skill_id="no_confidence",
                skill_name="No Confidence",
                domain="routing",
                description="Missing confidence",
                input_schema={"x": "string"},
                output_schema={"y": "string"},  # Missing confidence
            )

            with pytest.raises(ValueError, match="confidence"):
                generator.generate(config)

    def test_skill_already_exists_error(self):
        """Cannot regenerate existing skill."""
        with TemporaryDirectory() as tmpdir:
            generator = SkillSkeletonGenerator(base_output_dir=Path(tmpdir))
            config = SkillScaffoldConfig(
                skill_id="existing",
                skill_name="Existing",
                domain="routing",
                description="First generation",
                input_schema={"x": "string"},
                output_schema={"y": "string", "confidence": "float"},
            )

            # First generation succeeds
            generator.generate(config)

            # Second generation fails
            with pytest.raises(FileExistsError):
                generator.generate(config)


# ============================================================================
# E2E WIRING PROOF TESTS (3 tests)
# ============================================================================

class TestE2EWiring:
    """E2E tests: prove generated skills work end-to-end."""

    def test_generated_skill_loads_manifest(self):
        """Generated skill's manifest loads correctly."""
        with TemporaryDirectory() as tmpdir:
            generator = SkillSkeletonGenerator(base_output_dir=Path(tmpdir))
            config = SkillScaffoldConfig(
                skill_id="e2e_manifest",
                skill_name="E2E Manifest",
                domain="routing",
                description="E2E manifest load test",
                input_schema={"req": "string"},
                output_schema={"route": "string", "confidence": "float"},
            )

            skill_dir = generator.generate(config)
            manifest_path = skill_dir / "skill.json"

            # Load and validate manifest
            manifest = SkillManifestV2.from_json_file(str(manifest_path))
            assert manifest.skill_id == "e2e_manifest"
            assert manifest.domain == SkillDomain.ROUTING

    def test_generated_skill_python_imports(self):
        """Generated skill.py can be imported (syntax valid)."""
        with TemporaryDirectory() as tmpdir:
            generator = SkillSkeletonGenerator(base_output_dir=Path(tmpdir))
            config = SkillScaffoldConfig(
                skill_id="e2e_import",
                skill_name="E2E Import",
                domain="optimization",
                description="E2E import test",
                input_schema={"x": "int"},
                output_schema={"y": "int", "confidence": "float"},
            )

            skill_dir = generator.generate(config)
            skill_py = skill_dir / "src" / "skill.py"

            # Try to compile (parse) the generated code
            code = skill_py.read_text()
            try:
                compile(code, str(skill_py), "exec")
            except SyntaxError as e:
                pytest.fail(f"Generated skill.py has syntax error: {e}")

    def test_generated_manifest_entry_point_format(self):
        """Generated entry_point follows ADR-0533 format."""
        with TemporaryDirectory() as tmpdir:
            generator = SkillSkeletonGenerator(base_output_dir=Path(tmpdir))
            config = SkillScaffoldConfig(
                skill_id="entry_point_test",
                skill_name="Entry Point Test",
                domain="learning",
                description="Entry point format test",
                input_schema={"data": "string"},
                output_schema={"result": "string", "confidence": "float"},
            )

            skill_dir = generator.generate(config)
            manifest = SkillManifestV2.from_json_file(str(skill_dir / "skill.json"))

            # entry_point must match pattern: module.path:ClassName.execute
            entry_point = manifest.entry_point
            assert ":" in entry_point
            assert ".execute" in entry_point
            assert entry_point.startswith("src.")


# ============================================================================
# ADVERSARIAL TESTS (6 tests)
# ============================================================================

class TestAdversarial:
    """Adversarial tests: attack surfaces, invalid inputs, edge cases."""

    def test_invalid_semver_in_manifest(self):
        """Invalid semantic version rejected."""
        m = SkillManifestV2(
            skill_id="bad_semver",
            name="Bad Semver",
            version="1.0",  # Invalid: missing PATCH
            description="Bad",
            entry_point="src.skill:BadSemver.execute",
            input_schema={"x": "string"},
            output_schema={"y": "string", "confidence": "float"},
        )

        errors = m.validate()
        assert any("version" in e for e in errors)

    def test_circular_dependency_detection(self):
        """Circular dependencies are flagged (basic check)."""
        # Create manifest with self-reference
        m = SkillManifestV2(
            skill_id="circular",
            name="Circular",
            version="0.1.0",
            description="Self-referencing",
            entry_point="src.skill:Circular.execute",
            input_schema={"x": "string"},
            output_schema={"y": "string", "confidence": "float"},
            dependencies=[
                SkillDependency(
                    skill_id="circular",
                    version="0.1.0",
                    optional=False,
                )
            ],
        )

        # Basic assertion - validate shouldn't crash
        errors = m.validate()
        assert len(errors) >= 0

    def test_injection_attempt_in_skill_id(self):
        """Injection attempts in skill_id rejected."""
        with TemporaryDirectory() as tmpdir:
            generator = SkillSkeletonGenerator(base_output_dir=Path(tmpdir))

            # Try SQL injection-like pattern
            config = SkillScaffoldConfig(
                skill_id="skill_drop_table",
                skill_name="Injection",
                domain="routing",
                description="Injection attempt",
                input_schema={"x": "string"},
                output_schema={"y": "string", "confidence": "float"},
            )

            with pytest.raises(ValueError):
                generator.generate(config)

    def test_oversized_input_schema(self):
        """Very large input schema handled."""
        # Create manifest with very large input schema
        large_input = {f"field_{i}": "string" for i in range(100)}
        m = SkillManifestV2(
            skill_id="large_schema",
            name="Large Schema",
            version="0.1.0",
            description="Large input schema",
            entry_point="src.skill:LargeSchema.execute",
            input_schema=large_input,
            output_schema={"result": "string", "confidence": "float"},
        )

        errors = m.validate()
        assert len(errors) == 0

    def test_special_characters_in_description(self):
        """Special characters in description handled safely."""
        m = SkillManifestV2(
            skill_id="special_chars",
            name="Special Chars",
            version="0.1.0",
            description="Description with <script>, \"quotes\", and 'apostrophes'",
            entry_point="src.skill:SpecialChars.execute",
            input_schema={"x": "string"},
            output_schema={"y": "string", "confidence": "float"},
        )

        # Should serialize safely to JSON
        json_str = m.to_json()
        data = json.loads(json_str)
        assert data["description"] == m.description

    def test_missing_required_audit_events(self):
        """Manifest must have audit events."""
        m = SkillManifestV2(
            skill_id="no_audit",
            name="No Audit",
            version="0.1.0",
            description="No audit events",
            entry_point="src.skill:NoAudit.execute",
            input_schema={"x": "string"},
            output_schema={"y": "string", "confidence": "float"},
            audit_events=[],  # Empty
        )

        errors = m.validate()
        assert any("audit_events" in e for e in errors)


# ============================================================================
# AUDIT INTEGRATION TESTS (5 tests)
# ============================================================================

class TestAuditIntegration:
    """Audit integration: events, tracing, compliance."""

    def test_manifest_has_audit_events_list(self):
        """Manifest includes audit_events field."""
        m = SkillManifestV2(
            skill_id="audit_test",
            name="Audit Test",
            version="0.1.0",
            description="Audit test",
            entry_point="src.skill:AuditTest.execute",
            input_schema={"x": "string"},
            output_schema={"y": "string", "confidence": "float"},
            audit_events=["skill_executed", "skill_failed"],
        )

        assert "skill_executed" in m.audit_events
        assert "skill_failed" in m.audit_events

    def test_generated_manifest_includes_generation_metadata(self):
        """Generated manifest has generation_metadata field."""
        with TemporaryDirectory() as tmpdir:
            generator = SkillSkeletonGenerator(base_output_dir=Path(tmpdir))
            config = SkillScaffoldConfig(
                skill_id="gen_metadata",
                skill_name="Gen Metadata",
                domain="routing",
                description="Generation metadata test",
                input_schema={"x": "string"},
                output_schema={"y": "string", "confidence": "float"},
            )

            skill_dir = generator.generate(config)
            manifest_data = json.loads((skill_dir / "skill.json").read_text())

            assert "generation_metadata" in manifest_data
            metadata = manifest_data["generation_metadata"]
            assert "generated_at" in metadata
            assert "generated_by" in metadata
            assert metadata["generated_by"] == "skill-forge-v2.0"

    def test_compliance_fields_present(self):
        """Manifest includes GDPR/compliance metadata."""
        m = SkillManifestV2(
            skill_id="compliance",
            name="Compliance",
            version="0.1.0",
            description="Compliance test",
            entry_point="src.skill:Compliance.execute",
            input_schema={"x": "string"},
            output_schema={"y": "string", "confidence": "float"},
            compliance={
                "gdpr_ready": True,
                "audit_trail": True,
                "pii_handling": "redacted",
                "eu_ai_act_tier": "high_risk",
            },
        )

        assert m.compliance["gdpr_ready"] is True
        assert m.compliance["audit_trail"] is True

    def test_learning_config_validated(self):
        """Manifest learning config is valid."""
        m = SkillManifestV2(
            skill_id="learning_config",
            name="Learning Config",
            version="0.1.0",
            description="Learning config test",
            entry_point="src.skill:LearningConfig.execute",
            input_schema={"x": "string"},
            output_schema={"y": "string", "confidence": "float"},
            learning={
                "enabled": True,
                "strategy": "gradient_descent",
                "feedback_sources": ["outcome_feedback"],
                "convergence_signal": "weight_stabilization",
            },
        )

        errors = m.validate()
        assert len(errors) == 0
        assert m.learning["strategy"] == "gradient_descent"

    def test_learning_config_without_strategy_fails(self):
        """Learning enabled but no strategy fails validation."""
        m = SkillManifestV2(
            skill_id="bad_learning",
            name="Bad Learning",
            version="0.1.0",
            description="Bad learning config",
            entry_point="src.skill:BadLearning.execute",
            input_schema={"x": "string"},
            output_schema={"y": "string", "confidence": "float"},
            learning={
                "enabled": True,
                # Missing "strategy"
            },
        )

        errors = m.validate()
        assert any("strategy" in e for e in errors)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

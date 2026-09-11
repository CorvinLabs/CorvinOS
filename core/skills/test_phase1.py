"""
Phase 1 Unit Tests: SkillManifestV2 + Skeleton Generation

K_MAX = 5 iterations, Tier-1/2/3 gates.
"""

import json
import pytest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.skills.phase1_manifest_v2 import (
    SkillManifestV2,
    SkillManifestValidator,
    BootLayer,
    SkillDomain,
    SkillParameter,
    SkillDependency,
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

"""
E2E Tests for ADR-0533: OS-Skill Manifest Schema & Versioning Strategy

Tests cover:
1. Valid manifest passes validation
2. Invalid manifests rejected with clear errors
3. In-flight task uses locked version
4. Version constraints match correctly
5. Triggers register properly
6. Module encapsulation enforced
"""

import pytest
import tempfile
import json
from pathlib import Path
from datetime import datetime, timedelta
import yaml
import sys

# Add core/skills to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core" / "skills"))

from skill_validator import (
    SkillValidator,
    validate_skill_manifest,
    SkillValidationReport,
    ALLOWED_TRIGGER_TYPES,
)
from version_manager import (
    SkillVersionManager,
    SkillVersionConstraint,
    TenantVersionPin,
    VersionResolver,
    SkillRun,
)
from compiler import (
    SkillCompiler,
    compile_skill,
    CompilationReport,
)


class TestValidManifests:
    """Tests for valid manifests that pass validation."""
    
    @pytest.fixture
    def valid_manifest_dict(self):
        """Return a valid manifest dictionary."""
        return {
            "name": "os.delegation_router",
            "version": "1.2.3",
            "goal": "Route tasks to appropriate engines",
            "description": "Reads feedback and learns task patterns to route efficiently",
            "author": "Corvin OS Team",
            "license": "Apache-2.0",
            "created_at": "2026-09-01T10:00:00Z",
            "updated_at": "2026-09-01T10:00:00Z",
            "triggers": [
                {
                    "name": "before_delegation_decision",
                    "event_type": "decision_point",
                    "phase": "pre_routing",
                    "condition": "every_turn",
                    "async_allowed": False,
                    "timeout_ms": 5000,
                }
            ],
            "input_schema": {
                "type": "object",
                "required": ["task_shape", "tenant_id"],
                "properties": {
                    "task_shape": {"type": "string"},
                    "tenant_id": {"type": "string"},
                },
                "additionalProperties": False,
            },
            "output_schema": {
                "type": "object",
                "required": ["decision", "confidence"],
                "properties": {
                    "decision": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "additionalProperties": False,
            },
            "learning_signal": {
                "metrics": ["latency_actual_vs_predicted", "cost_per_token"],
                "scoring_rule": "mde < 5%",
                "feedback_sources": [
                    {
                        "event_type": "turn_completed",
                        "extract": ["latency", "cost"],
                    }
                ],
                "sanitization": {
                    "disallow_fields": ["prompt", "response", "user_id"],
                    "pii_patterns": ["email", "phone", "credit_card"],
                    "fail_closed": True,
                },
            },
            "boot_layer": "core",
            "origin": "builtin",
            "scope": "local_development",
        }
    
    def test_valid_manifest_passes_validation(self, valid_manifest_dict):
        """Test that a completely valid manifest passes validation."""
        validator = SkillValidator()
        report = validator.validate_manifest_dict(valid_manifest_dict)
        
        assert report.is_valid
        assert report.skill_id == "os.delegation_router"
        assert report.version == "1.2.3"
        assert len(report.blockers) == 0
    
    def test_valid_manifest_with_dependencies(self, valid_manifest_dict):
        """Test valid manifest with dependencies."""
        valid_manifest_dict["depends_on"] = [
            {
                "name": "os.context_adapter",
                "version": ">=1.0.0",
                "required": True,
            },
            {
                "name": "os.workflow_optimizer",
                "version": "~0.9.0",
                "required": False,
            },
        ]
        
        validator = SkillValidator()
        report = validator.validate_manifest_dict(valid_manifest_dict)
        
        assert report.is_valid
        assert len(report.blockers) == 0
    
    def test_valid_manifest_with_canary(self, valid_manifest_dict):
        """Test valid manifest with canary deployment config."""
        valid_manifest_dict["canary"] = {
            "enabled": True,
            "traffic_percent": 10,
            "duration_days": 7,
            "success_criteria": {
                "score_improvement_percent": 2,
                "error_rate_max_percent": 1,
            },
            "auto_rollback_on_failure": True,
        }
        
        validator = SkillValidator()
        report = validator.validate_manifest_dict(valid_manifest_dict)
        
        assert report.is_valid
    
    def test_valid_manifest_with_state_config(self, valid_manifest_dict):
        """Test valid manifest with state persistence config."""
        valid_manifest_dict["state"] = {
            "runs_retention_days": 90,
            "feedback_log_max_size_mb": 1000,
            "grading_stats_epochs": 10,
        }
        
        validator = SkillValidator()
        report = validator.validate_manifest_dict(valid_manifest_dict)
        
        assert report.is_valid


class TestInvalidManifests:
    """Tests for invalid manifests."""
    
    def test_missing_required_field(self):
        """Test that missing required fields are caught."""
        manifest = {"name": "test.skill"}  # Missing version, goal, etc.
        
        validator = SkillValidator()
        report = validator.validate_manifest_dict(manifest)
        
        assert not report.is_valid
        assert any("Missing required field" in b for b in report.blockers)
    
    def test_invalid_version_format(self):
        """Test that invalid version format is rejected."""
        manifest = {
            "name": "test.skill",
            "version": "1.2",  # Missing patch version
            "goal": "Test skill",
            "description": "Description",
            "triggers": [{"name": "test", "event_type": "decision_point", "phase": "pre_routing", "condition": "every_turn", "async_allowed": False, "timeout_ms": 1000}],
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
            "output_schema": {"type": "object", "properties": {}, "additionalProperties": False},
            "learning_signal": {
                "metrics": ["latency_actual_vs_predicted"],
                "scoring_rule": "mde < 5%",
                "feedback_sources": [{"event_type": "turn_completed", "extract": ["latency"]}],
                "sanitization": {"disallow_fields": [], "pii_patterns": ["email"], "fail_closed": True},
            },
            "boot_layer": "bundled",
            "origin": "builtin",
            "scope": "local_development",
        }
        
        validator = SkillValidator()
        report = validator.validate_manifest_dict(manifest)
        
        assert not report.is_valid
        assert any("Invalid version" in b for b in report.blockers)
    
    def test_invalid_trigger_event_type(self):
        """Test that invalid trigger event types are rejected."""
        manifest = {
            "name": "test.skill",
            "version": "1.0.0",
            "goal": "Test",
            "description": "Description",
            "triggers": [
                {
                    "name": "test",
                    "event_type": "invalid_type",  # Not in ALLOWED_TRIGGER_TYPES
                    "phase": "pre_routing",
                    "condition": "every_turn",
                    "async_allowed": False,
                    "timeout_ms": 1000,
                }
            ],
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
            "output_schema": {"type": "object", "properties": {}, "additionalProperties": False},
            "learning_signal": {
                "metrics": ["latency_actual_vs_predicted"],
                "scoring_rule": "mde < 5%",
                "feedback_sources": [{"event_type": "turn_completed", "extract": ["latency"]}],
                "sanitization": {"disallow_fields": [], "pii_patterns": ["email"], "fail_closed": True},
            },
            "boot_layer": "bundled",
            "origin": "builtin",
            "scope": "local_development",
        }
        
        validator = SkillValidator()
        report = validator.validate_manifest_dict(manifest)
        
        assert not report.is_valid
        assert any("event_type" in b for b in report.blockers)
    
    def test_invalid_boot_layer(self):
        """Test that invalid boot_layer is rejected."""
        manifest = {
            "name": "test.skill",
            "version": "1.0.0",
            "goal": "Test",
            "description": "Description",
            "triggers": [{"name": "test", "event_type": "decision_point", "phase": "pre_routing", "condition": "every_turn", "async_allowed": False, "timeout_ms": 1000}],
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
            "output_schema": {"type": "object", "properties": {}, "additionalProperties": False},
            "learning_signal": {
                "metrics": ["latency_actual_vs_predicted"],
                "scoring_rule": "mde < 5%",
                "feedback_sources": [{"event_type": "turn_completed", "extract": ["latency"]}],
                "sanitization": {"disallow_fields": [], "pii_patterns": ["email"], "fail_closed": True},
            },
            "boot_layer": "invalid_layer",  # Not in allowed values
            "origin": "builtin",
            "scope": "local_development",
        }
        
        validator = SkillValidator()
        report = validator.validate_manifest_dict(manifest)
        
        assert not report.is_valid
        assert any("boot_layer" in b for b in report.blockers)
    
    def test_missing_fail_closed_sanitization(self):
        """Test that missing fail_closed sanitization is rejected."""
        manifest = {
            "name": "test.skill",
            "version": "1.0.0",
            "goal": "Test",
            "description": "Description",
            "triggers": [{"name": "test", "event_type": "decision_point", "phase": "pre_routing", "condition": "every_turn", "async_allowed": False, "timeout_ms": 1000}],
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
            "output_schema": {"type": "object", "properties": {}, "additionalProperties": False},
            "learning_signal": {
                "metrics": ["latency_actual_vs_predicted"],
                "scoring_rule": "mde < 5%",
                "feedback_sources": [{"event_type": "turn_completed", "extract": ["latency"]}],
                "sanitization": {
                    "disallow_fields": [],
                    "pii_patterns": ["email"],
                    "fail_closed": False,  # Must be True!
                },
            },
            "boot_layer": "bundled",
            "origin": "builtin",
            "scope": "local_development",
        }
        
        validator = SkillValidator()
        report = validator.validate_manifest_dict(manifest)
        
        assert not report.is_valid
        assert any("fail_closed" in b for b in report.blockers)


class TestVersionManagement:
    """Tests for version management and in-flight-freeze semantics."""
    
    def test_in_flight_freeze_semantics(self):
        """Test that in-flight runs use locked version."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = SkillVersionManager(Path(tmpdir))
            
            # Start a run on v1.2.0
            run = manager.start_run(
                run_id="run_001",
                skill_id="os.test",
                skill_version="1.2.0",
                tenant_id="_default",
            )
            
            assert run.skill_version_at_start == "1.2.0"
            
            # Version used for run remains 1.2.0 even if we query later
            version = manager.get_run_version("run_001")
            assert version == "1.2.0"
            
            # Verify persistence
            manager2 = SkillVersionManager(Path(tmpdir))
            version2 = manager2.get_run_version("run_001")
            assert version2 == "1.2.0"
    
    def test_version_constraint_matching(self):
        """Test semantic version constraint matching."""
        # Test >=
        constraint = SkillVersionConstraint.from_string(">=1.0.0")
        assert constraint.matches("1.0.0")
        assert constraint.matches("1.5.0")
        assert constraint.matches("2.0.0")
        assert not constraint.matches("0.9.9")
        
        # Test ~
        constraint = SkillVersionConstraint.from_string("~1.2.3")
        assert constraint.matches("1.2.3")
        assert constraint.matches("1.2.5")
        assert not constraint.matches("1.3.0")
        
        # Test ^
        constraint = SkillVersionConstraint.from_string("^1.2.3")
        assert constraint.matches("1.2.3")
        assert constraint.matches("1.5.0")
        assert not constraint.matches("2.0.0")
    
    def test_tenant_version_pinning(self):
        """Test tenant-level version pinning."""
        config = {
            "os.delegation_router": {
                "enabled": True,
                "version": "1.2.3",
            },
            "os.context_adapter": {
                "enabled": False,
            },
        }
        
        pin = TenantVersionPin(config)
        
        assert pin.get_skill_version("os.delegation_router") == "1.2.3"
        assert pin.get_skill_version("os.context_adapter") is None
        assert pin.is_skill_enabled("os.delegation_router")
        assert not pin.is_skill_enabled("os.context_adapter")
    
    def test_version_resolver(self):
        """Test version resolution."""
        resolver = VersionResolver()
        
        # Resolve to latest version
        resolved = resolver.resolve_version(
            skill_id="os.test",
            available_versions=["1.0.0", "1.2.3", "1.1.0"],
        )
        assert resolved == "1.2.3"  # Highest semver version
        
        # Resolve with tenant pin
        pin = TenantVersionPin({
            "os.test": {"version": "1.0.0"}
        })
        resolved = resolver.resolve_version(
            skill_id="os.test",
            available_versions=["1.0.0", "1.2.3"],
            tenant_pin=pin,
        )
        assert resolved == "1.0.0"  # Pinned version
    
    def test_dependency_version_resolution(self):
        """Test resolving dependency versions with constraints."""
        resolver = VersionResolver()
        
        # Resolve >=1.0.0
        resolved = resolver.resolve_dependency_version(
            dependency_name="os.context_adapter",
            constraint_str=">=1.0.0",
            available_versions=["0.9.0", "1.0.0", "1.5.0", "2.0.0"],
        )
        assert resolved == "2.0.0"  # Latest matching
        
        # Resolve ~1.2.x
        resolved = resolver.resolve_dependency_version(
            dependency_name="os.context_adapter",
            constraint_str="~1.2.0",
            available_versions=["1.0.0", "1.2.3", "1.2.5", "1.3.0"],
        )
        assert resolved == "1.2.5"  # Latest matching ~1.2.x


class TestSkillCompilation:
    """Tests for skill compilation and module encapsulation."""
    
    @pytest.fixture
    def valid_skill_dir(self):
        """Create a temporary valid skill directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir)
            
            # Create manifest
            manifest = {
                "name": "os.test_skill",
                "version": "1.0.0",
                "goal": "Test skill",
                "description": "A test skill used by the compiler E2E",
                "author": "Test",
                "license": "Apache-2.0",
                "created_at": "2026-09-01T00:00:00Z",
                "updated_at": "2026-09-01T00:00:00Z",
                "triggers": [
                    {
                        "name": "on_test",
                        "event_type": "decision_point",
                        "phase": "pre_routing",
                        "condition": "every_turn",
                        "async_allowed": False,
                        "timeout_ms": 5000,
                    }
                ],
                "input_schema": {
                    "type": "object",
                    "required": ["test_input"],
                    "properties": {"test_input": {"type": "string"}},
                    "additionalProperties": False,
                },
                "output_schema": {
                    "type": "object",
                    "required": ["result"],
                    "properties": {"result": {"type": "string"}},
                    "additionalProperties": False,
                },
                "learning_signal": {
                    "metrics": ["quality_score_outcome"],
                    "scoring_rule": "accuracy >= 0.95",
                    "feedback_sources": [
                        {"event_type": "turn_completed", "extract": ["accuracy"]}
                    ],
                    "sanitization": {
                        "disallow_fields": [],
                        "pii_patterns": ["email"],
                        "fail_closed": True,
                    },
                },
                "boot_layer": "bundled",
                "origin": "builtin",
                "scope": "local_development",
            }
            
            with open(skill_dir / "manifest.yaml", "w") as f:
                yaml.dump(manifest, f)
            
            # Create skill.py
            skill_code = """
# Simple test skill
from corvin.skills import SkillRuntime

def execute(input_data):
    return {"result": "test"}
"""
            with open(skill_dir / "skill.py", "w") as f:
                f.write(skill_code)
            
            yield skill_dir
    
    def test_valid_skill_compilation(self, valid_skill_dir):
        """Test that a valid skill compiles successfully."""
        compiler = SkillCompiler()
        report = compiler.compile_skill(valid_skill_dir)
        
        assert report.is_valid
        assert report.skill_id == "os.test_skill"
        assert report.version == "1.0.0"
        assert report.runtime_instance is not None
    
    def test_missing_manifest(self):
        """Test compilation fails with missing manifest."""
        with tempfile.TemporaryDirectory() as tmpdir:
            compiler = SkillCompiler()
            report = compiler.compile_skill(Path(tmpdir))
            
            assert not report.is_valid
            assert any("Manifest not found" in e for e in report.errors)
    
    def test_module_encapsulation_validation(self):
        """Test that forbidden direct imports are caught."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir)
            
            # Create manifest
            manifest = {
                "name": "os.bad_skill",
                "version": "1.0.0",
                "goal": "Skill that must fail compilation",
                "description": "Skill with a forbidden direct import",
                "author": "Test",
                "license": "Apache-2.0",
                "created_at": "2026-09-01T00:00:00Z",
                "updated_at": "2026-09-01T00:00:00Z",
                "triggers": [
                    {
                        "name": "test",
                        "event_type": "decision_point",
                        "phase": "pre_routing",
                        "condition": "every_turn",
                        "async_allowed": False,
                        "timeout_ms": 1000,
                    }
                ],
                "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
                "output_schema": {"type": "object", "properties": {}, "additionalProperties": False},
                "learning_signal": {
                    "metrics": ["quality_score_outcome"],
                    "scoring_rule": "accuracy >= 0.95",
                    "feedback_sources": [{"event_type": "turn_completed", "extract": ["accuracy"]}],
                    "sanitization": {"disallow_fields": [], "pii_patterns": ["email"], "fail_closed": True},
                },
                "boot_layer": "bundled",
                "origin": "builtin",
                "scope": "local_development",
            }
            
            with open(skill_dir / "manifest.yaml", "w") as f:
                yaml.dump(manifest, f)
            
            # Create skill.py with forbidden import
            skill_code = """
from corvin.skills.brain_loader import infer  # Forbidden!

def execute(input_data):
    return infer(input_data)
"""
            with open(skill_dir / "skill.py", "w") as f:
                f.write(skill_code)
            
            compiler = SkillCompiler()
            report = compiler.compile_skill(skill_dir)
            
            assert not report.is_valid
            assert any("Direct import forbidden" in e for e in report.errors)


class TestIntegration:
    """Integration tests for end-to-end scenarios."""
    
    def test_manifest_validation_file_path(self):
        """Test validation from a manifest file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.yaml"
            
            manifest = {
                "name": "os.integration_test",
                "version": "2.0.0",
                "goal": "Integration test",
                "description": "Validate a manifest read from a file path",
                "author": "Test",
                "license": "Apache-2.0",
                "created_at": "2026-09-01T00:00:00Z",
                "updated_at": "2026-09-01T00:00:00Z",
                "triggers": [
                    {
                        "name": "test",
                        "event_type": "decision_point",
                        "phase": "pre_routing",
                        "condition": "every_turn",
                        "async_allowed": False,
                        "timeout_ms": 5000,
                    }
                ],
                "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
                "output_schema": {"type": "object", "properties": {}, "additionalProperties": False},
                "learning_signal": {
                    "metrics": ["quality_score_outcome"],
                    "scoring_rule": "accuracy >= 0.9",
                    "feedback_sources": [{"event_type": "turn_completed", "extract": ["accuracy"]}],
                    "sanitization": {"disallow_fields": [], "pii_patterns": ["email"], "fail_closed": True},
                },
                "boot_layer": "bundled",
                "origin": "builtin",
                "scope": "local_development",
            }
            
            with open(manifest_path, "w") as f:
                yaml.dump(manifest, f)
            
            report = validate_skill_manifest(manifest_path)
            
            assert report.is_valid
            assert report.skill_id == "os.integration_test"
    
    def test_full_workflow_validation_versioning_compilation(self):
        """Test complete workflow: validate -> version -> compile."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir)
            
            # 1. Create manifest
            manifest = {
                "name": "os.complete_workflow",
                "version": "1.5.0",
                "goal": "Complete workflow test",
                "description": "Test the complete validate-version-compile workflow",
                "author": "Test",
                "license": "Apache-2.0",
                "created_at": "2026-09-01T00:00:00Z",
                "updated_at": "2026-09-01T00:00:00Z",
                "triggers": [
                    {
                        "name": "test",
                        "event_type": "decision_point",
                        "phase": "pre_routing",
                        "condition": "every_turn",
                        "async_allowed": False,
                        "timeout_ms": 5000,
                    }
                ],
                "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
                "output_schema": {"type": "object", "properties": {}, "additionalProperties": False},
                "learning_signal": {
                    "metrics": ["quality_score_outcome"],
                    "scoring_rule": "accuracy >= 0.9",
                    "feedback_sources": [{"event_type": "turn_completed", "extract": ["accuracy"]}],
                    "sanitization": {"disallow_fields": [], "pii_patterns": ["email"], "fail_closed": True},
                },
                "boot_layer": "bundled",
                "origin": "builtin",
                "scope": "local_development",
            }
            
            with open(skill_dir / "manifest.yaml", "w") as f:
                yaml.dump(manifest, f)
            
            with open(skill_dir / "skill.py", "w") as f:
                f.write("def execute(input_data): return {'result': 'ok'}")
            
            # 2. Validate
            validator = SkillValidator()
            validation_report = validator.validate_manifest_dict(manifest)
            assert validation_report.is_valid
            
            # 3. Test versioning
            with tempfile.TemporaryDirectory() as state_dir:
                manager = SkillVersionManager(Path(state_dir))
                run = manager.start_run("run_001", "os.complete_workflow", "1.5.0")
                assert manager.get_run_version("run_001") == "1.5.0"
            
            # 4. Compile
            compiler = SkillCompiler()
            compile_report = compiler.compile_skill(skill_dir)
            assert compile_report.is_valid
            assert compile_report.runtime_instance["version"] == "1.5.0"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

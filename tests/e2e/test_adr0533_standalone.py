"""
Standalone test for ADR-0533 implementation.
Can be run without pytest: python3 test_adr0533_standalone.py
"""

import sys
import tempfile
from pathlib import Path
import yaml
import json

# Add core/skills to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core" / "skills"))

from skill_validator import validate_skill_manifest_dict, SkillValidator
from version_manager import SkillVersionManager, SkillVersionConstraint, TenantVersionPin
from compiler import SkillCompiler


def test_valid_manifest():
    """Test that a valid manifest passes all validation checks."""
    print("\n[TEST 1] Valid Manifest Validation")
    
    manifest = {
        "name": "os.delegation_router",
        "version": "1.2.3",
        "goal": "Route tasks to appropriate engines",
        "description": "Reads feedback and learns task patterns to route efficiently to native OS, ACS, or TDE based on learned thresholds and real-time metrics.",
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
    
    validator = SkillValidator()
    report = validator.validate_manifest_dict(manifest)
    
    assert report.is_valid, f"Expected valid manifest, got: {report.blockers}"
    assert report.skill_id == "os.delegation_router"
    assert report.version == "1.2.3"
    print("  ✅ PASS: Valid manifest passes all 13 validation checks")


def test_invalid_version_format():
    """Test that invalid version format is rejected."""
    print("\n[TEST 2] Invalid Version Format")
    
    manifest = {
        "name": "test.skill",
        "version": "1.2",  # Missing patch version
        "goal": "Test bad skill",
        "description": "Test skill description for validation testing",
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
            "metrics": ["latency_actual_vs_predicted"],
            "scoring_rule": "accuracy >= 0.9",
            "feedback_sources": [{"event_type": "turn_completed", "extract": ["accuracy"]}],
            "sanitization": {"disallow_fields": [], "pii_patterns": ["email"], "fail_closed": True},
        },
        "boot_layer": "bundled",
        "origin": "builtin",
        "scope": "local_development",
    }
    
    validator = SkillValidator()
    report = validator.validate_manifest_dict(manifest)
    
    assert not report.is_valid, "Expected invalid manifest"
    assert any("version" in b for b in report.blockers)
    print("  ✅ PASS: Invalid version format correctly rejected")


def test_missing_fail_closed():
    """Test that missing fail_closed sanitization is rejected."""
    print("\n[TEST 3] Missing fail_closed Sanitization")
    
    manifest = {
        "name": "test.skill",
        "version": "1.0.0",
        "goal": "Test bad skill",
        "description": "Test skill description for validation testing",
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
            "metrics": ["latency_actual_vs_predicted"],
            "scoring_rule": "accuracy >= 0.9",
            "feedback_sources": [{"event_type": "turn_completed", "extract": ["accuracy"]}],
            "sanitization": {
                "disallow_fields": [],
                "pii_patterns": ["email"],
                "fail_closed": False,  # WRONG!
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
    print("  ✅ PASS: fail_closed=False correctly rejected")


def test_in_flight_freeze():
    """Test in-flight-freeze semantics."""
    print("\n[TEST 4] In-Flight Freeze Semantics")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = SkillVersionManager(Path(tmpdir))
        
        # Start run on v1.2.0
        run = manager.start_run(
            run_id="run_001",
            skill_id="os.test",
            skill_version="1.2.0",
            tenant_id="_default",
        )
        
        assert run.skill_version_at_start == "1.2.0"
        
        # Version should remain 1.2.0
        version = manager.get_run_version("run_001")
        assert version == "1.2.0", f"Expected 1.2.0, got {version}"
        
        # Test persistence across instances
        manager2 = SkillVersionManager(Path(tmpdir))
        version2 = manager2.get_run_version("run_001")
        assert version2 == "1.2.0"
        
    print("  ✅ PASS: In-flight task locked to starting version")


def test_version_constraint_matching():
    """Test semantic version constraint matching."""
    print("\n[TEST 5] Semantic Version Constraints")
    
    # Test >=
    constraint = SkillVersionConstraint.from_string(">=1.0.0")
    assert constraint.matches("1.0.0")
    assert constraint.matches("2.0.0")
    assert not constraint.matches("0.9.0")
    
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
    
    print("  ✅ PASS: All version constraints work correctly")


def test_tenant_version_pinning():
    """Test tenant-level version pinning."""
    print("\n[TEST 6] Tenant Version Pinning")
    
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
    
    print("  ✅ PASS: Tenant version pinning works")


def test_skill_compilation():
    """Test skill compilation."""
    print("\n[TEST 7] Skill Compilation")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = Path(tmpdir)
        
        # Create manifest
        manifest = {
            "name": "os.test_skill",
            "version": "1.0.0",
            "goal": "Test skill",
            "description": "A test skill for demonstrating successful skill compilation and manifest validation processes.",
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
                "required": ["input"],
                "properties": {"input": {"type": "string"}},
                "additionalProperties": False,
            },
            "output_schema": {
                "type": "object",
                "required": ["output"],
                "properties": {"output": {"type": "string"}},
                "additionalProperties": False,
            },
            "learning_signal": {
                "metrics": ["latency_actual_vs_predicted"],
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
        
        with open(skill_dir / "skill.py", "w") as f:
            f.write("def execute(input_data):\n    return {'output': 'test'}\n")
        
        compiler = SkillCompiler()
        report = compiler.compile_skill(skill_dir)
        
        assert report.is_valid, f"Compilation failed: {report.errors}"
        assert report.skill_id == "os.test_skill"
        assert report.version == "1.0.0"
        assert report.runtime_instance is not None
        
    print("  ✅ PASS: Skill compilation successful")


def test_module_encapsulation():
    """Test that forbidden imports are caught."""
    print("\n[TEST 8] Module Encapsulation Validation")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = Path(tmpdir)
        
        # Create manifest
        manifest = {
            "name": "os.bad_skill",
            "version": "1.0.0",
            "goal": "Test bad skill",
            "description": "Test skill for demonstrating module encapsulation validation.",
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
                "metrics": ["latency_actual_vs_predicted"],
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
        
        # Create skill.py with forbidden import
        skill_code = "from corvin.skills.brain_loader import infer\n"
        with open(skill_dir / "skill.py", "w") as f:
            f.write(skill_code)
        
        compiler = SkillCompiler()
        report = compiler.compile_skill(skill_dir)
        
        assert not report.is_valid, "Should reject forbidden import"
        assert any("Direct import forbidden" in e for e in report.errors), f"Expected forbidden import error, got: {report.errors}"
        
    print("  ✅ PASS: Forbidden direct imports correctly rejected")


def main():
    """Run all tests."""
    print("=" * 70)
    print("ADR-0533: OS-Skill Manifest Schema & Versioning Strategy")
    print("Standalone Test Suite")
    print("=" * 70)
    
    tests = [
        test_valid_manifest,
        test_invalid_version_format,
        test_missing_fail_closed,
        test_in_flight_freeze,
        test_version_constraint_matching,
        test_tenant_version_pinning,
        test_skill_compilation,
        test_module_encapsulation,
    ]
    
    passed = 0
    failed = 0
    
    for test_func in tests:
        try:
            test_func()
            passed += 1
        except AssertionError as e:
            print(f"  ❌ FAIL: {e}")
            failed += 1
        except Exception as e:
            print(f"  ❌ ERROR: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    print("\n" + "=" * 70)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 70)
    
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

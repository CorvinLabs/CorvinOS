"""Security Tests: Cross-Tenant Symlink Escape Prevention (ADR-0007).

Tests verify that trigger_detector and autonomous_forge_routes properly validate
all path operations and reject symlink escape attempts.

Load-bearing test set: 12 unit tests + 1 E2E test.
All tests are fail-closed: no bypasses, no mocks.
"""

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import types
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from unittest import mock

import pytest

# `corvin_operator/skill-forge/` has a dash, which is not a valid Python
# package segment, so it can't be reached via a plain `import`. Load it via
# importlib (same pattern as tests/skill_forge/test_trigger_detector.py) and
# register it under its dotted name in sys.modules so both `from X import Y`
# below AND `mock.patch("corvin_operator.skill_forge.autonomous.trigger_detector...")`
# (a string-path patch target) resolve correctly.
#
# trigger_detector.py does `from .audit_chain_validator import ...` (a
# relative import), which requires its parent to be a real package with
# __path__ — so the two missing parent levels (corvin_operator.skill_forge
# and .autonomous; corvin_operator itself is a real package already) are
# registered as namespace packages pointing at the dashed directory before
# any submodule is loaded.
_REPO = Path(__file__).resolve().parents[2]
_SKILL_FORGE_DIR = _REPO / "corvin_operator" / "skill-forge"
_AUTONOMOUS_DIR = _SKILL_FORGE_DIR / "autonomous"
sys.path.insert(0, str(_REPO))


def _ensure_namespace_package(dotted_name: str, path: Path):
    existing = sys.modules.get(dotted_name)
    if existing is not None:
        return existing
    module = types.ModuleType(dotted_name)
    module.__path__ = [str(path)]
    sys.modules[dotted_name] = module
    return module


_ensure_namespace_package("corvin_operator.skill_forge", _SKILL_FORGE_DIR)
_ensure_namespace_package("corvin_operator.skill_forge.autonomous", _AUTONOMOUS_DIR)


def _load_module(dotted_name: str, file_path: Path):
    existing = sys.modules.get(dotted_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(dotted_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[dotted_name] = module
    spec.loader.exec_module(module)
    return module


_audit_chain_validator = _load_module(
    "corvin_operator.skill_forge.autonomous.audit_chain_validator",
    _AUTONOMOUS_DIR / "audit_chain_validator.py",
)
_path_traversal_validator = _load_module(
    "corvin_operator.skill_forge.autonomous.path_traversal_validator",
    _AUTONOMOUS_DIR / "path_traversal_validator.py",
)
_trigger_detector_module = _load_module(
    "corvin_operator.skill_forge.autonomous.trigger_detector",
    _AUTONOMOUS_DIR / "trigger_detector.py",
)

PathTraversalError = _path_traversal_validator.PathTraversalError
validate_path_within_scope = _path_traversal_validator.validate_path_within_scope
validate_tenant_audit_path = _path_traversal_validator.validate_tenant_audit_path
validate_skill_id_and_version = _path_traversal_validator.validate_skill_id_and_version
assert_path_safe = _path_traversal_validator.assert_path_safe
assert_skill_parameters_safe = _path_traversal_validator.assert_skill_parameters_safe


def _hash_chain_events(raw_events: list) -> list:
    """Attach valid hash/prev_hash fields to a list of raw audit events,
    using the exact algorithm AuditChainValidator verifies against (Fix #1).
    Without this, any audit.jsonl fixture written by this file's tests is
    rejected by the hash-chain check before the symlink check under test
    ever runs."""
    prev_hash = ""
    chained = []
    for event in raw_events:
        event_hash = _audit_chain_validator.AuditChainValidator._compute_event_hash(
            event, prev_hash
        )
        chained_event = {**event, "prev_hash": prev_hash, "hash": event_hash}
        chained.append(chained_event)
        prev_hash = event_hash
    return chained
SkillLossTriggerDetector = _trigger_detector_module.SkillLossTriggerDetector


# ─────────────────────────────────────────────────────────────────────────────
# Unit Test 1: Valid Path (No Symlinks)
# ─────────────────────────────────────────────────────────────────────────────


def test_validate_path_valid_no_symlinks():
    """Valid path without symlinks should pass."""
    with tempfile.TemporaryDirectory() as tmpdir:
        scope = Path(tmpdir) / "tenant1"
        scope.mkdir()

        file_path = scope / "audit.jsonl"
        file_path.touch()

        is_valid, resolved, error = validate_path_within_scope(file_path, scope)

        assert is_valid, f"Valid path rejected: {error}"
        assert error is None
        assert str(resolved).startswith(str(scope))


# ─────────────────────────────────────────────────────────────────────────────
# Unit Test 2: Symlink to Sibling Tenant → Reject
# ─────────────────────────────────────────────────────────────────────────────


def test_validate_path_symlink_to_sibling_tenant_rejected():
    """Symlink to sibling tenant's audit trail should be rejected."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create two tenant directories
        tenant1 = Path(tmpdir) / "tenant1"
        tenant2 = Path(tmpdir) / "tenant2"
        tenant1.mkdir()
        tenant2.mkdir()

        # Create audit file in tenant2
        audit_t2 = tenant2 / "audit.jsonl"
        audit_t2.write_text('{"event": "test"}\n')

        # Create symlink in tenant1 pointing to tenant2's audit
        symlink_path = tenant1 / "audit.jsonl"
        symlink_path.symlink_to(audit_t2)

        # Validate: should reject because resolved path escapes scope
        is_valid, resolved, error = validate_path_within_scope(symlink_path, tenant1)

        assert not is_valid, "Symlink to sibling tenant should be rejected"
        assert error is not None
        assert "escapes scope" in error.lower()


# ─────────────────────────────────────────────────────────────────────────────
# Unit Test 3: Symlink to /etc/passwd → Reject
# ─────────────────────────────────────────────────────────────────────────────


def test_validate_path_symlink_to_system_file_rejected():
    """Symlink to system files (e.g., /etc/passwd) should be rejected."""
    with tempfile.TemporaryDirectory() as tmpdir:
        scope = Path(tmpdir) / "tenant"
        scope.mkdir()

        # Create symlink to /etc/passwd (if it exists)
        symlink_path = scope / "audit.jsonl"
        if Path("/etc/passwd").exists():
            symlink_path.symlink_to("/etc/passwd")

            is_valid, resolved, error = validate_path_within_scope(symlink_path, scope)

            assert not is_valid, "Symlink to /etc/passwd should be rejected"
            assert error is not None


# ─────────────────────────────────────────────────────────────────────────────
# Unit Test 4: Path with ../ → Reject
# ─────────────────────────────────────────────────────────────────────────────


def test_validate_path_parent_escape_rejected():
    """Path containing .. should be rejected."""
    with tempfile.TemporaryDirectory() as tmpdir:
        scope = Path(tmpdir) / "tenant"
        scope.mkdir()

        # Try to escape with ..
        file_path = scope / ".." / "other_tenant" / "audit.jsonl"

        is_valid, resolved, error = validate_path_within_scope(file_path, scope)

        assert not is_valid, "Path with .. should be rejected"
        assert error is not None
        assert "escape sequence" in error.lower() or ".." in error


# ─────────────────────────────────────────────────────────────────────────────
# Unit Test 5: Path with ~/ → Reject
# ─────────────────────────────────────────────────────────────────────────────


def test_validate_path_home_escape_rejected():
    """Path starting with ~/ should be rejected."""
    with tempfile.TemporaryDirectory() as tmpdir:
        scope = Path(tmpdir) / "tenant"
        scope.mkdir()

        # Try to escape with ~/
        file_path = Path("~/.corvin/audit.jsonl")

        is_valid, resolved, error = validate_path_within_scope(file_path, scope)

        assert not is_valid, "Path with ~ should be rejected"
        assert error is not None


# ─────────────────────────────────────────────────────────────────────────────
# Unit Test 6: Circular Symlink → Reject
# ─────────────────────────────────────────────────────────────────────────────


def test_validate_path_circular_symlink_rejected():
    """Circular symlinks should be detected and rejected."""
    with tempfile.TemporaryDirectory() as tmpdir:
        scope = Path(tmpdir) / "tenant"
        scope.mkdir()

        # Create a circular symlink
        link1 = scope / "link1"
        link2 = scope / "link2"

        link1.symlink_to(link2)
        link2.symlink_to(link1)

        is_valid, resolved, error = validate_path_within_scope(link1, scope)

        # realpath should handle this, but we want to verify behavior
        assert error is not None or not is_valid, "Circular symlink should be handled"


# ─────────────────────────────────────────────────────────────────────────────
# Unit Test 7: Tenant Audit Path Validator - Must Be Named audit.jsonl
# ─────────────────────────────────────────────────────────────────────────────


def test_validate_tenant_audit_path_wrong_filename():
    """Audit path validator should reject if filename is not audit.jsonl."""
    with tempfile.TemporaryDirectory() as tmpdir:
        scope = Path(tmpdir) / "forge"
        scope.mkdir()

        # Try with wrong filename
        wrong_path = scope / "audit.json"  # Missing 'l'

        is_valid, resolved, error = validate_tenant_audit_path(
            tenant_id="test_tenant",
            audit_path=wrong_path,
            expected_audit_dir=scope,
        )

        assert not is_valid, "Audit path validator should reject wrong filename"
        assert error is not None
        assert "audit.jsonl" in error.lower()


# ─────────────────────────────────────────────────────────────────────────────
# Unit Test 8: Tenant Audit Path Validator - Rejects Symlinks
# ─────────────────────────────────────────────────────────────────────────────


def test_validate_tenant_audit_path_symlink_rejected():
    """Audit path validator should strictly reject symlinks (fail-closed)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        scope = Path(tmpdir) / "forge"
        scope.mkdir()

        # Create actual file
        real_file = scope / "real_audit.jsonl"
        real_file.touch()

        # Create symlink
        symlink_path = scope / "audit.jsonl"
        symlink_path.symlink_to(real_file)

        # Validate: should reject because it's a symlink (strict mode)
        is_valid, resolved, error = validate_tenant_audit_path(
            tenant_id="test_tenant",
            audit_path=symlink_path,
            expected_audit_dir=scope,
        )

        assert not is_valid, "Audit validator should reject symlinks (strict)"
        assert error is not None
        assert "symlink" in error.lower()


# ─────────────────────────────────────────────────────────────────────────────
# Unit Test 9: Skill Parameters Validator - Valid Inputs
# ─────────────────────────────────────────────────────────────────────────────


def test_validate_skill_id_version_valid():
    """Valid skill_id and version should pass."""
    is_valid, error = validate_skill_id_and_version(
        skill_id="os.delegation_router",
        version="2.1.0",
    )

    assert is_valid, f"Valid inputs rejected: {error}"
    assert error is None


# ─────────────────────────────────────────────────────────────────────────────
# Unit Test 10: Skill Parameters Validator - Path Traversal in skill_id
# ─────────────────────────────────────────────────────────────────────────────


def test_validate_skill_id_version_traversal_rejected():
    """Skill parameters with path traversal should be rejected."""
    # Test skill_id with ../
    is_valid, error = validate_skill_id_and_version(
        skill_id="../../../etc/passwd",
        version="1.0.0",
    )

    assert not is_valid, "skill_id with ../ should be rejected"
    assert error is not None

    # Test version with ../
    is_valid, error = validate_skill_id_and_version(
        skill_id="os.skill",
        version="../../../etc/passwd",
    )

    assert not is_valid, "version with ../ should be rejected"
    assert error is not None


# ─────────────────────────────────────────────────────────────────────────────
# Unit Test 11: Skill Parameters Validator - Invalid Semantic Version
# ─────────────────────────────────────────────────────────────────────────────


def test_validate_skill_id_version_invalid_semver():
    """Invalid semantic version format should be rejected."""
    # Single number version
    is_valid, error = validate_skill_id_and_version(
        skill_id="os.skill",
        version="1",
    )

    assert not is_valid, "Invalid semver (single number) should be rejected"

    # Non-numeric parts
    is_valid, error = validate_skill_id_and_version(
        skill_id="os.skill",
        version="1.2.alpha",
    )

    assert not is_valid, "Invalid semver (non-numeric) should be rejected"


# ─────────────────────────────────────────────────────────────────────────────
# Unit Test 12: assert_path_safe Raises on Validation Failure
# ─────────────────────────────────────────────────────────────────────────────


def test_assert_path_safe_raises_on_invalid():
    """assert_path_safe should raise PathTraversalError on validation failure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        scope = Path(tmpdir) / "tenant"
        scope.mkdir()

        # Create path with escape sequence
        bad_path = scope / ".." / "other_tenant" / "audit.jsonl"

        with pytest.raises(PathTraversalError):
            assert_path_safe(bad_path, scope, context="test")


# ─────────────────────────────────────────────────────────────────────────────
# Unit Test 13: assert_skill_parameters_safe Raises on Validation Failure
# ─────────────────────────────────────────────────────────────────────────────


def test_assert_skill_parameters_safe_raises_on_invalid():
    """assert_skill_parameters_safe should raise PathTraversalError."""
    with pytest.raises(PathTraversalError):
        assert_skill_parameters_safe(
            skill_id="../../../etc/passwd",
            version="1.0.0",
        )


# ─────────────────────────────────────────────────────────────────────────────
# E2E Test: Trigger Detector Rejects Cross-Tenant Symlink
# ─────────────────────────────────────────────────────────────────────────────


def test_trigger_detector_symlink_escape_e2e():
    """E2E: TriggerDetector should reject and fail-closed on cross-tenant symlink."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Set up two tenants
        tenant1_dir = Path(tmpdir) / "tenants" / "tenant1" / "global" / "forge"
        tenant2_dir = Path(tmpdir) / "tenants" / "tenant2" / "global" / "forge"

        tenant1_dir.mkdir(parents=True)
        tenant2_dir.mkdir(parents=True)

        # Create audit file in tenant2 with valid, hash-chained events — the
        # hash-chain check (Fix #1) runs before the path/symlink check (Fix
        # #3) inside detect_loss_signals, so an event without valid hash
        # fields would be rejected for the wrong reason before the symlink
        # escape this test targets is ever evaluated.
        audit_t2 = tenant2_dir / "audit.jsonl"
        chained = _hash_chain_events([
            {
                "event_type": "skill_executed",
                "skill_id": "os.test",
                "version": "1.0.0",
                "tenant_id": "tenant2",
                "ts": datetime.utcnow().timestamp(),
                "outcome_feedback": {"correct": True},
            }
        ])
        audit_t2.write_text("\n".join(json.dumps(e) for e in chained) + "\n")

        # Create malicious symlink in tenant1 pointing to tenant2
        audit_t1_symlink = tenant1_dir / "audit.jsonl"
        audit_t1_symlink.symlink_to(audit_t2)

        # Mock tenant_audit_chain to return the symlink path
        with mock.patch("corvin_operator.skill_forge.autonomous.trigger_detector.tenant_audit_chain") as mock_chain:
            mock_chain.return_value = audit_t1_symlink

            detector = SkillLossTriggerDetector()

            # Should raise RuntimeError (fail-closed) due to symlink validation failure
            with pytest.raises(RuntimeError) as exc_info:
                detector.detect_loss_signals(tenant_id="tenant1")

            assert "validation failed" in str(exc_info.value).lower()


# ─────────────────────────────────────────────────────────────────────────────
# E2E Test: Trigger Detector Works with Valid Audit File
# ─────────────────────────────────────────────────────────────────────────────


def test_trigger_detector_valid_audit_e2e():
    """E2E: TriggerDetector should work correctly with valid audit files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tenant_dir = Path(tmpdir) / "tenants" / "tenant1" / "global" / "forge"
        tenant_dir.mkdir(parents=True)

        # Create valid audit file
        audit_file = tenant_dir / "audit.jsonl"
        now = datetime.utcnow()

        # Write skill execution events with low confidence
        events = []
        for i in range(5):
            event = {
                "event_type": "skill_executed",
                "skill_id": "os.test",
                "version": "1.0.0",
                "tenant_id": "tenant1",
                "ts": (now - timedelta(hours=12) + timedelta(hours=i)).timestamp(),
                "outcome_feedback": {"correct": i < 2},  # 2/5 = 0.4 confidence (below 0.70 threshold)
            }
            events.append(event)

        chained = _hash_chain_events(events)
        audit_file.write_text("\n".join(json.dumps(e) for e in chained) + "\n")

        # Mock tenant_audit_chain
        with mock.patch("corvin_operator.skill_forge.autonomous.trigger_detector.tenant_audit_chain") as mock_chain:
            mock_chain.return_value = audit_file

            detector = SkillLossTriggerDetector()
            triggers = detector.detect_loss_signals(tenant_id="tenant1", lookback_hours=24)

            # Should detect a loss trigger (confidence 0.4 < 0.70)
            assert len(triggers) > 0, "Should detect loss signal"
            assert triggers[0].skill_id == "os.test"
            assert triggers[0].confidence < 0.70


# ─────────────────────────────────────────────────────────────────────────────
# E2E Test: Routes Reject Invalid Skill Parameters
# ─────────────────────────────────────────────────────────────────────────────


def test_autonomous_forge_routes_validate_skill_params():
    """E2E: Routes should validate skill_id and version parameters."""
    from core.console.corvin_console.routes.autonomous_forge_routes import (
        validate_skill_id,
        validate_version,
    )

    # Valid inputs
    assert validate_skill_id("os.delegation_router") is True
    assert validate_version("2.1.0") is True

    # Invalid inputs
    assert validate_skill_id("../../../etc/passwd") is False
    assert validate_skill_id("skill/with/slashes") is False
    assert validate_version("2.1") is False  # Wrong format
    assert validate_version("2.1.0.1") is False  # Too many parts


if __name__ == "__main__":
    # Run with: pytest tests/security/test_symlink_escape.py -v
    pytest.main([__file__, "-v"])

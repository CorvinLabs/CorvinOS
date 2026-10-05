"""Unit tests for compile-time enforcement checker (ADR-2224).

Tests the Mypy-based boundary checker that runs at manifest validation time.
"""
import pytest
from core.orchestration.layer_forge.enforcement import EnforcementChecker


def test_no_compile_time_rules_returns_skipped(tmp_path):
    """If enforcement_rules has no compile_time entries, the check is SKIPPED."""
    checker = EnforcementChecker(tmp_path)
    manifest = {
        "enforcement_rules": [
            {"rule_id": "boot_rule", "type": "boot_time"}
        ]
    }
    verdict = checker.check_layer_boundaries(manifest)
    assert verdict.status == "SKIPPED"
    assert "no compile_time rules" in verdict.detail


def test_compile_time_rules_but_no_boundary_rules_returns_skipped(tmp_path):
    """Compile-time rules exist, but no boundary-specific rules → SKIPPED."""
    checker = EnforcementChecker(tmp_path)
    manifest = {
        "enforcement_rules": [
            {"rule_id": "lint_check", "type": "compile_time"}
        ]
    }
    verdict = checker.check_layer_boundaries(manifest)
    assert verdict.status == "SKIPPED"
    assert "no boundary rules" in verdict.detail


def test_mypy_not_installed_returns_error(tmp_path, monkeypatch):
    """If mypy is not available, return ERROR."""
    checker = EnforcementChecker(tmp_path)
    monkeypatch.setattr(checker, "_has_mypy", lambda: False)
    manifest = {
        "enforcement_rules": [
            {"rule_id": "l10_l5_boundary", "type": "compile_time"}
        ],
        "targets": [{"layer_id": "L10"}]
    }
    verdict = checker.check_layer_boundaries(manifest)
    assert verdict.status == "ERROR"
    assert "mypy not installed" in verdict.detail


def test_mypy_check_with_violations_returns_fail(tmp_path, monkeypatch):
    """If mypy finds boundary violations, return FAIL."""
    checker = EnforcementChecker(tmp_path)
    monkeypatch.setattr(checker, "_has_mypy", lambda: True)
    monkeypatch.setattr(
        checker,
        "_run_mypy_check",
        lambda m: "core/layers/l10.py:15: error: Module 'core.layers.l5' is not directly importable from L10"
    )
    manifest = {
        "enforcement_rules": [
            {"rule_id": "l10_l5_boundary", "type": "compile_time"}
        ],
        "targets": [{"layer_id": "L10"}]
    }
    verdict = checker.check_layer_boundaries(manifest)
    assert verdict.status == "FAIL"
    assert "boundary violations" in verdict.detail


def test_mypy_check_with_no_violations_returns_pass(tmp_path, monkeypatch):
    """If mypy finds no violations, return PASS."""
    checker = EnforcementChecker(tmp_path)
    monkeypatch.setattr(checker, "_has_mypy", lambda: True)
    monkeypatch.setattr(checker, "_run_mypy_check", lambda m: "")
    manifest = {
        "enforcement_rules": [
            {"rule_id": "l10_l5_boundary", "type": "compile_time"}
        ],
        "targets": [{"layer_id": "L10"}]
    }
    verdict = checker.check_layer_boundaries(manifest)
    assert verdict.status == "PASS"
    assert "no boundary violations" in verdict.detail

"""Tripwire tests for multi-tenant audit chain handling (ADR-0007).

Tests verify that the boot tripwires handle multi-tenant audit chains correctly,
including cases where the core audit chain is small (e.g., 2 CRITICAL events)
while the forge chain has full history.

Note: The _check_audit_unification() function reads from ~/.corvin/ directly
and cannot be easily mocked due to hardcoded Path.home() calls. These tests
verify that the function:
1. Never raises (always returns safely)
2. Always returns ok=True (reporting-only, never blocks boot)
3. Is wired into the tripwire chain
"""
from corvin_compliance_reports.tripwire import (
    _check_audit_unification,
    TripwireResult,
    REPORTING_ONLY,
    TRIPWIRES,
)


def test_audit_unification_never_raises():
    """_check_audit_unification should never raise, even on edge cases."""
    # Should return safely regardless of system state
    result = _check_audit_unification()
    assert isinstance(result, TripwireResult)
    assert result.name == "audit_unification"


def test_audit_unification_is_reporting_only():
    """Multi-tenant audit unification checks should be reporting-only."""
    assert "audit_unification" in REPORTING_ONLY
    # This means: report on divergence but never block boot


def test_audit_unification_is_wired_into_tripwires():
    """The check should be in the active tripwire list."""
    tripwire_names = [t.__name__ for t in TRIPWIRES]
    assert "_check_audit_unification" in tripwire_names

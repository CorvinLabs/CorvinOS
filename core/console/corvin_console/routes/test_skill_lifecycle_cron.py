"""Test daily cleanup cron job (ADR-0409, Commit 4)."""

from datetime import datetime, timedelta
from core.console.corvin_console.routes.skill_lifecycle_cron import (
    cleanup_ephemeral_skills_daily,
    log_cleanup_results
)


def test_cleanup_result_structure():
    """Daily cleanup returns correct result structure."""
    # Should return stats even with no registries
    result = cleanup_ephemeral_skills_daily(tenant_ids=["test_tenant"], ttl_hours=24)

    assert isinstance(result, dict)
    assert 'tenant_results' in result
    assert 'total_deleted' in result
    assert 'total_errors' in result
    assert isinstance(result['total_deleted'], int)
    assert isinstance(result['total_errors'], int)


def test_cleanup_no_tenants():
    """Cleanup handles gracefully when no tenants exist."""
    result = cleanup_ephemeral_skills_daily(tenant_ids=[], ttl_hours=24)

    assert result['total_deleted'] == 0
    assert result['total_errors'] == 0
    assert isinstance(result['tenant_results'], dict)


def test_log_cleanup_results():
    """log_cleanup_results handles empty and populated results."""
    # Empty result
    empty_result = {'tenant_results': {}, 'total_deleted': 0, 'total_errors': 0}
    try:
        log_cleanup_results(empty_result)
        success = True
    except Exception:
        success = False

    assert success, "log_cleanup_results should handle empty result"

    # Populated result
    populated_result = {
        'tenant_results': {
            'test_tenant': {
                'deleted_count': 5,
                'errors': [],
                'audit_events': ['event1']
            }
        },
        'total_deleted': 5,
        'total_errors': 0
    }

    try:
        log_cleanup_results(populated_result)
        success = True
    except Exception:
        success = False

    assert success, "log_cleanup_results should handle populated result"


if __name__ == "__main__":
    test_cleanup_result_structure()
    print("✅ Test 1: Daily cleanup result structure")

    test_cleanup_no_tenants()
    print("✅ Test 2: No tenants cleanup")

    test_log_cleanup_results()
    print("✅ Test 3: log_cleanup_results")

    print("\n✅ All daily cleanup cron tests passed")

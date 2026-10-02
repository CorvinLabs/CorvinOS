"""Test session-close cleanup handler (ADR-0409, Commit 3)."""

from core.console.corvin_console.routes.skill_lifecycle_cleanup import on_session_close, cleanup_session_skills


def test_cleanup_result_structure():
    """cleanup_session_skills returns correct result structure."""
    # Should always return dict with expected keys
    result = cleanup_session_skills(tenant_id="test", session_id="sess_123")

    assert isinstance(result, dict)
    assert 'deleted_count' in result
    assert 'errors' in result
    assert 'audit_events' in result
    assert isinstance(result['deleted_count'], int)
    assert isinstance(result['errors'], list)
    assert isinstance(result['audit_events'], list)


def test_on_session_close_no_error():
    """on_session_close handles missing registries gracefully (best-effort)."""
    # Should not raise exception even if registry doesn't exist
    try:
        on_session_close(tenant_id="test", session_id="sess_123")
        success = True
    except Exception:
        success = False

    assert success, "on_session_close should handle missing registry gracefully"


if __name__ == "__main__":
    test_cleanup_result_structure()
    print("✅ Test 1: cleanup_session_skills result structure")

    test_on_session_close_no_error()
    print("✅ Test 2: on_session_close best-effort cleanup")

    print("\n✅ All session-close cleanup tests passed")

"""@e2e_for decorator for reachability proof (Phase 2)."""
from __future__ import annotations


def e2e_for(pattern_id: str):
    """Mark a test as E2E proof for a pattern.
    
    Usage:
        @e2e_for("pattern_retry_backoff_exponential")
        def test_openai_tts_with_429_retry():
            # Real API call, not mock
            response = say_py("test.opus", "test", "de", "alloy", "openai")
            assert response.success
            assert response.retries >= 1
    """
    def decorator(test_func):
        test_func._e2e_for = pattern_id
        return test_func
    return decorator

"""Test: session_auto_renewal_hook — Auto-session split on token budget."""

import pytest
import asyncio
import tempfile
import os
from pathlib import Path


@pytest.fixture
def temp_corvin_home():
    """Temporary CORVIN_HOME for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        old_home = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = tmpdir
        yield tmpdir
        if old_home:
            os.environ["CORVIN_HOME"] = old_home
        else:
            os.environ.pop("CORVIN_HOME", None)


def test_session_auto_renewal_hook_import():
    """Verify the module imports without error."""
    from session_auto_renewal_hook import SessionAutoRenewalHook, get_renewal_hook
    assert SessionAutoRenewalHook is not None
    assert get_renewal_hook() is not None


@pytest.mark.asyncio
async def test_check_and_maybe_split_session_ok(temp_corvin_home):
    """Test: Normal operation (below 85%) returns 'ok'."""
    try:
        import context_budget as _cb
    except ImportError:
        pytest.skip("context_budget not available")

    from session_auto_renewal_hook import SessionAutoRenewalHook

    # Register budget
    session_id = "test_session_1"
    _cb.register_session_budget(session_id, quota=10000, oom_policy="reject")

    # Add tokens (40% of budget)
    _cb.account_turn(session_id, "t1", 4000)

    hook = SessionAutoRenewalHook(context_budget_module=_cb)
    action, new_session_id = await hook.check_and_maybe_split_session(
        chat_key="user_123",
        tokens_used=1000,
        session_id=session_id,
        goal="Test goal"
    )

    assert action == "ok"
    assert new_session_id is None


@pytest.mark.asyncio
async def test_check_and_maybe_split_session_warn(temp_corvin_home):
    """Test: Warning at 85%+."""
    try:
        import context_budget as _cb
    except ImportError:
        pytest.skip("context_budget not available")

    from session_auto_renewal_hook import SessionAutoRenewalHook

    session_id = "test_session_2"
    _cb.register_session_budget(session_id, quota=10000, oom_policy="reject")

    # Add 8700 tokens (87% of budget)
    _cb.account_turn(session_id, "t1", 8700)

    hook = SessionAutoRenewalHook(context_budget_module=_cb)
    action, new_session_id = await hook.check_and_maybe_split_session(
        chat_key="user_123",
        tokens_used=100,
        session_id=session_id,
        goal="Test goal"
    )

    assert action == "warn"
    assert new_session_id is None


@pytest.mark.asyncio
async def test_check_and_maybe_split_session_over_budget(temp_corvin_home):
    """Test: Over budget triggers split."""
    try:
        import context_budget as _cb
    except ImportError:
        pytest.skip("context_budget not available")

    from session_auto_renewal_hook import SessionAutoRenewalHook

    session_id = "test_session_3"
    _cb.register_session_budget(session_id, quota=10000, oom_policy="reject")

    # Exceed budget
    _cb.account_turn(session_id, "t1", 10500)

    hook = SessionAutoRenewalHook(context_budget_module=_cb)
    action, new_session_id = await hook.check_and_maybe_split_session(
        chat_key="user_123",
        tokens_used=100,
        session_id=session_id,
        goal="Test goal"
    )

    # Without auto_starter/lifecycle_manager, split will fail gracefully
    assert action in ("split", "split_failed")


def test_renewal_hook_singleton():
    """Test: Singleton pattern works."""
    from session_auto_renewal_hook import get_renewal_hook

    hook1 = get_renewal_hook()
    hook2 = get_renewal_hook()
    assert hook1 is hook2


def test_adapter_imports_renewal_hook():
    """Test: adapter.py can import the renewal hook."""
    import sys
    from pathlib import Path

    # Ensure the shared dir is in path
    shared_dir = Path(__file__).parent
    if str(shared_dir) not in sys.path:
        sys.path.insert(0, str(shared_dir))

    # This should not raise
    from session_auto_renewal_hook import get_renewal_hook
    hook = get_renewal_hook()
    assert hook is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

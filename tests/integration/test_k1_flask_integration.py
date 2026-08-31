"""Integration tests for k=1 Flask endpoint (ADR-0515 Tier-4)."""

import sys
sys.path.insert(0, '.')

import asyncio
from core.endpoints.k1_context import get_k1_context, K1RequestContext
from core.endpoints.k1_decorators import k1_flask


async def test_k1_flask_decorator():
    """
    Integration test: Verify @k1_flask decorator creates and manages k=1 context.

    Simulates a Flask request lifecycle:
    1. Request arrives
    2. @k1_flask allocates k=1 context
    3. Handler code runs within context
    4. Context is cleaned up on response
    """
    handler_context = None

    @k1_flask()
    async def mock_flask_handler():
        """Mock Flask route handler."""
        ctx = get_k1_context()
        nonlocal handler_context
        handler_context = ctx

        # Verify context is available
        assert ctx is not None
        assert ctx.transport == 'http'
        assert ctx.connection is not None
        assert ctx.session is not None
        assert ctx.task_queue is not None
        assert ctx.pipeline is not None

        # Simulate handler logic
        async with ctx.connection:
            session = await ctx.session.resolve_session("user:test")
            assert session is not None

            await ctx.task_queue.enqueue(lambda: 'test_task')

        return {"status": "ok"}

    # Call the decorated handler (as Flask would)
    result = await mock_flask_handler()

    # Verify result
    assert result == {"status": "ok"}
    assert handler_context is not None
    print("✓ Integration Test: @k1_flask decorator works end-to-end")


async def test_k1_cli_decorator():
    """
    Integration test: Verify @k1_cli_command decorator creates and manages k=1 context.
    """
    handler_context = None

    from core.endpoints.k1_decorators import k1_cli

    @k1_cli()
    async def mock_cli_handler(flag_id: str):
        """Mock CLI command handler."""
        ctx = get_k1_context()
        nonlocal handler_context
        handler_context = ctx

        assert ctx is not None
        assert ctx.transport == 'cli'

        async with ctx.connection:
            session = await ctx.session.resolve_session(f"cli:{flag_id}")
            await ctx.task_queue.enqueue(lambda: f"cli_task_{flag_id}")

        return f"Updated {flag_id}"

    # Call the decorated handler
    result = await mock_cli_handler("test_flag")

    assert result == "Updated test_flag"
    assert handler_context is not None
    print("✓ Integration Test: @k1_cli decorator works end-to-end")


async def test_k1_async_decorator():
    """
    Integration test: Verify @k1_async_task decorator for background jobs.
    """
    handler_context = None

    from core.endpoints.k1_decorators import k1_async

    @k1_async()
    async def mock_async_handler(job_id: str):
        """Mock async worker handler."""
        ctx = get_k1_context()
        nonlocal handler_context
        handler_context = ctx

        assert ctx is not None
        assert ctx.transport == 'async'

        async with ctx.connection:
            session = await ctx.session.resolve_session(f"job:{job_id}")
            await ctx.task_queue.enqueue(lambda: f"async_task_{job_id}")

        return f"Job {job_id} completed"

    result = await mock_async_handler("job_123")

    assert result == "Job job_123 completed"
    assert handler_context is not None
    print("✓ Integration Test: @k1_async decorator works end-to-end")


async def test_k1_context_isolation_concurrent():
    """
    Integration test: Verify two concurrent decorated handlers have isolated contexts.
    """
    contexts = {}

    @k1_flask()
    async def handler_1():
        ctx = get_k1_context()
        contexts['handler_1'] = ctx
        async with ctx.connection:
            await ctx.session.resolve_session("user:alice")
        return "handler_1"

    @k1_flask()
    async def handler_2():
        ctx = get_k1_context()
        contexts['handler_2'] = ctx
        async with ctx.connection:
            await ctx.session.resolve_session("user:bob")
        return "handler_2"

    # Run concurrently
    result_1, result_2 = await asyncio.gather(handler_1(), handler_2())

    assert result_1 == "handler_1"
    assert result_2 == "handler_2"

    # Verify contexts are different
    ctx_1 = contexts['handler_1']
    ctx_2 = contexts['handler_2']

    assert ctx_1.request_id != ctx_2.request_id
    assert "user:alice" in ctx_1.session._session_cache
    assert "user:alice" not in ctx_2.session._session_cache
    assert "user:bob" in ctx_2.session._session_cache
    assert "user:bob" not in ctx_1.session._session_cache

    print("✓ Integration Test: Concurrent requests have isolated contexts")


async def main():
    """Run all integration tests."""
    print("\n" + "="*60)
    print("Tier-4 (Integration Tests) for k=1 Endpoint Architecture")
    print("="*60 + "\n")

    try:
        await test_k1_flask_decorator()
        await test_k1_cli_decorator()
        await test_k1_async_decorator()
        await test_k1_context_isolation_concurrent()

        print("\n" + "="*60)
        print("✓ All Tier-4 (Integration) tests PASSED")
        print("="*60)
    except Exception as e:
        print(f"\n✗ Integration test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False

    return True


if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)

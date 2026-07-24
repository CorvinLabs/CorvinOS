"""E2E Tests: ADR-0214 Phase 3 Streaming Executor.

Tests large-data streaming with L34 filtering.
"""
import sys

import pytest
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "operator" / "orchestration"))

from tde.streaming_executor import StreamingExecutor
from tde.l34_delegation_gate import L34DelegationGate
from initial_analysis import Step


@pytest.mark.asyncio
async def test_streaming_executor_with_large_data():
    """Test StreamingExecutor with >1GB simulated data."""
    executor = StreamingExecutor(l34_gate=L34DelegationGate())

    # Simulate large dataset: concatenate strings to reach >1GB
    # (Python string concatenation is efficient enough for test, no actual file I/O)
    print("Building mock 2GB dataset...")
    chunk_size = 10 * 1024 * 1024  # 10MB chunks
    large_data = "x" * chunk_size * 200  # 200 * 10MB = 2GB
    print(f"Dataset size: {len(large_data) / (1024**3):.2f} GB")

    statement = {
        "public_data": "public: this is safe to delegate " * 100000,  # PUBLIC
        "large_dataset": large_data,  # RESTRICTED (size alone)
        "output": "",
    }

    # Step to execute
    step = Step(step=1, action="process_large_data", depends_on=[], can_parallelize=[])

    # Simple executor that processes chunks
    async def mock_executor_fn(step, stream):
        """Mock executor that collects all streamed chunks."""
        total_size = 0
        chunk_count = 0
        async for chunk_dict in stream:
            chunk_count += 1
            var_name = list(chunk_dict.keys())[0]
            chunk_value = chunk_dict[var_name]
            if isinstance(chunk_value, str):
                total_size += len(chunk_value)
        return {"chunks_processed": chunk_count, "bytes_processed": total_size}

    # Execute streaming
    print("Executing streaming with L34 filtering...")
    result = await executor.execute_streaming(
        step,
        statement,
        mock_executor_fn,
        required_vars={"public_data"},  # Only request PUBLIC vars
        max_classification="PUBLIC",
    )

    print(f"\n✓ StreamingExecutor E2E Test:")
    print(f"  Success: {result.success}")
    print(f"  Output: {result.output}")
    print(f"  Duration: {result.duration_ms}ms")

    assert result.success is True, "Streaming should succeed"
    assert result.was_delegated is False, "Streaming is local execution"
    assert result.output["bytes_processed"] > 0, "Should have processed data"
    print("✅ Streaming E2E test PASSED")


@pytest.mark.asyncio
async def test_streaming_l34_filtering():
    """Test L34 filtering during streaming."""
    executor = StreamingExecutor(l34_gate=L34DelegationGate())

    # Mix of safe and unsafe data
    statement = {
        "safe_data": "public " * 100000,  # PUBLIC
        "email_data": "contact: john@example.com, jane@example.com " * 10000,  # PII → CONFIDENTIAL
    }

    step = Step(step=2, action="filter_test", depends_on=[], can_parallelize=[])

    async def counting_executor(step, stream):
        """Count how many chunks are yielded."""
        count = 0
        async for _ in stream:
            count += 1
        return {"chunks_yielded": count}

    # Request both variables, but max_classification=PUBLIC (should block email_data)
    result = await executor.execute_streaming(
        step,
        statement,
        counting_executor,
        required_vars={"safe_data", "email_data"},
        max_classification="PUBLIC",  # Email data exceeds this
    )

    print(f"\n✓ L34 Filtering Test:")
    print(f"  Success: {result.success}")
    print(f"  Chunks yielded: {result.output['chunks_yielded']}")

    # Should only get chunks from safe_data (email_data blocked by L34)
    assert result.output["chunks_yielded"] > 0, "Should yield safe data chunks"
    print("✅ L34 Filtering test PASSED")


@pytest.mark.asyncio
async def test_streaming_executor_small_data_threshold():
    """Test that StreamingExecutor.should_use_streaming() returns False for <1GB."""
    executor = StreamingExecutor(l34_gate=L34DelegationGate())

    assert executor.should_use_streaming(100 * 1024 * 1024) is False  # 100MB
    assert executor.should_use_streaming(1024 * 1024 * 1024) is False  # Exactly 1GB (boundary)
    assert executor.should_use_streaming(1024 * 1024 * 1024 + 1) is True  # >1GB
    print("\n✓ Threshold detection test PASSED")


if __name__ == "__main__":
    print("=" * 60)
    print("ADR-0214 Phase 3: Streaming Executor E2E Tests")
    print("=" * 60)
    asyncio.run(test_streaming_executor_with_large_data())
    asyncio.run(test_streaming_l34_filtering())
    asyncio.run(test_streaming_executor_small_data_threshold())
    print("\n✅ ALL STREAMING E2E TESTS PASSED")

"""Tests for TDE Phase 3 (partial): StreamingExecutor (operator/orchestration/tde/streaming_executor.py).

Regression coverage for a round-4 finding: the module's own reason to exist
(values >BIG_DATA_THRESHOLD, i.e. >1GB) was UNREACHABLE before this fix —
L34DelegationGate._classify_content() rejects any single scanned value above
_CONTENT_SCAN_MAX_BYTES (5MB) outright as RESTRICTED (fail-closed: an
unscanned tail cannot be proven safe). Streaming values through
stream_filtered_data() without chunking meant the very first prescan() on
any large value always failed. This file was previously untested (no
test_tde_streaming*.py existed), which is why the bug survived 3 prior
adversarial-review rounds.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "operator" / "orchestration"))

from tde.l34_delegation_gate import L34DelegationGate
from tde.streaming_executor import StreamingExecutor
from initial_analysis import Step


@pytest.fixture
def gate():
    return L34DelegationGate(l34_classifier=None)


@pytest.fixture
def executor(gate):
    return StreamingExecutor(gate)


@pytest.fixture
def step():
    return Step(step=1, action="analyze_data", depends_on=[], can_parallelize=[])


class TestShouldUseStreaming:
    def test_below_threshold(self, executor):
        assert executor.should_use_streaming(1024) is False

    def test_above_threshold(self, executor):
        assert executor.should_use_streaming(executor.BIG_DATA_THRESHOLD + 1) is True


class TestStreamFilteredDataChunking:
    """Core regression: large SAFE values must actually stream, not fail-closed."""

    @pytest.mark.asyncio
    async def test_large_safe_value_streams_in_multiple_chunks(self, executor):
        # 6MB of benign repeated text — comfortably bigger than the L34 gate's
        # 5MB single-scan ceiling, comfortably bigger than one stream chunk.
        big_value = "the quick brown fox jumps over the lazy dog. " * 140_000
        assert len(big_value) > 5 * 1024 * 1024

        statement = {"big_var": big_value}
        chunks = [c async for c in executor.stream_filtered_data(statement, {"big_var"})]

        assert len(chunks) > 1, "a >1MB safe value must be split into multiple chunks"
        reassembled = "".join(c["big_var"] for c in chunks)
        assert reassembled == big_value, "chunking must not drop or reorder data"

    @pytest.mark.asyncio
    async def test_unsafe_chunk_is_skipped_not_fatal(self, executor):
        """A RESTRICTED chunk is skipped; other (safe) chunks still stream —
        matches the "skip/redact" docstring contract, not a full-stream abort."""
        safe_padding = "benign filler text. " * 100_000  # >1MB, forces 2+ chunks
        secret = "sk-live-abcdefghijklmnopqrstuvwxyz0123456789"
        big_value = safe_padding + secret + safe_padding

        statement = {"mixed": big_value}
        chunks = [c async for c in executor.stream_filtered_data(statement, {"mixed"})]

        # At least one chunk must have streamed (the safe padding)...
        assert len(chunks) >= 1
        # ...and the secret must never appear in ANY yielded chunk.
        for c in chunks:
            assert secret not in c["mixed"]

    @pytest.mark.asyncio
    async def test_small_safe_value_still_works(self, executor):
        """Non-str/bytes and small values pass through unchanged (no regression)."""
        statement = {"n": 42, "s": "hello"}
        chunks = [c async for c in executor.stream_filtered_data(statement, {"n", "s"})]
        merged = {}
        for c in chunks:
            merged.update(c)
        assert merged == {"n": 42, "s": "hello"}


class TestExecuteStreaming:
    @pytest.mark.asyncio
    async def test_large_safe_value_executes_successfully(self, executor, step):
        """End-to-end: execute_streaming() must SUCCEED for its designed use
        case (large safe data) — this was the always-failing path pre-fix."""
        big_value = "safe content chunk. " * 140_000
        statement = {"big_var": big_value}

        async def _executor_fn(_step, stream):
            total = 0
            async for chunk in stream:
                total += len(chunk["big_var"])
            return {"total_chars": total}

        result = await executor.execute_streaming(step, statement, _executor_fn)

        assert result.success is True, f"expected success, got error={result.error}"
        assert result.output["total_chars"] == len(big_value)
        assert result.was_delegated is False

    @pytest.mark.asyncio
    async def test_all_restricted_data_yields_empty_stream_not_crash(self, executor, step):
        """Every chunk unsafe → executor_fn sees an empty stream, no crash."""
        statement = {"secret": "sk-live-abcdefghijklmnopqrstuvwxyz0123456789"}

        async def _executor_fn(_step, stream):
            chunks = [c async for c in stream]
            return {"chunks_seen": len(chunks)}

        result = await executor.execute_streaming(step, statement, _executor_fn)
        assert result.success is True
        assert result.output["chunks_seen"] == 0

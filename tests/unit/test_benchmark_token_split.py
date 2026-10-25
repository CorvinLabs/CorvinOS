"""The token-savings benchmark sums all four billed token classes through the one
normaliser every span emitter shares (``engine_span.usage_split``, ADR-0759).

``core/learning/token_accounting.py`` — a second copy of that sum — was retired
by ADR-2282; this pins that the benchmark still reads cache-creation and
cache-read input, which reading ``input_tokens`` alone undercounts by ~99.99 %
on a cached turn (measured: 2 vs 24 433 + 38 060)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "benchmark" / "token-savings"))

import run_benchmark  # noqa: E402


def test_benchmark_reads_all_four_classes_from_usage_split():
    usage = {"input_tokens": 2, "cache_read_input_tokens": 24433,
             "cache_creation_input_tokens": 38060, "output_tokens": 512}
    assert run_benchmark.token_components(usage) == {
        "fresh_input": 2, "cache_creation": 38060, "cache_read": 24433, "output": 512,
    }


def test_benchmark_uses_the_shared_normaliser():
    import engine_span

    assert run_benchmark.usage_split is engine_span.usage_split
    assert not hasattr(run_benchmark, "total_tokens")


def test_malformed_usage_counts_zero():
    assert run_benchmark.token_components(None) == {
        "fresh_input": 0, "cache_creation": 0, "cache_read": 0, "output": 0,
    }
    assert run_benchmark.token_components({"input_tokens": True, "output_tokens": "7"}) == {
        "fresh_input": 0, "cache_creation": 0, "cache_read": 0, "output": 0,
    }

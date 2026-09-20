"""
Tests for scientific LLM benchmarking infrastructure.

Tests the complete pipeline: task loading, baseline runs, routing runs,
quality grading, metrics calculation, and report generation.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import Mock, patch
from core.benchmarking import (
    BenchmarkConfig,
    calculate_metrics,
    TokenMetrics,
    LatencyMetrics,
    QualityMetrics,
)
from core.benchmarking.golden_truth import LLMJudge


@pytest.fixture
def sample_baseline_runs():
    """Sample baseline (Opus) runs."""
    return [
        {
            "task_id": "task_1",
            "category": "qa",
            "tier": "simple",
            "model": "claude-opus-5",
            "input_tokens": 20,
            "output_tokens": 10,
            "total_tokens": 30,
            "latency_ms": 150,
            "output_text": "The answer is 4.",
            "status": "success",
            "timestamp": "2026-09-20T12:00:00.000Z",
        },
        {
            "task_id": "task_2",
            "category": "code",
            "tier": "medium",
            "model": "claude-opus-5",
            "input_tokens": 100,
            "output_tokens": 200,
            "total_tokens": 300,
            "latency_ms": 800,
            "output_text": "def reverse_list(lst):\n    return lst[::-1]",
            "status": "success",
            "timestamp": "2026-09-20T12:00:01.000Z",
        },
        {
            "task_id": "task_3",
            "category": "code",
            "tier": "complex",
            "model": "claude-opus-5",
            "input_tokens": 500,
            "output_tokens": 1500,
            "total_tokens": 2000,
            "latency_ms": 2000,
            "output_text": "[Complete design document...]",
            "status": "success",
            "timestamp": "2026-09-20T12:00:02.000Z",
        },
    ]


@pytest.fixture
def sample_routing_runs():
    """Sample routing runs."""
    return [
        {
            "task_id": "task_1",
            "category": "qa",
            "tier": "simple",
            "model": "claude-haiku-4-5",
            "routing_decision": {
                "model": "claude-haiku-4-5",
                "engine": "native",
                "tier": "simple",
                "confidence": 0.95,
                "signal_strength": "strong",
            },
            "input_tokens": 20,
            "output_tokens": 10,
            "total_tokens": 30,
            "latency_ms": 100,
            "output_text": "The answer is 4.",
            "status": "success",
            "timestamp": "2026-09-20T12:05:00.000Z",
        },
        {
            "task_id": "task_2",
            "category": "code",
            "tier": "medium",
            "model": "claude-sonnet-5",
            "routing_decision": {
                "model": "claude-sonnet-5",
                "engine": "native",
                "tier": "medium",
                "confidence": 0.80,
                "signal_strength": "medium",
            },
            "input_tokens": 100,
            "output_tokens": 180,
            "total_tokens": 280,
            "latency_ms": 600,
            "output_text": "def reverse_list(lst):\n    return lst[::-1]",
            "status": "success",
            "timestamp": "2026-09-20T12:05:01.000Z",
        },
        {
            "task_id": "task_3",
            "category": "code",
            "tier": "complex",
            "model": "claude-opus-5",
            "routing_decision": {
                "model": "claude-opus-5",
                "engine": "native",
                "tier": "complex",
                "confidence": 0.98,
                "signal_strength": "strong",
            },
            "input_tokens": 500,
            "output_tokens": 1400,
            "total_tokens": 1900,
            "latency_ms": 1800,
            "output_text": "[Complete design document...]",
            "status": "success",
            "timestamp": "2026-09-20T12:05:02.000Z",
        },
    ]


@pytest.fixture
def sample_quality_scores():
    """Sample quality judge scores."""
    return [
        {
            "task_id": "task_1",
            "category": "qa",
            "baseline_score": 2,
            "routed_score": 2,
            "timestamp": "2026-09-20T12:10:00.000Z",
        },
        {
            "task_id": "task_2",
            "category": "code",
            "baseline_score": 2,
            "routed_score": 2,
            "timestamp": "2026-09-20T12:10:01.000Z",
        },
        {
            "task_id": "task_3",
            "category": "code",
            "baseline_score": 2,
            "routed_score": 1,
            "timestamp": "2026-09-20T12:10:02.000Z",
        },
    ]


def test_metrics_calculation_token_savings(
    sample_baseline_runs,
    sample_routing_runs,
    sample_quality_scores,
):
    """Test token savings calculation."""
    metrics = calculate_metrics(
        baseline_runs=sample_baseline_runs,
        routing_runs=sample_routing_runs,
        quality_scores=sample_quality_scores,
        baseline_timestamp="2026-09-20T12:00:00.000Z",
        routing_timestamp="2026-09-20T12:05:00.000Z",
    )

    assert metrics.token_metrics.total_tokens_opus == 2330
    assert metrics.token_metrics.total_tokens_routed == 2210
    assert metrics.token_metrics.mean_savings_pct > 0


def test_metrics_calculation_latency(
    sample_baseline_runs,
    sample_routing_runs,
    sample_quality_scores,
):
    """Test latency improvement calculation."""
    metrics = calculate_metrics(
        baseline_runs=sample_baseline_runs,
        routing_runs=sample_routing_runs,
        quality_scores=sample_quality_scores,
        baseline_timestamp="2026-09-20T12:00:00.000Z",
        routing_timestamp="2026-09-20T12:05:00.000Z",
    )

    assert metrics.latency_metrics.mean_improvement_pct > 0
    assert metrics.latency_metrics.p50_latency_opus_ms > 0
    assert metrics.latency_metrics.p99_latency_opus_ms > 0


def test_metrics_calculation_quality(
    sample_baseline_runs,
    sample_routing_runs,
    sample_quality_scores,
):
    """Test quality accuracy calculation."""
    metrics = calculate_metrics(
        baseline_runs=sample_baseline_runs,
        routing_runs=sample_routing_runs,
        quality_scores=sample_quality_scores,
        baseline_timestamp="2026-09-20T12:00:00.000Z",
        routing_timestamp="2026-09-20T12:05:00.000Z",
    )

    assert metrics.quality_metrics.accuracy_opus_pct == 100.0
    assert metrics.quality_metrics.accuracy_routed_pct >= 66.0


def test_llm_judge_creation():
    """Test LLM judge initialization."""
    judge = LLMJudge()

    assert judge.model == "claude-opus-5"
    assert "code" in judge.grading_rubrics
    assert "qa" in judge.grading_rubrics


def test_benchmark_config_creation():
    """Test BenchmarkConfig dataclass."""
    config = BenchmarkConfig(
        dataset_path="tests/benchmarking/datasets/benchmark_tasks_v1.jsonl",
        baseline_model="claude-opus-5",
    )

    assert config.dataset_path == "tests/benchmarking/datasets/benchmark_tasks_v1.jsonl"
    assert config.baseline_model == "claude-opus-5"
    assert config.temperature == 0.0
    assert config.max_tokens == 4096

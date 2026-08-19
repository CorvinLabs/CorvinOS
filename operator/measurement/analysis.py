"""Analysis pipeline for canary vs control group comparison.

ADR-0392 § Phase 1: Measurement

Loads metrics from JSON lines, splits by group (control/canary), and
computes statistical summaries for decision-making.

Example:
    >>> metrics = load_metrics("/tmp/metrics.jsonl")
    >>> baseline, canary = split_by_group(metrics)
    >>> comparison = compare_groups(baseline, canary)
    >>> print(comparison["context_reduction_pct"])
    35.2
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, stdev
from typing import Any

__all__ = [
    "load_metrics",
    "split_by_group",
    "compare_groups",
    "generate_report",
]


@dataclass(frozen=True)
class TokenMetricRecord:
    """Simplified metric record for analysis."""

    timestamp: str
    turn_id: str
    tenant_id: str
    context_size_before: int
    context_size_after: int
    tokens_saved: int
    latency_ms: int
    group: str  # "canary" or "control"


def load_metrics(path: str | Path) -> list[TokenMetricRecord]:
    """Load metrics from a JSON lines file.

    Args:
        path: Path to metrics.jsonl file.

    Returns:
        List of TokenMetricRecord objects.
    """
    metrics = []
    path = Path(path)

    if not path.is_file():
        return metrics

    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                metric = TokenMetricRecord(
                    timestamp=data["timestamp"],
                    turn_id=data["turn_id"],
                    tenant_id=data["tenant_id"],
                    context_size_before=data["context_size_before"],
                    context_size_after=data["context_size_after"],
                    tokens_saved=data["tokens_saved"],
                    latency_ms=data["latency_ms"],
                    group=data.get("group", "unknown"),
                )
                metrics.append(metric)
    except Exception:  # noqa: BLE001 — corrupt file → skip bad lines
        pass

    return metrics


def split_by_group(
    metrics: list[TokenMetricRecord],
) -> tuple[list[TokenMetricRecord], list[TokenMetricRecord]]:
    """Split metrics into control and canary groups.

    Args:
        metrics: List of metrics to split.

    Returns:
        Tuple of (control_metrics, canary_metrics).
    """
    control = [m for m in metrics if m.group == "control"]
    canary = [m for m in metrics if m.group == "canary"]
    return control, canary


def compare_groups(
    baseline: list[TokenMetricRecord],
    canary: list[TokenMetricRecord],
) -> dict[str, Any]:
    """Compare statistics between baseline (control) and canary groups.

    Args:
        baseline: Control group metrics (should have Phase 1-3 OFF).
        canary: Canary group metrics (should have Phase 1-3 ON).

    Returns:
        Dict with comparative statistics.
    """
    if not baseline or not canary:
        return {
            "baseline_turns": len(baseline),
            "canary_turns": len(canary),
            "error": "insufficient data for comparison",
        }

    # Context reduction percentage
    baseline_reductions = [
        (m.tokens_saved / m.context_size_before * 100)
        if m.context_size_before > 0
        else 0.0
        for m in baseline
    ]
    canary_reductions = [
        (m.tokens_saved / m.context_size_before * 100)
        if m.context_size_before > 0
        else 0.0
        for m in canary
    ]

    # Latency
    baseline_latencies = [m.latency_ms for m in baseline]
    canary_latencies = [m.latency_ms for m in canary]

    # Tokens saved
    baseline_tokens = [m.tokens_saved for m in baseline]
    canary_tokens = [m.tokens_saved for m in canary]

    # Context size before
    baseline_sizes = [m.context_size_before for m in baseline]
    canary_sizes = [m.context_size_before for m in canary]

    result = {
        "baseline_turns": len(baseline),
        "canary_turns": len(canary),
        # Context reduction (the key metric)
        "baseline_avg_reduction_pct": mean(baseline_reductions),
        "canary_avg_reduction_pct": mean(canary_reductions),
        "reduction_improvement_pct": (
            (mean(canary_reductions) - mean(baseline_reductions))
            if mean(baseline_reductions) > 0
            else mean(canary_reductions)
        ),
        # Latency impact
        "baseline_avg_latency_ms": mean(baseline_latencies),
        "canary_avg_latency_ms": mean(canary_latencies),
        "latency_delta_ms": mean(canary_latencies) - mean(baseline_latencies),
        # Tokens saved
        "baseline_avg_tokens_saved": mean(baseline_tokens),
        "canary_avg_tokens_saved": mean(canary_tokens),
        "tokens_saved_improvement": (
            mean(canary_tokens) - mean(baseline_tokens)
        ),
        # Context size (absolute)
        "baseline_avg_context_size": mean(baseline_sizes),
        "canary_avg_context_size": mean(canary_sizes),
        "context_size_reduction": (
            mean(baseline_sizes) - mean(canary_sizes)
        ),
    }

    # P95 latency
    if len(baseline_latencies) >= 20 and len(canary_latencies) >= 20:
        sorted_baseline = sorted(baseline_latencies)
        sorted_canary = sorted(canary_latencies)
        baseline_p95_idx = int(len(sorted_baseline) * 0.95)
        canary_p95_idx = int(len(sorted_canary) * 0.95)
        result["baseline_p95_latency_ms"] = sorted_baseline[baseline_p95_idx]
        result["canary_p95_latency_ms"] = sorted_canary[canary_p95_idx]
        result["p95_latency_delta_ms"] = (
            sorted_canary[canary_p95_idx] - sorted_baseline[baseline_p95_idx]
        )

    # Stddev for variability (if enough samples)
    if len(baseline_latencies) > 1:
        result["baseline_latency_stddev"] = stdev(baseline_latencies)
    if len(canary_latencies) > 1:
        result["canary_latency_stddev"] = stdev(canary_latencies)

    return result


def generate_report(path: str | Path) -> dict[str, Any]:
    """Load metrics and generate a full comparison report.

    Args:
        path: Path to metrics.jsonl file.

    Returns:
        Dict with 'baseline', 'canary', 'comparison', and 'recommendation'.
    """
    metrics = load_metrics(path)
    baseline, canary = split_by_group(metrics)
    comparison = compare_groups(baseline, canary)

    # Simple recommendation logic
    recommendation = "CONTINUE"
    if comparison.get("error"):
        recommendation = "COLLECT_MORE_DATA"
    elif len(baseline) < 100 or len(canary) < 100:
        recommendation = "COLLECT_MORE_DATA"
    elif (
        comparison.get("reduction_improvement_pct", 0) < 10
        and comparison.get("latency_delta_ms", 0) > 50
    ):
        recommendation = "INVESTIGATE_LATENCY_IMPACT"
    elif comparison.get("reduction_improvement_pct", 0) < 5:
        recommendation = "MARGINAL_IMPROVEMENT"

    return {
        "timestamp": Path(path).stat().st_mtime,
        "baseline_summary": _summarize_group(baseline),
        "canary_summary": _summarize_group(canary),
        "comparison": comparison,
        "recommendation": recommendation,
    }


def _summarize_group(metrics: list[TokenMetricRecord]) -> dict[str, Any]:
    """Summarize a single group."""
    if not metrics:
        return {"turns": 0}

    latencies = [m.latency_ms for m in metrics]
    tokens_saved = [m.tokens_saved for m in metrics]
    sizes_before = [m.context_size_before for m in metrics]

    return {
        "turns": len(metrics),
        "avg_latency_ms": mean(latencies),
        "avg_tokens_saved": mean(tokens_saved),
        "avg_context_size_before": mean(sizes_before),
        "min_latency_ms": min(latencies),
        "max_latency_ms": max(latencies),
    }

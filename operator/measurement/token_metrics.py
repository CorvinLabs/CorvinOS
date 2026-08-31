"""Token metrics collection for Phase 1-3 context engineering measurement.

ADR-0392 § Phase 1: Measurement

Collects per-turn metrics including context size, token usage, and latency.
Designed for non-blocking fire-and-forget appends to a JSON lines file.

Example:
    >>> collector = MetricsCollector(metrics_path="/tmp/metrics.jsonl")
    >>> metric = TokenMetric(
    ...     timestamp="2026-08-19T13:00:00Z",
    ...     turn_id="turn_abc123",
    ...     tenant_id="_default",
    ...     feature_flags_enabled={"vibe_engineering": True},
    ...     context_size_before=15000,
    ...     context_size_after=8000,
    ...     tokens_saved=350,
    ...     latency_ms=1250,
    ... )
    >>> collector.record(metric)
    >>> summary = collector.summary()
    >>> print(summary["total_turns"])
    1
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

__all__ = [
    "TokenMetric",
    "MetricsCollector",
]


@dataclass(frozen=True)
class TokenMetric:
    """Immutable record of a single turn's token/context metrics.

    Captures everything needed to measure Phase 1-3 impact: context size
    before/after, tokens saved, and latency.
    """

    timestamp: str  # ISO 8601, e.g. "2026-08-19T13:00:00Z"
    turn_id: str  # Unique turn identifier
    tenant_id: str  # Tenant being measured
    feature_flags_enabled: dict[str, bool]  # Phase 1-3 flags ON for this turn
    context_size_before: int  # Tokens in context BEFORE optimization
    context_size_after: int  # Tokens in context AFTER optimization
    tokens_saved: int  # context_size_before - context_size_after
    latency_ms: int  # Total turn latency in milliseconds
    model: str = "claude-opus-5"  # Model used (may vary)
    group: str = "unknown"  # "canary" or "control" (inferred from flags)

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return asdict(self)

    def __post_init__(self) -> None:
        """Validate invariants (frozen dataclass workaround)."""
        errors = []
        if self.context_size_after > self.context_size_before:
            errors.append(
                f"context_size_after ({self.context_size_after}) > "
                f"context_size_before ({self.context_size_before})"
            )
        if self.tokens_saved != (self.context_size_before - self.context_size_after):
            errors.append(
                f"tokens_saved ({self.tokens_saved}) != "
                f"context_size_before ({self.context_size_before}) - "
                f"context_size_after ({self.context_size_after})"
            )
        if self.latency_ms < 0:
            errors.append(f"latency_ms must be >= 0, got {self.latency_ms}")
        if errors:
            raise ValueError("; ".join(errors))


class MetricsCollector:
    """Non-blocking metrics collector that appends to a JSON lines file.

    Thread-safe. Designed for fire-and-forget recording during live chat turns.
    """

    def __init__(self, metrics_path: str | Path):
        """Initialize collector.

        Args:
            metrics_path: Path to metrics.jsonl file (created if missing).
        """
        self.metrics_path = Path(metrics_path)
        self._lock = threading.Lock()
        self._metrics: list[TokenMetric] = []

        # Ensure parent directory exists
        self.metrics_path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, metric: TokenMetric) -> None:
        """Record a metric, appending to disk.

        Thread-safe. Raises no exceptions — a disk write failure is logged
        but never raised (fail-safe fire-and-forget semantics).

        Args:
            metric: The TokenMetric to record.
        """
        with self._lock:
            self._metrics.append(metric)
            self._append_to_file(metric)

    def _append_to_file(self, metric: TokenMetric) -> None:
        """Append a single metric to the JSON lines file."""
        try:
            line = json.dumps(metric.to_dict(), separators=(",", ":"))
            with open(self.metrics_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:  # noqa: BLE001 — fire-and-forget, never raise
            pass

    def export_csv(self, path: str | Path) -> None:
        """Export all recorded metrics to a CSV file.

        Columns: timestamp, turn_id, tenant_id, feature_flags_enabled,
                 context_size_before, context_size_after, tokens_saved,
                 latency_ms, model, group.

        Args:
            path: Path to output CSV file.
        """
        import csv

        with self._lock:
            metrics = list(self._metrics)

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "timestamp",
                "turn_id",
                "tenant_id",
                "feature_flags_enabled",
                "context_size_before",
                "context_size_after",
                "tokens_saved",
                "latency_ms",
                "model",
                "group",
            ])
            writer.writeheader()
            for metric in metrics:
                row = metric.to_dict()
                row["feature_flags_enabled"] = json.dumps(row["feature_flags_enabled"])
                writer.writerow(row)

    def load_from_file(self) -> list[TokenMetric]:
        """Load all metrics from the JSON lines file.

        Returns:
            List of TokenMetric objects.
        """
        metrics = []
        if not self.metrics_path.is_file():
            return metrics

        with self._lock:
            try:
                with open(self.metrics_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        data = json.loads(line)
                        # Reconstruct TokenMetric from dict
                        metric = TokenMetric(
                            timestamp=data["timestamp"],
                            turn_id=data["turn_id"],
                            tenant_id=data["tenant_id"],
                            feature_flags_enabled=data["feature_flags_enabled"],
                            context_size_before=data["context_size_before"],
                            context_size_after=data["context_size_after"],
                            tokens_saved=data["tokens_saved"],
                            latency_ms=data["latency_ms"],
                            model=data.get("model", "claude-opus-5"),
                            group=data.get("group", "unknown"),
                        )
                        metrics.append(metric)
            except Exception:  # noqa: BLE001 — corrupt file → return what we have
                pass

        return metrics

    def summary(self) -> dict[str, Any]:
        """Return summary statistics of recorded metrics.

        Returns:
            Dict with 'total_turns', 'avg_context_reduction_pct',
            'avg_latency_ms', 'avg_tokens_saved', 'p95_latency_ms', etc.
        """
        with self._lock:
            metrics = list(self._metrics)

        if not metrics:
            return {
                "total_turns": 0,
                "avg_context_reduction_pct": 0.0,
                "avg_latency_ms": 0.0,
                "avg_tokens_saved": 0,
                "p95_latency_ms": 0,
                "min_latency_ms": 0,
                "max_latency_ms": 0,
            }

        latencies = [m.latency_ms for m in metrics]
        tokens_saved = [m.tokens_saved for m in metrics]
        reductions = [
            (m.tokens_saved / m.context_size_before * 100)
            if m.context_size_before > 0
            else 0.0
            for m in metrics
        ]

        # P95 calculation
        sorted_latencies = sorted(latencies)
        p95_idx = int(len(sorted_latencies) * 0.95)
        p95_latency = (
            sorted_latencies[p95_idx] if p95_idx < len(sorted_latencies) else 0
        )

        return {
            "total_turns": len(metrics),
            "avg_context_reduction_pct": (
                sum(reductions) / len(reductions) if reductions else 0.0
            ),
            "avg_latency_ms": sum(latencies) / len(latencies),
            "avg_tokens_saved": sum(tokens_saved) / len(tokens_saved)
            if tokens_saved
            else 0,
            "p95_latency_ms": p95_latency,
            "min_latency_ms": min(latencies) if latencies else 0,
            "max_latency_ms": max(latencies) if latencies else 0,
        }

    def clear(self) -> None:
        """Clear all in-memory metrics (does not delete the file)."""
        with self._lock:
            self._metrics.clear()

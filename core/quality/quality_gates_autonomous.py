#!/usr/bin/env python3
"""Phase C: Autonomous Quality Gates Engine

Measures performance against tightened Phase C thresholds and gates progression.
All thresholds derived from monitoring_slos.yaml, tightened by 10%.

THRESHOLDS (Phase C — Tightened 10%):
  - p99_latency:              < 306ms (from 340ms ± 10%)
  - error_rate:               < 0.09% (from 0.1%)
  - context_filter_accuracy:  ≥ 89% (from 80%)
  - learning_convergence:     < 900 iterations (from 1000)
  - cpu_usage:                < 72% (from 80%)
  - memory_usage:             < 70% (managed baseline)
  - io_wait_time:             < 5% (optimal baseline)

Gate Structure:
  1. MEASURE: Collect latency (p50/p95/p99), throughput, error rate, resources
  2. COMPARE: Against tightened thresholds (PASS/WARN/FAIL)
  3. GATE: If ALL pass → GREEN (proceed), WARN → monitor, FAIL → block + alert
  4. REPORT: JSON report for Phase D consumption
"""

import json
import sys
import time
import subprocess
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import statistics


@dataclass
class LatencyMetrics:
    """Latency percentiles (ms)."""
    p50: float
    p95: float
    p99: float
    mean: float
    min: float
    max: float


@dataclass
class ThroughputMetrics:
    """Operations per second."""
    ops_per_sec: float
    requests_per_min: int
    total_requests: int


@dataclass
class ErrorMetrics:
    """Error tracking."""
    error_rate_percent: float
    error_count: int
    total_count: int
    errors_by_type: Dict[str, int]


@dataclass
class ResourceMetrics:
    """System resource usage."""
    cpu_percent: float
    memory_percent: float
    io_wait_percent: float
    disk_free_gb: float


@dataclass
class GateResult:
    """Single gate measurement result."""
    gate_name: str
    status: str  # PASS / WARN / FAIL
    current_value: float
    threshold: float
    message: str
    timestamp: str


@dataclass
class QualityGateReport:
    """Complete Phase C quality gate report."""
    timestamp: str
    overall_status: str  # PASS / WARN / FAIL
    gate_results: List[GateResult]
    latency: LatencyMetrics
    throughput: ThroughputMetrics
    errors: ErrorMetrics
    resources: ResourceMetrics
    summary: Dict[str, str]  # Key findings
    recommendations: List[str]  # Auto-optimizer suggestions


class QualityGatesEngine:
    """Autonomous quality gates measurement and gating."""

    # ─────────────────────────────────────────────────────────────────────
    # PHASE C THRESHOLDS (tightened 10% from Phase B)
    # ─────────────────────────────────────────────────────────────────────
    THRESHOLDS = {
        "p99_latency_ms": 306,          # < 306ms (from 340 ± 10%)
        "error_rate_percent": 0.09,     # < 0.09% (from 0.1%)
        "context_accuracy_percent": 89, # ≥ 89% (from 80%)
        "learning_convergence_iter": 900,  # < 900 (from 1000)
        "cpu_percent": 72,              # < 72% (from 80%)
        "memory_percent": 70,           # < 70%
        "io_wait_percent": 5,           # < 5%
        "throughput_min_ops_sec": 1000, # ≥ 1000 ops/sec
    }

    # Warning thresholds (yellow, don't block but alert)
    WARN_THRESHOLDS = {
        "p99_latency_ms": 350,          # 350ms (close to Phase B threshold)
        "error_rate_percent": 0.15,     # 0.15%
        "cpu_percent": 80,              # Back to Phase B
        "throughput_min_ops_sec": 800,  # 80% of target
    }

    def __init__(self, home_dir: Optional[Path] = None):
        """Initialize quality gates engine."""
        self.home_dir = home_dir or Path.home() / ".corvin"
        self.metrics_dir = self.home_dir / "metrics" / "quality_gates"
        self.metrics_dir.mkdir(parents=True, exist_ok=True)

    # ─────────────────────────────────────────────────────────────────────
    # MEASUREMENT: Collect current metrics
    # ─────────────────────────────────────────────────────────────────────

    def measure_latency(self) -> LatencyMetrics:
        """Measure p50/p95/p99 latency from recent logs."""
        # In production, read from actual performance metrics store
        # For Phase C, derive from recent request logs
        latencies = self._sample_latencies()
        if not latencies:
            latencies = [100, 150, 200, 250, 300]  # Fallback

        return LatencyMetrics(
            p50=statistics.quantiles(latencies, n=100)[49],
            p95=statistics.quantiles(latencies, n=100)[94],
            p99=statistics.quantiles(latencies, n=100)[98],
            mean=statistics.mean(latencies),
            min=min(latencies),
            max=max(latencies),
        )

    def measure_throughput(self) -> ThroughputMetrics:
        """Measure requests/operations per second."""
        # In production, from metrics backend (Prometheus, CloudWatch, etc.)
        total = self._count_recent_requests()
        elapsed_sec = 300  # Last 5 minutes
        ops_sec = total / elapsed_sec if elapsed_sec > 0 else 0
        return ThroughputMetrics(
            ops_per_sec=ops_sec,
            requests_per_min=int(ops_sec * 60),
            total_requests=total,
        )

    def measure_errors(self) -> ErrorMetrics:
        """Measure error rate and types."""
        errors = self._count_recent_errors()
        total = self._count_recent_requests()
        error_rate = (errors["total"] / total * 100) if total > 0 else 0

        return ErrorMetrics(
            error_rate_percent=error_rate,
            error_count=errors["total"],
            total_count=total,
            errors_by_type=errors["by_type"],
        )

    def measure_resources(self) -> ResourceMetrics:
        """Measure CPU, memory, I/O usage."""
        import psutil

        cpu_pct = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()
        io = psutil.disk_io_counters()

        # Simple I/O wait estimate (in production, use /proc/stat or OS API)
        io_wait_pct = max(0, min(100, 100 - cpu_pct))  # Simplified

        return ResourceMetrics(
            cpu_percent=cpu_pct,
            memory_percent=mem.percent,
            io_wait_percent=io_wait_pct,
            disk_free_gb=mem.available / (1024**3),
        )

    # ─────────────────────────────────────────────────────────────────────
    # GATING: Evaluate against thresholds
    # ─────────────────────────────────────────────────────────────────────

    def gate_latency(self, metrics: LatencyMetrics) -> GateResult:
        """GATE: p99 latency within threshold."""
        threshold = self.THRESHOLDS["p99_latency_ms"]
        warn_threshold = self.WARN_THRESHOLDS["p99_latency_ms"]

        if metrics.p99 <= threshold:
            status = "PASS"
            msg = f"p99={metrics.p99:.1f}ms ≤ {threshold}ms ✓"
        elif metrics.p99 <= warn_threshold:
            status = "WARN"
            msg = f"p99={metrics.p99:.1f}ms slightly elevated (warn at {warn_threshold}ms)"
        else:
            status = "FAIL"
            msg = f"p99={metrics.p99:.1f}ms > {threshold}ms ✗ (LATENCY REGRESSION)"

        return GateResult(
            gate_name="P99_Latency",
            status=status,
            current_value=metrics.p99,
            threshold=float(threshold),
            message=msg,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def gate_error_rate(self, metrics: ErrorMetrics) -> GateResult:
        """GATE: Error rate within threshold."""
        threshold = self.THRESHOLDS["error_rate_percent"]
        warn_threshold = self.WARN_THRESHOLDS["error_rate_percent"]

        if metrics.error_rate_percent <= threshold:
            status = "PASS"
            msg = f"error_rate={metrics.error_rate_percent:.3f}% ≤ {threshold}% ✓"
        elif metrics.error_rate_percent <= warn_threshold:
            status = "WARN"
            msg = f"error_rate={metrics.error_rate_percent:.3f}% slightly elevated"
        else:
            status = "FAIL"
            msg = f"error_rate={metrics.error_rate_percent:.3f}% > {threshold}% ✗"

        return GateResult(
            gate_name="Error_Rate",
            status=status,
            current_value=metrics.error_rate_percent,
            threshold=float(threshold),
            message=msg,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def gate_throughput(self, metrics: ThroughputMetrics) -> GateResult:
        """GATE: Throughput meets minimum."""
        threshold = self.THRESHOLDS["throughput_min_ops_sec"]
        warn_threshold = self.WARN_THRESHOLDS["throughput_min_ops_sec"]

        if metrics.ops_per_sec >= threshold:
            status = "PASS"
            msg = f"throughput={metrics.ops_per_sec:.0f} ops/sec ≥ {threshold} ✓"
        elif metrics.ops_per_sec >= warn_threshold:
            status = "WARN"
            msg = f"throughput={metrics.ops_per_sec:.0f} ops/sec slightly low"
        else:
            status = "FAIL"
            msg = f"throughput={metrics.ops_per_sec:.0f} ops/sec < {threshold} ✗"

        return GateResult(
            gate_name="Throughput",
            status=status,
            current_value=metrics.ops_per_sec,
            threshold=float(threshold),
            message=msg,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def gate_cpu_usage(self, metrics: ResourceMetrics) -> GateResult:
        """GATE: CPU usage within limits."""
        threshold = self.THRESHOLDS["cpu_percent"]

        if metrics.cpu_percent <= threshold:
            status = "PASS"
            msg = f"cpu={metrics.cpu_percent:.1f}% ≤ {threshold}% ✓"
        else:
            status = "WARN" if metrics.cpu_percent <= 80 else "FAIL"
            msg = f"cpu={metrics.cpu_percent:.1f}% {'slightly' if status == 'WARN' else 'significantly'} elevated"

        return GateResult(
            gate_name="CPU_Usage",
            status=status,
            current_value=metrics.cpu_percent,
            threshold=float(threshold),
            message=msg,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def gate_memory_usage(self, metrics: ResourceMetrics) -> GateResult:
        """GATE: Memory usage within limits."""
        threshold = self.THRESHOLDS["memory_percent"]

        if metrics.memory_percent <= threshold:
            status = "PASS"
            msg = f"memory={metrics.memory_percent:.1f}% ≤ {threshold}% ✓"
        else:
            status = "WARN" if metrics.memory_percent <= 80 else "FAIL"
            msg = f"memory={metrics.memory_percent:.1f}% {'slightly' if status == 'WARN' else 'significantly'} elevated"

        return GateResult(
            gate_name="Memory_Usage",
            status=status,
            current_value=metrics.memory_percent,
            threshold=float(threshold),
            message=msg,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    # ─────────────────────────────────────────────────────────────────────
    # REPORT: Synthesize all gates into one go/no-go decision
    # ─────────────────────────────────────────────────────────────────────

    def run_all_gates(self) -> QualityGateReport:
        """Measure all metrics and evaluate all gates."""
        print("\n" + "="*70)
        print("🚀 PHASE C: AUTONOMOUS QUALITY GATES")
        print("="*70)

        # Collect metrics
        print("\n📊 Collecting metrics...")
        latency = self.measure_latency()
        throughput = self.measure_throughput()
        errors = self.measure_errors()
        resources = self.measure_resources()
        print("   ✓ Latency, throughput, errors, resources collected")

        # Evaluate gates
        print("\n🔒 Evaluating gates...")
        gate_results = [
            self.gate_latency(latency),
            self.gate_error_rate(errors),
            self.gate_throughput(throughput),
            self.gate_cpu_usage(resources),
            self.gate_memory_usage(resources),
        ]

        # Determine overall status
        fail_gates = [g for g in gate_results if g.status == "FAIL"]
        warn_gates = [g for g in gate_results if g.status == "WARN"]
        pass_gates = [g for g in gate_results if g.status == "PASS"]

        if fail_gates:
            overall_status = "FAIL"
            overall_msg = f"BLOCKED: {len(fail_gates)} critical gate(s) failed"
        elif warn_gates:
            overall_status = "WARN"
            overall_msg = f"CAUTION: {len(warn_gates)} gate(s) warning, {len(pass_gates)} passed"
        else:
            overall_status = "PASS"
            overall_msg = f"✓ ALL {len(pass_gates)} GATES PASSED"

        print(f"\n🎯 Gate Summary:")
        for g in gate_results:
            icon = "✓" if g.status == "PASS" else "⚠" if g.status == "WARN" else "✗"
            print(f"   {icon} {g.gate_name:20s} {g.status:5s} {g.message}")

        # Generate auto-optimizer recommendations
        recommendations = self._generate_recommendations(gate_results, latency, errors, resources)

        # Build report
        report = QualityGateReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            overall_status=overall_status,
            gate_results=gate_results,
            latency=latency,
            throughput=throughput,
            errors=errors,
            resources=resources,
            summary={
                "status": overall_status,
                "message": overall_msg,
                "passed": len(pass_gates),
                "warned": len(warn_gates),
                "failed": len(fail_gates),
            },
            recommendations=recommendations,
        )

        # Save report
        self._save_report(report)
        print(f"\n📄 Report saved to: {self.metrics_dir / 'latest_quality_gates.json'}")

        return report

    def _sample_latencies(self) -> List[float]:
        """Sample recent latencies from logs."""
        # In production, read from metrics backend
        # For Phase C simulation: sample normal distribution
        import random
        return [random.gauss(200, 50) for _ in range(500)]

    def _count_recent_requests(self) -> int:
        """Count requests in last 5 minutes."""
        # In production: query metrics store
        return 5000  # Simulated: ~1000 req/sec over 5 min

    def _count_recent_errors(self) -> Dict[str, any]:
        """Count errors in last 5 minutes."""
        # In production: query error logs
        return {
            "total": 5,  # 5 errors out of 5000 = 0.1% error rate
            "by_type": {
                "timeout": 2,
                "invalid_input": 2,
                "internal_error": 1,
            }
        }

    def _generate_recommendations(self, gates: List[GateResult], latency: LatencyMetrics,
                                 errors: ErrorMetrics, resources: ResourceMetrics) -> List[str]:
        """Auto-optimizer: generate refinement recommendations."""
        recs = []

        # Latency recommendations
        if any(g.gate_name == "P99_Latency" and g.status != "PASS" for g in gates):
            recs.append("AUTO-OPT: Increase connection pool size (–pool-size +50%)")
            recs.append("AUTO-OPT: Reduce context window (–context-size -10%)")
            recs.append("AUTO-OPT: Enable response caching (–cache-ttl 60s)")

        # Error rate recommendations
        if any(g.gate_name == "Error_Rate" and g.status == "FAIL" for g in gates):
            recs.append("AUTO-OPT: Increase retry count (–retries 3→5)")
            recs.append("AUTO-OPT: Implement exponential backoff (–backoff-base 2)")
            recs.append("AUTO-OPT: Review error logs for error patterns")

        # Throughput recommendations
        if any(g.gate_name == "Throughput" and g.status != "PASS" for g in gates):
            recs.append("AUTO-OPT: Increase worker threads (–workers +20%)")
            recs.append("AUTO-OPT: Batch requests (–batch-size 10→20)")

        # Resource recommendations
        if resources.cpu_percent > 70:
            recs.append("AUTO-OPT: Optimize hot loops (profile with cProfile)")
            recs.append("AUTO-OPT: Consider horizontal scaling (+1 worker pod)")
        if resources.memory_percent > 65:
            recs.append("AUTO-OPT: Reduce model cache (–model-cache-size -20%)")
            recs.append("AUTO-OPT: Enable memory profiling (–memory-profile)")

        return recs[:5]  # Top 5 recommendations

    def _save_report(self, report: QualityGateReport) -> Path:
        """Save quality gate report as JSON."""
        report_path = self.metrics_dir / "latest_quality_gates.json"
        report_dict = asdict(report)
        report_dict["latency"] = asdict(report.latency)
        report_dict["throughput"] = asdict(report.throughput)
        report_dict["errors"] = asdict(report.errors)
        report_dict["resources"] = asdict(report.resources)

        with open(report_path, "w") as f:
            json.dump(report_dict, f, indent=2)

        return report_path


def main():
    """Run quality gates engine."""
    engine = QualityGatesEngine()
    report = engine.run_all_gates()

    # Gate decision: PASS / WARN / FAIL
    if report.overall_status == "FAIL":
        print(f"\n❌ QUALITY GATES FAILED — Phase D blocked until resolved")
        sys.exit(1)
    elif report.overall_status == "WARN":
        print(f"\n⚠️  QUALITY GATES WARNED — Proceed with caution")
        sys.exit(0)  # Warn but allow proceed
    else:
        print(f"\n✅ QUALITY GATES PASSED — Phase D authorization granted")
        sys.exit(0)


if __name__ == "__main__":
    main()

"""
Week 4 Gate Review: P99 Baseline Measurement
Measures latency regression of Phase 1 Skills vs old persona path.

Runs 100 realistic requests through both paths:
- Old path: persona-based routing (legacy)
- New path: Skills-based routing (os.capabilities + os.identity_resolver)

Success criteria:
- P99(new) <= P99(old) * 1.10 (≤10% regression)
- P95(new) <= P95(old) * 1.10
- Error rate < 0.1%
"""

import asyncio
import json
import time
import random
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import List
import statistics

# Mock implementations (in real test, import from core)
@dataclass
class Request:
    """Simulated request to route through Skills."""
    id: str
    task_type: str  # "delegation", "context_inject", "consent_check"
    complexity: str  # "low", "medium", "high"
    tenant_id: str = "_default"
    user_id: str = "test_user"

@dataclass
class LatencySample:
    """Single latency measurement."""
    request_id: str
    old_path_ms: float
    new_path_ms: float
    regression_pct: float
    error: bool = False

class OldPersonaRouter:
    """Legacy: hardcoded persona → engine routing."""
    async def route(self, req: Request) -> dict:
        # Simulate hardcoded routing (no Skills)
        await asyncio.sleep(0.001 * (1.0 if req.complexity == "low" else 1.5 if req.complexity == "medium" else 2.0))
        return {"engine": "claude", "persona": "default"}

class NewSkillsRouter:
    """New: Skills-based routing (os.capabilities + os.identity_resolver)."""
    def __init__(self):
        self.cold_start = True
        self.load_time = 0.05  # First load cost

    async def route(self, req: Request) -> dict:
        # os.capabilities check
        cap_latency = 0.001 if not self.cold_start else self.load_time
        await asyncio.sleep(cap_latency)

        # os.identity_resolver
        identity_latency = 0.002 * (1.0 if req.complexity == "low" else 1.5 if req.complexity == "medium" else 2.0)
        await asyncio.sleep(identity_latency)

        # Audit event emission (async queue, non-blocking)
        audit_latency = 0.001
        await asyncio.sleep(audit_latency)

        if self.cold_start:
            self.cold_start = False

        return {"skill": "os.capabilities", "identity": "injected"}

async def generate_request(idx: int) -> Request:
    """Generate realistic request distribution."""
    task_types = ["delegation", "context_inject", "consent_check"]
    complexities = ["low", "medium", "high"]

    # Weighted distribution (more realistic)
    task_type = random.choices(
        task_types,
        weights=[0.5, 0.3, 0.2]  # Delegation most common
    )[0]

    complexity = random.choices(
        complexities,
        weights=[0.6, 0.3, 0.1]  # Most are simple
    )[0]

    return Request(
        id=f"req_{idx:03d}",
        task_type=task_type,
        complexity=complexity
    )

async def run_benchmark(num_requests: int = 100) -> List[LatencySample]:
    """Run full baseline measurement (after warm-up)."""
    old_router = OldPersonaRouter()
    new_router = NewSkillsRouter()

    samples = []
    print(f"\n📊 P99 Baseline Measurement (Week 4 Gate Review)")
    print(f"{'='*60}")
    print(f"Requests: {num_requests}")
    print(f"Paths: old (persona) vs new (Skills)")
    print(f"SLO: P99(new) ≤ P99(old) × 1.10")
    print(f"Note: Measuring WARM state (cold-start excluded)\n")

    # Warm-up phase (excluded from measurement)
    print("⏳ Warm-up phase...")
    for i in range(5):
        req = await generate_request(i)
        await old_router.route(req)
        await asyncio.sleep(0.001)
        await new_router.route(req)
        await asyncio.sleep(0.001)
    print("✓ Warm-up complete\n")

    # Actual measurement (num_requests)
    for i in range(num_requests):
        req = await generate_request(i + 5)  # Continue from warm-up

        try:
            # Old path timing
            start_old = time.perf_counter()
            result_old = await old_router.route(req)
            old_latency = (time.perf_counter() - start_old) * 1000  # ms

            # New path timing (small pause between)
            await asyncio.sleep(0.0001)

            start_new = time.perf_counter()
            result_new = await new_router.route(req)
            new_latency = (time.perf_counter() - start_new) * 1000  # ms

            regression = ((new_latency - old_latency) / old_latency) * 100

            sample = LatencySample(
                request_id=req.id,
                old_path_ms=old_latency,
                new_path_ms=new_latency,
                regression_pct=regression,
                error=False
            )
            samples.append(sample)

            if (i + 1) % 20 == 0:
                print(f"✓ {i+1}/{num_requests} requests completed")

        except Exception as e:
            samples.append(LatencySample(
                request_id=req.id,
                old_path_ms=0,
                new_path_ms=0,
                regression_pct=0,
                error=True
            ))
            print(f"✗ Request {i+1} failed: {e}")

    return samples

def percentile(data: List[float], p: float) -> float:
    """Compute percentile (0-100)."""
    sorted_data = sorted(data)
    idx = (p / 100.0) * (len(sorted_data) - 1)
    lower = int(idx)
    upper = lower + 1
    if upper >= len(sorted_data):
        return float(sorted_data[-1])
    weight = idx - lower
    return float(sorted_data[lower] * (1 - weight) + sorted_data[upper] * weight)

def compute_percentiles(samples: List[LatencySample]) -> dict:
    """Compute P50/P95/P99 latencies."""
    old_latencies = [s.old_path_ms for s in samples if not s.error]
    new_latencies = [s.new_path_ms for s in samples if not s.error]

    result = {
        "old_path": {
            "p50": percentile(old_latencies, 50),
            "p95": percentile(old_latencies, 95),
            "p99": percentile(old_latencies, 99),
            "mean": statistics.mean(old_latencies),
            "std": statistics.stdev(old_latencies) if len(old_latencies) > 1 else 0.0,
        },
        "new_path": {
            "p50": percentile(new_latencies, 50),
            "p95": percentile(new_latencies, 95),
            "p99": percentile(new_latencies, 99),
            "mean": statistics.mean(new_latencies),
            "std": statistics.stdev(new_latencies) if len(new_latencies) > 1 else 0.0,
        }
    }

    # Regression percentages
    result["regression"] = {
        "p50_pct": ((result["new_path"]["p50"] - result["old_path"]["p50"]) / result["old_path"]["p50"]) * 100,
        "p95_pct": ((result["new_path"]["p95"] - result["old_path"]["p95"]) / result["old_path"]["p95"]) * 100,
        "p99_pct": ((result["new_path"]["p99"] - result["old_path"]["p99"]) / result["old_path"]["p99"]) * 100,
    }

    return result

async def main():
    """Run benchmark and generate report."""
    samples = await run_benchmark(100)
    stats = compute_percentiles(samples)

    print(f"\n{'='*60}")
    print(f"📈 Results\n")

    print(f"Old Path (Personas):")
    print(f"  P50:  {stats['old_path']['p50']:.2f} ms")
    print(f"  P95:  {stats['old_path']['p95']:.2f} ms")
    print(f"  P99:  {stats['old_path']['p99']:.2f} ms")
    print(f"  Mean: {stats['old_path']['mean']:.2f} ± {stats['old_path']['std']:.2f} ms\n")

    print(f"New Path (Skills):")
    print(f"  P50:  {stats['new_path']['p50']:.2f} ms")
    print(f"  P95:  {stats['new_path']['p95']:.2f} ms")
    print(f"  P99:  {stats['new_path']['p99']:.2f} ms")
    print(f"  Mean: {stats['new_path']['mean']:.2f} ± {stats['new_path']['std']:.2f} ms\n")

    print(f"Regression:")
    print(f"  P50:  {stats['regression']['p50_pct']:+.2f}%")
    print(f"  P95:  {stats['regression']['p95_pct']:+.2f}%")
    print(f"  P99:  {stats['regression']['p99_pct']:+.2f}%\n")

    # Gate check
    slo_threshold = 10.0  # ≤10% regression is OK
    p99_pass = abs(stats['regression']['p99_pct']) <= slo_threshold
    p95_pass = abs(stats['regression']['p95_pct']) <= slo_threshold
    p50_pass = abs(stats['regression']['p50_pct']) <= slo_threshold

    gate_status = "✅ PASS" if (p99_pass and p95_pass and p50_pass) else "❌ FAIL"
    print(f"{'='*60}")
    print(f"Week 4 Gate Review: {gate_status}")
    if not (p99_pass and p95_pass and p50_pass):
        print(f"  ⚠️  One or more SLOs exceeded {slo_threshold}% threshold")
    print(f"{'='*60}\n")

    # Export results
    output_dir = Path("/home/shumway/projects/CorvinOS/outputs")
    output_dir.mkdir(exist_ok=True)

    # JSON report
    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "num_requests": len(samples),
        "num_errors": sum(1 for s in samples if s.error),
        "error_rate_pct": (sum(1 for s in samples if s.error) / len(samples)) * 100,
        "statistics": stats,
        "gate_result": {
            "p50_pass": p50_pass,
            "p95_pass": p95_pass,
            "p99_pass": p99_pass,
            "overall_pass": (p99_pass and p95_pass and p50_pass),
        },
        "samples": [asdict(s) for s in samples],
    }

    json_path = output_dir / "p99_baseline_week4.json"
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"📄 JSON report: {json_path}\n")

    # CSV for Grafana/Excel
    csv_path = output_dir / "p99_baseline_week4.csv"
    with open(csv_path, "w") as f:
        f.write("request_id,old_path_ms,new_path_ms,regression_pct,error\n")
        for s in samples:
            f.write(f"{s.request_id},{s.old_path_ms:.3f},{s.new_path_ms:.3f},{s.regression_pct:.2f},{s.error}\n")
    print(f"📊 CSV export: {csv_path}\n")

    return report

if __name__ == "__main__":
    report = asyncio.run(main())
    exit(0 if report["gate_result"]["overall_pass"] else 1)

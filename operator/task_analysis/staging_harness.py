"""Staging test harness for TaskEngine validation (Week 1 deployment)."""

import json
import time
import statistics
from datetime import datetime
from pathlib import Path
from typing import Dict, List
from .engine import TaskEngine, EngineResult


class StagingHarness:
    """Orchestrates Week 1 staging validation."""

    def __init__(self, test_data_path: str = "/staging/test_data.json"):
        """Initialize harness.

        Args:
            test_data_path: Path to test data JSON file.
        """
        self.engine = TaskEngine()
        self.test_data_path = Path(test_data_path)
        self.results: List[Dict] = []
        self.start_time = None
        self.end_time = None

    def load_test_data(self) -> List[Dict]:
        """Load test data from file.

        Returns:
            List of test cases with raw_task + expected outputs.

        Raises:
            FileNotFoundError: If test data file doesn't exist.
        """
        if not self.test_data_path.exists():
            raise FileNotFoundError(f"Test data not found: {self.test_data_path}")

        with open(self.test_data_path) as f:
            return json.load(f)

    def run_analysis(self, tasks: List[Dict]) -> None:
        """Route each task through TaskEngine.

        Args:
            tasks: List of test cases.
        """
        self.start_time = datetime.now()
        print(f"Starting analysis of {len(tasks)} tasks...")

        for i, task in enumerate(tasks, 1):
            raw_task = task.get("raw_task")
            if not raw_task:
                print(f"  [{i}/{len(tasks)}] ❌ Missing raw_task")
                task["result"] = None
                task["status"] = "skipped"
                continue

            try:
                start = time.perf_counter()
                result = self.engine.route_task(raw_task)
                elapsed_ms = (time.perf_counter() - start) * 1000

                task["result"] = {
                    "decision_target": result.decision_target.value,
                    "carve_out_reason": result.carve_out_reason,
                    "confidence": result.confidence,
                    "model_recommendation": result.model_recommendation,
                    "task_complexity": result.task_complexity,
                    "estimated_cost_usd": result.estimated_cost_usd,
                }
                task["latency_ms"] = elapsed_ms
                task["status"] = "success"

                # Progress indicator
                target_abbr = result.decision_target.value[0].upper()
                model_abbr = result.model_recommendation[0].upper()
                conf_pct = f"{result.confidence * 100:.0f}%"
                print(
                    f"  [{i:2d}/{len(tasks)}] ✅ {target_abbr}/{model_abbr} "
                    f"(conf={conf_pct}, latency={elapsed_ms:.0f}ms)"
                )

            except Exception as e:
                task["result"] = None
                task["status"] = "failed"
                task["error"] = str(e)
                print(f"  [{i}/{len(tasks)}] ❌ {type(e).__name__}: {str(e)}")

        self.end_time = datetime.now()
        print(f"\nAnalysis complete in {self.total_runtime:.1f}s")

    def validate_accuracy(self) -> Dict[str, float]:
        """Measure routing accuracy against expected values.

        Returns:
            Dictionary with accuracy metrics (overall, by type, by severity).
        """
        print("\n=== ACCURACY VALIDATION ===")

        metrics = {
            "total": 0,
            "correct": 0,
            "by_type": {},
            "by_severity": {},
        }

        for task in self.results:
            if task["status"] != "success":
                continue

            expected_target = task.get("expected_target")
            actual_target = task["result"]["decision_target"]
            task_type = task.get("expected_type", "unknown")
            severity = task.get("expected_severity", "unknown")

            metrics["total"] += 1

            # Track by type
            if task_type not in metrics["by_type"]:
                metrics["by_type"][task_type] = {"total": 0, "correct": 0}
            metrics["by_type"][task_type]["total"] += 1

            # Track by severity
            if severity not in metrics["by_severity"]:
                metrics["by_severity"][severity] = {"total": 0, "correct": 0}
            metrics["by_severity"][severity]["total"] += 1

            # Check if correct
            if actual_target == expected_target:
                metrics["correct"] += 1
                metrics["by_type"][task_type]["correct"] += 1
                metrics["by_severity"][severity]["correct"] += 1

        # Calculate percentages
        if metrics["total"] == 0:
            print("❌ No successful tasks to validate")
            return metrics

        overall_accuracy = metrics["correct"] / metrics["total"]
        print(f"\nOverall Accuracy: {overall_accuracy:.1%} ({metrics['correct']}/{metrics['total']})")

        if overall_accuracy >= 0.85:
            print("✅ PASS: Accuracy >= 85%")
        else:
            print(f"❌ FAIL: Accuracy = {overall_accuracy:.1%} (target >= 85%)")

        print("\nBy Type:")
        for typ in sorted(metrics["by_type"].keys()):
            data = metrics["by_type"][typ]
            type_acc = data["correct"] / data["total"] if data["total"] > 0 else 0.0
            print(f"  {typ:12s}: {type_acc:6.1%} ({data['correct']:2d}/{data['total']:2d})")

        print("\nBy Severity:")
        for severity in sorted(metrics["by_severity"].keys()):
            data = metrics["by_severity"][severity]
            sev_acc = data["correct"] / data["total"] if data["total"] > 0 else 0.0
            print(f"  {severity:12s}: {sev_acc:6.1%} ({data['correct']:2d}/{data['total']:2d})")

        metrics["overall_accuracy"] = overall_accuracy
        return metrics

    def validate_latency(self) -> Dict[str, float]:
        """Measure P95 latency.

        Returns:
            Dictionary with latency statistics (ms).
        """
        print("\n=== LATENCY VALIDATION ===")

        latencies = [
            task.get("latency_ms", 0)
            for task in self.results
            if task.get("status") == "success" and task.get("latency_ms")
        ]

        if not latencies:
            print("❌ No latency data available")
            return {}

        latencies_sorted = sorted(latencies)
        p95_idx = int(0.95 * len(latencies_sorted))
        p95 = latencies_sorted[p95_idx]

        metrics = {
            "min_ms": min(latencies),
            "median_ms": statistics.median(latencies),
            "mean_ms": statistics.mean(latencies),
            "p95_ms": p95,
            "max_ms": max(latencies),
        }

        print(f"\nLatency Stats (ms):")
        print(f"  Min:    {metrics['min_ms']:7.0f}")
        print(f"  Median: {metrics['median_ms']:7.0f}")
        print(f"  Mean:   {metrics['mean_ms']:7.0f}")
        print(f"  P95:    {metrics['p95_ms']:7.0f}")
        print(f"  Max:    {metrics['max_ms']:7.0f}")

        if p95 < 700:
            print(f"✅ PASS: P95 = {p95:.0f}ms (target < 700ms)")
        else:
            print(f"⚠️  WARN: P95 = {p95:.0f}ms (target < 700ms) — investigate bottleneck")

        return metrics

    def validate_contract_violations(self) -> int:
        """Check for contract violations.

        Returns:
            Count of tasks with contract violations (should be 0).
        """
        print("\n=== CONTRACT VIOLATION CHECK ===")

        violations = [task for task in self.results if task.get("error") and "contract" in task["error"].lower()]

        if violations:
            print(f"❌ FAIL: {len(violations)} contract violations detected:")
            for task in violations[:5]:  # Show first 5
                print(f"  - {task['id']}: {task['error'][:100]}")
            if len(violations) > 5:
                print(f"  ... and {len(violations) - 5} more")
        else:
            print("✅ PASS: Zero contract violations")

        return len(violations)

    def generate_report(self, output_path: str = "/staging/staging_report.json") -> None:
        """Generate final staging report.

        Args:
            output_path: Where to write the report.
        """
        print("\n=== GENERATING REPORT ===")

        accuracy = self.validate_accuracy()
        latency = self.validate_latency()
        violations = self.validate_contract_violations()

        report = {
            "timestamp": datetime.now().isoformat(),
            "total_tasks": len([t for t in self.results if t["status"] == "success"]),
            "accuracy": accuracy,
            "latency": latency,
            "contract_violations": violations,
            "total_runtime_seconds": self.total_runtime,
            "sign_off": {
                "accuracy_pass": accuracy.get("overall_accuracy", 0) >= 0.85,
                "latency_pass": latency.get("p95_ms", 1000) < 700,
                "violations_pass": violations == 0,
                "ready_for_canary": (
                    accuracy.get("overall_accuracy", 0) >= 0.85
                    and latency.get("p95_ms", 1000) < 700
                    and violations == 0
                ),
            },
        }

        # Print sign-off summary
        print("\n=== STAGING SIGN-OFF ===")
        print(f"Accuracy >= 85%:    {'✅' if report['sign_off']['accuracy_pass'] else '❌'}")
        print(f"P95 Latency < 700ms: {'✅' if report['sign_off']['latency_pass'] else '❌'}")
        print(f"Zero Violations:    {'✅' if report['sign_off']['violations_pass'] else '❌'}")
        print()

        if report["sign_off"]["ready_for_canary"]:
            print("🟢 READY FOR WEEK 2 CANARY (5% prod)")
        else:
            print("🔴 NOT READY — Address failures above and re-test")

        # Write report
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(report, f, indent=2)

        print(f"\nReport written to: {output_path}")

    @property
    def total_runtime(self) -> float:
        """Total runtime in seconds."""
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return 0.0

    def run(self, test_data_path: str = None) -> bool:
        """Full staging workflow.

        Args:
            test_data_path: Optional path to test data (overrides default).

        Returns:
            True if ready for Week 2 canary, False otherwise.
        """
        if test_data_path:
            self.test_data_path = Path(test_data_path)

        # Phase 1: Load
        print("Loading test data...")
        self.results = self.load_test_data()

        # Phase 2: Analyze
        self.run_analysis(self.results)

        # Phase 3: Validate
        accuracy = self.validate_accuracy()
        latency = self.validate_latency()
        violations = self.validate_contract_violations()

        self.generate_report()

        # Phase 4: Return readiness
        return (
            accuracy.get("overall_accuracy", 0) >= 0.85
            and latency.get("p95_ms", 1000) < 700
            and violations == 0
        )


if __name__ == "__main__":
    harness = StagingHarness()
    ready = harness.run()
    exit(0 if ready else 1)

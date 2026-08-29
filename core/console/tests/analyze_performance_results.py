"""Analyze and compare plugin marketplace performance test results.

Usage:
    python analyze_performance_results.py --baseline=phase2_baseline.json --current=phase4_results.json
    python analyze_performance_results.py --compare-across-builds *.json
    python analyze_performance_results.py --generate-report results/ --output report.html
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import statistics
from datetime import datetime

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# ─── Data Models ───────────────────────────────────────────────────────────

@dataclass
class MetricComparison:
    """Compare a metric across two test runs."""
    metric_name: str
    baseline_value: float
    current_value: float
    unit: str = "ms"
    regression: bool = False
    regression_pct: float = 0.0
    status: str = "✓ PASS"

    def __post_init__(self):
        if self.baseline_value > 0:
            self.regression_pct = (
                (self.current_value - self.baseline_value) / self.baseline_value * 100
            )
            self.regression = self.regression_pct > 20  # >20% increase is regression
            self.status = "✗ REGRESSION" if self.regression else "✓ PASS"


@dataclass
class TestRunResult:
    """Results from a single test run."""
    timestamp: str
    test_name: str
    duration_sec: float
    total_requests: int
    successful_requests: int
    failed_requests: int
    error_rate: float
    metrics: Dict[str, float]  # Endpoint -> latency metrics
    slo_compliance: Dict[str, bool]  # Metric -> passed


# ─── Performance Analyzer ──────────────────────────────────────────────────

class PerformanceAnalyzer:
    """Analyze performance test results."""

    def __init__(self):
        self.baseline_results = {}
        self.current_results = {}
        self.comparisons = []

    def load_baseline(self, filepath: Path) -> TestRunResult:
        """Load baseline test results."""
        with open(filepath) as f:
            data = json.load(f)

        result = TestRunResult(
            timestamp=data.get("timestamp", "unknown"),
            test_name=data.get("test_name", "baseline"),
            duration_sec=data.get("duration_sec", 0),
            total_requests=data.get("total_requests", 0),
            successful_requests=data.get("successful_requests", 0),
            failed_requests=data.get("failed_requests", 0),
            error_rate=data.get("error_rate", 0),
            metrics=data.get("metrics", {}),
            slo_compliance=data.get("slo_compliance", {}),
        )

        self.baseline_results = result
        logger.info(f"Loaded baseline: {filepath}")
        return result

    def load_current(self, filepath: Path) -> TestRunResult:
        """Load current test results."""
        with open(filepath) as f:
            data = json.load(f)

        result = TestRunResult(
            timestamp=data.get("timestamp", datetime.now().isoformat()),
            test_name=data.get("test_name", "current"),
            duration_sec=data.get("duration_sec", 0),
            total_requests=data.get("total_requests", 0),
            successful_requests=data.get("successful_requests", 0),
            failed_requests=data.get("failed_requests", 0),
            error_rate=data.get("error_rate", 0),
            metrics=data.get("metrics", {}),
            slo_compliance=data.get("slo_compliance", {}),
        )

        self.current_results = result
        logger.info(f"Loaded current results: {filepath}")
        return result

    def compare_results(self, regression_threshold_pct: float = 20.0) -> List[MetricComparison]:
        """Compare baseline vs current results."""
        if not self.baseline_results or not self.current_results:
            logger.error("Missing baseline or current results for comparison")
            return []

        comparisons = []

        # Compare each metric
        for metric_name, baseline_value in self.baseline_results.metrics.items():
            current_value = self.current_results.metrics.get(metric_name)

            if current_value is None:
                logger.warning(f"Metric {metric_name} not found in current results")
                continue

            comparison = MetricComparison(
                metric_name=metric_name,
                baseline_value=baseline_value,
                current_value=current_value,
            )

            comparisons.append(comparison)

        self.comparisons = comparisons
        return comparisons

    def print_comparison_table(self):
        """Print comparison results as table."""
        if not self.comparisons:
            logger.warning("No comparisons to print")
            return

        print("\n" + "="*100)
        print("PERFORMANCE COMPARISON REPORT")
        print("="*100)

        print(f"\nBaseline: {self.baseline_results.test_name} ({self.baseline_results.timestamp})")
        print(f"Current:  {self.current_results.test_name} ({self.current_results.timestamp})")

        # Metrics table
        print(f"\n{'Metric':<35} {'Baseline':<15} {'Current':<15} {'Change':<12} {'Status':<15}")
        print("-" * 100)

        for comp in sorted(self.comparisons, key=lambda c: c.regression_pct, reverse=True):
            baseline_str = f"{comp.baseline_value:.2f} {comp.unit}"
            current_str = f"{comp.current_value:.2f} {comp.unit}"
            change_str = f"{comp.regression_pct:+.1f}%"

            print(f"{comp.metric_name:<35} {baseline_str:<15} {current_str:<15} "
                  f"{change_str:<12} {comp.status:<15}")

        # Summary
        print("-" * 100)
        passed = sum(1 for c in self.comparisons if not c.regression)
        total = len(self.comparisons)

        print(f"\nSummary: {passed}/{total} metrics passed regression threshold ({100*passed/total:.0f}%)")

        if any(c.regression for c in self.comparisons):
            print("\n⚠ REGRESSIONS DETECTED:")
            for comp in self.comparisons:
                if comp.regression:
                    print(f"  - {comp.metric_name}: {comp.regression_pct:+.1f}%")

        print("="*100 + "\n")

    def check_slo_compliance(self) -> Dict[str, bool]:
        """Check SLO compliance for current results."""
        if not self.current_results:
            logger.error("No current results to check")
            return {}

        compliance = self.current_results.slo_compliance

        print("\n" + "="*80)
        print("SLO COMPLIANCE CHECK")
        print("="*80)

        print(f"\n{'SLO Metric':<40} {'Status':<20}")
        print("-" * 60)

        passed = 0
        for metric, is_met in compliance.items():
            status = "✓ PASS" if is_met else "✗ FAIL"
            print(f"{metric:<40} {status:<20}")
            if is_met:
                passed += 1

        print("-" * 60)
        print(f"Summary: {passed}/{len(compliance)} SLOs met ({100*passed/len(compliance):.0f}%)")
        print("="*80 + "\n")

        return compliance

    def generate_html_report(self, output_path: Path):
        """Generate HTML report of results."""
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Performance Test Report</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
        h1, h2 {{ color: #333; }}
        .section {{ background: white; padding: 20px; margin: 20px 0; border-radius: 5px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        table {{ width: 100%; border-collapse: collapse; margin: 15px 0; }}
        th, td {{ padding: 10px; text-align: left; border-bottom: 1px solid #ddd; }}
        th {{ background: #4CAF50; color: white; }}
        tr:hover {{ background: #f5f5f5; }}
        .pass {{ color: #4CAF50; font-weight: bold; }}
        .fail {{ color: #f44336; font-weight: bold; }}
        .regression {{ background: #fff3cd; }}
        .summary {{ font-size: 18px; margin: 15px 0; }}
        .metric-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
        .metric-card {{ background: #f9f9f9; padding: 15px; border-left: 4px solid #2196F3; }}
    </style>
</head>
<body>
    <h1>Plugin Marketplace Performance Test Report</h1>

    <div class="section">
        <h2>Test Summary</h2>
        <div class="metric-grid">
            <div class="metric-card">
                <strong>Baseline:</strong> {self.baseline_results.test_name}<br/>
                <small>{self.baseline_results.timestamp}</small>
            </div>
            <div class="metric-card">
                <strong>Current:</strong> {self.current_results.test_name}<br/>
                <small>{self.current_results.timestamp}</small>
            </div>
        </div>
    </div>

    <div class="section">
        <h2>Metric Comparison</h2>
        <table>
            <tr>
                <th>Metric</th>
                <th>Baseline</th>
                <th>Current</th>
                <th>Change</th>
                <th>Status</th>
            </tr>
"""

        for comp in sorted(self.comparisons, key=lambda c: c.regression_pct, reverse=True):
            status_class = "fail" if comp.regression else "pass"
            row_class = "regression" if comp.regression else ""

            html += f"""
            <tr class="{row_class}">
                <td>{comp.metric_name}</td>
                <td>{comp.baseline_value:.2f} {comp.unit}</td>
                <td>{comp.current_value:.2f} {comp.unit}</td>
                <td>{comp.regression_pct:+.1f}%</td>
                <td class="{status_class}">{comp.status}</td>
            </tr>
"""

        html += """
        </table>
    </div>

    <div class="section">
        <h2>SLO Compliance</h2>
"""

        for metric, is_met in self.current_results.slo_compliance.items():
            status_class = "pass" if is_met else "fail"
            status = "✓ PASS" if is_met else "✗ FAIL"
            html += f'<p><span class="{status_class}">{status}</span> {metric}</p>'

        html += """
    </div>

    <div class="section">
        <h2>Statistics</h2>
        <table>
            <tr>
                <th>Metric</th>
                <th>Value</th>
            </tr>
"""

        stats = [
            ("Total Requests", self.current_results.total_requests),
            ("Successful", self.current_results.successful_requests),
            ("Failed", self.current_results.failed_requests),
            ("Error Rate", f"{self.current_results.error_rate:.2%}"),
            ("Duration", f"{self.current_results.duration_sec:.2f}s"),
        ]

        for metric, value in stats:
            html += f"<tr><td>{metric}</td><td>{value}</td></tr>"

        html += """
        </table>
    </div>

</body>
</html>
"""

        with open(output_path, "w") as f:
            f.write(html)

        logger.info(f"HTML report saved to {output_path}")


# ─── CLI Interface ────────────────────────────────────────────────────────

def main():
    """Run analysis from command line."""
    import argparse

    parser = argparse.ArgumentParser(description="Analyze plugin marketplace performance results")
    parser.add_argument("--baseline", type=Path, help="Baseline test results JSON")
    parser.add_argument("--current", type=Path, help="Current test results JSON")
    parser.add_argument("--compare-across-builds", nargs="+", type=Path,
                       help="Compare multiple test results")
    parser.add_argument("--generate-report", type=Path,
                       help="Generate HTML report from results directory")
    parser.add_argument("--output", type=Path, default=Path("performance_report.html"),
                       help="Output file for report")
    parser.add_argument("--threshold", type=float, default=20.0,
                       help="Regression threshold percentage")

    args = parser.parse_args()

    analyzer = PerformanceAnalyzer()

    # Mode 1: Compare baseline vs current
    if args.baseline and args.current:
        logger.info("Comparing baseline vs current results")
        analyzer.load_baseline(args.baseline)
        analyzer.load_current(args.current)
        analyzer.compare_results(args.threshold)
        analyzer.print_comparison_table()
        analyzer.check_slo_compliance()
        analyzer.generate_html_report(args.output)

    # Mode 2: Compare across multiple builds
    elif args.compare_across_builds:
        logger.info(f"Comparing {len(args.compare_across_builds)} builds")
        results = []
        for filepath in args.compare_across_builds:
            analyzer_temp = PerformanceAnalyzer()
            result = analyzer_temp.load_current(filepath)
            results.append(result)
            logger.info(f"Loaded: {result.test_name}")

        # TODO: Implement cross-build comparison with trends

    # Mode 3: Generate report from directory
    elif args.generate_report:
        logger.info(f"Generating report from {args.generate_report}")
        # TODO: Implement batch report generation

    else:
        parser.print_help()


if __name__ == "__main__":
    main()

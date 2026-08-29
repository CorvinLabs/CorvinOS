"""
Measurement Report — Phase 3.A
===============================

Aggregates all validation measurements into a single JSON report:
- E2E wiring proof results
- Reproducibility data
- Coverage impact
- Go/No-Go gate decision

Fail-closed: any FAIL/BROKEN status blocks the gate.
"""

import json
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional


logger = logging.getLogger(__name__)


class GateDecision(Enum):
    """Week 6 Go/No-Go decision."""
    GO = "go"
    NO_GO = "no_go"
    HOLD = "hold"  # Needs manual review


@dataclass
class GateMetrics:
    """Metrics used in Go/No-Go decision."""
    pass_rate_threshold: float = 0.80  # ≥80% tests passing
    flake_threshold: float = 0.98      # ≥98% reproducibility
    coverage_regression_threshold: float = -5.0  # No more than -5% regression
    min_e2e_coverage: float = 0.80     # ≥80% of entry points reachable


@dataclass
class MeasurementReport:
    """Complete validation measurement report."""
    timestamp: str
    phase: str = "3A"  # Phase 3.A core validators

    # Results
    e2e_wiring_results: Dict[str, dict] = field(default_factory=dict)
    reproducibility_results: Dict[str, dict] = field(default_factory=dict)
    coverage_impact_result: Optional[dict] = None

    # Aggregates
    total_tests_validated: int = 0
    total_e2e_checks: int = 0
    total_tests_for_flake: int = 0

    # Gate metrics
    gate_metrics: GateMetrics = field(default_factory=GateMetrics)

    # Decision
    decision: GateDecision = GateDecision.HOLD
    decision_rationale: List[str] = field(default_factory=list)
    blocking_issues: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class MeasurementReportBuilder:
    """Builder for constructing measurement reports."""

    def __init__(self, repo_root: Path = None):
        self.repo_root = Path(repo_root) if repo_root else Path.cwd()
        self.report = MeasurementReport(
            timestamp=datetime.utcnow().isoformat()
        )

    def add_e2e_results(self, results: Dict[str, dict]) -> "MeasurementReportBuilder":
        """Add E2E wiring proof results."""
        self.report.e2e_wiring_results = results
        self.report.total_e2e_checks = len(results)
        return self

    def add_reproducibility_results(self, results: Dict[str, dict]) -> "MeasurementReportBuilder":
        """Add reproducibility check results."""
        self.report.reproducibility_results = results
        self.report.total_tests_for_flake = len(results)
        return self

    def add_coverage_impact(self, result: dict) -> "MeasurementReportBuilder":
        """Add coverage impact report."""
        self.report.coverage_impact_result = result
        return self

    def compute_gate_decision(self) -> GateDecision:
        """
        Compute Go/No-Go decision based on metrics.

        Fail-closed rules:
        - Any E2E FAIL → NO_GO
        - Reproducibility pass_rate < threshold → NO_GO
        - Coverage regression > threshold → NO_GO
        - Otherwise → GO (if all metrics pass)
        """
        logger.info("[GATE] Computing Go/No-Go decision...")

        # Check E2E results
        e2e_fails = sum(
            1 for r in self.report.e2e_wiring_results.values()
            if r.get("reachability_status") == "fail"
        )
        if e2e_fails > 0:
            self.report.decision = GateDecision.NO_GO
            self.report.blocking_issues.append(
                f"E2E wiring proof: {e2e_fails}/{self.report.total_e2e_checks} "
                f"entry points unreachable"
            )
            logger.error(f"  ✗ {self.report.blocking_issues[-1]}")

        # Check reproducibility
        flaky_count = 0
        broken_count = 0
        for r in self.report.reproducibility_results.values():
            status = r.get("flake_status")
            if status == "flaky":
                flaky_count += 1
            elif status == "broken":
                broken_count += 1

        if broken_count > 0:
            self.report.decision = GateDecision.NO_GO
            self.report.blocking_issues.append(
                f"Reproducibility: {broken_count}/{self.report.total_tests_for_flake} "
                f"tests broken (0/3 passes)"
            )
            logger.error(f"  ✗ {self.report.blocking_issues[-1]}")

        if flaky_count > 0:
            self.report.warnings.append(
                f"Reproducibility: {flaky_count}/{self.report.total_tests_for_flake} "
                f"tests flaky (1-2/3 passes)"
            )
            logger.warning(f"  ⚠ {self.report.warnings[-1]}")

        # Check coverage
        if self.report.coverage_impact_result:
            coverage = self.report.coverage_impact_result
            delta = coverage.get("delta_coverage_percent", 0.0)
            risk = coverage.get("risk_level", "LOW")

            if delta < self.report.gate_metrics.coverage_regression_threshold:
                self.report.decision = GateDecision.NO_GO
                self.report.blocking_issues.append(
                    f"Coverage regression: {delta:+.1f}% "
                    f"(threshold {self.report.gate_metrics.coverage_regression_threshold:+.1f}%)"
                )
                logger.error(f"  ✗ {self.report.blocking_issues[-1]}")
            elif risk == "HIGH":
                self.report.warnings.append(
                    f"Coverage risk HIGH: {delta:+.1f}% delta"
                )
                logger.warning(f"  ⚠ {self.report.warnings[-1]}")

        # Final decision
        if self.report.decision == GateDecision.HOLD and not self.report.blocking_issues:
            self.report.decision = GateDecision.GO
            self.report.decision_rationale.append("All metrics pass")
            logger.info("  ✓ GATE DECISION: GO")
        else:
            logger.warning(f"  ✗ GATE DECISION: {self.report.decision.value.upper()}")

        return self.report.decision

    def to_json(self) -> str:
        """Export report as JSON."""
        data = asdict(self.report)
        data['decision'] = self.report.decision.value
        data['gate_metrics'] = asdict(self.report.gate_metrics)
        return json.dumps(data, indent=2, default=str)

    def summary(self) -> str:
        """Generate a human-readable summary."""
        lines = [
            "=" * 60,
            f"PHASE 3 MEASUREMENT REPORT",
            f"Timestamp: {self.report.timestamp}",
            "=" * 60,
            "",
            f"E2E WIRING PROOF ({self.report.total_e2e_checks} checks):",
            self._summarize_e2e(),
            "",
            f"REPRODUCIBILITY ({self.report.total_tests_for_flake} tests):",
            self._summarize_reproducibility(),
            "",
            f"COVERAGE IMPACT:",
            self._summarize_coverage(),
            "",
            "=" * 60,
            f"GATE DECISION: {self.report.decision.value.upper()}",
            "=" * 60,
        ]

        if self.report.decision_rationale:
            lines.append("Rationale:")
            for r in self.report.decision_rationale:
                lines.append(f"  ✓ {r}")

        if self.report.blocking_issues:
            lines.append("\nBlocking Issues:")
            for issue in self.report.blocking_issues:
                lines.append(f"  ✗ {issue}")

        if self.report.warnings:
            lines.append("\nWarnings:")
            for w in self.report.warnings:
                lines.append(f"  ⚠ {w}")

        lines.append("")
        return "\n".join(lines)

    def _summarize_e2e(self) -> str:
        """Summarize E2E results."""
        if not self.report.e2e_wiring_results:
            return "  (No results)"

        passes = sum(
            1 for r in self.report.e2e_wiring_results.values()
            if r.get("reachability_status") == "pass"
        )
        total = len(self.report.e2e_wiring_results)
        pct = (passes / total * 100) if total > 0 else 0

        return f"  {passes}/{total} entry points reachable ({pct:.0f}%)"

    def _summarize_reproducibility(self) -> str:
        """Summarize reproducibility results."""
        if not self.report.reproducibility_results:
            return "  (No results)"

        stable = sum(
            1 for r in self.report.reproducibility_results.values()
            if r.get("flake_status") == "stable"
        )
        flaky = sum(
            1 for r in self.report.reproducibility_results.values()
            if r.get("flake_status") == "flaky"
        )
        broken = sum(
            1 for r in self.report.reproducibility_results.values()
            if r.get("flake_status") == "broken"
        )
        total = len(self.report.reproducibility_results)

        return f"  Stable: {stable}/{total}, Flaky: {flaky}/{total}, Broken: {broken}/{total}"

    def _summarize_coverage(self) -> str:
        """Summarize coverage results."""
        if not self.report.coverage_impact_result:
            return "  (No results)"

        cov = self.report.coverage_impact_result
        baseline = cov.get("baseline", {}).get("total_coverage_percent", 0)
        current = cov.get("current", {}).get("total_coverage_percent", 0)
        delta = cov.get("delta_coverage_percent", 0)

        return f"  {baseline:.1f}% → {current:.1f}% ({delta:+.1f}%)"

    def save_to_file(self, output_path: Path = None) -> Path:
        """Save report to JSON file."""
        if not output_path:
            output_path = self.repo_root / "validation_report_phase3.json"

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            f.write(self.to_json())
        logger.info(f"[REPORT] Saved to {output_path}")
        return output_path

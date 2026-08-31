"""Descriptive analysis and reporting for TDE benchmark SIMULATION results.

HONESTY NOTE (adversarial review 2026-07-24): the numbers analyzed here come
from operator/benchmarking/harness.py's _simulate_tokens() — a deterministic
model that ENCODES the assumed per-category savings ratios; nothing in this
package executes TDE or measures real token usage. Consequently this module
reports DESCRIPTIVE statistics of the simulation only and never claims
statistical significance: an earlier revision bucketed a pseudo p-value and
then unconditionally overrode it to 0.01 for any mean delta > 500 — that
fabrication has been removed. For honest, measured numbers use
operator/orchestration/tde/bench.py (real SendIntegration runs, wall-clock
only until token instrumentation exists).
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class CategoryStats:
    """Statistics for one task category."""

    category: str
    n_tasks: int
    tokens_cc_mean: float
    tokens_cc_stdev: float
    tokens_tde_mean: float
    tokens_tde_stdev: float
    delta_mean: float
    delta_pct_mean: float
    ci_lower: float
    ci_upper: float
    tasks_improved: int
    tasks_regressed: int


class BenchmarkAnalysis:
    """Analyze benchmark results with statistical rigor."""

    def __init__(self, results: list[dict[str, Any]]):
        """
        Initialize with raw results.

        Each result: {
            "task_id": str,
            "category": str,
            "tokens_cc": int,
            "tokens_tde": int,
            "engine": str,
            ...
        }
        """
        self.results = results
        self.deltas_by_category: dict[str, list[float]] = {}
        self.compute_deltas()

    def compute_deltas(self):
        """Compute per-task deltas and group by category."""
        for result in self.results:
            category = result["category"]
            delta = result.get("delta_tokens", 0)

            if category not in self.deltas_by_category:
                self.deltas_by_category[category] = []

            self.deltas_by_category[category].append(delta)

    def aggregate_results(self) -> dict[str, Any]:
        """Overall savings across all tasks."""
        if not self.results:
            return {}

        total_cc = sum(r.get("tokens_cc", 0) for r in self.results)
        total_tde = sum(r.get("tokens_tde", 0) for r in self.results)
        savings = total_cc - total_tde
        savings_pct = (savings / total_cc * 100) if total_cc > 0 else 0

        return {
            "total_tokens_cc": int(total_cc),
            "total_tokens_tde": int(total_tde),
            "total_savings": int(savings),
            "savings_pct": round(savings_pct, 2),
            "tasks_improved": sum(1 for r in self.results if r.get("delta_tokens", 0) > 0),
            "tasks_regressed": sum(1 for r in self.results if r.get("delta_tokens", 0) < 0),
            "tasks_neutral": sum(1 for r in self.results if r.get("delta_tokens", 0) == 0),
            "total_tasks": len(self.results),
        }

    def confidence_intervals(self, confidence: float = 0.95) -> dict[str, Any]:
        """Compute confidence intervals for each category."""
        ci_results = {}

        for category, deltas in self.deltas_by_category.items():
            if not deltas:
                continue

            # Bootstrap confidence interval (percentile method)
            sorted_deltas = sorted(deltas)
            idx_lower = int(len(sorted_deltas) * 0.025)
            idx_upper = int(len(sorted_deltas) * 0.975)
            ci_lower = float(sorted_deltas[max(0, idx_lower)])
            ci_upper = float(sorted_deltas[min(len(sorted_deltas) - 1, idx_upper)])
            mean_delta = statistics.mean(deltas)
            stdev = statistics.stdev(deltas) if len(deltas) > 1 else 0

            # Convert to percentage
            # Need baseline tokens for percentage
            category_results = [r for r in self.results if r["category"] == category]
            mean_cc_tokens = sum(r["tokens_cc"] for r in category_results) / len(category_results)
            pct_mean = (mean_delta / mean_cc_tokens * 100) if mean_cc_tokens > 0 else 0
            pct_lower = (ci_lower / mean_cc_tokens * 100) if mean_cc_tokens > 0 else 0
            pct_upper = (ci_upper / mean_cc_tokens * 100) if mean_cc_tokens > 0 else 0

            # A "95% CI" from n<10 percentile-bootstrap samples is noise
            # dressed as rigor (the shipped big_data category had n=1 and
            # printed "95% CI: 13198 to 13198" — review 2026-07-24). Below
            # the minimum we report the observed range, labeled as such.
            if len(deltas) < 10:
                ci_results[category] = {
                    "n_samples": len(deltas),
                    "mean_delta_tokens": round(mean_delta, 1),
                    "stdev_tokens": round(stdev, 1),
                    "range_tokens": [round(min(deltas), 1), round(max(deltas), 1)],
                    "ci_lower_tokens": None,
                    "ci_upper_tokens": None,
                    "mean_delta_pct": round(pct_mean, 2),
                    "significant_at_95": False,
                    "interpretation": (
                        f"simulated mean delta {pct_mean:.1f}% over n={len(deltas)} "
                        "samples — too few for a confidence interval; observed "
                        "range reported instead"
                    ),
                }
                continue

            ci_results[category] = {
                "n_samples": len(deltas),
                "mean_delta_tokens": round(mean_delta, 1),
                "stdev_tokens": round(stdev, 1),
                "ci_lower_tokens": round(ci_lower, 1),
                "ci_upper_tokens": round(ci_upper, 1),
                "mean_delta_pct": round(pct_mean, 2),
                "ci_lower_pct": round(pct_lower, 2),
                "ci_upper_pct": round(pct_upper, 2),
                "significant_at_95": ci_lower > 0,
                "interpretation": (
                    f"simulated: TDE saves {pct_mean:.1f}% (bootstrap 95% CI: "
                    f"{pct_lower:.1f}%-{pct_upper:.1f}%)"
                    if pct_mean > 0
                    else f"simulated: TDE costs {abs(pct_mean):.1f}% more"
                ),
            }

        return ci_results

    def by_category_stats(self) -> dict[str, CategoryStats]:
        """Detailed stats grouped by category."""
        stats = {}

        for category in self.deltas_by_category.keys():
            cat_results = [r for r in self.results if r["category"] == category]
            if not cat_results:
                continue

            tokens_cc = [r["tokens_cc"] for r in cat_results]
            tokens_tde = [r["tokens_tde"] for r in cat_results]
            deltas = [r.get("delta_tokens", 0) for r in cat_results]

            sorted_deltas = sorted(deltas)
            idx_lower = int(len(sorted_deltas) * 0.025)
            idx_upper = int(len(sorted_deltas) * 0.975)
            ci_lower = float(sorted_deltas[max(0, idx_lower)])
            ci_upper = float(sorted_deltas[min(len(sorted_deltas) - 1, idx_upper)])

            stats[category] = CategoryStats(
                category=category,
                n_tasks=len(cat_results),
                tokens_cc_mean=statistics.mean(tokens_cc),
                tokens_cc_stdev=statistics.stdev(tokens_cc) if len(tokens_cc) > 1 else 0,
                tokens_tde_mean=statistics.mean(tokens_tde),
                tokens_tde_stdev=statistics.stdev(tokens_tde) if len(tokens_tde) > 1 else 0,
                delta_mean=statistics.mean(deltas),
                delta_pct_mean=(statistics.mean(deltas) / statistics.mean(tokens_cc) * 100)
                if statistics.mean(tokens_cc) > 0
                else 0,
                ci_lower=ci_lower,
                ci_upper=ci_upper,
                tasks_improved=sum(1 for d in deltas if d > 0),
                tasks_regressed=sum(1 for d in deltas if d < 0),
            )

        return stats

    def statistical_tests(self) -> dict[str, Any]:
        """Paired t-test and other statistical tests."""
        if len(self.results) < 2:
            return {}

        cc_tokens = [r["tokens_cc"] for r in self.results]
        tde_tokens = [r["tokens_tde"] for r in self.results]
        deltas = [cc - tde for cc, tde in zip(cc_tokens, tde_tokens)]

        # Paired t-test (manual calculation)
        mean_delta = statistics.mean(deltas)
        stdev_delta = statistics.stdev(deltas) if len(deltas) > 1 else 0
        n = len(deltas)
        se_delta = stdev_delta / (n ** 0.5) if stdev_delta > 0 else 0
        t_stat = mean_delta / se_delta if se_delta > 0 else 0

        # NO p-value: the deltas are outputs of a deterministic simulation of
        # the very savings being "tested" (harness._simulate_tokens), and the
        # pairing mixes tasks of wildly different scales — a significance test
        # here would be meaningless even if computed correctly. An earlier
        # revision bucketed a pseudo p-value and then FORCED it to 0.01
        # whenever mean_delta > 500 (adversarial review 2026-07-24); removed.
        return {
            "test": "descriptive_only",
            "n_samples": n,
            "mean_delta_tokens": round(mean_delta, 1),
            "stdev_delta": round(stdev_delta, 1),
            "t_statistic_descriptive": round(t_stat, 3),
            "p_value": None,
            "significant_at_05": False,
            "interpretation": (
                "SIMULATION — deltas follow directly from the model's assumed "
                "per-category savings ratios; no statistical significance can "
                "be or is claimed. Use tde.bench for measured numbers."
            ),
        }

    def export_json(self, output_file: Path):
        """Export complete analysis as JSON."""
        # Convert CategoryStats to dict
        cat_stats = self.by_category_stats()
        cat_stats_dict = {}
        for cat, stats in cat_stats.items():
            cat_stats_dict[cat] = {
                "category": stats.category,
                "n_tasks": stats.n_tasks,
                "tokens_cc_mean": round(stats.tokens_cc_mean, 1),
                "tokens_cc_stdev": round(stats.tokens_cc_stdev, 1),
                "tokens_tde_mean": round(stats.tokens_tde_mean, 1),
                "tokens_tde_stdev": round(stats.tokens_tde_stdev, 1),
                "delta_mean": round(stats.delta_mean, 1),
                "delta_pct_mean": round(stats.delta_pct_mean, 2),
                "ci_lower": round(stats.ci_lower, 1),
                "ci_upper": round(stats.ci_upper, 1),
                "tasks_improved": stats.tasks_improved,
                "tasks_regressed": stats.tasks_regressed,
            }

        data = {
            "metadata": {
                "analysis_timestamp": datetime.now().isoformat(),
                "version": "1.1",
                "mode": "simulation",
                "honesty_note": (
                    "All token numbers are outputs of a deterministic "
                    "simulation (harness._simulate_tokens) encoding assumed "
                    "savings ratios — not measured LLM usage."
                ),
            },
            "aggregate": self.aggregate_results(),
            "by_category": cat_stats_dict,
            "confidence_intervals": self.confidence_intervals(),
            "statistical_tests": self.statistical_tests(),
            "per_task_results": self.results,
        }

        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(data, indent=2))

    def export_summary(self, output_file: Path):
        """Export human-readable summary."""
        agg = self.aggregate_results()
        cat_stats = self.by_category_stats()
        stat_tests = self.statistical_tests()

        summary = f"""
TDE BENCHMARK SIMULATION SUMMARY
================================
NOTE: all numbers below are SIMULATED (deterministic model of assumed
savings ratios) — no real TDE execution or token measurement occurred.

Overall Results:
  Total Tasks: {agg.get('total_tasks', 0)}
  Control (CC) Total: {agg.get('total_tokens_cc', 0):,} tokens
  Treatment (TDE) Total: {agg.get('total_tokens_tde', 0):,} tokens
  Savings: {agg.get('total_savings', 0):,} tokens ({agg.get('savings_pct', 0):.1f}%)

  Tasks Improved: {agg.get('tasks_improved', 0)}
  Tasks Regressed: {agg.get('tasks_regressed', 0)}
  Tasks Neutral: {agg.get('tasks_neutral', 0)}

Statistics (descriptive only — no significance claimed):
  {stat_tests.get('interpretation', 'N/A')}

Results by Category:
"""

        for category, stats in sorted(cat_stats.items()):
            summary += f"""
  {category.upper()}:
    Tasks: {stats.n_tasks}
    CC avg: {stats.tokens_cc_mean:,.0f} ± {stats.tokens_cc_stdev:,.0f} tokens
    TDE avg: {stats.tokens_tde_mean:,.0f} ± {stats.tokens_tde_stdev:,.0f} tokens
    Savings (simulated): {stats.delta_pct_mean:.1f}% (observed delta range: {stats.ci_lower:.0f} to {stats.ci_upper:.0f} tokens)
    Improved: {stats.tasks_improved}/{stats.n_tasks}
"""

        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(summary)

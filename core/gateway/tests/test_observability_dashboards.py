"""Per-subtask E2E for ADR-0007 Phase 6.4 — Grafana templates.

Static validation: both dashboards parse as JSON, declare the
required Grafana fields, and only reference metric names exposed
by the audit_metrics aggregator (Phase 6.1). The latter is the
load-bearing check — a panel that references a missing metric
silently renders empty, which is exactly the failure mode the
Phase 6 design wanted to eliminate.

The dashboards do not need a running Grafana to test — Grafana
imports them as JSON; structural validity + metric-name parity
covers the design contract.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "gateway"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))

_DASH_DIR = _REPO / "docs" / "observability" / "grafana"
_DASHBOARDS = ("corvin-overview.json", "corvin-security.json")


def _allowed_metrics() -> set[str]:
    """Snapshot the metric names the aggregator emits."""
    with tempfile.TemporaryDirectory(prefix="gw-dash-test-") as td:
        os.environ["CORVIN_HOME"] = td
        (Path(td) / "tenants" / "_default" / "global" / "forge").mkdir(parents=True)
        try:
            from corvin_gateway import audit_metrics
            body = audit_metrics.render("_default")
        finally:
            os.environ.pop("CORVIN_HOME", None)
    names: set[str] = set()
    for line in body.splitlines():
        if not line or line.startswith("#"):
            continue
        # "metric_name{labels} value" → take the first token before `{` or space
        token = line.split(" ", 1)[0].split("{", 1)[0]
        names.add(token)
    # Histogram render emits `_bucket`, `_sum`, `_count` siblings; the
    # PromQL queries in the dashboards use `_bucket` directly, but the
    # base name `corvin_gateway_run_duration_seconds` is also valid
    # (it's the family root). Inject it.
    names.add("corvin_gateway_run_duration_seconds")
    return names


_METRIC_REF_RE = re.compile(r"\bcorvin_[a-z0-9_]+(?:_total|_seconds_bucket|_seconds_sum|_seconds_count|_seconds|_intact|_events_total)?\b")


def _metric_refs_in_dashboard(path: Path) -> set[str]:
    text = path.read_text()
    return set(_METRIC_REF_RE.findall(text))


class DashboardShapeTests(unittest.TestCase):
    def test_overview_dashboard_loads(self):
        for fn in _DASHBOARDS:
            p = _DASH_DIR / fn
            self.assertTrue(p.exists(), str(p))
            data = json.loads(p.read_text())
            for key in ("title", "panels", "uid", "schemaVersion", "tags"):
                self.assertIn(key, data, f"{fn}: missing {key}")
            self.assertIsInstance(data["panels"], list)
            self.assertGreater(len(data["panels"]), 0, fn)


class DashboardMetricParityTests(unittest.TestCase):
    """Every metric the dashboards reference MUST exist in the aggregator."""

    def test_overview_metrics_known(self):
        allowed = _allowed_metrics()
        refs = _metric_refs_in_dashboard(_DASH_DIR / "corvin-overview.json")
        unknown = refs - allowed
        self.assertEqual(
            unknown, set(),
            f"Unknown metrics referenced by overview dashboard: {unknown}",
        )

    def test_security_metrics_known(self):
        allowed = _allowed_metrics()
        refs = _metric_refs_in_dashboard(_DASH_DIR / "corvin-security.json")
        unknown = refs - allowed
        self.assertEqual(
            unknown, set(),
            f"Unknown metrics referenced by security dashboard: {unknown}",
        )


class DashboardTemplatingTests(unittest.TestCase):
    def test_dashboards_have_tenant_templating(self):
        for fn in _DASHBOARDS:
            p = _DASH_DIR / fn
            data = json.loads(p.read_text())
            templating = data.get("templating", {}).get("list", [])
            names = [v.get("name") for v in templating]
            self.assertIn(
                "tenant", names,
                f"{fn} must expose a 'tenant' templated variable",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Per-subtask E2E for ADR-0007 Phase 6.1 — audit-chain metrics projection.

Covers:
  * Aggregator over an empty chain → all-zero, chain_intact=True
  * Counter projection: tool.created → corvin_forge_tools_created_total
  * Histogram: run lifecycle → run_duration_seconds bucket math
  * Label whitelist: unknown values collapse to "other"
  * Label whitelist: PII / unbounded values rejected
  * Time-window filter: since=<ts> drops older events
  * TTL cache: second call within TTL doesn't re-read chain
  * Prometheus format: well-formed, ``# HELP`` + ``# TYPE`` per family
  * Audit-chain intact gauge: 1 on clean chain, 0 on tampered
  * parse_duration: 30s / 5m / 2h / 7d / bare-int
  * snapshot_to_dict: JSON-safe primitives
  * render_table: deterministic ordering
  * Counter family with all dimensions: dialectic decisions
  * No-PII contract: token fingerprints / run_ids never appear as labels
  * Multi-tenant isolation: tenant A's events do not appear in tenant B's
    aggregate.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "gateway"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))

from corvin_gateway import audit_metrics  # noqa: E402
from forge import security_events as _se  # noqa: E402


# ── Fixture ──────────────────────────────────────────────────────────


@contextmanager
def sandbox(tenants=("acme",)):
    with tempfile.TemporaryDirectory(prefix="gw-metrics-test-") as td:
        home = Path(td)
        os.environ["CORVIN_HOME"] = str(home)
        for t in tenants:
            (home / "tenants" / t / "global" / "forge").mkdir(parents=True)
        audit_metrics.clear_cache()
        try:
            yield home
        finally:
            os.environ.pop("CORVIN_HOME", None)
            audit_metrics.clear_cache()


def _chain(home: Path, tenant: str) -> Path:
    return home / "tenants" / tenant / "global" / "forge" / "audit.jsonl"


def _write(home: Path, tenant: str, event_type: str, **kw) -> dict:
    """Append one hash-chained event into the tenant's chain."""
    p = _chain(home, tenant)
    return _se.write_event(
        p, event_type,
        ts=kw.pop("ts", None),
        run_id=kw.pop("run_id", ""),
        tool=kw.pop("tool", ""),
        severity=kw.pop("severity", None),
        details=kw or {},
    )


# ── Aggregator basics ────────────────────────────────────────────────


class EmptyChainTests(unittest.TestCase):
    def test_empty_chain_no_crash(self):
        with sandbox(("acme",)):
            snap = audit_metrics.aggregate("acme")
            self.assertEqual(snap.events_read, 0)
            self.assertTrue(snap.chain_intact)
            self.assertEqual(snap.counters, {})

    def test_render_empty_chain_includes_help_lines(self):
        with sandbox(("acme",)):
            body = audit_metrics.render("acme")
            self.assertIn("# HELP corvin_gateway_runs_total", body)
            self.assertIn("# TYPE corvin_gateway_runs_total counter", body)
            self.assertIn("# HELP corvin_audit_chain_intact", body)


class CounterProjectionTests(unittest.TestCase):
    def test_tool_created_counts_by_persona(self):
        with sandbox(("acme",)) as home:
            _write(home, "acme", "tool.created", persona="coder")
            _write(home, "acme", "tool.created", persona="coder")
            _write(home, "acme", "tool.created", persona="research")
            snap = audit_metrics.aggregate("acme")
            counters = snap.counters["corvin_forge_tools_created_total"]
            self.assertEqual(counters.by_labels[("coder",)], 2)
            self.assertEqual(counters.by_labels[("research",)], 1)

    def test_unknown_persona_collapses_to_other(self):
        with sandbox(("acme",)) as home:
            _write(home, "acme", "tool.created", persona="my-custom-persona")
            snap = audit_metrics.aggregate("acme")
            counters = snap.counters["corvin_forge_tools_created_total"]
            self.assertEqual(counters.by_labels[("other",)], 1)

    def test_missing_persona_collapses_to_other(self):
        with sandbox(("acme",)) as home:
            _write(home, "acme", "tool.created")  # no persona field
            snap = audit_metrics.aggregate("acme")
            counters = snap.counters["corvin_forge_tools_created_total"]
            self.assertEqual(counters.by_labels[("other",)], 1)

    def test_dialectic_three_dimensions(self):
        with sandbox(("acme",)) as home:
            _write(home, "acme", "decision.dialectical",
                   site="path_gate", mode="fast", choice="thesis")
            _write(home, "acme", "decision.dialectical",
                   site="path_gate", mode="fast", choice="thesis")
            _write(home, "acme", "decision.dialectical",
                   site="forge_creation", mode="cli", choice="synthesis")
            snap = audit_metrics.aggregate("acme")
            c = snap.counters["corvin_dialectic_decisions_total"]
            self.assertEqual(c.by_labels[("path_gate", "fast", "thesis")], 2)
            self.assertEqual(
                c.by_labels[("forge_creation", "cli", "synthesis")], 1,
            )

    def test_webhook_outcomes_split(self):
        with sandbox(("acme",)) as home:
            _write(home, "acme", "gateway.webhook_dispatched",
                   host="webhook.example.com")
            _write(home, "acme", "gateway.webhook_dispatched",
                   host="other.example.com")
            _write(home, "acme", "gateway.webhook_delivery_failed",
                   last_error="connection refused")
            snap = audit_metrics.aggregate("acme")
            c = snap.counters["corvin_gateway_webhooks_total"]
            self.assertEqual(c.by_labels[("delivered",)], 2)
            self.assertEqual(c.by_labels[("failed",)], 1)

    def test_path_gate_reads_tool_from_top_level(self):
        with sandbox(("acme",)) as home:
            _write(home, "acme", "path_gate.denied",
                   tool="Bash", reason="write-to-forge")
            snap = audit_metrics.aggregate("acme")
            c = snap.counters["corvin_path_gate_denied_total"]
            self.assertEqual(c.by_labels[("Bash",)], 1)

    # ── ADR-0012 data-locality counters ──────────────────────────────

    def test_data_registered_by_format(self):
        with sandbox(("acme",)) as home:
            _write(home, "acme", "data.registered",
                   format="csv", size_b=1024, rowcount=42)
            _write(home, "acme", "data.registered",
                   format="csv")
            _write(home, "acme", "data.registered",
                   format="parquet")
            snap = audit_metrics.aggregate("acme")
            c = snap.counters["corvin_data_registered_total"]
            self.assertEqual(c.by_labels[("csv",)], 2)
            self.assertEqual(c.by_labels[("parquet",)], 1)

    def test_data_unregistered_counter(self):
        with sandbox(("acme",)) as home:
            _write(home, "acme", "data.unregistered",
                   data_handle="data_test1", found=True)
            _write(home, "acme", "data.unregistered",
                   data_handle="data_test2", found=False)
            snap = audit_metrics.aggregate("acme")
            c = snap.counters["corvin_data_unregistered_total"]
            self.assertEqual(c.by_labels[()], 2)

    def test_data_policy_violated_by_reason(self):
        with sandbox(("acme",)) as home:
            _write(home, "acme", "data.policy_violated",
                   reason="unsupported-format")
            _write(home, "acme", "data.policy_violated",
                   reason="unsupported-format")
            _write(home, "acme", "data.policy_violated",
                   reason="register-failed")
            snap = audit_metrics.aggregate("acme")
            c = snap.counters["corvin_data_policy_violated_total"]
            self.assertEqual(c.by_labels[("unsupported-format",)], 2)
            self.assertEqual(c.by_labels[("register-failed",)], 1)

    def test_data_snapshot_oversized_counter(self):
        with sandbox(("acme",)) as home:
            _write(home, "acme", "data.snapshot_oversized",
                   data_handle="data_x", cap_tokens=4000,
                   estimated_tokens=12000, columns=80)
            _write(home, "acme", "data.snapshot_oversized",
                   data_handle="data_y", cap_tokens=4000,
                   estimated_tokens=8000, columns=60)
            snap = audit_metrics.aggregate("acme")
            c = snap.counters["corvin_data_snapshot_oversized_total"]
            self.assertEqual(c.by_labels[()], 2)

    def test_data_pii_detected_classes(self):
        with sandbox(("acme",)) as home:
            _write(home, "acme", "data.pii_detected",
                   data_handle="data_x",
                   classes={"email": 1, "phone": 2})
            _write(home, "acme", "data.pii_detected",
                   data_handle="data_y",
                   classes={"email": 3})
            snap = audit_metrics.aggregate("acme")
            c = snap.counters["corvin_data_pii_detected_total"]
            self.assertEqual(c.by_labels[("email",)], 4)
            self.assertEqual(c.by_labels[("phone",)], 2)

    def test_data_pii_no_pii_class_dropped(self):
        with sandbox(("acme",)) as home:
            _write(home, "acme", "data.pii_detected",
                   data_handle="data_x",
                   classes={"<no_pii>": 5, "email": 1})
            snap = audit_metrics.aggregate("acme")
            c = snap.counters["corvin_data_pii_detected_total"]
            # <no_pii> dropped — only real PII classes show up
            self.assertNotIn(("<no_pii>",), c.by_labels)
            self.assertEqual(c.by_labels[("email",)], 1)

    def test_data_unknown_format_collapses_to_other(self):
        with sandbox(("acme",)) as home:
            _write(home, "acme", "data.registered",
                   format="some-future-format")
            snap = audit_metrics.aggregate("acme")
            c = snap.counters["corvin_data_registered_total"]
            self.assertEqual(c.by_labels[("other",)], 1)


# ── Histogram ────────────────────────────────────────────────────────


class HistogramTests(unittest.TestCase):
    def test_run_duration_buckets(self):
        with sandbox(("acme",)) as home:
            base = 1_700_000_000.0
            # Pair: created @ base, completed @ base+1.5s
            _write(home, "acme", "gateway.run_created",
                   ts=base, run_id="run_1")
            _write(home, "acme", "gateway.run_status_changed",
                   ts=base + 1.5, run_id="run_1",
                   **{"from": "running", "to": "completed"})
            # Another pair: 8s
            _write(home, "acme", "gateway.run_created",
                   ts=base + 10, run_id="run_2")
            _write(home, "acme", "gateway.run_status_changed",
                   ts=base + 18, run_id="run_2",
                   **{"from": "running", "to": "completed"})
            snap = audit_metrics.aggregate("acme")
            hist = snap.histograms["corvin_gateway_run_duration_seconds"]
            self.assertEqual(hist.counts[("completed",)], 2)
            self.assertAlmostEqual(hist.sums[("completed",)], 9.5, places=5)
            # 1.5 lands in 2.0+ buckets; 8.0 in 10.0+
            buckets = hist.bucket_counts[("completed",)]
            # buckets: (0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0) + Inf
            self.assertEqual(buckets[0], 0)   # ≤0.5
            self.assertEqual(buckets[1], 0)   # ≤1.0
            self.assertEqual(buckets[2], 1)   # ≤2.0 (1.5 only)
            self.assertEqual(buckets[3], 1)   # ≤5.0
            self.assertEqual(buckets[4], 2)   # ≤10.0 (both)
            self.assertEqual(buckets[-1], 2)  # +Inf

    def test_runs_counter_increments_on_terminal(self):
        with sandbox(("acme",)) as home:
            _write(home, "acme", "gateway.run_status_changed",
                   **{"from": "running", "to": "completed"})
            _write(home, "acme", "gateway.run_status_changed",
                   **{"from": "running", "to": "failed"})
            _write(home, "acme", "gateway.run_status_changed",
                   **{"from": "running", "to": "budget_exceeded"})
            # An intermediate transition does NOT count:
            _write(home, "acme", "gateway.run_status_changed",
                   **{"from": "accepted", "to": "running"})
            snap = audit_metrics.aggregate("acme")
            c = snap.counters["corvin_gateway_runs_total"]
            self.assertEqual(c.by_labels[("completed",)], 1)
            self.assertEqual(c.by_labels[("failed",)], 1)
            self.assertEqual(c.by_labels[("budget_exceeded",)], 1)
            # "running" is in the allowlist but should NOT increment via
            # this code path (we only emit on terminal transitions).
            self.assertNotIn(("running",), c.by_labels)


# ── Time window ──────────────────────────────────────────────────────


class TimeWindowTests(unittest.TestCase):
    def test_since_filter_drops_old_events(self):
        with sandbox(("acme",)) as home:
            old = 1_700_000_000.0
            new = old + 3600
            _write(home, "acme", "tool.created", ts=old, persona="coder")
            _write(home, "acme", "tool.created", ts=new, persona="coder")
            # since=cutoff between old and new
            snap = audit_metrics.aggregate("acme", since=old + 1)
            c = snap.counters["corvin_forge_tools_created_total"]
            self.assertEqual(c.by_labels[("coder",)], 1)


# ── Prometheus rendering ─────────────────────────────────────────────


class PrometheusFormatTests(unittest.TestCase):
    def test_well_formed_output(self):
        with sandbox(("acme",)) as home:
            _write(home, "acme", "tool.created", persona="coder")
            _write(home, "acme", "consent.observer_dropped")
            body = audit_metrics.render("acme")
            # Stable structure
            lines = body.splitlines()
            help_lines = [ln for ln in lines if ln.startswith("# HELP")]
            type_lines = [ln for ln in lines if ln.startswith("# TYPE")]
            self.assertEqual(len(help_lines), len(type_lines))
            self.assertGreater(len(help_lines), 5)
            # Specific samples present
            self.assertIn(
                'corvin_forge_tools_created_total{persona="coder"} 1', body,
            )
            self.assertIn("corvin_consent_drops_total 1", body)

    def test_chain_intact_gauge(self):
        with sandbox(("acme",)) as home:
            _write(home, "acme", "tool.created", persona="coder")
            body = audit_metrics.render("acme")
            self.assertIn("corvin_audit_chain_intact 1", body)

    def test_chain_intact_zero_on_tamper(self):
        with sandbox(("acme",)) as home:
            _write(home, "acme", "tool.created", persona="coder")
            _write(home, "acme", "tool.created", persona="forge")
            # Tamper: rewrite the second line with a different persona.
            path = _chain(home, "acme")
            lines = path.read_text().splitlines()
            rec = json.loads(lines[1])
            rec["details"]["persona"] = "browser"
            lines[1] = json.dumps(rec)
            path.write_text("\n".join(lines) + "\n")
            audit_metrics.clear_cache()
            body = audit_metrics.render("acme")
            self.assertIn("corvin_audit_chain_intact 0", body)


# ── Cache ────────────────────────────────────────────────────────────


class CacheTests(unittest.TestCase):
    def test_cache_hits_on_second_call(self):
        with sandbox(("acme",)) as home:
            _write(home, "acme", "tool.created", persona="coder")
            body1 = audit_metrics.render("acme")
            # Add an event AFTER the first render
            _write(home, "acme", "tool.created", persona="coder")
            body2 = audit_metrics.render("acme")
            self.assertEqual(body1, body2,
                "second render within TTL must hit cache")

    def test_clear_cache_picks_up_new_events(self):
        with sandbox(("acme",)) as home:
            _write(home, "acme", "tool.created", persona="coder")
            body1 = audit_metrics.render("acme")
            _write(home, "acme", "tool.created", persona="coder")
            audit_metrics.clear_cache()
            body2 = audit_metrics.render("acme")
            self.assertNotEqual(body1, body2)
            self.assertIn(
                'corvin_forge_tools_created_total{persona="coder"} 2', body2,
            )


# ── parse_duration ───────────────────────────────────────────────────


class ParseDurationTests(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(audit_metrics.parse_duration("30s"), 30.0)

    def test_minutes(self):
        self.assertEqual(audit_metrics.parse_duration("5m"), 300.0)

    def test_hours(self):
        self.assertEqual(audit_metrics.parse_duration("2h"), 7200.0)

    def test_days(self):
        self.assertEqual(audit_metrics.parse_duration("7d"), 604800.0)

    def test_bare_int(self):
        self.assertEqual(audit_metrics.parse_duration("42"), 42.0)

    def test_rejects_garbage(self):
        for bad in ["", "abc", "1x", "1.5z"]:
            with self.assertRaises(ValueError, msg=f"rejected: {bad!r}"):
                audit_metrics.parse_duration(bad)


# ── Multi-tenant isolation ───────────────────────────────────────────


class MultiTenantIsolationTests(unittest.TestCase):
    def test_tenants_dont_leak(self):
        with sandbox(("acme", "globex")) as home:
            _write(home, "acme",   "tool.created", persona="coder")
            _write(home, "acme",   "tool.created", persona="coder")
            _write(home, "globex", "tool.created", persona="research")
            snap_a = audit_metrics.aggregate("acme")
            audit_metrics.clear_cache()
            snap_g = audit_metrics.aggregate("globex")
            self.assertEqual(
                snap_a.counters["corvin_forge_tools_created_total"]
                       .by_labels[("coder",)], 2)
            self.assertNotIn(
                "corvin_forge_tools_created_total",
                {k for k in snap_g.counters if k == "corvin_forge_tools_created_total"
                 and snap_g.counters[k].by_labels.get(("coder",), 0) > 0},
            )
            # globex sees only its own event
            self.assertEqual(
                snap_g.counters["corvin_forge_tools_created_total"]
                       .by_labels[("research",)], 1)


# ── No-PII contract ──────────────────────────────────────────────────


class NoPIIContractTests(unittest.TestCase):
    def test_run_id_never_in_label_set(self):
        # _ALLOWLIST keys are the only legal label names.
        self.assertNotIn("run_id", audit_metrics._ALLOWLIST)
        self.assertNotIn("tenant_id", audit_metrics._ALLOWLIST)
        self.assertNotIn("user", audit_metrics._ALLOWLIST)
        self.assertNotIn("email", audit_metrics._ALLOWLIST)
        self.assertNotIn("fingerprint", audit_metrics._ALLOWLIST)
        self.assertNotIn("token", audit_metrics._ALLOWLIST)

    def test_unknown_label_name_returns_other(self):
        # Calling _safe_label with an unknown name MUST collapse to "other".
        self.assertEqual(
            audit_metrics._safe_label("definitely-not-a-label", "anything"),
            "other",
        )

    def test_cardinality_bound_per_label(self):
        # Each allow-list must have ≤ 32 entries (ADR cardinality budget).
        for name, values in audit_metrics._ALLOWLIST.items():
            self.assertLessEqual(
                len(values), 32,
                f"label {name!r} has {len(values)} values; cap is 32",
            )


# ── Snapshot helpers ─────────────────────────────────────────────────


class SnapshotHelperTests(unittest.TestCase):
    def test_snapshot_to_dict_json_safe(self):
        with sandbox(("acme",)) as home:
            _write(home, "acme", "tool.created", persona="coder")
            snap = audit_metrics.aggregate("acme")
            d = audit_metrics.snapshot_to_dict(snap)
            # Round-trip through JSON
            s = json.dumps(d)
            d2 = json.loads(s)
            self.assertEqual(d2["events_read"], 1)
            self.assertEqual(d2["chain_intact"], True)
            counters = d2["counters"]["corvin_forge_tools_created_total"]
            self.assertEqual(len(counters), 1)
            self.assertEqual(counters[0]["labels"]["persona"], "coder")
            self.assertEqual(counters[0]["value"], 1)

    def test_render_table_includes_counter_and_histogram(self):
        with sandbox(("acme",)) as home:
            _write(home, "acme", "tool.created", persona="coder")
            base = 1_700_000_000.0
            _write(home, "acme", "gateway.run_created",
                   ts=base, run_id="run_1")
            _write(home, "acme", "gateway.run_status_changed",
                   ts=base + 0.4, run_id="run_1",
                   **{"from": "running", "to": "completed"})
            snap = audit_metrics.aggregate("acme")
            table = audit_metrics.render_table(snap)
            self.assertIn("corvin_forge_tools_created_total", table)
            self.assertIn("persona=coder", table)
            self.assertIn("corvin_gateway_run_duration_seconds", table)
            self.assertIn("(histogram)", table)


if __name__ == "__main__":
    unittest.main()

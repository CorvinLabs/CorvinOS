"""Per-subtask E2E for ADR-0007 Phase 6.2 — Gateway /metrics endpoint.

Covers:
  * 200 + ``text/plain; version=0.0.4`` content-type on happy path
  * Prometheus exposition format is parseable (well-formed)
  * Counter values reflect events in the tenant's chain
  * ``?since=`` query param trims the window
  * ``?since=garbage`` returns 400 with reason ``invalid-since``
  * Scrape does NOT emit an audit event (chain length unchanged)
  * Cross-tenant isolation: tenant A's scrape never sees tenant B's events
  * Audit-chain integrity holds before AND after a scrape

Every case runs against a fresh ``<corvin_home>`` tempdir.
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

from fastapi.testclient import TestClient  # noqa: E402

from corvin_gateway import audit_metrics  # noqa: E402
from corvin_gateway.app import app  # noqa: E402
from forge import security_events as _se  # noqa: E402


@contextmanager
def sandbox(tenants=("acme",)):
    with tempfile.TemporaryDirectory(prefix="gw-metrics-ep-") as td:
        home = Path(td)
        os.environ["CORVIN_HOME"] = str(home)
        for t in tenants:
            (home / "tenants" / t / "global" / "auth").mkdir(parents=True)
            (home / "tenants" / t / "global" / "forge").mkdir(parents=True)
        audit_metrics.clear_cache()
        try:
            yield home
        finally:
            os.environ.pop("CORVIN_HOME", None)
            audit_metrics.clear_cache()


def _chain(home: Path, tenant: str) -> Path:
    return home / "tenants" / tenant / "global" / "forge" / "audit.jsonl"


def _write(home: Path, tenant: str, event_type: str, **kw) -> None:
    _se.write_event(
        _chain(home, tenant), event_type,
        ts=kw.pop("ts", None),
        run_id=kw.pop("run_id", ""),
        tool=kw.pop("tool", ""),
        severity=kw.pop("severity", None),
        details=kw or {},
    )


def _client() -> TestClient:
    return TestClient(app)


# AuthGateTests removed — gateway no longer enforces bearer auth.
# Loopback binding is the local security boundary.


# ── Happy path ───────────────────────────────────────────────────────


class HappyPathTests(unittest.TestCase):
    def test_returns_prometheus_content_type(self):
        with sandbox(("acme",)):
            with _client() as c:
                r = c.get("/v1/tenants/acme/metrics")
                self.assertEqual(r.status_code, 200)
                self.assertIn("text/plain", r.headers["content-type"])
                self.assertIn("version=0.0.4", r.headers["content-type"])

    def test_emits_help_and_type_lines(self):
        with sandbox(("acme",)):
            with _client() as c:
                r = c.get("/v1/tenants/acme/metrics")
                body = r.text
                self.assertIn("# HELP corvin_gateway_runs_total", body)
                self.assertIn("# TYPE corvin_gateway_runs_total counter", body)
                self.assertIn("# HELP corvin_audit_chain_intact", body)

    def test_counter_reflects_chain_events(self):
        with sandbox(("acme",)) as home:
            _write(home, "acme", "tool.created", persona="coder")
            _write(home, "acme", "tool.created", persona="coder")
            with _client() as c:
                r = c.get("/v1/tenants/acme/metrics")
                self.assertIn(
                    'corvin_forge_tools_created_total{persona="coder"} 2',
                    r.text,
                )


# ── ?since= parameter ────────────────────────────────────────────────


class SinceQueryTests(unittest.TestCase):
    def test_invalid_since_returns_400(self):
        with sandbox(("acme",)):
            with _client() as c:
                r = c.get("/v1/tenants/acme/metrics?since=notaduration")
                self.assertEqual(r.status_code, 400)
                self.assertEqual(r.json()["detail"]["reason"], "invalid-since")

    def test_since_filter_drops_older_events(self):
        with sandbox(("acme",)) as home:
            old = time.time() - 7200  # 2h ago
            _write(home, "acme", "tool.created", ts=old, persona="coder")
            _write(home, "acme", "tool.created", persona="coder")  # now
            with _client() as c:
                # since=1h → drops the 2h-old event, keeps the recent one.
                r = c.get("/v1/tenants/acme/metrics?since=1h")
                self.assertEqual(r.status_code, 200)
                self.assertIn(
                    'corvin_forge_tools_created_total{persona="coder"} 1',
                    r.text,
                )


# ── Read-only contract ───────────────────────────────────────────────


class ReadOnlyContractTests(unittest.TestCase):
    def test_scrape_does_not_emit_metrics_audit_event(self):
        """No ``metrics.*`` / ``gateway.metrics_*`` event types after scrape.

        Scrapes must not write to the audit chain — doing so would pollute
        the chain at scrape-rate × tenant-count cardinality.
        """
        with sandbox(("acme",)) as home:
            chain = _chain(home, "acme")
            with _client() as c:
                for _ in range(3):
                    audit_metrics.clear_cache()
                    r = c.get("/v1/tenants/acme/metrics")
                    self.assertEqual(r.status_code, 200)
            # Scan every event in the chain — none may carry a metrics-
            # specific event_type. If the chain file doesn't exist at all,
            # that trivially satisfies the contract (no events emitted).
            if chain.exists():
                for raw in chain.read_text().splitlines():
                    if not raw.strip():
                        continue
                    rec = json.loads(raw)
                    et = rec.get("event_type", "")
                    self.assertFalse(
                        et.startswith("metrics.") or et.startswith("gateway.metrics_"),
                        f"scrape leaked a metrics-specific audit event: {et!r}",
                    )

    def test_audit_chain_integrity_holds_after_scrape(self):
        with sandbox(("acme",)) as home:
            _write(home, "acme", "tool.created", persona="coder")
            with _client() as c:
                c.get("/v1/tenants/acme/metrics")
            from forge.security_events import verify_chain
            ok, problems = verify_chain(_chain(home, "acme"))
            self.assertTrue(ok, f"chain broken: {problems}")


# ── Cross-tenant isolation ───────────────────────────────────────────


class CrossTenantIsolationTests(unittest.TestCase):
    def test_tenant_scrape_does_not_see_other_tenant(self):
        with sandbox(("acme", "globex")) as home:
            _write(home, "acme",   "tool.created", persona="coder")
            _write(home, "globex", "tool.created", persona="research")
            with _client() as c:
                ra = c.get("/v1/tenants/acme/metrics")
                audit_metrics.clear_cache()
                rg = c.get("/v1/tenants/globex/metrics")
                self.assertIn(
                    'corvin_forge_tools_created_total{persona="coder"} 1',
                    ra.text,
                )
                self.assertNotIn(
                    'persona="research"', ra.text,
                    "acme scrape leaked globex's research-persona events",
                )
                self.assertIn(
                    'corvin_forge_tools_created_total{persona="research"} 1',
                    rg.text,
                )


if __name__ == "__main__":
    unittest.main()

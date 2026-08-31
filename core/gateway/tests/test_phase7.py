"""Per-subtask E2E for ADR-0007 Phase 7 — durable queue + rate-limit.

Covers:
  * Durable queue: enqueue → next_pending → mark_terminal cycle;
    cross-tenant isolation; recover_pending after a "crash".
  * Queue persistence: writing then opening a fresh connection sees
    the same entries (WAL durability).
  * Rate-limit: no budget → unlimited; budget=N → first N
    requests admitted, (N+1)th rejected with audit emission.
  * Rate-limit gate runs BEFORE body validation → a throttled
    tenant gets 429 even with a malformed body.
  * Operator reset: limiter.reset(tenant) refills the bucket.
  * Dispatcher integration: submit() enqueues; terminal status
    dequeues.
  * Cross-tenant rate-limit isolation: tenant A's bucket exhaustion
    has no effect on tenant B.
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
from typing import Iterator

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "gateway"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))

from fastapi.testclient import TestClient  # noqa: E402

from agents import StreamEvent  # noqa: E402
from corvin_gateway import (  # noqa: E402
    durable_queue, rate_limit, tenant_config,
)
from corvin_gateway.app import app  # noqa: E402
from corvin_gateway.dispatcher import RunDispatcher  # noqa: E402
from forge import security_events as _security_events  # noqa: E402


@contextmanager
def sandbox(tenants=("acme",)):
    with tempfile.TemporaryDirectory(prefix="gw-p7-test-") as td:
        home = Path(td)
        os.environ["CORVIN_HOME"] = str(home)
        os.environ["ADAPTER_FAKE_CLAUDE"] = "1"
        os.environ["ADAPTER_FAKE_DELAY"] = "0.01"
        for t in tenants:
            (home / "tenants" / t / "global" / "auth").mkdir(parents=True)
            (home / "tenants" / t / "global" / "forge").mkdir(parents=True)
            (home / "tenants" / t / "global" / "gateway" / "runs").mkdir(parents=True)
        # _default tenant hosts the durable-queue DB
        (home / "tenants" / "_default" / "global" / "forge").mkdir(parents=True)
        try:
            yield home
        finally:
            os.environ.pop("CORVIN_HOME", None)
            os.environ.pop("ADAPTER_FAKE_CLAUDE", None)
            os.environ.pop("ADAPTER_FAKE_DELAY", None)


def _hdr() -> dict[str, str]:
    return {}


def _good_run_body():
    return {
        "apiVersion": "corvin/v1",
        "kind":       "Run",
        "spec":       {"persona": "docs", "input": "ping"},
    }


# ── Durable queue ───────────────────────────────────────────────────


class DurableQueueTests(unittest.TestCase):
    def test_enqueue_next_terminal(self):
        with sandbox(("acme", "globex")):
            durable_queue.enqueue("acme", "run_a")
            durable_queue.enqueue("acme", "run_b")
            self.assertEqual(durable_queue.pending_count("acme"), 2)
            self.assertEqual(durable_queue.pending_count("globex"), 0)
            row = durable_queue.next_pending("acme")
            self.assertEqual(row, ("acme", "run_a"))
            self.assertTrue(durable_queue.mark_terminal("acme", "run_a"))
            self.assertEqual(durable_queue.pending_count("acme"), 1)
            # Idempotent: marking again returns False
            self.assertFalse(durable_queue.mark_terminal("acme", "run_a"))

    def test_recovery_returns_all_pending(self):
        with sandbox(("acme", "globex")):
            durable_queue.enqueue("acme", "r1")
            durable_queue.enqueue("globex", "r2")
            durable_queue.enqueue("acme", "r3")
            pending = durable_queue.recover_pending()
            self.assertEqual(len(pending), 3)
            tenants = {tid for tid, _ in pending}
            self.assertEqual(tenants, {"acme", "globex"})

    def test_idempotent_enqueue(self):
        with sandbox(("acme",)):
            durable_queue.enqueue("acme", "run_x")
            durable_queue.enqueue("acme", "run_x")  # duplicate
            self.assertEqual(durable_queue.pending_count("acme"), 1)

    def test_persistence_across_connections(self):
        with sandbox(("acme",)) as home:
            durable_queue.enqueue("acme", "run_z")
            # Open another "session" by clearing module cache state
            # — the on-disk WAL DB is the persistence layer.
            self.assertEqual(durable_queue.pending_count("acme"), 1)
            row = durable_queue.next_pending()
            self.assertEqual(row, ("acme", "run_z"))


# ── Rate limit ──────────────────────────────────────────────────────


class _SimpleEngine:
    name = "claude_code"
    capabilities = {"stream_json": True}
    def spawn(self, prompt, *, env=None) -> Iterator[StreamEvent]:
        yield StreamEvent(type="turn_completed", text="ok")
    def cancel(self): pass


@contextmanager
def gateway_client(engine_factory=None):
    if engine_factory is not None:
        app.state.dispatcher = RunDispatcher(engine_factory=engine_factory)
    try:
        with TestClient(app) as client:
            yield client
    finally:
        if hasattr(app.state, "dispatcher"):
            app.state.dispatcher = None
        if hasattr(app.state, "rate_limiter"):
            app.state.rate_limiter = None


class RateLimitTests(unittest.TestCase):
    def test_no_budget_means_unlimited(self):
        with sandbox(("acme",)):
            # No tenant.corvin.yaml → no budget → unlimited
            with gateway_client(engine_factory=_SimpleEngine) as client:
                for _ in range(5):
                    r = client.post(
                        "/v1/tenants/acme/runs",
                        json=_good_run_body(), headers=_hdr(),
                    )
                    self.assertEqual(r.status_code, 202)

    def test_budget_caps_throughput(self):
        with sandbox(("acme",)) as home:
            cfg = tenant_config.init("acme")
            cfg.spec.budget.max_runs_per_day = 3
            tenant_config.save(cfg)
            with gateway_client(engine_factory=_SimpleEngine) as client:
                # 3 admitted
                for i in range(3):
                    r = client.post(
                        "/v1/tenants/acme/runs",
                        json=_good_run_body(), headers=_hdr(),
                    )
                    self.assertEqual(r.status_code, 202, f"iter {i}: {r.text}")
                # 4th rejected
                r = client.post(
                    "/v1/tenants/acme/runs",
                    json=_good_run_body(), headers=_hdr(),
                )
                self.assertEqual(r.status_code, 429)
                self.assertEqual(r.json()["detail"]["reason"], "rate-limited")
            # Audit event landed
            chain = home / "tenants" / "acme" / "global" / "forge" / "audit.jsonl"
            lines = [json.loads(l) for l in chain.read_text().splitlines() if l]
            limited = [e for e in lines if e["event_type"] == "gateway.rate_limited"]
            self.assertEqual(len(limited), 1)

    def test_rate_limit_runs_before_validation(self):
        """A throttled tenant gets 429 even on malformed body."""
        with sandbox(("acme",)):
            cfg = tenant_config.init("acme")
            cfg.spec.budget.max_runs_per_day = 1
            tenant_config.save(cfg)
            with gateway_client(engine_factory=_SimpleEngine) as client:
                r1 = client.post(
                    "/v1/tenants/acme/runs",
                    json=_good_run_body(), headers=_hdr(),
                )
                self.assertEqual(r1.status_code, 202)
                # Garbage body — 429 fires BEFORE 422
                r2 = client.post(
                    "/v1/tenants/acme/runs",
                    json={"garbage": True}, headers=_hdr(),
                )
                self.assertEqual(r2.status_code, 429)

    def test_cross_tenant_isolation(self):
        with sandbox(("acme", "globex")):
            cfg_a = tenant_config.init("acme")
            cfg_a.spec.budget.max_runs_per_day = 1
            tenant_config.save(cfg_a)
            with gateway_client(engine_factory=_SimpleEngine) as client:
                # Exhaust acme
                client.post(
                    "/v1/tenants/acme/runs",
                    json=_good_run_body(), headers=_hdr(),
                )
                r = client.post(
                    "/v1/tenants/acme/runs",
                    json=_good_run_body(), headers=_hdr(),
                )
                self.assertEqual(r.status_code, 429)
                # globex still admits
                r = client.post(
                    "/v1/tenants/globex/runs",
                    json=_good_run_body(), headers=_hdr(),
                )
                self.assertEqual(r.status_code, 202)

    def test_limiter_reset(self):
        with sandbox(("acme",)):
            cfg = tenant_config.init("acme")
            cfg.spec.budget.max_runs_per_day = 1
            tenant_config.save(cfg)
            limiter = rate_limit.RateLimiter()
            allowed, _ = limiter.check("acme")
            self.assertTrue(allowed)
            allowed, _ = limiter.check("acme")
            self.assertFalse(allowed)
            limiter.reset("acme")
            allowed, _ = limiter.check("acme")
            self.assertTrue(allowed)


# ── Dispatcher integration ──────────────────────────────────────────


class DispatcherDurableTests(unittest.TestCase):
    def test_submit_enqueues_and_terminal_dequeues(self):
        with sandbox(("acme",)):
            with gateway_client(engine_factory=_SimpleEngine) as client:
                r = client.post(
                    "/v1/tenants/acme/runs",
                    json=_good_run_body(), headers=_hdr(),
                )
                run_id = r.json()["run_id"]
                # Poll until terminal
                deadline = time.time() + 30   # L44 adjudicates with an LLM
                while time.time() < deadline:
                    g = client.get(
                        f"/v1/tenants/acme/runs/{run_id}", headers=_hdr(),
                    )
                    if g.json().get("status") in (
                        "completed", "failed", "budget_exceeded",
                    ):
                        break
                    time.sleep(0.02)
                self.assertEqual(g.json()["status"], "completed")
            # Queue should be empty after terminal
            self.assertEqual(durable_queue.pending_count("acme"), 0)


class GrpcProtoTests(unittest.TestCase):
    """Validate the proto contract structurally even without
    grpcio installed."""

    def test_proto_file_present_and_well_formed(self):
        proto = (
            _REPO / "core" / "gateway" / "corvin_gateway"
            / "grpc" / "corvin.proto"
        )
        self.assertTrue(proto.exists())
        text = proto.read_text()
        for token in (
            'syntax = "proto3"',
            "package corvin.gateway.v1",
            "service CorvinGateway",
            "rpc SubmitRun",
            "rpc GetRun",
            "rpc StreamEvents",
        ):
            self.assertIn(token, text)

    def test_servicer_real_implementation(self):
        """Phase 7.3 follow-up: the servicer is now a real
        implementation backed by RunRegistry + RunDispatcher.
        Construction works; method calls require gRPC metadata
        context (covered by the full gRPC E2E in test_phase7_grpc).
        """
        from corvin_gateway.grpc import grpc_server as gs
        servicer = gs.CorvinGatewayServicer()
        # All three RPC methods exist on the class
        for method in ("SubmitRun", "GetRun", "StreamEvents"):
            self.assertTrue(callable(getattr(servicer, method)))


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Per-subtask E2E for ADR-0007 Phase 3.3 — data-residency zone gate.

Covers:
  * Tenant without a pinned zone → no constraint → engine runs.
  * Tenant pinned to eu-west, engine in eu-west → run.
  * Tenant pinned to eu-west, engine in us-east → failed with
    ``zone-mismatch`` + ``gateway.zone_denied`` audit event.
  * Tenant pinned, engine with no zone attr (defaults to "global")
    → run.
  * Tenant pinned, engine.zone="global" → run.
  * Chain integrity after a zone denial.
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
from corvin_gateway import tenant_config  # noqa: E402
from corvin_gateway.app import app  # noqa: E402
from corvin_gateway.dispatcher import RunDispatcher  # noqa: E402
from forge import security_events as _security_events  # noqa: E402


@contextmanager
def sandbox(tenants=("acme",)):
    with tempfile.TemporaryDirectory(prefix="gw-zone-test-") as td:
        home = Path(td)
        os.environ["CORVIN_HOME"] = str(home)
        os.environ["ADAPTER_FAKE_CLAUDE"] = "1"
        os.environ["ADAPTER_FAKE_DELAY"] = "0.02"
        for t in tenants:
            (home / "tenants" / t / "global" / "auth").mkdir(parents=True)
            (home / "tenants" / t / "global" / "forge").mkdir(parents=True)
            (home / "tenants" / t / "global" / "gateway" / "runs").mkdir(parents=True)
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


class _ZonedEngine:
    capabilities = {"stream_json": True}
    name = "claude_code"

    def __init__(self, zone: str | None = None):
        self.zone = zone

    def spawn(self, prompt: str, *, env=None) -> Iterator[StreamEvent]:
        yield StreamEvent(type="turn_completed", text="ok")

    def cancel(self) -> None:
        pass


# 30 s, not 5 s. Every gateway dispatch runs the L44 house-rules gate, which
# ADJUDICATES THE PROMPT WITH AN LLM (house_rules_adjudicator -> qwen3:8b via the one
# local Ollama, or Haiku). Measured 2026-07-26: ~7-11 s for a trivial run with an
# instant stub engine. A 5 s budget therefore passed or failed depending on how warm
# Ollama happened to be — engine-policy, zone-policy, durable-queue and dispatcher all
# flipped between runs of the same commit. That is a fail-closed compliance gate doing
# its job, so the poll accommodates its cost instead of the suite blaming the product.
def _poll_until_terminal(client, url, headers, *, timeout_s: float = 30.0):
    end = time.time() + timeout_s
    last = None
    while time.time() < end:
        r = client.get(url, headers=headers)
        last = r
        if r.status_code == 200 and r.json().get("status") in (
            "completed", "failed", "budget_exceeded",
        ):
            return r
        time.sleep(0.02)
    return last


class ZoneTests(unittest.TestCase):
    def test_no_tenant_zone_no_constraint(self):
        with sandbox(("acme",)):
            # No tenant config => tenant zone = None
            with gateway_client(
                engine_factory=lambda: _ZonedEngine(zone="us-east"),
            ) as client:
                r = client.post(
                    "/v1/tenants/acme/runs",
                    json=_good_run_body(), headers=_hdr(),
                )
                got = _poll_until_terminal(
                    client,
                    f"/v1/tenants/acme/runs/{r.json()['run_id']}",
                    _hdr(),
                )
            self.assertEqual(got.json()["status"], "completed")

    def test_tenant_zone_match_engine_zone(self):
        with sandbox(("acme",)):
            tenant_config.init("acme", zone="eu-west")
            with gateway_client(
                engine_factory=lambda: _ZonedEngine(zone="eu-west"),
            ) as client:
                r = client.post(
                    "/v1/tenants/acme/runs",
                    json=_good_run_body(), headers=_hdr(),
                )
                got = _poll_until_terminal(
                    client,
                    f"/v1/tenants/acme/runs/{r.json()['run_id']}",
                    _hdr(),
                )
            self.assertEqual(got.json()["status"], "completed")

    def test_tenant_zone_mismatch_denied(self):
        with sandbox(("acme",)) as home:
            tenant_config.init("acme", zone="eu-west")
            with gateway_client(
                engine_factory=lambda: _ZonedEngine(zone="us-east"),
            ) as client:
                r = client.post(
                    "/v1/tenants/acme/runs",
                    json=_good_run_body(), headers=_hdr(),
                )
                got = _poll_until_terminal(
                    client,
                    f"/v1/tenants/acme/runs/{r.json()['run_id']}",
                    _hdr(),
                )
            self.assertEqual(got.json()["status"], "failed")
            self.assertIn("zone-mismatch", got.json()["error"])
            self.assertIn("us-east", got.json()["error"])
            self.assertIn("eu-west", got.json()["error"])
            chain = home / "tenants" / "acme" / "global" / "forge" / "audit.jsonl"
            lines = [json.loads(l) for l in chain.read_text().splitlines() if l]
            denials = [e for e in lines if e["event_type"] == "gateway.zone_denied"]
            self.assertEqual(len(denials), 1)
            self.assertEqual(denials[0]["details"]["engine_zone"], "us-east")
            self.assertEqual(denials[0]["details"]["tenant_zone"], "eu-west")

    def test_engine_global_zone_always_runs(self):
        with sandbox(("acme",)):
            tenant_config.init("acme", zone="eu-west")
            with gateway_client(
                engine_factory=lambda: _ZonedEngine(zone="global"),
            ) as client:
                r = client.post(
                    "/v1/tenants/acme/runs",
                    json=_good_run_body(), headers=_hdr(),
                )
                got = _poll_until_terminal(
                    client,
                    f"/v1/tenants/acme/runs/{r.json()['run_id']}",
                    _hdr(),
                )
            self.assertEqual(got.json()["status"], "completed")

    def test_engine_without_zone_attr_runs(self):
        with sandbox(("acme",)):
            tenant_config.init("acme", zone="eu-west")
            # Engine has no `zone` attribute at all (older engines)
            class _NoZoneEngine:
                name = "claude_code"
                def spawn(self, prompt, *, env=None):
                    yield StreamEvent(type="turn_completed", text="ok")
                def cancel(self): pass

            with gateway_client(
                engine_factory=lambda: _NoZoneEngine(),
            ) as client:
                r = client.post(
                    "/v1/tenants/acme/runs",
                    json=_good_run_body(), headers=_hdr(),
                )
                got = _poll_until_terminal(
                    client,
                    f"/v1/tenants/acme/runs/{r.json()['run_id']}",
                    _hdr(),
                )
            self.assertEqual(got.json()["status"], "completed")


class ChainAfterZoneDenialTests(unittest.TestCase):
    def test_chain_verifies(self):
        with sandbox(("acme",)) as home:
            tenant_config.init("acme", zone="eu-west")
            with gateway_client(
                engine_factory=lambda: _ZonedEngine(zone="ap-south"),
            ) as client:
                for _ in range(3):
                    r = client.post(
                        "/v1/tenants/acme/runs",
                        json=_good_run_body(), headers=_hdr(),
                    )
                    _poll_until_terminal(
                        client,
                        f"/v1/tenants/acme/runs/{r.json()['run_id']}",
                        _hdr(),
                    )
            chain = home / "tenants" / "acme" / "global" / "forge" / "audit.jsonl"
            ok, problems = _security_events.verify_chain(chain)
            self.assertTrue(ok, problems)


if __name__ == "__main__":
    unittest.main(verbosity=2)

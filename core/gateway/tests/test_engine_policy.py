"""Per-subtask E2E for ADR-0007 Phase 3.2 — engine-policy enforcement.

Covers:
  * Default (no tenant config) → engine runs (Phase 2 behaviour).
  * Allowlist contains engine.name → engine runs.
  * Allowlist excludes engine.name → run fails with
    ``engine-not-allowed`` + ``gateway.engine_denied`` audit event.
  * forbid_engines contains engine.name → run fails even when
    allowlist also contains it.
  * Malformed tenant config → fail-closed with diagnostic.
  * Audit chain integrity holds across denial.
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
from typing import Any, Iterator

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


# ── Fixtures ─────────────────────────────────────────────────────────


@contextmanager
def sandbox(tenants=("acme",)):
    with tempfile.TemporaryDirectory(prefix="gw-policy-test-") as td:
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


class _NamedEngine:
    capabilities = {"stream_json": True}

    def __init__(self, name: str = "test-engine"):
        self.name = name

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


# ── Tests ────────────────────────────────────────────────────────────


class DefaultPolicyTests(unittest.TestCase):
    def test_no_config_allows_any_engine(self):
        with sandbox(("acme",)):
            with gateway_client(
                engine_factory=lambda: _NamedEngine("future_engine"),
            ) as client:
                r = client.post(
                    "/v1/tenants/acme/runs",
                    json=_good_run_body(), headers=_hdr(),
                )
                run_id = r.json()["run_id"]
                got = _poll_until_terminal(
                    client, f"/v1/tenants/acme/runs/{run_id}", _hdr(),
                )
            self.assertEqual(got.json()["status"], "completed")


class AllowlistTests(unittest.TestCase):
    def test_engine_in_allowlist_runs(self):
        with sandbox(("acme",)):
            tenant_config.init(
                "acme", display_name="ACME",
                allowed_engines=["claude_code"],
            )
            with gateway_client(
                engine_factory=lambda: _NamedEngine("claude_code"),
            ) as client:
                r = client.post(
                    "/v1/tenants/acme/runs",
                    json=_good_run_body(), headers=_hdr(),
                )
                run_id = r.json()["run_id"]
                got = _poll_until_terminal(
                    client, f"/v1/tenants/acme/runs/{run_id}", _hdr(),
                )
            self.assertEqual(got.json()["status"], "completed")

    def test_engine_not_in_allowlist_denied(self):
        with sandbox(("acme",)) as home:
            tenant_config.init(
                "acme", display_name="ACME",
                allowed_engines=["claude_code"],
            )
            with gateway_client(
                engine_factory=lambda: _NamedEngine("codex_cli"),
            ) as client:
                r = client.post(
                    "/v1/tenants/acme/runs",
                    json=_good_run_body(), headers=_hdr(),
                )
                run_id = r.json()["run_id"]
                got = _poll_until_terminal(
                    client, f"/v1/tenants/acme/runs/{run_id}", _hdr(),
                )
            self.assertEqual(got.json()["status"], "failed")
            self.assertIn("engine-not-allowed", got.json()["error"])
            self.assertIn("codex_cli", got.json()["error"])
            # Audit event landed
            chain = home / "tenants" / "acme" / "global" / "forge" / "audit.jsonl"
            lines = [json.loads(l) for l in chain.read_text().splitlines() if l]
            denied = [e for e in lines if e["event_type"] == "gateway.engine_denied"]
            self.assertEqual(len(denied), 1)
            self.assertEqual(denied[0]["details"]["engine"], "codex_cli")
            self.assertIn("claude_code", denied[0]["details"]["allowed_engines"])


class ForbidlistTests(unittest.TestCase):
    def test_forbid_beats_allow(self):
        with sandbox(("acme",)) as home:
            cfg = tenant_config.init("acme", display_name="ACME")
            cfg.spec.data_residency.allowed_engines = ["claude_code"]
            cfg.spec.data_residency.forbid_engines = ["claude_code"]
            tenant_config.save(cfg)
            with gateway_client(
                engine_factory=lambda: _NamedEngine("claude_code"),
            ) as client:
                r = client.post(
                    "/v1/tenants/acme/runs",
                    json=_good_run_body(), headers=_hdr(),
                )
                run_id = r.json()["run_id"]
                got = _poll_until_terminal(
                    client, f"/v1/tenants/acme/runs/{run_id}", _hdr(),
                )
            self.assertEqual(got.json()["status"], "failed")
            self.assertIn("engine-not-allowed", got.json()["error"])


class MalformedConfigTests(unittest.TestCase):
    def test_malformed_yaml_fails_closed(self):
        with sandbox(("acme",)) as home:
            # Inject a corrupt tenant.corvin.yaml
            p = home / "tenants" / "acme" / "global" / "tenant.corvin.yaml"
            p.write_text("not: a: valid: yaml:")
            os.chmod(p, 0o600)
            with gateway_client(
                engine_factory=lambda: _NamedEngine("claude_code"),
            ) as client:
                r = client.post(
                    "/v1/tenants/acme/runs",
                    json=_good_run_body(), headers=_hdr(),
                )
                run_id = r.json()["run_id"]
                got = _poll_until_terminal(
                    client, f"/v1/tenants/acme/runs/{run_id}", _hdr(),
                )
            self.assertEqual(got.json()["status"], "failed")
            self.assertIn("tenant-config-malformed", got.json()["error"])


class ChainIntegrityTests(unittest.TestCase):
    def test_chain_verifies_after_denial(self):
        with sandbox(("acme",)) as home:
            tenant_config.init(
                "acme", display_name="ACME",
                allowed_engines=["claude_code"],
            )
            with gateway_client(
                engine_factory=lambda: _NamedEngine("codex_cli"),
            ) as client:
                # One allow, two denials
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
            self.assertTrue(ok, f"chain broken: {problems}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

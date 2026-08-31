"""Per-subtask E2E for ADR-0007 Phase 2.2 — FastAPI app + run-submission.

Covers:
  * POST /v1/tenants/{tid}/runs — 202 + run_id (happy path, no auth required)
  * Persisted record matches the request body
  * GET /v1/tenants/{tid}/runs/{run_id} — full round-trip
  * 404 on unknown run_id
  * 422 on malformed run spec (missing required field, extra field,
    wrong apiVersion, wrong kind)
  * 500 on non-provisioned tenant
  * Status-machine: accepted → running → completed
  * Refusal of transition out of terminal state
  * Audit-chain integrity across the full lifecycle

Note: bearer-token auth has been removed from the gateway. Loopback
binding is the local security boundary; OIDC wires in for cloud.

Every case runs against a fresh ``<corvin_home>`` tempdir; the
operator's real ``~/.corvin/`` is never touched.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "gateway"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))

from fastapi.testclient import TestClient  # noqa: E402

from corvin_gateway import runs  # noqa: E402
from corvin_gateway.app import app  # noqa: E402
from forge import security_events as _security_events  # noqa: E402


# ── Fixture: isolated corvin_home + provisioned tenants ─────────────


@contextmanager
def sandbox(tenants=("acme",)):
    with tempfile.TemporaryDirectory(prefix="gw-app-test-") as td:
        home = Path(td)
        os.environ["CORVIN_HOME"] = str(home)
        for t in tenants:
            (home / "tenants" / t / "global" / "forge").mkdir(parents=True)
            (home / "tenants" / t / "global" / "gateway" / "runs").mkdir(parents=True)
        try:
            yield home
        finally:
            os.environ.pop("CORVIN_HOME", None)


def _good_run_body(persona="customer-support", input_text="Refund order #4711"):
    return {
        "apiVersion": "corvin/v1",
        "kind":       "Run",
        "spec": {
            "persona": persona,
            "input":   input_text,
        },
    }


def _client():
    return TestClient(app)


# ── /healthz ─────────────────────────────────────────────────────────


class HealthzTests(unittest.TestCase):
    def test_healthz_unauthenticated(self):
        with sandbox():
            r = _client().get("/healthz")
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertEqual(body["status"], "ok")
            self.assertIn("version", body)


# ── POST happy path ──────────────────────────────────────────────────


class SubmitRunHappyTests(unittest.TestCase):
    def test_post_returns_202_and_run_id(self):
        with sandbox(("acme",)) as home:
            r = _client().post(
                "/v1/tenants/acme/runs",
                json=_good_run_body(),
            )
            self.assertEqual(r.status_code, 202, r.text)
            body = r.json()
            self.assertIn("run_id", body)
            self.assertTrue(body["run_id"].startswith("run_"))
            self.assertEqual(body["status"], "accepted")
            p = home / "tenants" / "acme" / "global" / "gateway" / "runs" / f"{body['run_id']}.json"
            self.assertTrue(p.exists())
            mode = p.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600, f"got 0o{mode:o}")

    def test_get_round_trip(self):
        with sandbox(("acme",)):
            post = _client().post(
                "/v1/tenants/acme/runs",
                json=_good_run_body(persona="docs-bot", input_text="hello"),
            )
            run_id = post.json()["run_id"]
            r = _client().get(f"/v1/tenants/acme/runs/{run_id}")
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertEqual(body["run_id"], run_id)
            self.assertEqual(body["status"], "accepted")
            self.assertEqual(body["tenant_id"], "acme")
            self.assertEqual(body["request"]["spec"]["persona"], "docs-bot")
            self.assertEqual(body["request"]["spec"]["input"], "hello")
            self.assertIsNone(body["result"])
            self.assertIsNone(body["error"])


# ── Validation (422) ─────────────────────────────────────────────────


class ValidationTests(unittest.TestCase):
    def test_missing_persona_is_422(self):
        with sandbox(("acme",)):
            body = _good_run_body()
            del body["spec"]["persona"]
            r = _client().post("/v1/tenants/acme/runs", json=body)
            self.assertEqual(r.status_code, 422)
            self.assertEqual(r.json()["detail"]["reason"], "invalid-run-spec")

    def test_wrong_kind_is_422(self):
        with sandbox(("acme",)):
            body = _good_run_body()
            body["kind"] = "Workflow"
            r = _client().post("/v1/tenants/acme/runs", json=body)
            self.assertEqual(r.status_code, 422)

    def test_wrong_api_version_is_422(self):
        with sandbox(("acme",)):
            body = _good_run_body()
            body["apiVersion"] = "corvin/v2"
            r = _client().post("/v1/tenants/acme/runs", json=body)
            self.assertEqual(r.status_code, 422)

    def test_extra_field_in_spec_is_422(self):
        with sandbox(("acme",)):
            body = _good_run_body()
            body["spec"]["foo"] = "bar"
            r = _client().post("/v1/tenants/acme/runs", json=body)
            self.assertEqual(r.status_code, 422)

    def test_webhook_must_be_http_scheme(self):
        with sandbox(("acme",)):
            body = _good_run_body()
            body["spec"]["webhook"] = {"url": "ftp://acme.com/cb", "secret_ref": "s1"}
            r = _client().post("/v1/tenants/acme/runs", json=body)
            self.assertEqual(r.status_code, 422)

    def test_webhook_https_round_trip(self):
        with sandbox(("acme",)):
            body = _good_run_body()
            body["spec"]["webhook"] = {
                "url": "https://acme.com/callback",
                "secret_ref": "webhook-secret-1",
            }
            r = _client().post("/v1/tenants/acme/runs", json=body)
            self.assertEqual(r.status_code, 202, r.text)


# ── 404 + 500 ────────────────────────────────────────────────────────


class NotFoundAndProvisionTests(unittest.TestCase):
    def test_unknown_run_id_is_404(self):
        with sandbox(("acme",)):
            r = _client().get("/v1/tenants/acme/runs/run_doesnotexist1234567")
            self.assertEqual(r.status_code, 404)

    def test_unprovisioned_tenant_returns_500(self):
        with sandbox(("acme",)):
            import shutil
            from corvin_gateway import runs as runs_mod
            original = runs_mod._runs_dir
            def fake_runs_dir(tid):
                return Path("/nonexistent/corvin/tenants") / tid / "global" / "gateway" / "runs"
            runs_mod._runs_dir = fake_runs_dir
            try:
                r = _client().post("/v1/tenants/acme/runs", json=_good_run_body())
                self.assertEqual(r.status_code, 500)
                self.assertEqual(r.json()["detail"]["reason"], "tenant-not-provisioned")
            finally:
                runs_mod._runs_dir = original


# ── Status state-machine ────────────────────────────────────────────


class StatusStateMachineTests(unittest.TestCase):
    def test_full_transition_chain(self):
        with sandbox(("acme",)):
            post = _client().post("/v1/tenants/acme/runs", json=_good_run_body())
            run_id = post.json()["run_id"]
            registry = runs.RunRegistry()
            registry.set_status("acme", run_id, "running")
            r = _client().get(f"/v1/tenants/acme/runs/{run_id}")
            self.assertEqual(r.json()["status"], "running")
            registry.set_status(
                "acme", run_id, "completed",
                result={"final_text": "Refunded order #4711."},
            )
            r = _client().get(f"/v1/tenants/acme/runs/{run_id}")
            body = r.json()
            self.assertEqual(body["status"], "completed")
            self.assertEqual(body["result"]["final_text"], "Refunded order #4711.")

    def test_no_transition_out_of_terminal(self):
        with sandbox(("acme",)):
            post = _client().post("/v1/tenants/acme/runs", json=_good_run_body())
            run_id = post.json()["run_id"]
            registry = runs.RunRegistry()
            registry.set_status("acme", run_id, "running")
            registry.set_status("acme", run_id, "failed", error="engine timeout")
            with self.assertRaises(ValueError):
                registry.set_status("acme", run_id, "completed")


# ── Audit chain ─────────────────────────────────────────────────────


class AuditChainTests(unittest.TestCase):
    def test_chain_verifies_after_full_lifecycle(self):
        with sandbox(("acme",)) as home:
            client = _client()
            for _ in range(3):
                client.post("/v1/tenants/acme/runs", json=_good_run_body())
            # one 404 → audit warning
            client.get("/v1/tenants/acme/runs/run_doesnotexist0000000")
            chain = home / "tenants" / "acme" / "global" / "forge" / "audit.jsonl"
            ok, problems = _security_events.verify_chain(chain)
            self.assertTrue(ok, f"chain broken: {problems}")
            lines = [json.loads(l) for l in chain.read_text().splitlines() if l]
            events = {e["event_type"] for e in lines}
            self.assertIn("gateway.run_created", events)
            self.assertIn("gateway.run_not_found", events)


# ── Status-set validation ───────────────────────────────────────────


class StatusSetValidationTests(unittest.TestCase):
    def test_invalid_status_rejected(self):
        with sandbox(("acme",)):
            client = _client()
            run_id = client.post(
                "/v1/tenants/acme/runs",
                json=_good_run_body(),
            ).json()["run_id"]
            registry = runs.RunRegistry()
            with self.assertRaises(ValueError):
                registry.set_status("acme", run_id, "nonsense")  # type: ignore


if __name__ == "__main__":
    unittest.main(verbosity=2)

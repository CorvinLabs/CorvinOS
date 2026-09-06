"""HTTP E2E proof for the ADR-0549 learning-loop console routes (2026-09-06, F3).

Drives the REAL FastAPI router through ``TestClient`` with an authenticated
session (the ``_sandbox`` pattern of test_learning_routes_e2e.py) against a
BOOTED ACP registry whose learning emitter writes the audit-first
``EventStore`` — then verifies the side effects on disk:

* ``POST /learning/feedback`` records a ``feedback`` learning event, runs the
  optimizer, answers with the hypotheses it tested (nothing is discarded);
* ``GET  /learning/config-versions`` is EMPTY until a hypothesis is accepted,
  then lists the real version with its config snapshot;
* ``POST /learning/config/rollback`` really rolls back (and 404s on unknown);
* ``GET  /learning/preferences`` is EMPTY without outcomes and derived from
  recorded task outcomes otherwise;
* every mutation lands in the console audit chain (``action_performed``) and
  every learning event in the CORE hash chain (``learning.<type>``).

The former implementation answered all of these with hard-coded mock data.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

# ── Path bootstrap (same as test_learning_routes_e2e.py) ─────────────────────
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_OPERATOR = _REPO / "operator"
_CONSOLE = _REPO / "core" / "console"
for _p in [
    str(_OPERATOR),
    str(_OPERATOR / "license"),
    str(_OPERATOR / "forge"),
    str(_OPERATOR / "bridges" / "shared"),
    str(_CONSOLE),
    str(_REPO),
]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _reset_modules():
    for key in list(sys.modules):
        if any(key.startswith(p) for p in ("corvin_console", "corvin_gateway", "forge")):
            del sys.modules[key]


@contextmanager
def _sandbox(tmp_path: Path):
    """Sandboxed console app + live session + BOOTED ACP registry with learning."""
    home = tmp_path / "corvin_home"
    tenant_id = "_default"
    tenant_home = home / "tenants" / tenant_id
    (tenant_home / "global" / "auth").mkdir(parents=True)
    (tenant_home / "global" / "forge").mkdir(parents=True)
    (tenant_home / "global" / "console" / "sessions").mkdir(parents=True)
    chain = home / "audit.jsonl"
    prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_TENANT_ID", "VOICE_AUDIT_PATH")}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = tenant_id
    os.environ["VOICE_AUDIT_PATH"] = str(chain)
    emitter = None
    try:
        _reset_modules()
        from corvin_console import auth as _auth
        from corvin_console.app import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        # Boot the ACP registry with a learning backend on the sandbox store.
        from core.learning.event_emitter import EventEmitter
        from core.learning.event_store import EventStore
        from core.skills.boot import boot_skills
        from core.skills.skill_registry_phase1 import LearningEmitterBackend

        emitter = EventEmitter(EventStore(tenant_home))
        boot_skills(
            tenant_id,
            audit_emit=lambda *_a, **_k: None,
            learning_backend=LearningEmitterBackend(emitter, session_id="test"),
        )

        rec = _auth.create_session(tenant_id=tenant_id, token_fingerprint="test-fp")
        csrf = _auth.derive_csrf_token(rec.csrf_secret, rec.sid)
        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("corvin_console_sid", rec.sid)
        client.headers.update({"X-CSRF-Token": csrf})
        yield client, home, tenant_id, emitter, chain
    finally:
        try:
            if emitter is not None:
                emitter.stop(timeout=5.0)
        except Exception:  # noqa: BLE001
            pass
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()


def _events_on_disk(home: Path, tenant_id: str) -> list[dict]:
    out: list[dict] = []
    for f in sorted((home / "tenants" / tenant_id / "learning" / "events").glob("*.jsonl")):
        out += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    return out


def _chain(chain: Path) -> list[dict]:
    if not chain.exists():
        return []
    return [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]


def _console_chain(home: Path, tenant_id: str) -> list[dict]:
    out: list[dict] = []
    for f in (home / "tenants" / tenant_id).rglob("audit.jsonl"):
        out += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    return out


class LearningLoopRoutesE2E(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def test_health_reports_booted_loop(self):
        with _sandbox(Path(self._tmp)) as (client, home, tenant_id, emitter, chain):
            r = client.get("/v1/console/learning/health")
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertEqual(body["status"], "operational")
            self.assertTrue(body["emitter_booted"])
            self.assertEqual(body["tunable_skills"], ["os.delegation_router"])

    def test_config_versions_and_preferences_are_empty_not_mocked(self):
        with _sandbox(Path(self._tmp)) as (client, home, tenant_id, emitter, chain):
            r = client.get("/v1/console/learning/config-versions")
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json(), [])  # no fabricated "v1.0.0 Initial config"
            r = client.get("/v1/console/learning/preferences")
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json(), {})  # no fabricated profile
            r = client.get("/v1/console/learning/config-versions?skill_id=os.nope")
            self.assertEqual(r.status_code, 400)

    def test_feedback_is_recorded_interpreted_and_audited(self):
        with _sandbox(Path(self._tmp)) as (client, home, tenant_id, emitter, chain):
            r = client.post(
                "/v1/console/learning/feedback",
                json={
                    "task_id": "task-1",
                    "outcome_quality": "excellent",
                    "would_repeat": True,
                    "reason": "was fast and clear — secret: hunter2",
                },
            )
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertEqual(body["status"], "recorded")
            self.assertTrue(body["learning_event_queued"])
            # excellent+repeat → confidence_threshold; "fast" → speed_weight; "clear" → clarity_weight
            params = {h["param"] for h in body["hypotheses"]}
            self.assertEqual(params, {"confidence_threshold", "speed_weight", "clarity_weight"})
            # baseline phase (epoch <= 50): tested, not accepted — reported honestly
            self.assertTrue(all(h["accepted"] is False for h in body["hypotheses"]))
            self.assertTrue(all(h["optimizer_reason"] == "baseline_collection_phase" for h in body["hypotheses"]))
            self.assertEqual(body["current_config"]["confidence_threshold"], 0.7)

            emitter.stop(timeout=5.0)
            events = _events_on_disk(home, tenant_id)
            fb = [e for e in events if e["event_type"] == "feedback"]
            self.assertEqual(len(fb), 1)
            self.assertEqual(fb[0]["signal"]["outcome_quality"], "excellent")
            self.assertTrue(fb[0]["signal"]["has_reason"])
            self.assertNotIn("hunter2", json.dumps(fb[0]))  # free text never persisted
            self.assertTrue(fb[0]["audit_ref"])  # joined to the core chain

            core = [c for c in _chain(chain) if c.get("event_type") == "learning.feedback"]
            self.assertEqual(len(core), 1)
            self.assertNotIn("hunter2", json.dumps(core))
            console = [c for c in _console_chain(home, tenant_id) if "feedback_received" in json.dumps(c)]
            self.assertGreaterEqual(len(console), 1)

            # the optimizer epoch was persisted under the TENANT home (never ~/.corvin)
            cfg = home / "tenants" / tenant_id / "skills" / "os_delegation_router_config.json"
            self.assertTrue(cfg.is_file(), cfg)
            self.assertEqual(json.loads(cfg.read_text())["optimizer"]["epoch"], 4)  # 1 + 3 hypotheses

    def test_invalid_feedback_rejected(self):
        with _sandbox(Path(self._tmp)) as (client, home, tenant_id, emitter, chain):
            r = client.post("/v1/console/learning/feedback", json={"task_id": "t", "outcome_quality": "meh"})
            self.assertEqual(r.status_code, 400)
            r = client.post("/v1/console/learning/feedback", json={"task_id": "", "outcome_quality": "good"})
            self.assertEqual(r.status_code, 422)

    def test_accepted_hypothesis_creates_version_and_rollback_is_real(self):
        with _sandbox(Path(self._tmp)) as (client, home, tenant_id, emitter, chain):
            from core.learning.outcome_sink import emit_task_outcome
            from core.skills.os_skills.skill_adapter import SkillAdapter

            # Fast-forward the optimizer past the 50-epoch baseline with a low
            # baseline success rate, so the next hypothesis shows improvement.
            adapter = SkillAdapter("os.delegation_router", tenant_id)
            adapter.state.epoch = 51
            adapter.state.baseline_success_rate = 0.0
            adapter._persist()
            # Ten successful real task outcomes → recent success rate 1.0
            for i in range(10):
                self.assertTrue(emit_task_outcome(
                    tenant_id=tenant_id, task_id=f"t{i}", status="completed", exit_code=0,
                    duration_ms=10, engine="native", task_type="chat", emitter=emitter,
                ))
            import time as _t
            for _ in range(50):  # wait for the worker to flush
                if len([e for e in _events_on_disk(home, tenant_id) if e["event_type"] == "outcome"]) >= 10:
                    break
                _t.sleep(0.05)

            r = client.post(
                "/v1/console/learning/feedback",
                json={"task_id": "task-9", "outcome_quality": "excellent", "would_repeat": True},
            )
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertEqual(body["recent_outcomes"], {"successes": 10, "total": 10})
            accepted = [h for h in body["hypotheses"] if h["accepted"]]
            self.assertEqual(len(accepted), 1)
            self.assertEqual(accepted[0]["param"], "confidence_threshold")
            self.assertEqual(body["current_version"], "v1")
            self.assertAlmostEqual(body["current_config"]["confidence_threshold"], 0.75)

            r = client.get("/v1/console/learning/config-versions")
            versions = r.json()
            self.assertEqual([v["version_id"] for v in versions], ["v1"])
            self.assertAlmostEqual(versions[0]["config"]["confidence_threshold"], 0.75)

            # the learned config is what the router reads on its next execution
            from core.skills.os_skills.skill_adapter import load_skill_config
            cfg, version = load_skill_config("os.delegation_router", tenant_id)
            self.assertEqual((round(cfg.confidence_threshold, 2), version), (0.75, "v1"))

            # rollback: unknown version → 404, known → config restored + audited
            r = client.post("/v1/console/learning/config/rollback?to_version=v9")
            self.assertEqual(r.status_code, 404)
            r = client.post("/v1/console/learning/config/rollback?to_version=v1")
            self.assertEqual(r.status_code, 200, r.text)
            self.assertAlmostEqual(r.json()["config"]["confidence_threshold"], 0.75)

            # preferences now derive from the recorded outcomes
            r = client.get("/v1/console/learning/preferences")
            prefs = r.json()
            self.assertIn("chat", prefs)
            self.assertEqual(prefs["chat"]["observation_count"], 10)
            self.assertIn("native", prefs["chat"]["preferred_skills"])

            emitter.stop(timeout=5.0)
            console = _console_chain(home, tenant_id)
            self.assertTrue(any("skill_config_updated:hypothesis_accepted" in json.dumps(c) for c in console))
            self.assertTrue(any("learning.config_rollback" in json.dumps(c) for c in console))
            events = _events_on_disk(home, tenant_id)
            self.assertTrue(any(e["event_type"] == "config_updated" for e in events))

    def test_preference_confirm_records_event(self):
        with _sandbox(Path(self._tmp)) as (client, home, tenant_id, emitter, chain):
            r = client.post("/v1/console/learning/preferences/confirm?task_type=chat")
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.json()["learning_event_queued"])
            r = client.post("/v1/console/learning/preferences/confirm?task_type=../x")
            self.assertEqual(r.status_code, 422)
            emitter.stop(timeout=5.0)
            self.assertTrue(any(e["event_type"] == "preference" for e in _events_on_disk(home, tenant_id)))

    def test_mutations_require_csrf(self):
        with _sandbox(Path(self._tmp)) as (client, home, tenant_id, emitter, chain):
            client.headers.pop("X-CSRF-Token", None)
            r = client.post("/v1/console/learning/feedback", json={"task_id": "t", "outcome_quality": "good"})
            self.assertEqual(r.status_code, 403)
            r = client.post("/v1/console/learning/config/rollback?to_version=v1")
            self.assertEqual(r.status_code, 403)


if __name__ == "__main__":
    unittest.main()

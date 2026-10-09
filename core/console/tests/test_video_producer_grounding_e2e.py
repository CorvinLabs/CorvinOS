"""HTTP E2E for Video Producer grounding (PLAN-0942 P4).

Real console router, real session + CSRF, real L34/L35/L44 gates, real audit chain in a
temp CORVIN_HOME, the real marketplace plugin's ``grounding`` module reading a fixture
knowledge base (CORVIN_KB_REPO) and the real, git-tracked CorvinOS tree. Replaced: the
production runner (it would call an LLM, a TTS provider and ffmpeg) and the L44 Tier-1
classifier leaf (repo convention, see test_video_producer_routes_e2e.py).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_video_producer_routes_e2e import (  # noqa: E402
    _RecordingRunner, _chain_text, _route_module, _sandbox, _write_tenant_yaml,
)

PACK_ONLY = "Fixture-only sentence: the chain walker lives in verify_chain."
TASK = "Erkläre in drei Minuten, wie die Corvin Auditchain funktioniert."


def _fixture_kb(root: Path) -> Path:
    kb = root / "kb-fixture"
    (kb / "decisions").mkdir(parents=True)
    (kb / "kb" / "graph").mkdir(parents=True)
    (kb / "kb" / "_meta").mkdir(parents=True)
    (kb / "scripts").mkdir()
    (kb / "scripts" / "kb.py").write_text("# fixture: kb_repo() checks that it exists; never executed\n")
    (kb / "kb" / "_meta" / "sources.yaml").write_text("sources: []\n")
    doc = kb / "decisions" / "ADR-0640-audit-chain.md"
    doc.write_text(
        "---\nid: ADR-0640\nstatus: accepted\npaths:\n- operator/forge/forge/security_events.py\n---\n\n"
        "# ADR-0640 — Audit chain\n\n## Decision\n\nEvery record is chained; `verify_chain` walks the file. "
        + PACK_ONLY + "\n"
    )
    ents = [{"uid": "U1", "id": "ADR-0640", "type": "decision", "title": "Audit chain — forgery resistance",
             "status": "accepted", "path": str(doc)}]
    (kb / "kb" / "graph" / "entities.jsonl").write_text("\n".join(json.dumps(e) for e in ents) + "\n")
    (kb / "kb" / "graph" / "relations.jsonl").write_text("")
    return kb


class VideoProducerGroundingE2E(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        self._kb = _fixture_kb(self._tmp)
        fake = self._tmp / "claude"
        fake.write_text("#!/bin/sh\nexit 0\n")
        fake.chmod(0o755)
        self._env = mock.patch.dict(os.environ, {"CORVIN_KB_REPO": str(self._kb), "CORVIN_CLAUDE_BIN": str(fake)})
        self._env.start()
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("CORVIN_KB_TENANT", None)
        self._restore: list = []

    def tearDown(self):
        self._env.stop()
        for obj, attr, orig in reversed(self._restore):
            setattr(obj, attr, orig)

    def _create(self, client, csrf, task=TASK):
        import house_rules as _hr  # type: ignore  # on sys.path via corvin_console._spawn_gates
        self._restore.append((_hr, "_house_rules_classifier", _hr._house_rules_classifier))
        _hr._house_rules_classifier = lambda task, rules, auth, **kw: ("", 0.99, "benign (test stub)")
        return client.post("/v1/console/video/jobs", json={"task": task}, headers={"X-CSRF-Token": csrf})

    def _records(self, home: Path, event: str) -> list:
        out = []
        for line in _chain_text(home).splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("event_type") == event:
                out.append(rec)
        return out

    def test_operator_tenant_gets_a_gated_audited_pack(self):
        with _sandbox(self._tmp) as (client, csrf, home, _clients):
            runner = _RecordingRunner()
            _route_module().get_runner = lambda: runner
            r = self._create(client, csrf)
            self.assertEqual(r.status_code, 200, r.text)
            cfg = runner.calls[-1][2]
            self.assertEqual(cfg["storyboard_backend"], "claude_cli")
            pack = cfg["grounding_pack"]
            self.assertEqual([s["id"] for s in pack["sections"]], ["ADR-0640"])
            text = pack["sections"][0]["text"]
            self.assertIn("sha256(prev_hash || canonical_record_json)[:16]", text)  # from the real tracked code
            self.assertEqual(pack["sections"][0]["truth"], "live")
            released = self._records(home, "video_producer.grounding_released")
            self.assertEqual(len(released), 1)
            d = released[0]["details"]
            self.assertEqual(d["entity_ids"], "ADR-0640")
            self.assertEqual(d["job_id"], r.json()["job_id"])
            self.assertEqual(len(d["pack_sha256"]), 16)
            self.assertNotIn(PACK_ONLY, _chain_text(home))
            stored = "\n".join(p.read_text(errors="replace") for p in (home / "tenants").rglob("*.json"))
            self.assertNotIn(PACK_ONLY, stored)

    def test_another_tenant_never_gets_the_operators_knowledge(self):
        with _sandbox(self._tmp, tenants=("_default", "acme")) as (_c, _t, home, clients):
            runner = _RecordingRunner()
            _route_module().get_runner = lambda: runner
            client, csrf = clients["acme"]
            r = self._create(client, csrf, task="Ein Video über ADR-0640 und die Corvin Audit Chain")
            self.assertEqual(r.status_code, 200, r.text)
            cfg = runner.calls[-1][2]
            self.assertNotIn("grounding_pack", cfg)
            self.assertNotIn("grounding_status", cfg)
            self.assertEqual(self._records(home, "video_producer.grounding_released"), [])

    def test_a_policy_that_keeps_internal_data_in_the_eu_refuses_the_pack(self):
        with _sandbox(self._tmp) as (client, csrf, home, _clients):
            runner = _RecordingRunner()
            _route_module().get_runner = lambda: runner
            # task text is PUBLIC (admitted to the US cloud); the pack is declared INTERNAL
            _write_tenant_yaml(home, "_default", {"data_classification": {"matrix": {
                "PUBLIC": ["local", "eu_cloud", "us_cloud"], "INTERNAL": ["local", "eu_cloud"],
                "CONFIDENTIAL": ["local"], "SECRET": ["local"]}}})
            r = self._create(client, csrf)
            self.assertEqual(r.status_code, 200, r.text)
            cfg = runner.calls[-1][2]
            self.assertNotIn("grounding_pack", cfg)
            self.assertEqual(cfg["grounding_status"], {"status": "refused", "reason": "pack_gate"})
            refused = self._records(home, "video_producer.grounding_refused")
            self.assertEqual(len(refused), 1)
            self.assertTrue(refused[0]["details"]["gate"].startswith("pack:"))
            self.assertEqual(self._records(home, "video_producer.grounding_released"), [])
            self.assertNotIn(PACK_ONLY, _chain_text(home))

    def test_non_corvin_task_and_local_backend_get_no_pack(self):
        with _sandbox(self._tmp) as (client, csrf, home, _clients):
            runner = _RecordingRunner()
            _route_module().get_runner = lambda: runner
            self.assertEqual(self._create(client, csrf, task="Erkläre Photosynthese für Schüler").status_code, 200)
            self.assertNotIn("grounding_pack", runner.calls[-1][2])
            os.environ["CORVIN_CLAUDE_BIN"] = str(self._tmp / "no-such-claude")  # -> local Ollama
            self.assertEqual(self._create(client, csrf).status_code, 200)
            cfg = runner.calls[-1][2]
            self.assertNotIn("grounding_pack", cfg)
            self.assertEqual(cfg["grounding_status"], {"status": "unavailable", "reason": "local_storyboard_model"})


if __name__ == "__main__":
    unittest.main()

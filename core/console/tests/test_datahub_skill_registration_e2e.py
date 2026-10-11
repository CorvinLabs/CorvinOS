"""PLAN-0948 D1 through the real console router: a Data Hub SKILL artifact is a registered,
attributable, injectable SkillForge skill - and goes away with its artifact."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_admin_route import _sandbox  # noqa: E402

_BASE = "/v1/console/datahub"


def _registry(tenant="_default"):
    from core.skills.os_skills.datahub_unified.register import _registry as reg
    return reg(tenant)


class DataHubSkillRegistrationTest(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = self.tmp / "data" / "rows.json"
        self.src.parent.mkdir(parents=True)
        self.src.write_text(json.dumps([{"name": "a", "score": 1}, {"name": "b", "score": 2}]))

    def _create(self, client, csrf, **over):
        body = {"name": "orders", "description": "Order rows", "creation_type": "skill",
                "data_source": "json", "data_path": str(self.src)}
        body.update(over)
        return client.post(f"{_BASE}/create", headers={"X-CSRF-Token": csrf}, json=body)

    def test_skill_artifact_becomes_an_attributed_injectable_skill(self):
        with _sandbox(self.tmp) as (client, csrf, _home, _):
            r = self._create(client, csrf)
            self.assertEqual(r.status_code, 201, r.text)
            meta = r.json()["metadata"]
            aid = meta["artifact_id"]
            self.assertEqual(meta["skill_name"], "assistant.datahub_orders")
            self.assertEqual(meta["data_sources"], [f"datahub:{aid}"])

            spec = _registry().get("assistant.datahub_orders")
            self.assertIsNotNone(spec, "the artifact was not written to the SkillForge registry")
            self.assertEqual(spec.meta["data_sources"], [f"datahub:{aid}"])
            self.assertEqual(spec.type, "learned-experience")
            # the capped, disclosed seed: without it the injection gate never sees the skill
            self.assertEqual(spec.n_grades, 1)
            self.assertLessEqual(spec.mean_score, 0.3)

            from core.skills.prompt_skill_adapter import skillforge_sources
            self.assertIn("assistant.datahub_orders",
                          [s.name for s in skillforge_sources("_default")])

    def test_only_skill_artifacts_are_registered(self):
        with _sandbox(self.tmp) as (client, csrf, _home, _):
            r = self._create(client, csrf, creation_type="dataset", name="orders_ds")
            self.assertEqual(r.status_code, 201, r.text)
            self.assertIsNone(r.json()["metadata"]["skill_name"])
            self.assertIsNone(_registry().get("assistant.datahub_orders_ds"))

    def test_deleting_the_artifact_removes_the_skill(self):
        with _sandbox(self.tmp) as (client, csrf, _home, _):
            aid = self._create(client, csrf).json()["artifact_id"]
            self.assertIsNotNone(_registry().get("assistant.datahub_orders"))
            d = client.delete(f"{_BASE}/{aid}", headers={"X-CSRF-Token": csrf})
            self.assertEqual(d.status_code, 200, d.text)
            self.assertIsNone(_registry().get("assistant.datahub_orders"))

    def test_the_source_hash_and_path_are_kept_for_regeneration_but_not_in_the_listing(self):
        with _sandbox(self.tmp) as (client, csrf, home, _):
            aid = self._create(client, csrf).json()["artifact_id"]
            stored = json.loads(next(Path(home).rglob(f"datahub_artifacts/{aid}.json")).read_text())
            self.assertEqual(len(stored["source"]["sha256"]), 64)
            self.assertTrue(stored["source"]["path"].endswith("rows.json"))
            listing = client.get(f"{_BASE}/list").json()
            self.assertNotIn("rows.json", json.dumps(listing))


if __name__ == "__main__":
    unittest.main()

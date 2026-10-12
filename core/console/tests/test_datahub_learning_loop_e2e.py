"""PLAN-0948 D2-D4 end to end through the production entry points.

create (console route) -> inject (skill_inject.collect_active_skills) -> operator follow-up
(skill_inject.grade_from_user_followup) -> durable inbox -> LearningHost tick -> weights/consensus ->
regeneration queued -> regenerated from the SOURCE (audit-first) -> state persisted and restored.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_admin_route import _sandbox  # noqa: E402

_BASE = "/v1/console/datahub"


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class DataHubLearningLoopTest(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = self.tmp / "data" / "rows.json"
        self.src.parent.mkdir(parents=True)
        self.src.write_text(json.dumps([{"name": "a", "score": 1}, {"name": "b", "score": 2}]))

    def _create(self, client, csrf):
        r = client.post(f"{_BASE}/create", headers={"X-CSRF-Token": csrf}, json={
            "name": "orders", "description": "Order rows", "creation_type": "skill",
            "data_source": "json", "data_path": str(self.src)})
        self.assertEqual(r.status_code, 201, r.text)
        aid = r.json()["artifact_id"]
        self.skill = f"assistant.datahub_{aid}"   # the skill id is derived from the artifact id
        return aid

    def _inject(self):
        from corvin_operator.bridges.shared import skill_inject
        return skill_inject.collect_active_skills(
            channel_id=None, profile={}, task_text="show me the order rows", tenant_id="_default")

    def _followup(self, text):
        from corvin_operator.bridges.shared import skill_inject
        return skill_inject.grade_from_user_followup(
            channel_id=None, profile={}, user_text=text, prev_run_id="run-1",
            prev_skill_names=[self.skill])

    def test_producers_write_content_free_events_to_the_inbox(self):
        with _sandbox(self.tmp) as (client, csrf, _home, _):
            aid = self._create(client, csrf)
            block = self._inject()
            self.assertIn(self.skill, block or "", "the Data Hub skill was not offered to the turn")
            self.assertTrue(self._followup("falsch, das stimmt nicht"))

            from core.background import learning_inbox as li
            events, _cursor = li.read_from("_default", None)
            kinds = [e["event_type"] for e in events]
            self.assertEqual(kinds, ["skill_executed", "user_feedback"])
            for e in events:
                self.assertEqual(e["payload"]["skill_id"], self.skill)
                self.assertEqual(e["payload"]["data_sources"], [f"datahub:{aid}"])
            self.assertLess(events[1]["payload"]["signal"], 0)          # rejection
            self.assertNotIn("falsch", json.dumps(events))               # no user text

    def test_ordinary_skills_produce_nothing(self):
        with _sandbox(self.tmp) as (client, csrf, _home, _):
            from corvin_operator.bridges.shared import skill_inject
            skill_inject.grade_from_user_followup(
                channel_id=None, profile={}, user_text="falsch", prev_run_id="r",
                prev_skill_names=["adr_gate"])
            from core.background import learning_inbox as li
            self.assertEqual(li.read_from("_default", None)[0], [])

    def test_negative_feedback_learns_queues_regenerates_and_survives_a_restart(self):
        with _sandbox(self.tmp) as (client, csrf, home, _):
            aid = self._create(client, csrf)
            self._inject()
            for _ in range(3):
                self._followup("falsch, nochmal")

            from core.background.learning_host import LearningHost, state_path
            from core.skills.os_skills.datahub_unified import regenerate

            host = LearningHost("_default")
            self.assertGreaterEqual(_run(host.tick()), 4)
            sid = f"datahub:{aid}"
            # learned: the source it was attributed to moved below neutral, and it is a NEW source
            self.assertLess(host.daemon.weight_learner.weights[sid], 0.5)
            # net-negative consensus over 3 events queued the skill, exactly once
            self.assertEqual(host.daemon.regeneration_scheduler.regeneration_queue, [self.skill])
            decisions = [d["event_type"] for d in host.daemon.audit_trail]
            self.assertIn("weight_updated", decisions)
            self.assertIn("regeneration_queued", decisions)
            self.assertTrue(state_path("_default").is_file())

            # persisted and restored: a fresh host (the process restarted) knows all of it
            again = LearningHost("_default")
            self.assertTrue(again._restored)
            self.assertEqual(again.daemon.weight_learner.weights[sid], host.daemon.weight_learner.weights[sid])
            self.assertEqual(again.daemon.regeneration_scheduler.regeneration_queue, [self.skill])
            self.assertEqual(again.cursor, host.cursor)
            self.assertEqual(_run(again.tick()), 0, "nothing new: the cursor must not replay the inbox")

            # the source changes, then the queued regeneration runs (batch window elapsed)
            self.src.write_text(json.dumps([{"name": "a", "score": 1}, {"name": "b", "score": 2},
                                            {"name": "c", "score": 3}]))
            again.daemon.regeneration_scheduler.batch_window_seconds = 0.0
            work = _run(again.tick(now=10**12))      # far in the future: the source check is due
            self.assertGreaterEqual(work, 1)
            self.assertEqual(again.regenerations[-1]["outcome"], "completed", again.regenerations)

            art = next(a for a in regenerate.live_skill_artifacts("_default") if a["artifact_id"] == aid)
            self.assertEqual(art["row_count"], 3)
            self.assertEqual(art["generation"], 2)
            self.assertIn("Based on analysis of 3 rows", art["body"])
            from core.skills.os_skills.datahub_unified.register import _registry
            spec = _registry("_default").get(self.skill)
            self.assertEqual(spec.meta["data_sources"], [sid])        # meta preserved
            self.assertGreaterEqual(spec.n_grades, 1)                  # grades preserved
            self.assertIn("3 rows", _registry("_default").get_body(self.skill) or "")

            # audit-first: intent then outcome on the tenant chain, and the chain still verifies
            from core.paths.tenant import tenant_audit_chain
            chain = tenant_audit_chain("_default")     # audit_sink writes THE tenant chain
            recs = [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
            regen = [r["details"]["stage"] for r in recs if r.get("event_type") == "learning.regeneration"]
            self.assertEqual(regen, ["applying", "finished"])
            from corvin_operator.forge.forge import security_events as se
            self.assertTrue(se.verify_chain(chain)[0])

    def test_a_source_that_now_holds_a_secret_is_refused_not_regenerated(self):
        with _sandbox(self.tmp) as (client, csrf, _home, _):
            aid = self._create(client, csrf)
            from core.skills.os_skills.datahub_unified import regenerate
            self.src.write_text(json.dumps([{"note": "key AKIAIOSFODNN7EXAMPLE secret_access=" + "x" * 30}]))
            out = regenerate.regenerate_skill("_default", self.skill, reason="source_changed")
            self.assertEqual(out.outcome, "refused", out)
            art = next(a for a in regenerate.live_skill_artifacts("_default") if a["artifact_id"] == aid)
            self.assertEqual(art["row_count"], 2)                      # untouched

    def test_an_idle_host_backs_off_and_does_nothing(self):
        with _sandbox(self.tmp) as (_client, _csrf, _home, _):
            from core.background import learning_host as lh
            host = lh.LearningHost("_default")
            self.assertEqual(_run(host.tick()), 0)
            self.assertFalse(lh.state_path("_default").exists(), "an idle tick must not write state")

    def test_the_daemon_is_visible_through_the_registry_skill_and_the_dashboard_route(self):
        with _sandbox(self.tmp) as (client, csrf, _home, _):
            self._create(client, csrf)
            self._inject()
            for _ in range(3):
                self._followup("falsch")
            from core.background.learning_host import LearningHost
            _run(LearningHost("_default").tick())

            # the dashboard route (real session) shows what the host saved
            r = client.get("/v1/console/api/learning/daemon")
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertEqual(body["data"]["state"], "saved")
            self.assertEqual(body["data"]["queue"], [self.skill])
            self.assertGreaterEqual(body["data"]["decisions"], 2)
            self.assertFalse(body["running"], "no host thread runs in a test process")
            self.assertNotIn("falsch", r.text)

            # ...and the registry Skill reports the same, audited like any Skill run
            from core.skills.os_skills.learning_daemon_skill import LearningDaemonSkill
            out = LearningDaemonSkill().execute({"tenant_id": "_default"})
            self.assertEqual((out["state"], out["queued"]), ("saved", 1))

    def test_a_source_swapped_for_a_symlink_into_another_tenant_is_refused(self):
        """Review DH-1: create checks the path once; regeneration must re-check it."""
        with _sandbox(self.tmp) as (client, csrf, home, _):
            aid = self._create(client, csrf)
            other = Path(home) / "tenants" / "acme" / "global" / "acme_private.json"
            other.parent.mkdir(parents=True, exist_ok=True)
            other.write_text(json.dumps([{"name": "x"}] * 3))
            self.src.unlink()
            self.src.symlink_to(other)
            from core.skills.os_skills.datahub_unified import regenerate
            out = regenerate.regenerate_skill("_default", self.skill, reason="source_changed")
            self.assertEqual(out.outcome, "refused", out)
            art = next(a for a in regenerate.live_skill_artifacts("_default") if a["artifact_id"] == aid)
            self.assertEqual(art["row_count"], 2)
            self.assertEqual(regenerate.current_sha256(str(self.src), "_default"), "")

    def test_every_terminal_regeneration_outcome_is_recorded(self):
        """Review DH-11: refused/failed/no_artifact paths wrote no record."""
        with _sandbox(self.tmp) as (client, csrf, home, _):
            self._create(client, csrf)
            self.src.write_text("[]")
            from core.skills.os_skills.datahub_unified import regenerate
            from core.paths.tenant import tenant_audit_chain
            self.assertEqual(regenerate.regenerate_skill("_default", self.skill).outcome, "refused")
            self.assertEqual(regenerate.regenerate_skill("_default", "assistant.datahub_ffff").outcome,
                             "no_artifact")
            recs = [json.loads(l) for l in tenant_audit_chain("_default").read_text().splitlines() if l.strip()]
            outcomes = [r["details"]["outcome"] for r in recs if r.get("event_type") == "learning.regeneration"]
            self.assertEqual(outcomes, ["refused", "no_artifact"])

    # -- adversarial review round 2 (R2-DH-1/3/4/5/7) ------------------------------------------

    def test_an_unreadable_artifact_record_raises_instead_of_shortening_the_list(self):
        """R2-DH-1: a short list read as 'deleted' evicts the learned state of live skills."""
        with _sandbox(self.tmp) as (client, csrf, home, _):
            self._create(client, csrf)
            from core.skills.os_skills.datahub_unified import regenerate
            (regenerate._artifacts_dir("_default") / "broken.json").write_text("{not json")
            with self.assertRaises(regenerate.ArtifactStoreUnreadable):
                regenerate.live_skill_artifacts("_default")
            self.assertEqual(regenerate.regenerate_skill("_default", self.skill).outcome, "failed")

    def test_a_hard_linked_source_is_refused(self):
        """R2-DH-3: a hard link to another tenant's file passes every path check."""
        with _sandbox(self.tmp) as (client, csrf, home, _):
            self._create(client, csrf)
            other = Path(home) / "tenants" / "acme" / "global" / "acme_private.json"
            other.parent.mkdir(parents=True, exist_ok=True)
            other.write_text(json.dumps([{"name": "secret-ish"}] * 5))
            self.src.unlink()
            try:
                import os
                os.link(other, self.src)
            except OSError:
                self.skipTest("hard links not possible across these directories")
            from core.skills.os_skills.datahub_unified import regenerate
            out = regenerate.regenerate_skill("_default", self.skill, reason="source_changed")
            self.assertEqual(out.outcome, "refused", out)

    def test_the_stored_hash_is_the_hash_of_what_was_learned_from(self):
        """R2-DH-4: hash and ingestion read the same bytes (one snapshot)."""
        with _sandbox(self.tmp) as (client, csrf, home, _):
            aid = self._create(client, csrf)
            self.src.write_text(json.dumps([{"name": n, "score": i} for i, n in enumerate("abcde")]))
            import hashlib
            want = hashlib.sha256(self.src.read_bytes()).hexdigest()
            from core.skills.os_skills.datahub_unified import regenerate
            out = regenerate.regenerate_skill("_default", self.skill, reason="source_changed")
            self.assertEqual(out.outcome, "completed", out)
            art = next(a for a in regenerate.live_skill_artifacts("_default") if a["artifact_id"] == aid)
            self.assertEqual(art["source"]["sha256"], want)
            self.assertEqual(art["row_count"], 5)

    def test_a_failed_registry_update_restores_body_and_grades(self):
        """R2-DH-5: update_body writes the body, then the grades - a failure between them is undone."""
        with _sandbox(self.tmp) as (client, csrf, home, _):
            self._create(client, csrf)
            from core.skills.os_skills.datahub_unified import regenerate
            from core.skills.os_skills.datahub_unified.register import SCOPE
            reg = regenerate._registry("_default")
            reg._registry(SCOPE).set_grades(self.skill, [{"score": 0.4, "notes": "seed"}])
            body_before = reg.get_body(self.skill)
            self.src.write_text(json.dumps([{"name": "z", "score": 9}] * 3))
            from skill_forge.registry import SkillRegistry
            real = SkillRegistry.set_grades

            def boom(self_, name, grades):
                if grades and grades[0].get("notes") == "seed" and getattr(boom, "armed", True):
                    boom.armed = False
                    raise OSError("disk full")
                return real(self_, name, grades)
            SkillRegistry.set_grades = boom
            try:
                out = regenerate.regenerate_skill("_default", self.skill, reason="source_changed")
            finally:
                SkillRegistry.set_grades = real
            self.assertEqual(out.outcome, "failed", out)
            self.assertIn("restored", out.detail)
            reg = regenerate._registry("_default")
            self.assertEqual(regenerate._strip_front_matter(reg.get_body(self.skill)),
                             regenerate._strip_front_matter(body_before))
            self.assertEqual([g.get("notes") for g in reg.get_in_scope(self.skill, SCOPE).grades], ["seed"])

    def test_a_record_naming_another_artifacts_skill_is_ignored(self):
        """R2-DH-7: a planted record must not get another skill rebuilt from ITS source."""
        with _sandbox(self.tmp) as (client, csrf, home, _):
            aid = self._create(client, csrf)
            from core.skills.os_skills.datahub_unified import regenerate
            planted = dict(next(a for a in regenerate.live_skill_artifacts("_default")
                                if a["artifact_id"] == aid))
            planted["artifact_id"] = "ab" * 16
            (regenerate._artifacts_dir("_default") / f"{planted['artifact_id']}.json").write_text(
                json.dumps(planted))
            ids = [a["artifact_id"] for a in regenerate.live_skill_artifacts("_default")]
            self.assertEqual(ids, [aid])


if __name__ == "__main__":
    unittest.main()

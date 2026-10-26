"""D6 end to end: the Data Hub tidies up after itself - through the production entry points.

A Data Hub skill is created through the console route (artifact + registry skill + plugin-slot mirror), the
state is then damaged the way real life damages it (an orphan mirror, an artifact marked deleted behind the
skill's back, the same data uploaded twice, a registry full of dead skills), and the ONLY thing that acts is a
``LearningHost.tick()`` - the call the shipped hosts make. Nothing here calls ``prune.run`` to do the work.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_admin_route import _sandbox  # noqa: E402

_BASE = "/v1/console/datahub"
_REPO = Path(__file__).resolve().parents[3]
_REAL_SLOT = _REPO / "corvin_operator" / "skill-forge" / "skills" / "dyn"


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class DataHubPruneTest(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = self.tmp / "data" / "rows.json"
        self.src.parent.mkdir(parents=True)
        self.src.write_text(json.dumps([{"name": "a", "score": 1}, {"name": "b", "score": 2}]))
        self.src2 = self.tmp / "data" / "other.json"
        self.src2.write_text(json.dumps([{"city": "x", "pop": 10}, {"city": "y", "pop": 20}, {"city": "z", "pop": 30}]))

    # ── helpers ──────────────────────────────────────────────────────────────

    def _create(self, client, csrf, *, src=None, name="orders") -> str:
        r = client.post(f"{_BASE}/create", headers={"X-CSRF-Token": csrf}, json={
            "name": name, "description": "Order rows", "creation_type": "skill",
            "data_source": "json", "data_path": str(src or self.src)})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["artifact_id"]

    @staticmethod
    def _slot() -> Path:
        return Path(os.environ["CORVIN_PLUGIN_SLOT_DIR"])

    def _orphan(self, hex_id: str, *, age_s: float = 7200.0, prefix: str = "assistant_datahub_") -> Path:
        d = self._slot() / f"{prefix}{hex_id}"
        d.mkdir(parents=True, exist_ok=True)
        md = d / "SKILL.md"
        md.write_text(f"---\nname: {prefix}{hex_id}\ndescription: test leftover\n---\n\n# leftover {hex_id}\n")
        old = time.time() - age_s
        os.utime(md, (old, old))
        return d

    @staticmethod
    def _artifact_file(home: Path, aid: str) -> Path:
        return home / "tenants" / "_default" / "global" / "datahub_artifacts" / f"{aid}.json"

    def _mark_deleted(self, home: Path, aid: str) -> None:
        p = self._artifact_file(home, aid)
        d = json.loads(p.read_text())
        d["status"] = "deleted"
        p.write_text(json.dumps(d))

    @staticmethod
    def _registered(name: str):
        from core.skills.os_skills.datahub_unified.register import _registry
        return _registry("_default").get(name)

    @staticmethod
    def _chain(event: str) -> list[dict]:
        from core.paths.tenant import tenant_audit_chain
        out = []
        for line in Path(tenant_audit_chain("_default")).read_text().splitlines():
            if f'"{event}"' in line:
                out.append(json.loads(line))
        return out

    @staticmethod
    def _tick(host, now=None):
        return _run(host.tick(now=now if now is not None else time.time()))

    # ── the rules ────────────────────────────────────────────────────────────

    def test_a_host_tick_removes_orphan_mirrors_and_only_those(self):
        with _sandbox(self.tmp) as (client, csrf, home, _):
            aid = self._create(client, csrf)
            live = f"assistant.datahub_{aid}"
            self.assertTrue((self._slot() / f"assistant_datahub_{aid}" / "SKILL.md").is_file())
            old = [self._orphan(h) for h in ("0000aaaa", "0000abc1", "0000d00d")]
            fresh = self._orphan("00000fff", age_s=5.0)            # a create() may still be writing it
            not_hex = self._orphan("notes", prefix="assistant_datahub_")   # not a Data Hub artifact id
            other = self._orphan("0000aaaa", prefix="assistant_other_")    # not ours at all

            from core.background.learning_host import LearningHost, read_state_summary, state_path
            from core.skills.os_skills.datahub_unified import prune

            host = LearningHost("_default")
            self.assertGreaterEqual(self._tick(host), 3)

            for d in old:
                self.assertFalse(d.exists(), f"orphan mirror {d.name} was not removed")
            for d in (fresh, not_hex, other):
                self.assertTrue(d.exists(), f"{d.name} must stay (grace / not a Data Hub id / not ours)")
            self.assertIsNotNone(self._registered(live), "the live skill must stay")
            self.assertTrue((self._slot() / f"assistant_datahub_{aid}" / "SKILL.md").is_file())

            rows = prune.removed_list("_default")
            self.assertEqual(sorted(r["name"] for r in rows),
                             sorted(f"assistant.datahub_{h}" for h in ("0000aaaa", "0000abc1", "0000d00d")))
            self.assertTrue(all(r["reason"] == "orphan_mirror" and r["kind"] == "mirror" for r in rows))
            self.assertNotIn("leftover", json.dumps(rows), "the removal list must hold no body text")

            chain = self._chain("learning.datahub_pruned")
            self.assertEqual(len(chain), 3, "one chained intent per removal, written BEFORE the removal")
            self.assertTrue(all(r["details"]["stage"] == "applying" for r in chain))

            st = host.status()["prune"]
            self.assertEqual((st["last_removed"], st["total_removed"]), (3, 3))
            self.assertTrue(state_path("_default").is_file())
            self.assertEqual(read_state_summary("_default")["prune"]["total_removed"], 3)

            # idempotent and rate-limited: nothing new to do, and the pass is not due again within a day
            self.assertEqual(self._tick(host, time.time() + 3600), 0)
            self.assertEqual(len(prune.removed_list("_default")), 3)
            # a day later the grace period of the fresh orphan is over: it - and only it - goes now
            self._tick(host, time.time() + 25 * 3600)
            self.assertFalse(fresh.exists())
            self.assertEqual([r["name"] for r in prune.removed_list("_default")][3:], ["assistant.datahub_00000fff"])
            for d in (not_hex, other):
                self.assertTrue(d.exists(), f"{d.name} is not a Data Hub artifact mirror and must never be judged")

    def test_a_legacy_name_slug_goes_and_its_name_is_never_recorded(self):
        """Before review DH-2 the skill id came from the NAME the operator typed - here a patient's. Such a
        leftover is removed, and neither the removal list nor the audit chain may repeat the name."""
        template = ("---\nname: x\ndescription: leftover\n---\n\n# Patient\n\n**Type:** Learned Experience\n"
                    "**Generated:** Automated from data\n\n## Pattern\n\nBased on analysis of 2 rows:\n")
        with _sandbox(self.tmp) as (client, csrf, home, _):
            legacy = self._slot() / "assistant_datahub_patient_erika_mustermann_hiv_positive"
            by_hand = self._slot() / "assistant_datahub_notes"           # a skill someone wrote by hand
            for d, text in ((legacy, template), (by_hand, "---\nname: notes\n---\n\nmy own notes\n")):
                d.mkdir(parents=True)
                (d / "SKILL.md").write_text(text)
                old = time.time() - 7200
                os.utime(d / "SKILL.md", (old, old))

            from core.background.learning_host import LearningHost
            from core.paths.tenant import tenant_audit_chain
            from core.skills.os_skills.datahub_unified import prune

            host = LearningHost("_default")
            self._tick(host)

            self.assertFalse(legacy.exists(), "the legacy leftover must be removed")
            self.assertTrue(by_hand.exists(), "a hand-written skill without the creator's template is not ours")
            rows = prune.removed_list("_default")
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]["name"].startswith("assistant.datahub_legacy_"), rows)
            blob = json.dumps(rows) + Path(tenant_audit_chain("_default")).read_text()
            for word in ("erika", "mustermann", "hiv", "patient"):
                self.assertNotIn(word, blob.lower(), f"the legacy name leaked into a record: {word}")

    def test_a_mirror_another_tenant_still_holds_is_never_an_orphan(self):
        with _sandbox(self.tmp, tenants=("_default", "other")) as (_client, _csrf, home, clients):
            other_client, other_csrf = clients["other"]
            prev = os.environ.get("CORVIN_TENANT_ID")
            os.environ["CORVIN_TENANT_ID"] = "other"
            try:
                aid = self._create(other_client, other_csrf)
            finally:
                os.environ["CORVIN_TENANT_ID"] = prev or "_default"
            mirror = self._slot() / f"assistant_datahub_{aid}"
            self.assertTrue(mirror.is_dir(), "precondition: the other tenant's skill is mirrored")
            old = time.time() - 7200
            os.utime(mirror / "SKILL.md", (old, old))

            from core.background.learning_host import LearningHost
            self._tick(LearningHost("_default"))
            self.assertTrue(mirror.is_dir(), "tenant _default's pass removed a mirror tenant 'other' still holds")

    def test_a_skill_whose_artifact_is_gone_is_removed_with_its_mirror(self):
        with _sandbox(self.tmp) as (client, csrf, home, _):
            keep = self._create(client, csrf, src=self.src2, name="cities")
            gone = self._create(client, csrf)
            self._mark_deleted(home, gone)                          # deleted behind the skill's back
            from core.background.learning_host import LearningHost
            from core.skills.os_skills.datahub_unified import prune

            self._tick(LearningHost("_default"))

            self.assertIsNone(self._registered(f"assistant.datahub_{gone}"))
            self.assertFalse((self._slot() / f"assistant_datahub_{gone}").exists())
            self.assertIsNotNone(self._registered(f"assistant.datahub_{keep}"))
            self.assertEqual([r["reason"] for r in prune.removed_list("_default")], ["artifact_gone"])

            v = prune.verify("_default", [f"assistant.datahub_{gone}"], scan_roots=[self.tmp / "data"])
            self.assertTrue(v["ok"], v)

    def test_duplicates_keep_one_and_an_earned_grade_is_never_overruled(self):
        with _sandbox(self.tmp) as (client, csrf, home, _):
            a = self._create(client, csrf)
            b = self._create(client, csrf, name="orders copy")      # the same data under another name
            na, nb = f"assistant.datahub_{a}", f"assistant.datahub_{b}"
            from core.background.learning_host import LearningHost
            from core.skills.os_skills.datahub_unified import prune
            from core.skills.os_skills.datahub_unified.register import _registry

            reg = _registry("_default")
            self.assertNotEqual(reg.get_body(na), reg.get_body(nb), "precondition: the titles differ")
            self.assertEqual(prune._dup_key(reg.get_body(na)), prune._dup_key(reg.get_body(nb)),
                             "precondition: the same data yields the same data profile")
            # the OLDER one carries an earned (organic) grade: it must be the one that stays
            reg.grade(na, "run-earned", 0.9, notes="operator approved", organic=True)
            plan = prune.plan("_default")
            self.assertEqual([(c.name, c.reason, c.kept) for c in plan.candidates], [(nb, "duplicate", na)])

            self._tick(LearningHost("_default"))
            self.assertIsNotNone(self._registered(na))
            self.assertIsNone(self._registered(nb))
            self.assertEqual(prune.removed_list("_default")[-1]["kept"], na)

    def test_two_skills_with_earned_grades_are_both_kept(self):
        with _sandbox(self.tmp) as (client, csrf, home, _):
            a = self._create(client, csrf)
            b = self._create(client, csrf, name="orders copy")
            from core.skills.os_skills.datahub_unified import prune
            from core.skills.os_skills.datahub_unified.register import _registry

            reg = _registry("_default")
            for n in (a, b):
                reg.grade(f"assistant.datahub_{n}", "run-earned", 0.9, notes="approved", organic=True)
            self.assertEqual(prune.plan("_default").candidates, [])

    def test_the_unused_rule_needs_a_full_watch_window_and_respects_use(self):
        with _sandbox(self.tmp) as (client, csrf, home, _):
            aid = self._create(client, csrf)
            name = f"assistant.datahub_{aid}"
            from core.skills.os_skills.datahub_unified import prune

            created = float(self._registered(name).created_at)
            late = created + prune.UNUSED_DAYS * 86400 + 10
            # watched for the whole window, never offered, never rated, bootstrap grade only -> goes
            p = prune.plan("_default", now=late, observing_since=created)
            self.assertEqual([(c.name, c.reason) for c in p.candidates], [(name, "unused")])
            # the same skill was offered to a turn -> stays
            self.assertEqual(prune.plan("_default", now=late, observing_since=created, used_ids=[name]).candidates, [])
            # the daemon has only watched for a day -> the rule is not active at all
            quiet = prune.plan("_default", now=late, observing_since=late - 86400)
            self.assertFalse(quiet.unused_rule_active)
            self.assertEqual(quiet.candidates, [])
            # a young skill is never unused
            self.assertEqual(prune.plan("_default", now=created + 86400, observing_since=created - 10**7).candidates, [])

    def test_a_pass_that_would_empty_the_registry_is_refused_as_a_whole(self):
        from unittest import mock

        with _sandbox(self.tmp) as (client, csrf, home, _):
            names = []
            for i in range(4):          # the free tier admits 5 new skills a day, so the guard is lowered below
                src = self.tmp / "data" / f"r{i}.json"
                src.write_text(json.dumps([{"k": i, "v": "x" * (i + 1)}]))
                aid = self._create(client, csrf, src=src, name=f"set{i}")
                names.append(aid)
                self.assertIsNotNone(self._registered(f"assistant.datahub_{aid}"), "precondition: registered")
                self._mark_deleted(home, aid)
            from core.background.learning_host import LearningHost
            from core.skills.os_skills.datahub_unified import prune

            host = LearningHost("_default")
            with mock.patch.object(prune, "BULK_GUARD_MIN", 3):
                self._tick(host)

            for aid in names:
                self.assertIsNotNone(self._registered(f"assistant.datahub_{aid}"), "the bulk guard must refuse")
            self.assertEqual(host.status()["prune"]["last_refused"], "bulk_guard")
            self.assertEqual(prune.removed_list("_default"), [])
            self.assertEqual(len(self._chain("learning.datahub_prune_refused")), 1)

    def test_no_chained_intent_means_nothing_is_touched(self):
        with _sandbox(self.tmp) as (client, csrf, home, _):
            d = self._orphan("0000aaaa")
            from core.background.learning_host import LearningHost
            from core.deployment import audit_sink
            from core.skills.os_skills.datahub_unified import prune

            real = audit_sink.emit

            def refuse(event, *a, **kw):
                if event == "learning.datahub_pruned":
                    raise audit_sink.AuditWriteFailed("test: record did not commit")
                return real(event, *a, **kw)

            audit_sink.emit = refuse
            try:
                host = LearningHost("_default")
                self._tick(host)
            finally:
                audit_sink.emit = real
            self.assertTrue(d.exists(), "audit-first: an item whose intent did not commit must be left alone")
            self.assertEqual(prune.removed_list("_default"), [])
            self.assertEqual(host.status()["prune"]["last_failed"], 1)

    def test_removed_names_have_no_reference_left_when_verified(self):
        with _sandbox(self.tmp) as (client, csrf, home, _):
            self._orphan("0000d00d")
            from core.background.learning_host import LearningHost
            from core.skills.os_skills.datahub_unified import prune

            self._tick(LearningHost("_default"))
            name = "assistant.datahub_0000d00d"
            self.assertTrue(prune.verify("_default", [name], scan_roots=[self.tmp / "data"])["ok"])
            (self.tmp / "data" / "note.md").write_text("see assistant_datahub_0000d00d for details")
            v = prune.verify("_default", [name], scan_roots=[self.tmp / "data"])
            self.assertFalse(v["ok"])
            self.assertIn(name, v["references"])


class PytestNeverWritesTheRealSlotTest(unittest.TestCase):
    """The cause of the pile: unset, the plugin slot is the checkout's REAL ``skills/dyn`` directory, and every
    test that created a skill left a mirror there. The root conftest now isolates ``CORVIN_PLUGIN_SLOT_DIR`` too."""

    def test_the_session_isolates_the_slot_and_a_registration_lands_in_the_sandbox(self):
        slot = os.environ.get("CORVIN_PLUGIN_SLOT_DIR", "")
        self.assertTrue(slot, "conftest must set CORVIN_PLUGIN_SLOT_DIR for the whole session")
        resolved = Path(slot).resolve()
        self.assertFalse(resolved == _REAL_SLOT.resolve() or _REAL_SLOT.resolve() in resolved.parents,
                         f"the slot points into the checkout: {slot}")

        from core.skills.os_skills.datahub_unified.register import _registry, register_skill_artifact

        aid = "feedface00000000"
        before = sorted(p.name for p in _REAL_SLOT.iterdir()) if _REAL_SLOT.is_dir() else []
        try:
            register_skill_artifact("_default", artifact_id=aid, name="isolation probe", description="probe",
                                    body="# probe\n\nbody\n")
            self.assertTrue((resolved / f"assistant_datahub_{aid}" / "SKILL.md").is_file(),
                            "the mirror must land in the sandboxed slot")
            after = sorted(p.name for p in _REAL_SLOT.iterdir()) if _REAL_SLOT.is_dir() else []
            self.assertEqual(before, after, "a test run changed the real plugin slot")
        finally:
            _registry("_default").delete(f"assistant.datahub_{aid}", reason="test cleanup")


if __name__ == "__main__":
    unittest.main()

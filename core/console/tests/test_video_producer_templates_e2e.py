"""HTTP E2E proof: CorvinTV video templates (CONCEPT-0137) are reachable from the console.

Same harness as test_video_producer_cinematic_e2e (real console router, real session + CSRF, real L44/L34/L35 gates and
the real audit chain; only the production runner is a recorder). The plugin's own prompt and checks are proven in the
plugin's tests/test_video_templates.py; this file proves the list a panel draws from, the remembered choice (file, audit
record, survives a new request), and that the chosen template id reaches the runner and the job_started record."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_video_producer_routes_e2e as _routes  # noqa: E402
from test_video_producer_routes_e2e import _RecordingRunner, _route_module, _sandbox  # noqa: E402

H = lambda csrf: {"X-CSRF-Token": csrf}  # noqa: E731
V = "/v1/console/video"
WANT = {"standard", "short", "marketing", "code_debugging", "research", "didactic", "tutorial", "release_notes",
        "executive_brief", "postmortem", "faq"}


def _events(home: Path, event: str) -> list:
    chain = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    out = []
    for ln in chain.read_text(encoding="utf-8").splitlines() if chain.exists() else []:
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if rec.get("event_type") == event or rec.get("event") == event:
            out.append(rec)
    return out


class VideoTemplatesE2E(unittest.TestCase):
    _base = _routes.VideoProducerRoutesE2E
    setUp, tearDown, _benign_l44 = _base.setUp, _base.tearDown, _base._benign_l44

    def _post(self, client, csrf, body):
        self._benign_l44()
        return client.post(f"{V}/jobs", json=body, headers=H(csrf))

    def test_the_list_carries_every_template_and_keeps_the_prompt_rules_server_side(self):
        with _sandbox(self._tmp) as (client, csrf, home, _c):
            r = client.get(f"{V}/templates")
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertEqual({t["id"] for t in body["templates"]}, WANT)
            self.assertEqual(body["templates"][0]["id"], "standard")
            self.assertEqual(body["default_template_id"], "standard")
            short = next(t for t in body["templates"] if t["id"] == "short")
            self.assertFalse(short["available"])
            self.assertTrue(short["unavailable_reason"])
            self.assertEqual(short["aspect"], "9:16")
            self.assertEqual(short["hook"], {"kind": "question", "max_words": 12})
            for t in body["templates"]:
                self.assertTrue(t["name"]["de"] and t["name"]["en"] and t["structure"])
                self.assertNotIn("rules", t)
                self.assertNotIn("avoid", t)

    def test_a_chosen_template_reaches_the_runner_and_the_job_started_record(self):
        with _sandbox(self._tmp) as (client, csrf, home, _c):
            mod = _route_module()
            runner = _RecordingRunner()
            mod.get_runner = lambda: runner
            r = self._post(client, csrf, {"task": "Why does caching help?", "video_template": "didactic"})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(runner.calls[-1][2]["video_template"], "didactic")
            started = _events(home, "video_producer.job_started")
            self.assertEqual(started[-1]["details"]["video_template"], "didactic")

    def test_a_plain_request_carries_no_template_key_and_records_standard(self):
        with _sandbox(self._tmp) as (client, csrf, home, _c):
            mod = _route_module()
            runner = _RecordingRunner()
            mod.get_runner = lambda: runner
            self.assertEqual(self._post(client, csrf, {"task": "Explain caching."}).status_code, 200)
            self.assertNotIn("video_template", runner.calls[-1][2])
            self.assertEqual(_events(home, "video_producer.job_started")[-1]["details"]["video_template"], "standard")

    def test_the_remembered_choice_is_audited_persisted_and_used_by_the_next_job(self):
        with _sandbox(self._tmp) as (client, csrf, home, _c):
            mod = _route_module()
            runner = _RecordingRunner()
            mod.get_runner = lambda: runner
            self.assertEqual(len(_events(home, "video_producer.template_default_changed")), 0)
            r = client.put(f"{V}/templates/default", json={"template_id": "research"}, headers=H(csrf))
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["default_template_id"], "research")
            ev = _events(home, "video_producer.template_default_changed")
            self.assertEqual(len(ev), 1)
            self.assertEqual((ev[0]["details"]["template_id"], ev[0]["details"]["previous_template_id"]), ("research", "standard"))
            self.assertEqual(client.get(f"{V}/templates").json()["default_template_id"], "research")
            stored = list(home.rglob("template_default"))
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0].read_text(encoding="ascii"), "research")
            self.assertEqual(stored[0].stat().st_mode & 0o777, 0o600)
            # repeating the same choice is not a change
            client.put(f"{V}/templates/default", json={"template_id": "research"}, headers=H(csrf))
            self.assertEqual(len(_events(home, "video_producer.template_default_changed")), 1)
            # the next plain job uses it; an explicit choice wins; "standard" switches it off for that job
            self.assertEqual(self._post(client, csrf, {"task": "Summarise the paper."}).status_code, 200)
            self.assertEqual(runner.calls[-1][2]["video_template"], "research")
            self.assertEqual(self._post(client, csrf, {"task": "x", "video_template": "faq"}).status_code, 200)
            self.assertEqual(runner.calls[-1][2]["video_template"], "faq")
            self.assertEqual(self._post(client, csrf, {"task": "x", "video_template": "standard"}).status_code, 200)
            self.assertNotIn("video_template", runner.calls[-1][2])

    def test_an_unavailable_or_unknown_template_is_refused_and_nothing_is_started_or_recorded(self):
        with _sandbox(self._tmp) as (client, csrf, home, _c):
            mod = _route_module()
            runner = _RecordingRunner()
            mod.get_runner = lambda: runner
            before = client.get(f"{V}/jobs").json()["total"]
            r = client.put(f"{V}/templates/default", json={"template_id": "short"}, headers=H(csrf))
            self.assertEqual(r.status_code, 409, r.text)
            self.assertIn("vertical", r.json()["detail"])
            self.assertEqual(client.put(f"{V}/templates/default", json={"template_id": "nope"}, headers=H(csrf)).status_code, 422)
            self.assertEqual(self._post(client, csrf, {"task": "x", "video_template": "short"}).status_code, 409)
            self.assertEqual(self._post(client, csrf, {"task": "x", "video_template": "nope"}).status_code, 422)
            self.assertEqual(self._post(client, csrf, {"task": "x", "video_template": "../etc"}).status_code, 422)
            self.assertEqual(runner.calls, [])
            self.assertEqual(client.get(f"{V}/jobs").json()["total"], before)
            self.assertEqual(len(_events(home, "video_producer.template_default_changed")), 0)
            self.assertEqual(client.get(f"{V}/templates").json()["default_template_id"], "standard")

    def test_a_film_and_a_revision_do_not_pick_up_the_remembered_template(self):
        with _sandbox(self._tmp) as (client, csrf, home, _c):
            mod = _route_module()
            runner = _RecordingRunner()
            mod.get_runner = lambda: runner
            client.put(f"{V}/templates/default", json={"template_id": "marketing"}, headers=H(csrf))
            r = self._post(client, csrf, {"task": "Show it.", "mode": "cinematic"})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertNotIn("video_template", runner.calls[-1][2])
            r = self._post(client, csrf, {"task": "Show it.", "mode": "cinematic", "video_template": "marketing"})
            self.assertEqual(r.status_code, 422, r.text)
            self.assertEqual(len(runner.calls), 1)


if __name__ == "__main__":
    unittest.main()

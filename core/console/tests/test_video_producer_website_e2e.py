"""HTTP E2E proof: a CorvinTV website tour is reachable from the console's job endpoint (ADR-2336, CONCEPT-0139).

Same harness as test_video_producer_cinematic_e2e (real console router, real session + CSRF, the real L44/L34/L35
gates, the real tenant audit chain; only the production runner is a recorder). The tour pipeline itself (real
job_main, Chromium, ffmpeg, the page in a real browser) is proven in the plugin's tests/test_website_tour_e2e.py;
this file proves the HOST side:

* the template list carries `website` with its mode and input; it can never become the tenant's default;
* a tour needs the page address in the brief, the tenant's website-capture switch and an enabled egress policy that
  allows the host - every refusal happens BEFORE a job exists (no job, no capture_started);
* an allowed request hands the runner mode `website`, the normalised address and the policy as DATA, no slide template
  and no grounding; `capture_started` carries the host and a hash prefix, never the address, a path or a query;
* when the job ends the host closes it with `capture_finished` (outcome ok / failed), once;
* the bundle of a finished tour downloads for its own tenant only.
"""
from __future__ import annotations

import importlib
import json
import sys
import tempfile
import time
import unittest
import zipfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_video_producer_routes_e2e as _routes  # noqa: E402
from test_video_producer_routes_e2e import _RecordingRunner, _route_module, _sandbox, _write_tenant_yaml  # noqa: E402
from test_video_producer_styles_e2e import _chain_records  # noqa: E402

V = "/v1/console/video"
H = lambda csrf: {"X-CSRF-Token": csrf}  # noqa: E731
TASK = "Tour https://example.com/pricing?campaign=SECRET-QUERY-123#top and explain the plans in German."


def _enable(ca, csrf, home, tenant="tenant_a", **egress):
    _write_tenant_yaml(home, tenant, {"egress": {"enabled": True, "default_action": "allow", **egress}})
    assert ca.put(f"{V}/settings", headers=H(csrf), json={"capture_enabled": True}).status_code == 200


def _wait(pred, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


class WebsiteTourRouteE2E(unittest.TestCase):
    _base = _routes.VideoProducerRoutesE2E
    setUp, tearDown, _benign_l44 = _base.setUp, _base.tearDown, _base._benign_l44

    def _post(self, client, csrf, body):
        self._benign_l44()
        return client.post(f"{V}/jobs", json=body, headers=H(csrf))

    def _jobs(self, client):
        return client.get(f"{V}/jobs").json()["total"]

    def test_the_template_is_listed_with_its_mode_and_can_never_be_the_default(self):
        with _sandbox(self._tmp, tenants=("tenant_a",)) as (client, csrf, home, _c):
            t = {x["id"]: x for x in client.get(f"{V}/templates").json()["templates"]}
            self.assertEqual((t["website"]["mode"], t["website"]["input"]), ("website", {"kind": "url"}))
            self.assertEqual(t["marketing"]["mode"], "slides")
            r = client.put(f"{V}/templates/default", headers=H(csrf), json={"template_id": "website"})
            self.assertEqual(r.status_code, 422, r.text)
            self.assertIn("default", r.json()["detail"])
            self.assertEqual(client.get(f"{V}/templates").json()["default_template_id"], "standard")

    def test_every_refusal_happens_before_a_job_exists(self):
        with _sandbox(self._tmp, tenants=("tenant_a",)) as (client, csrf, home, _c):
            mod = _route_module()
            runner = _RecordingRunner()
            mod.get_runner = lambda: runner
            before = self._jobs(client)
            body = {"task": TASK, "video_template": "website"}
            # no address
            r = self._post(client, csrf, {"task": "Explain the pricing page", "video_template": "website"})
            self.assertEqual(r.status_code, 422, r.text)
            self.assertIn("address", r.json()["detail"])
            # a revision cannot be a tour
            r = self._post(client, csrf, {**body, "base_job_id": "job_aaaaaaaa"})
            self.assertEqual(r.status_code, 422, r.text)
            # the switch is off (default)
            r = self._post(client, csrf, body)
            self.assertEqual(r.status_code, 403, r.text)
            self.assertIn("off", r.json()["detail"])
            # on, but no egress policy
            self.assertEqual(client.put(f"{V}/settings", headers=H(csrf), json={"capture_enabled": True}).status_code, 200)
            r = self._post(client, csrf, body)
            self.assertEqual(r.status_code, 403, r.text)
            self.assertIn("egress policy", r.json()["detail"])
            # a host the tenant's own policy forbids
            _write_tenant_yaml(home, "tenant_a", {"egress": {"enabled": True, "default_action": "allow", "forbidden_hosts": ["example.com"]}})
            r = self._post(client, csrf, body)
            self.assertEqual(r.status_code, 403, r.text)
            self.assertIn("does not allow", r.json()["detail"])
            # a loopback address is refused at the gate of the host too (the plugin refuses it again)
            r = self._post(client, csrf, {"task": "Tour http://127.0.0.1:8765/ now", "video_template": "website"})
            self.assertIn(r.status_code, (403, 422), r.text)
            self.assertEqual(runner.calls, [])
            self.assertEqual(self._jobs(client), before)
            self.assertEqual(_chain_records(home, "tenant_a", "video_producer.capture_started"), [])

    def test_an_allowed_tour_reaches_the_runner_as_data_and_is_audited_without_the_address(self):
        with _sandbox(self._tmp, tenants=("tenant_a",)) as (client, csrf, home, _c):
            mod = _route_module()
            runner = _RecordingRunner()
            mod.get_runner = lambda: runner
            _enable(client, csrf, home, forbidden_hosts=["evil.example"])
            r = self._post(client, csrf, {"task": TASK, "video_template": "website", "website_langs": ["de", "en"], "website_minutes": 3})
            self.assertEqual(r.status_code, 200, r.text)
            job_id, _task, cfg = runner.calls[-1]
            self.assertEqual(cfg["mode"], "website")
            self.assertEqual(cfg["website_url"], "https://example.com/pricing?campaign=SECRET-QUERY-123")  # normalised, no fragment
            self.assertEqual(cfg["website_egress"]["forbidden"], ["evil.example"])
            self.assertEqual(cfg["website_egress"]["default"], "allow")
            self.assertIsInstance(cfg["website_console_names"], list)
            self.assertEqual((cfg["website_langs"], cfg["website_minutes"]), (["de", "en"], 3))
            self.assertNotIn("video_template", cfg)
            self.assertFalse([k for k in cfg if k.startswith("grounding")], cfg)
            started = _chain_records(home, "tenant_a", "video_producer.capture_started")
            self.assertEqual(len(started), 1)
            d = started[0]["details"]
            self.assertEqual(d["host"], "example.com")
            self.assertEqual(len(d["url_sha256_prefix"]), 12)
            chain = (home / "tenants" / "tenant_a" / "global" / "forge" / "audit.jsonl").read_text(encoding="utf-8")
            for secret in ("SECRET-QUERY-123", "/pricing"):
                self.assertNotIn(secret, chain)
            job_started = _chain_records(home, "tenant_a", "video_producer.job_started")[-1]["details"]
            self.assertEqual(job_started["video_template"], "website")

            # when the job ends, the host closes the capture record - once
            st = importlib.import_module(f"{mod._PLUGIN_PKG}.storage").get_storage(str(home / "tenants" / "tenant_a" / "video_producer"))
            job = st.get_job(job_id)
            job.status, job.completed_at = "complete", datetime.now()
            st.save_job(job)
            self.assertTrue(_wait(lambda: _chain_records(home, "tenant_a", "video_producer.capture_finished")), "capture_finished was not written")
            time.sleep(0.4)
            fin = _chain_records(home, "tenant_a", "video_producer.capture_finished")
            self.assertEqual(len(fin), 1)
            self.assertEqual((fin[0]["details"]["outcome"], fin[0]["details"]["host"]), ("ok", "example.com"))
            self.assertNotIn("SECRET-QUERY-123", json.dumps(fin[0]))

    def test_a_plain_request_is_unchanged(self):
        with _sandbox(self._tmp, tenants=("tenant_a",)) as (client, csrf, home, _c):
            mod = _route_module()
            runner = _RecordingRunner()
            mod.get_runner = lambda: runner
            r = self._post(client, csrf, {"task": "Explain caching."})
            self.assertEqual(r.status_code, 200, r.text)
            cfg = runner.calls[-1][2]
            self.assertFalse([k for k in cfg if k == "mode" or k.startswith("website")], cfg)

    def test_the_bundle_of_a_finished_tour_downloads_for_its_own_tenant_only(self):
        import shutil
        import subprocess  # noqa: F401
        with _sandbox(self._tmp, tenants=("tenant_a", "tenant_b")) as (_c, _t, home, clients):
            (ca, csrf_a), (cb, _csrf_b) = clients["tenant_a"], clients["tenant_b"]
            mod = _route_module()
            pkg = importlib.import_module(mod._PLUGIN_PKG)  # noqa: F841
            storage_mod = importlib.import_module(f"{mod._PLUGIN_PKG}.storage")
            models = importlib.import_module(f"{mod._PLUGIN_PKG}.models")
            base = home / "tenants" / "tenant_a" / "video_producer"
            st = storage_mod.get_storage(str(base))
            job_id = "job_bbbbbbbb"
            out = base / "videos" / job_id
            (out / "site").mkdir(parents=True)
            (out / "output.mp4").write_bytes(b"\x00\x00\x00 ftypmp42")
            with zipfile.ZipFile(out / "website-bundle.zip", "w") as z:
                z.writestr("index.html", "<html></html>")
            st.save_job(models.VideoJob(id=job_id, task="t", status="complete"))
            st.save_video_output(models.VideoOutput(job_id=job_id, video_path=str(out / "output.mp4"), metadata={"mode": "website"}))
            r = ca.get(f"{V}/videos/{job_id}/bundle")
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.headers["content-type"], "application/zip")
            self.assertIn("index.html", zipfile.ZipFile(__import__("io").BytesIO(r.content)).namelist())
            self.assertEqual(cb.get(f"{V}/videos/{job_id}/bundle").status_code, 404)   # another tenant: absent
            # a slide video has no bundle
            slide = "job_cccccccc"
            (base / "videos" / slide).mkdir(parents=True)
            (base / "videos" / slide / "output.mp4").write_bytes(b"x")
            st.save_job(models.VideoJob(id=slide, task="t", status="complete"))
            st.save_video_output(models.VideoOutput(job_id=slide, video_path=str(base / "videos" / slide / "output.mp4"), metadata={}))
            self.assertEqual(ca.get(f"{V}/videos/{slide}/bundle").status_code, 404)


if __name__ == "__main__":
    unittest.main()

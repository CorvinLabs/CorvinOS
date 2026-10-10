"""HTTP E2E proof: revising a produced video and attaching source material (Video Producer panel).

Same harness as test_video_producer_routes_e2e (real console router, real session + CSRF, two tenants,
the real L44/L34/L35 gates; only the production runner is a recorder). A revision is a NEW job whose task
carries the previous storyboard plus the change request; the original stays untouched.
"""
from __future__ import annotations

import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_video_producer_routes_e2e import (  # noqa: E402
    VideoProducerRoutesE2E as _Base, _RecordingRunner, _route_module, _sandbox, _write_tenant_yaml,
)

H = lambda csrf: {"X-CSRF-Token": csrf}  # noqa: E731


def _complete_job_with_storyboard(rec_tenant_home: Path, job_id: str, task: str) -> None:
    """Persist a finished job with a two-scene storyboard through the plugin's own storage."""
    mod = _route_module()
    from datetime import datetime

    models = sys.modules[mod.VideoJob.__module__]
    sb = models.Storyboard(id="sb1", task=task, scenes=[
        models.Scene(id="s01", kind="title", duration_ms=8000, narration_text="Welcome to the audit chain."),
        models.Scene(id="s02", kind="narration", duration_ms=12000, narration_text="Every record is hash-linked to the last."),
    ])
    job = mod.VideoJob(id=job_id, task=task, status="complete", storyboard=sb, completed_at=datetime.now())
    mod._get_storage(str(rec_tenant_home / "video_producer")).save_job(job)


class RevisionAndSourcesE2E(unittest.TestCase):
    setUp, tearDown, _benign_l44, _create = _Base.setUp, _Base.tearDown, _Base._benign_l44, _Base._create

    def _post(self, client, csrf, body):
        self._benign_l44()
        return client.post("/v1/console/video/jobs", json=body, headers=H(csrf))

    def test_revision_is_a_new_job_carrying_the_old_storyboard_and_the_change_request(self):
        with _sandbox(self._tmp, tenants=("tenant_a", "tenant_b")) as (_c, _t, home, clients):
            mod = _route_module()
            runner = _RecordingRunner()
            mod.get_runner = lambda: runner
            (ca, csrf_a), (cb, csrf_b) = clients["tenant_a"], clients["tenant_b"]
            base_id = "job_aaaaaaaa"
            _complete_job_with_storyboard(home / "tenants" / "tenant_a", base_id, "Explain the audit chain.")

            r = self._post(ca, csrf_a, {"task": "Make scene 2 shorter and add a diagram", "base_job_id": base_id})
            self.assertEqual(r.status_code, 200, r.text)
            new_id = r.json()["job_id"]
            self.assertNotEqual(new_id, base_id)
            _job, sent, _cfg = runner.calls[-1]
            self.assertIn("REVISION OF AN EXISTING VIDEO", sent)
            self.assertIn("Every record is hash-linked to the last.", sent)
            self.assertIn("Change request: Make scene 2 shorter and add a diagram", sent)

            shown = ca.get(f"/v1/console/video/jobs/{new_id}").json()
            self.assertEqual(shown["task"], "Make scene 2 shorter and add a diagram", "the library shows the request, not the brief")
            self.assertEqual(shown["revision_of"], base_id)
            by_id = {j["id"]: j for j in ca.get("/v1/console/video/jobs").json()["jobs"]}
            self.assertEqual(by_id[new_id]["revision_of"], base_id)
            self.assertIsNone(by_id[base_id]["revision_of"])
            self.assertEqual(ca.get(f"/v1/console/video/jobs/{base_id}").json()["status"], "complete", "the original stays")

            # tenant isolation: B can neither revise nor see A's job
            rb = self._post(cb, csrf_b, {"task": "change it", "base_job_id": base_id})
            self.assertEqual(rb.status_code, 404, rb.text)

    def test_only_a_produced_video_can_be_revised_and_the_id_is_validated(self):
        with _sandbox(self._tmp) as (client, csrf, home, _clients):
            mod = _route_module()
            runner = _RecordingRunner()
            mod.get_runner = lambda: runner
            pending = self._create(client, csrf).json()["job_id"]
            runner.calls.clear()
            self.assertEqual(self._post(client, csrf, {"task": "x", "base_job_id": pending}).status_code, 409)
            self.assertEqual(self._post(client, csrf, {"task": "x", "base_job_id": "job_deadbeef"}).status_code, 404)
            self.assertEqual(self._post(client, csrf, {"task": "x", "base_job_id": "../../etc"}).status_code, 404)
            self.assertEqual(runner.calls, [])

    def test_sources_reach_the_runner_and_pass_the_same_gates_as_the_task(self):
        with _sandbox(self._tmp) as (client, csrf, home, _clients):
            mod = _route_module()
            runner = _RecordingRunner()
            mod.get_runner = lambda: runner
            ok = self._post(client, csrf, {"task": "Explain our notes in one scene.",
                                           "sources": [{"name": "notes.md", "text": "The sweep runs every 10 minutes."}]})
            self.assertEqual(ok.status_code, 200, ok.text)
            sent = runner.calls[-1][1]
            self.assertIn("SOURCE MATERIAL 1 (notes.md)", sent)
            self.assertIn("The sweep runs every 10 minutes.", sent)
            self.assertEqual(client.get(f"/v1/console/video/jobs/{ok.json()['job_id']}").json()["task"],
                             "Explain our notes in one scene.", "attachments do not bloat the library title")

            runner.calls.clear()
            _write_tenant_yaml(home, "_default", {"egress": {"enabled": False}})
            bad = self._post(client, csrf, {"task": "Explain our notes.",
                                            "sources": [{"name": "env.txt", "text": "password = hunter2secret"}]})
            self.assertEqual(bad.status_code, 403, "a secret inside an attachment is refused by L34, like in the task")
            self.assertEqual(runner.calls, [])

            too_many = [{"name": f"f{i}.txt", "text": "x"} for i in range(5)]
            self.assertEqual(self._post(client, csrf, {"task": "x", "sources": too_many}).status_code, 422)
            big = [{"name": f"f{i}.txt", "text": "y" * 15000} for i in range(3)]
            self.assertEqual(self._post(client, csrf, {"task": "x", "sources": big}).status_code, 413)

    def test_attachment_extraction_through_the_real_route(self):
        with _sandbox(self._tmp) as (client, csrf, _home, _clients):
            url = "/v1/console/video/attachments/extract"
            r = client.post(url, files=[("files", ("brief.md", io.BytesIO("# Title\nUmlaut: Größe".encode()), "text/markdown"))], headers=H(csrf))
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["sources"][0]["text"], "# Title\nUmlaut: Größe")
            self.assertEqual(client.post(url, files=[("files", ("a.exe", io.BytesIO(b"MZ"), "application/octet-stream"))], headers=H(csrf)).status_code, 415)
            self.assertEqual(client.post(url, files=[("files", ("a.txt", io.BytesIO(b"\xff\xfe\x00bad"), "text/plain"))], headers=H(csrf)).status_code, 415)
            self.assertEqual(client.post(url, files=[("files", ("a.txt", io.BytesIO(b""), "text/plain"))], headers=H(csrf)).status_code, 400)
            self.assertEqual(client.post(url, files=[("files", ("big.txt", io.BytesIO(b"z" * (2 * 1024 * 1024 + 1)), "text/plain"))], headers=H(csrf)).status_code, 413)
            self.assertEqual(client.post(url, files=[("files", (f"{i}.txt", io.BytesIO(b"x"), "text/plain")) for i in range(5)], headers=H(csrf)).status_code, 400)
            self.assertEqual(client.post(url, files=[("files", ("a.txt", io.BytesIO(b"x"), "text/plain"))]).status_code, 403, "CSRF required")
            if shutil.which("pdftotext"):
                self.assertEqual(client.post(url, files=[("files", ("a.pdf", io.BytesIO(b"not a pdf"), "application/pdf"))], headers=H(csrf)).status_code, 422)


if __name__ == "__main__":
    unittest.main()

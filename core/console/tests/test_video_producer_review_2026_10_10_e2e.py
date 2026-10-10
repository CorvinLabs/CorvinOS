"""HTTP E2E proof for the host-side fixes of the Video Producer adversarial review of 2026-10-10.

Same harness as test_video_producer_routes_e2e (real console router, real session + CSRF, real gates,
two tenants; only the production runner is a recorder). Covered: the body caps on the attachment and job
routes on BOTH hosts, the per-tenant cap on jobs in flight, a bounded pdftotext, the base video's
language named in a revision brief, and the quality checks that measured the wrong thing.
"""
from __future__ import annotations

import importlib
import io
import shutil
import sys
import tempfile
import time
import unittest
import zlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_video_producer_routes_e2e as _routes  # noqa: E402
from test_video_producer_revision_sources_e2e import H, _complete_job_with_storyboard  # noqa: E402
from test_video_producer_routes_e2e import _RecordingRunner, _route_module, _sandbox  # noqa: E402

V = "/v1/console/video"


def _pdf_bomb(lines: int = 200_000, pages: int = 2000) -> bytes:
    """~0.5 MB: one deflated content stream shared by every page (the review's reproduction)."""
    comp = zlib.compress((b"BT /F1 6 Tf 10 10 Td (" + b"A" * 200 + b") Tj ET\n") * lines, 9)
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % (5 + i) for i in range(pages)) + b"] /Count %d >>" % pages,
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(comp) + comp + b"\nendstream"]
    objs += [b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents 4 0 R >>"] * pages
    out, offs = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)
    return bytes(out)


class VideoProducerReview20261010E2E(unittest.TestCase):
    _base = _routes.VideoProducerRoutesE2E
    setUp, tearDown, _benign_l44, _create = _base.setUp, _base.tearDown, _base._benign_l44, _base._create

    def test_attachment_and_job_bodies_are_capped_before_parsing_on_both_hosts(self):
        from starlette import formparsers

        bc = importlib.import_module("corvin_console.body_cap")
        for name in ("standalone_cap_for", "gateway_cap_for"):
            cap_for = getattr(bc, name)
            self.assertEqual(cap_for(f"{V}/attachments/extract"), 8 * 1024 * 1024 + 256 * 1024, name)
            self.assertEqual(cap_for(f"{V}/jobs"), 1024 * 1024, name)
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, _home, _clients):
            client.app.middleware("http")(bc.make_body_cap_middleware(bc.gateway_cap_for))
            parsed = []
            orig = formparsers.MultiPartParser.parse

            async def spy(self_):
                parsed.append(1)
                return await orig(self_)

            with mock.patch.object(formparsers.MultiPartParser, "parse", spy):
                r = client.post(f"{V}/attachments/extract", files=[("files", ("x.txt", io.BytesIO(b"z" * (20 * 1024 * 1024)), "text/plain"))],
                                headers=H(csrf))
                self.assertEqual(r.status_code, 413, r.text[:200])
                self.assertEqual(parsed, [], "the refused body was parsed/spooled")
                ok = client.post(f"{V}/attachments/extract", files=[("files", (f"{i}.txt", io.BytesIO(b"y" * (1536 * 1024)), "text/plain")) for i in range(4)],
                                 headers=H(csrf))
                self.assertEqual(ok.status_code, 200, ok.text[:200])  # four legitimate 1.5 MiB files still fit
            big = client.post(f"{V}/jobs", content=b'{"task": "' + b"a" * (2 * 1024 * 1024) + b'"}',
                              headers={**H(csrf), "Content-Type": "application/json"})
            self.assertEqual(big.status_code, 413, big.text[:200])

    def test_a_tenant_cannot_have_more_than_two_jobs_in_flight(self):
        with _sandbox(self._tmp, tenants=("tenant_a", "tenant_b")) as (_c, _t, _home, clients):
            mod = _route_module()
            runner = _RecordingRunner()  # jobs stay "pending": nothing finishes them
            mod.get_runner = lambda: runner
            self.assertEqual(_routes.sys.modules[_routes._MOD]._MAX_ACTIVE_JOBS_PER_TENANT, 1000)  # the sandbox lifts it ...
            limit = mock.patch.object(mod, "_MAX_ACTIVE_JOBS_PER_TENANT", 2)  # ... this test restores the shipped value
            limit.start()
            self.addCleanup(limit.stop)
            (ca, csrf_a), (cb, csrf_b) = clients["tenant_a"], clients["tenant_b"]
            self.assertEqual(self._create(ca, csrf_a).status_code, 200)
            self.assertEqual(self._create(ca, csrf_a).status_code, 200)
            third = self._create(ca, csrf_a)
            self.assertEqual(third.status_code, 429, third.text)
            self.assertEqual(len(runner.calls), 2, "the refused job never reached the runner")
            self.assertEqual(ca.get(f"{V}/jobs").json()["total"], 2, "nor the store")
            self.assertEqual(self._create(cb, csrf_b).status_code, 200, "another tenant is not affected")

    @unittest.skipUnless(shutil.which("pdftotext"), "pdftotext not installed")
    def test_a_pdf_bomb_is_refused_within_bounded_memory(self):
        with _sandbox(self._tmp) as (client, csrf, _home, _clients):
            mod = _route_module()
            t0 = time.monotonic()
            r = client.post(f"{V}/attachments/extract", files=[("files", ("bomb.pdf", io.BytesIO(_pdf_bomb()), "application/pdf"))],
                            headers=H(csrf))
            elapsed = time.monotonic() - t0
            self.assertIn(r.status_code, (200, 422), r.text[:200])
            if r.status_code == 200:  # page-capped and output-capped, never the whole 2000 pages
                self.assertLessEqual(len(r.json()["sources"][0]["text"]), mod._MAX_SOURCE_CHARS)
            self.assertLess(elapsed, mod._PDF_TIMEOUT_S + 5)
            seen = []
            import subprocess as _sp

            def spy(argv, *a, **k):
                seen.append(list(argv))
                return _sp.CompletedProcess(argv, 0, b"text", b"")

            with mock.patch.object(_sp, "run", spy):
                ok = client.post(f"{V}/attachments/extract", files=[("files", ("a.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf"))],
                                 headers=H(csrf))
            self.assertEqual(ok.status_code, 200, ok.text)
            joined = " ".join(seen[0])
            self.assertIn("ulimit -v", joined)
            self.assertIn("-l", seen[0])

    def test_a_revision_brief_names_the_base_videos_language(self):
        with _sandbox(self._tmp) as (client, csrf, home, _clients):
            mod = _route_module()
            runner = _RecordingRunner()
            mod.get_runner = lambda: runner
            base_id = "job_bbbbbbbb"
            tenant_home = home / "tenants" / "_default"
            _complete_job_with_storyboard(tenant_home, base_id, "Erkläre die Audit-Chain.")
            models = sys.modules[mod.VideoJob.__module__]
            mod._get_storage(str(tenant_home / "video_producer")).save_video_output(
                models.VideoOutput(job_id=base_id, video_path=str(tenant_home / "x.mp4"), metadata={"language": "de"}))
            self._benign_l44()
            r = client.post(f"{V}/jobs", json={"task": "Mach Szene 2 kürzer", "base_job_id": base_id}, headers=H(csrf))
            self.assertEqual(r.status_code, 200, r.text)
            self.assertIn("Narration language: German", runner.calls[-1][1])


class QualityChecksMeasureTheRightThing(unittest.TestCase):
    """``checks_for`` is what /video/jobs/{id}/quality returns; it is fed the stored output metadata."""

    def _scenes(self):
        return [{"index": 1, "id": "s1", "planned_s": 8.0, "actual_s": 3.6, "voice_s": 3.6, "voice_drift_pct": 0.0,
                 "rendered": True, "has_voice": True, "has_slide": True, "drift_pct": -55.0},
                {"index": 2, "id": "s2", "planned_s": 8.0, "actual_s": 3.4, "voice_s": 3.4, "voice_drift_pct": 0.0,
                 "rendered": True, "has_voice": True, "has_slide": True, "drift_pct": -57.5}]

    def _checks(self, metadata):
        from corvin_console.video_quality import checks_for

        container = {"format": "mp4", "duration_s": 7.0, "size_bytes": 1}
        out = checks_for(container, None, {"codec": "aac", "sample_rate_hz": 24000, "channels": 1},
                         {"streams": 0, "files": []}, self._scenes(), [{"id": "s1"}, {"id": "s2"}], True, metadata=metadata)
        return {c["id"]: c for c in out}

    def test_a_silent_placeholder_voice_fails_the_voice_check(self):
        self.assertEqual(self._checks({"tts_provider_used": "mock"})["voice"]["status"], "fail")
        self.assertEqual(self._checks({"tts_provider_used": "openai"})["voice"]["status"], "pass")

    def test_timing_follows_the_narration_not_the_storyboard_hint(self):
        c = self._checks({"tts_provider_used": "openai"})
        self.assertEqual(c["timing"]["status"], "pass", c["timing"])
        self.assertEqual(c["runtime"]["status"], "pass", c["runtime"])

    def test_a_slide_kept_with_overlaps_fails_the_layout_check(self):
        c = self._checks({"layout_collisions": [{"scene": "s2", "action": "kept"}]})
        self.assertEqual(c["layout"]["status"], "fail")
        self.assertEqual(self._checks({"layout_collisions": [{"scene": "s2", "action": "replaced_with_quote"}]})["layout"]["status"], "pass")


if __name__ == "__main__":
    unittest.main()

"""HTTP E2E for the Video Producer board (ADR-2260 decisions 2, 3, 9, 11; T-0197/T-0199).

The real console router (session + CSRF, real gates), two tenants, the marketplace plugin's REAL importer, asset
store and Chromium, the real tenant audit chain, the real GDPR Art. 17 dispatch, and - for the video - the plugin's
REAL ProcessJobRunner starting the REAL job_main as a child process (storyboard injected, TTS reduced to the offline
mock tier, as in ``test_video_producer_process_e2e``). Nothing under test is stubbed.
"""
from __future__ import annotations

import asyncio
import importlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_video_producer_routes_e2e as _routes  # noqa: E402
from test_video_producer_routes_e2e import _route_module, _sandbox  # noqa: E402
from test_video_producer_styles_e2e import _chain_records, _plugin  # noqa: E402

V = "/v1/console/video"
H = lambda csrf: {"X-CSRF-Token": csrf}  # noqa: E731


def _board_svg() -> bytes:
    pkg = sys.modules[_route_module()._PLUGIN_PKG]
    return (Path(pkg.__file__).resolve().parent.parent / "tests" / "fixtures" / "penpot_board.svg").read_bytes()


def _many(n: int) -> bytes:
    # Penpot-shaped (the reader refuses an SVG without Penpot shape ids): one shape group per rectangle
    rects = "".join(f'<g id="shape-00000000-0000-4000-8000-{i:012d}">'
                    f'<rect x="{(i * 13) % 1500}" y="{(i * 29) % 800}" width="40" height="30" fill="#3366cc"/></g>'
                    for i in range(n))
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="900">{rects}</svg>'.encode()


def _import(client, csrf, body, name="board.svg"):
    return client.post(f"{V}/assets/import-penpot", files={"file": (name, io.BytesIO(body), "image/svg+xml")},
                       data={"caption": "Brand board"}, headers=H(csrf))


STEPS = [{"shapes": [0, 1], "label": "Brand mark"}, {"shapes": [2, 3, 4, 5]}, {"shapes": [6, 7]}]


def _playwright() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


@unittest.skipUnless(_playwright(), "playwright is not installed")
class BoardRoutesE2E(unittest.TestCase):
    _base = _routes.VideoProducerRoutesE2E
    setUp, tearDown, _benign_l44 = _base.setUp, _base.tearDown, _base._benign_l44

    def test_import_assign_steps_tenant_scope_audit_and_erasure(self):
        from forge import security_events as se

        with _sandbox(Path(tempfile.mkdtemp()), tenants=("tenant_a", "tenant_b")) as (_c, _t, home, clients):
            (ca, csrf_a), (cb, _csrf_b) = clients["tenant_a"], clients["tenant_b"]
            r = _import(ca, csrf_a, _board_svg())
            self.assertEqual(r.status_code, 201, r.text)
            asset = r.json()["asset"]
            aid = asset["id"]
            # imported: the model is stored, the design is UNASSIGNED
            self.assertEqual((asset["board"]["has_design"], asset["board"]["ready"]), (True, False))
            self.assertIn("assign steps first", asset["board"]["reason"])
            store_dir = home / "tenants" / "tenant_a" / "video_producer" / "assets" / aid
            self.assertTrue((store_dir / "design.json").is_file())

            d = ca.get(f"{V}/assets/{aid}/design")
            self.assertEqual(d.status_code, 200, d.text)
            body = d.json()
            self.assertEqual(len(body["design"]["shapes"]), 8)
            self.assertEqual((body["steps"], body["limits"]["max_steps"]), ([], 12))
            self.assertEqual(cb.get(f"{V}/assets/{aid}/design").status_code, 404, "another tenant's design")

            # attach = assign steps: refused with a 4xx when invalid, before anything is stored
            gap = ca.patch(f"{V}/assets/{aid}", json={"steps": [{"shapes": [0, 1]}]}, headers=H(csrf_a))
            self.assertEqual(gap.status_code, 422, gap.text)
            self.assertIn("exactly one step", gap.json()["detail"])
            markup = ca.patch(f"{V}/assets/{aid}", json={"steps": [{"shapes": list(range(8)), "label": "<b>x</b>"}]},
                              headers=H(csrf_a))
            self.assertEqual(markup.status_code, 422)
            ok = ca.patch(f"{V}/assets/{aid}", json={"steps": STEPS}, headers=H(csrf_a))
            self.assertEqual(ok.status_code, 200, ok.text)
            self.assertEqual((ok.json()["asset"]["board"]["ready"], ok.json()["asset"]["board"]["steps"]), (True, 3))
            again = ca.get(f"{V}/assets/{aid}/design").json()
            self.assertEqual(again["labels"], ["Brand mark", None, "Beispiel Video-Folie"])
            listed = {a["id"]: a for a in ca.get(f"{V}/assets").json()["assets"]}
            self.assertTrue(listed[aid]["board"]["ready"])
            # a caption-only change keeps the steps (absent = unchanged), null clears them
            ca.patch(f"{V}/assets/{aid}", json={"caption": "Renamed"}, headers=H(csrf_a))
            self.assertEqual(ca.get(f"{V}/assets/{aid}/design").json()["steps"], STEPS)
            cleared = ca.patch(f"{V}/assets/{aid}", json={"steps": None}, headers=H(csrf_a))
            self.assertFalse(cleared.json()["asset"]["board"]["ready"])
            ca.patch(f"{V}/assets/{aid}", json={"steps": STEPS}, headers=H(csrf_a))

            # a design over the board bound imports as a picture and is refused only as a board, with the count
            big = _import(ca, csrf_a, _many(300), "big.svg")
            self.assertEqual(big.status_code, 201, big.text)
            big_id = big.json()["asset"]["id"]
            refused = ca.patch(f"{V}/assets/{big_id}", json={"steps": [{"shapes": list(range(300))}]}, headers=H(csrf_a))
            self.assertEqual(refused.status_code, 422)
            self.assertIn("300 shapes", refused.json()["detail"])

            # audit: the existing events, the step count, never a byte of the design
            upd = _chain_records(home, "tenant_a", "video_producer.asset_updated")
            self.assertEqual([x["details"].get("step_count") for x in upd], [3, 3, 0, 3])
            acc = _chain_records(home, "tenant_a", "video_producer.asset_accessed")
            self.assertIn("design", [x["details"]["purpose"] for x in acc])
            chain = (home / "tenants" / "tenant_a" / "global" / "forge" / "audit.jsonl").read_text()
            self.assertNotIn("Beispiel", chain)
            self.assertNotIn("Brand mark", chain)
            for rec in upd + acc:
                self.assertNotIn("_dropped_fields", rec["details"])
            ok_chain, problems = se.verify_chain(home / "tenants" / "tenant_a" / "global" / "forge" / "audit.jsonl")
            self.assertTrue(ok_chain, problems)

            # GDPR Art. 17: the uploader's assets go whole - picture, record and design.json
            from corvin_console import chat_router
            from entity_extract import EntityPlan  # type: ignore[import-not-found]

            owner = json.loads((store_dir / "asset.json").read_text())["uploaded_by"]
            plan = EntityPlan(entity_type="erasure_request", slots={"subject_id": owner}, confidence=1.0)
            # /erase asks for a one-time code first; take the one the router would have shown the user.
            plan.slots["confirm"] = chat_router._issue_erase_code("tenant_a", plan.slots["subject_id"])
            result = asyncio.run(chat_router.dispatch(plan, "tenant_a"))
            layers = {lr["layer_id"]: lr for lr in result.payload["layers"]}
            self.assertEqual(layers["L-video-producer"]["status"], "applied", layers["L-video-producer"])
            self.assertFalse(store_dir.exists(), "design.json survived the erasure")


def _procs(job_id):
    needle = f"CORVIN_VIDEO_JOB_ID={job_id}\0".encode()
    out = []
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                if needle in open(f"/proc/{d}/environ", "rb").read():
                    out.append(int(d))
            except OSError:
                pass
    return out


@unittest.skipUnless(os.path.isdir("/proc") and _playwright(), "needs /proc and playwright")
class BoardVideoE2E(unittest.TestCase):
    _base = _routes.VideoProducerRoutesE2E
    setUp, tearDown, _benign_l44 = _base.setUp, _base.tearDown, _base._benign_l44

    def test_a_job_with_a_board_reveals_step_n_before_step_n_plus_one_in_the_mp4(self):
        from PIL import Image, ImageChops

        with _sandbox(Path(tempfile.mkdtemp()), tenants=("tenant_a",)) as (_c, _t, home, clients):
            mod = _route_module()
            client, csrf = clients["tenant_a"]
            aid = _import(client, csrf, _board_svg()).json()["asset"]["id"]
            self.assertEqual(client.patch(f"{V}/assets/{aid}", json={"steps": STEPS}, headers=H(csrf)).status_code, 200)
            storyboard = {"id": "sb_board_http", "didactic_strategy": "rich_visual", "scenes": [{
                "id": "s1", "kind": "solution", "duration_ms": 14000, "beats": [0, 1, 2],
                "narration_text": "Zuerst das Markenzeichen. Dann die Farbfelder der Marke. Zum Schluss der Titel der Folie.",
                "template": "board", "data": {"title": "Unser Board", "asset": aid}}]}
            ar = importlib.import_module(f"{mod._PLUGIN_PKG}.async_runner")
            entry = Path(ar.__file__).resolve().parents[1] / "tests" / "_vp_job_main_mock_tts.py"
            if not entry.is_file():
                self.skipTest("the loaded plugin copy has no tests/_vp_job_main_mock_tts.py")
            inner = ar.ProcessJobRunner(entry=[sys.executable, str(entry)])

            class Injecting:
                running_job_ids = staticmethod(inner.running_job_ids)
                cancel_job = staticmethod(inner.cancel_job)

                async def start_job(self, job_id, task, config):
                    return await inner.start_job(job_id, task, dict(config, storyboard=storyboard, tts_engine="auto"))

            mod.get_runner = lambda: Injecting()
            self._benign_l44()
            r = client.post(f"{V}/jobs", json={"task": "Show our imported brand board step by step."}, headers=H(csrf))
            self.assertEqual(r.status_code, 200, r.text)
            job_id = r.json()["job_id"]
            t0 = time.monotonic()
            while time.monotonic() - t0 < 300:
                d = client.get(f"{V}/jobs/{job_id}").json()
                if d["status"] in ("complete", "error", "cancelled"):
                    break
                time.sleep(0.3)
            self.assertEqual(d["status"], "complete", d)
            video = Path(d["video_output_path"])
            probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height",
                                               "-of", "json", str(video)], capture_output=True, text=True, check=True).stdout)
            self.assertIn({"codec_type": "video", "width": 1920, "height": 1080}, probe["streams"])
            storage = _plugin("storage").get_storage(str(home / "tenants" / "tenant_a" / "video_producer"))
            md = storage.get_video_output(job_id).metadata
            cue = md["cues"][0]
            self.assertEqual((cue["template"], cue["beats_source"]), ("board", "llm"), cue)
            self.assertEqual(md["board_fallbacks"], [])
            ats = [it["at"] for it in cue["items"]]
            self.assertTrue(all(a is not None for a in ats) and ats == sorted(ats), ats)

            def frame(t, name):
                out = Path(tempfile.mkdtemp()) / name
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(video),
                                "-frames:v", "1", str(out)], check=True)
                return Image.open(out).convert("RGB")

            # the board's own pixels: group 0 is the brand mark (blue circle), group 1 the colour swatches (orange)
            def has(img, rgb, tol=40):
                small = img.resize((480, 270))
                return sum(all(abs(p[i] - rgb[i]) <= tol for i in range(3)) for p in small.getdata())

            between = frame((ats[0] + ats[1]) / 2 + 0.3, "b01.png")      # step 0 shown, step 1 not yet
            after = frame(ats[1] + 1.2, "a1.png")
            self.assertGreater(has(between, (0x00, 0x66, 0xCC)), 30, "group 0 (brand mark) not revealed at its cue")
            self.assertLess(has(between, (0xFF, 0x66, 0x00)), 5, "group 1 (swatches) visible before its cue")
            self.assertGreater(has(after, (0xFF, 0x66, 0x00)), 5, "group 1 never appeared")
            self.assertIsNotNone(ImageChops.difference(between, after).getbbox())
            self.assertTrue(_wait_gone(job_id), _procs(job_id))


def _wait_gone(job_id, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not _procs(job_id):
            return True
        time.sleep(0.1)
    return not _procs(job_id)


if __name__ == "__main__":
    unittest.main()

"""HTTP E2E for the Video Producer process runner (PLAN-0946 R1a spike).

The real console router (session + CSRF, real gates, real tenant store); the production runner is the
plugin's REAL ProcessJobRunner starting the REAL job_main as a child process with the real Chromium and
ffmpeg, producing a real tiny MP4. Two things are substituted, both outside the code under test: the
operator-supplied storyboard is injected into the job config (no model call) and the child's TTS chain is
reduced to the offline mock tier through the runner's ``entry`` argument (no network, no spend).
"""
from __future__ import annotations

import importlib
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
from test_video_producer_styles_e2e import V, H, _no_previews, _wire  # noqa: E402

STORYBOARD = {
    "id": "sb_proc_e2e", "didactic_strategy": "rich_visual",
    "scenes": [
        {"id": "s1", "kind": "title", "duration_ms": 8000,
         "narration_text": "Willkommen bei Acme, hier sehen Sie unser eigenes Erscheinungsbild.",
         "template": "hero", "data": {"badge": "Acme", "title": "Unser Look.", "accent": "Nicht Corvin."}},
        {"id": "s2", "kind": "solution", "duration_ms": 8000,
         "narration_text": "Die Pipeline führt vom Auftrag über den Browser zum fertigen Video mit unseren Farben.",
         "template": "diagram", "data": {"title": "Pipeline", "nodes": [{"label": "Auftrag"}, {"label": "Browser"},
                                                                           {"label": "Video"}], "highlight": 1}},
    ],
}


def _procs(job_id):
    needle = f"CORVIN_VIDEO_JOB_ID={job_id}\0".encode()
    out = []
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                if needle in open(f"/proc/{d}/environ", "rb").read():
                    out.append((int(d), open(f"/proc/{d}/comm").read().strip()))
            except OSError:
                pass
    return out


def _descendants(root: int):
    kids = {}
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                ppid = int(open(f"/proc/{d}/stat").read().rsplit(")", 1)[1].split()[1])
            except (OSError, IndexError, ValueError):
                continue
            kids.setdefault(ppid, []).append(int(d))
    out, todo = [], [root]
    while todo:
        for c in kids.get(todo.pop(), []):
            out.append(c)
            todo.append(c)
    return out


def _chromium_of(leader: int):
    out = []
    for pid in _descendants(leader):
        try:
            argv0 = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0", 1)[0].decode("utf-8", "replace").lower()
        except OSError:
            continue
        if "chrom" in argv0 or "headless" in argv0:
            out.append(pid)
    return out


def _alive(pids):
    alive = []
    for p in pids:
        try:
            if open(f"/proc/{p}/stat").read().rsplit(")", 1)[1].split()[0] != "Z":
                alive.append(p)
        except OSError:
            pass
    return alive


def _wait(cond, timeout, step=0.1):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = cond()
        if v:
            return v
        time.sleep(step)
    return cond()


def _near(img, rgb, tol=70) -> int:
    return sum(all(abs(px[i] - rgb[i]) <= tol for i in range(3)) for px in img.resize((480, 270)).getdata())


@unittest.skipUnless(os.path.isdir("/proc"), "needs /proc (Linux)")
class ProcessRunnerHttpE2E(unittest.TestCase):
    _base = _routes.VideoProducerRoutesE2E
    setUp, tearDown, _benign_l44 = _base.setUp, _base.tearDown, _base._benign_l44

    def _runner(self, mod):
        ar = importlib.import_module(f"{mod._PLUGIN_PKG}.async_runner")
        entry = Path(ar.__file__).resolve().parents[1] / "tests" / "_vp_job_main_mock_tts.py"
        if not entry.is_file():
            self.skipTest("the loaded plugin copy has no tests/_vp_job_main_mock_tts.py")
        inner = ar.ProcessJobRunner(entry=[sys.executable, str(entry)])
        configs: list = []

        class Injecting:
            running_job_ids = staticmethod(inner.running_job_ids)
            cancel_job = staticmethod(inner.cancel_job)

            async def start_job(self, job_id, task, config):
                configs.append(config)
                # "auto" is the chain the test entry reduces to the offline mock tier; the route's default
                # ("openai") would make the child call the real API with the sandbox's dummy key
                return await inner.start_job(job_id, task, dict(config, storyboard=STORYBOARD, tts_engine="auto"))

        mod.get_runner = lambda: Injecting()
        return inner, configs

    def _job(self, client, csrf):
        self._benign_l44()
        r = client.post(f"{V}/jobs", json={"task": "Explain the quarterly review in two scenes."}, headers=H(csrf))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["job_id"]

    def test_styled_job_in_a_child_process_reaches_the_panel_path_and_the_pixels(self):
        from PIL import Image

        with _sandbox(Path(tempfile.mkdtemp()), tenants=("tenant_a",)) as (_c, _t, home, clients):
            mod = _route_module()
            mod._previews = _no_previews
            client, csrf = clients["tenant_a"]
            self._benign_l44()
            self.assertEqual(client.post(f"{V}/styles", json={"draft": _wire(), "set_default": True},
                                         headers=H(csrf)).status_code, 201)
            inner, configs = self._runner(mod)
            job_id = self._job(client, csrf)
            self.assertIsNotNone(configs[0].get("web_style"), "the route did not hand the Style object over")
            seen_status, seen_pct = set(), set()
            t0 = time.monotonic()
            while time.monotonic() - t0 < 300:
                d = client.get(f"{V}/jobs/{job_id}").json()
                seen_status.add(d["status"])
                seen_pct.add(d["percent"])
                if d["status"] in ("complete", "error", "cancelled"):
                    break
                time.sleep(0.2)
            self.assertEqual(d["status"], "complete", d)
            self.assertGreaterEqual(len(seen_pct), 3, seen_pct)   # progress travelled child -> record -> router
            self.assertIn("skills_running", seen_status)
            video = Path(d["video_output_path"])
            self.assertTrue(video.is_file() and video.stat().st_size > 10_000)
            png = Path(tempfile.mkdtemp()) / "f.png"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1.5", "-i", str(video), "-frames:v", "1",
                            str(png)], check=True)
            frame = Image.open(png).convert("RGB")
            self.assertGreater(_near(frame, (0x3B, 0xA3, 0xFF)), 150, "the style never reached the frame")
            self.assertLess(_near(frame, (0xE8, 0xA8, 0x3A), 40), 30, "Corvin's accent leaked into the styled video")
            self.assertTrue(_wait(lambda: not _procs(job_id), 10), _procs(job_id))
            self.assertEqual(list((home / "tenants" / "tenant_a" / "video_producer" / "jobs").glob("*.run")), [])

    def test_cancel_mid_render_through_the_router_state_leaves_no_process(self):
        with _sandbox(Path(tempfile.mkdtemp()), tenants=("tenant_a",)) as (_c, _t, home, clients):
            mod = _route_module()
            client, csrf = clients["tenant_a"]
            inner, _ = self._runner(mod)
            job_id = self._job(client, csrf)
            leader = _wait(lambda: getattr(inner._active.get(job_id), "proc", None), 30).pid
            # Playwright starts Chromium in its own process group and without the job's environment:
            # ancestry finds it, CORVIN_VIDEO_JOB_ID does not
            chrome = _wait(lambda: _chromium_of(leader), 120)
            self.assertTrue(chrome, _procs(job_id))
            self.assertTrue(inner.cancel_job(job_id))
            self.assertTrue(_wait(lambda: client.get(f"{V}/jobs/{job_id}").json()["status"] == "cancelled", 20))
            self.assertTrue(_wait(lambda: not _procs(job_id), 20), _procs(job_id))
            self.assertTrue(_wait(lambda: not _alive(chrome), 20), _alive(chrome))


if __name__ == "__main__":
    unittest.main()

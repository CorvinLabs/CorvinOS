"""CorvinTV REAL end-to-end: the plugin source moves under a running console, then a real video is made.

Reproduces the 2026-10-19 failure ("The job process ended unexpectedly (exit code 2)") the way it happened:
the console loads the plugin from one directory (a marketplace-cache copy), the directory disappears (the
resync), and the operator starts a job. Everything after that is production code with nothing substituted:
the console router (session + CSRF, real gates), ``_ensure_plugin``'s reload, the plugin's ProcessJobRunner
with its default entry (``<python> job_main.py``), the real storyboard backend (the host's LLM), the real
narration chain (OpenAI first), Chromium, ffmpeg - ending in an MP4 that ffprobe accepts.

Opt-in (network, spend): CORVIN_VP_REAL_E2E=1. Run it with the console's own interpreter, which has
Playwright/Chromium and the TTS packages, e.g.
    cd core/console/tests && CORVIN_VP_REAL_E2E=1 <console python> -m unittest test_video_producer_source_moved_real_e2e -v
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_video_producer_routes_e2e as _routes  # noqa: E402
from test_video_producer_routes_e2e import _route_module, _sandbox  # noqa: E402

V = "/v1/console/video"
_REPO = Path(__file__).resolve().parents[3]
_SRC = _REPO.parent / "Corvin-Marketplace" / "plugins" / "buildin" / "media" / "corvintv" / "src"
_REAL_KEY = os.environ.get("CORVIN_TTS_OPENAI_KEY") or os.environ.get("OPENAI_API_KEY")


@unittest.skipUnless(os.environ.get("CORVIN_VP_REAL_E2E") == "1", "opt-in: CORVIN_VP_REAL_E2E=1 (network, spend)")
@unittest.skipUnless((_SRC / "job_main.py").is_file(), "needs the sibling Corvin-Marketplace checkout")
class SourceMovedRealVideoE2E(unittest.TestCase):
    _base = _routes.VideoProducerRoutesE2E
    setUp, tearDown, _benign_l44 = _base.setUp, _base.tearDown, _base._benign_l44

    def test_a_job_after_the_source_moved_produces_a_real_video(self):
        stale = Path(tempfile.mkdtemp()) / "marketplace-cache" / "contributor" / "media" / "video_producer" / "src"
        shutil.copytree(_SRC, stale, ignore=shutil.ignore_patterns("__pycache__"))
        with _sandbox(self._tmp, tenants=("tenant_a",)) as (_c, _t, home, clients), \
                mock.patch.dict(os.environ, {"CORVIN_TTS_OPENAI_KEY": _REAL_KEY} if _REAL_KEY else {}):
            mod = _route_module()
            # the console as it was at 14:27: plugin loaded from the cache copy
            for n in [n for n in sys.modules if n.split(".")[0] == mod._PLUGIN_PKG]:
                sys.modules.pop(n)
            mod.VideoJob = mod._get_storage = mod.get_runner = mod.PLUGIN_SOURCE = mod._PLUGIN_SRC = None
            real_locs = mod._plugin_locations
            mod._plugin_locations = lambda: [(stale, "github-cache-stale"), *real_locs()]
            self._restore.append((mod, "_plugin_locations", real_locs))
            self.assertTrue(mod._ensure_plugin("tenant_a"))
            self.assertEqual(mod.PLUGIN_SOURCE, "github-cache-stale")

            # 20:59: the resync replaces the cache
            shutil.rmtree(stale.parents[3])

            # 22:36: the operator starts a job
            client, csrf = clients["tenant_a"]
            self._benign_l44()
            r = client.post(f"{V}/jobs", headers={"X-CSRF-Token": csrf}, json={
                "task": "Erkläre in zwei kurzen Szenen, was CorvinOS ist: ein lokales Betriebssystem für KI-Agenten "
                        "mit Console, Plugins und Audit-Kette."})
            self.assertEqual(r.status_code, 200, r.text)
            job_id = r.json()["job_id"]
            self.assertNotEqual(mod.PLUGIN_SOURCE, "github-cache-stale", "the vanished source was not replaced")

            t0, d = time.monotonic(), {}
            while time.monotonic() - t0 < 900:
                d = client.get(f"{V}/jobs/{job_id}").json()
                if d.get("status") in ("complete", "error", "cancelled"):
                    break
                time.sleep(1)
            print(f"\n[real e2e] {job_id}: {d.get('status')} after {time.monotonic() - t0:.0f}s "
                  f"from {mod.PLUGIN_SOURCE}; error={d.get('error_message')}")
            self.assertEqual(d.get("status"), "complete", d)
            video = Path(d["video_output_path"])
            self.assertTrue(video.is_file() and video.stat().st_size > 50_000, video)
            # ffmpeg, not ffprobe: the managed install ships ffmpeg only. Decoding the whole file proves it is intact.
            info = subprocess.run([shutil.which("ffmpeg") or "ffmpeg", "-hide_banner", "-v", "info", "-i", str(video),
                                   "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8",
                                  errors="replace")
            self.assertEqual(info.returncode, 0, info.stderr[-2000:])
            self.assertRegex(info.stderr, r"Stream #0:\d+.*: Video: h264")
            self.assertRegex(info.stderr, r"Stream #0:\d+.*: Audio: ")
            dur = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", info.stderr)
            self.assertIsNotNone(dur, info.stderr[-2000:])
            seconds = int(dur[1]) * 3600 + int(dur[2]) * 60 + float(dur[3])
            print(f"[real e2e] {video.name}: {seconds:.1f}s, {video.stat().st_size // 1024} KiB")
            self.assertGreater(seconds, 5.0)
            self.assertEqual(list((home / "tenants" / "tenant_a" / "video_producer" / "jobs").glob("*.run")), [])
            self.assertEqual(list((home / "tenants" / "tenant_a" / "video_producer" / "jobs").glob("*.stderr")), [])
            out = os.environ.get("CORVIN_VP_REAL_E2E_KEEP")
            if out:
                shutil.copy2(video, out)


if __name__ == "__main__":
    unittest.main()

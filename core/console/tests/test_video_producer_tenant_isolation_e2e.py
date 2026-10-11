"""F1 / F3 tenant isolation of the Video Producer (ADR-2247, PLAN-0946 R4, T-0093).

F1 - a model-written ``screenshot_url`` made the job's browser open a URL on the console's loopback, where
``GET /v1/console/auth/local-login`` hands ANY loopback peer an owner session of tenant ``_default``: one
tenant's job was logged into another tenant's console. The reproduction below drives it end to end:

* the REAL console router served over a REAL loopback socket (uvicorn), so a browser started by the job can
  reach ``local-login`` exactly as it could on a live host; every request that server answers is logged;
* two tenants - ``_default`` (the console owner, whom local-login logs in) and ``acme`` (the requester);
* tenant ``acme`` creates a job through the real route (session cookie + CSRF, real L44/L34/L35 plumbing);
* the storyboard is MODEL OUTPUT: the host's backend decision is pinned to ``claude_cli`` and
  ``CORVIN_CLAUDE_BIN`` points at a stand-in CLI that answers like ``claude -p --output-format json`` with a
  storyboard whose first scene is ``kind: screenshot`` + ``screenshot_url`` at that console. Nothing else of
  the storyboard path is replaced: the plugin parses, validates and renders it.

Substituted, all outside F1: the model (the stand-in CLI), the host's storyboard-backend gate decision, the
L44 classifier leaf (benign stub, repo convention) and the TTS (offline: the plugin's mock-tier job entry for
the process runner; a silent ffmpeg clip for a plugin build that still ran jobs on threads).

The test is red against the plugin as it shipped before the fix (1.4.0, ``545ffb4^``; select a plugin
source with ``VP_F1_PLUGIN_SRC=<path to the plugin's src>`` - it is installed into the sandbox as the
``_default`` installed instance, the first place the host looks) and green from 1.4.1 on.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_video_producer_routes_e2e as _routes  # noqa: E402
from test_video_producer_routes_e2e import _route_module, _sandbox  # noqa: E402

V = "/v1/console/video"
OWNER, REQUESTER = "_default", "acme"
TEXT1 = "Hier sehen Sie die Startseite, auf der alle Bestellungen des Tages zusammenlaufen."
TEXT2 = "Am Ende steht ein klarer Plan fuer die Fruehschicht, den jeder im Team versteht."


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _LoopbackConsole:
    """The real console app on a real 127.0.0.1 socket, with a log of every request it answered."""

    def __init__(self, app):
        import uvicorn

        self.served: list = []
        self.port = _free_port()

        async def logged(scope, receive, send):
            if scope["type"] != "http":
                return await app(scope, receive, send)
            ua = dict(scope.get("headers") or []).get(b"user-agent", b"").decode("latin-1")

            async def send_logged(msg):
                if msg["type"] == "http.response.start":
                    cookies = [v.decode("latin-1").split("=", 1)[0] for k, v in msg.get("headers", [])
                               if k.lower() == b"set-cookie"]
                    self.served.append({"path": scope["path"], "status": msg["status"], "ua": ua,
                                        "sets_cookie": cookies})
                await send(msg)

            return await app(scope, receive, send_logged)

        class _Server(uvicorn.Server):
            def install_signal_handlers(self):  # not the main thread
                pass

        self._server = _Server(uvicorn.Config(logged, host="127.0.0.1", port=self.port, log_level="warning",
                                              lifespan="off"))
        self._thread = threading.Thread(target=self._server.run, name="f1-console", daemon=True)

    def __enter__(self):
        self._thread.start()
        end = time.monotonic() + 15
        while not self._server.started and time.monotonic() < end:
            time.sleep(0.05)
        assert self._server.started, "loopback console did not start"
        return self

    def __exit__(self, *exc):
        self._server.should_exit = True
        self._thread.join(10)


def _stand_in_claude(path: Path, storyboard: dict) -> Path:
    """An executable that answers like ``claude -p --output-format json``: the model's reply is ``result``."""
    reply = json.dumps({"type": "result", "subtype": "success", "is_error": False,
                        "result": json.dumps(storyboard), "usage": {"input_tokens": 1, "output_tokens": 1}})
    path.write_text(f"#!{sys.executable}\nimport sys\nsys.stdin.read()\nprint({reply!r})\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _model_storyboard(port: int) -> dict:
    return {
        "id": "sb_f1", "didactic_strategy": "rich_visual",
        "scenes": [
            {"id": "s1", "kind": "screenshot", "duration_ms": 8000, "narration_text": TEXT1,
             "visual_description": "The console start page",
             "screenshot_url": f"http://127.0.0.1:{port}/v1/console/auth/local-login?next=/console/app/video-producer"},
            {"id": "s2", "kind": "summary", "duration_ms": 8000, "narration_text": TEXT2,
             "visual_description": "A shift plan",
             "template": "hero", "data": {"badge": "Plan", "title": "Die Fruehschicht.", "accent": "Klar geplant."}},
        ],
    }


def _silent_mp3(_text, out_path, _lang) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                    "-t", "3", "-q:a", "9", str(out_path)], check=True, timeout=60)


def _load_plugin_source(mod, home: Path) -> None:
    """Load the plugin the host would load - or the one named by VP_F1_PLUGIN_SRC, installed as the
    ``_default`` instance (the first location the host searches)."""
    override = os.environ.get("VP_F1_PLUGIN_SRC")
    if override:
        dest = home / "tenants" / "_default" / "plugins" / "instances" / "video_producer" / "src"
        shutil.copytree(override, dest, ignore=shutil.ignore_patterns("__pycache__"))
    for name in [n for n in sys.modules if n == mod._PLUGIN_PKG or n.startswith(mod._PLUGIN_PKG + ".")]:
        sys.modules.pop(name, None)
    mod.VideoJob, mod._get_storage, mod.get_runner, mod.PLUGIN_SOURCE = mod._load_plugin()
    assert mod._get_storage is not None, "no plugin source found"
    if override:
        assert mod.PLUGIN_SOURCE == "installed-instance", mod.PLUGIN_SOURCE


def _offline_runner(mod):
    """The runner the host picked, with only the narration made offline."""
    ar = importlib.import_module(f"{mod._PLUGIN_PKG}.async_runner")
    if hasattr(ar, "get_process_runner"):
        entry = Path(ar.__file__).resolve().parents[1] / "tests" / "_vp_job_main_mock_tts.py"
        inner = ar.ProcessJobRunner(entry=[sys.executable, str(entry)])

        class Offline:
            running_job_ids = staticmethod(inner.running_job_ids)
            cancel_job = staticmethod(inner.cancel_job)

            async def start_job(self, job_id, task, config):
                return await inner.start_job(job_id, task, dict(config, tts_engine="auto"))

        mod.get_runner = lambda: Offline()
        return "process"
    sk = importlib.import_module(f"{mod._PLUGIN_PKG}.skill")
    sk._synthesize_narration_openai = _silent_mp3  # a pre-1.4.1 build ran the job in this process
    return "thread"


def _keys(obj) -> set:
    if isinstance(obj, dict):
        return set(obj) | set().union(*(_keys(v) for v in obj.values()))
    if isinstance(obj, list):
        return set().union(*(_keys(v) for v in obj)) if obj else set()
    return set()


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
class CrossTenantConsoleCaptureE2E(unittest.TestCase):
    setUp, tearDown, _benign_l44 = (_routes.VideoProducerRoutesE2E.setUp, _routes.VideoProducerRoutesE2E.tearDown,
                                    _routes.VideoProducerRoutesE2E._benign_l44)

    def test_a_requesting_tenants_job_never_reaches_the_console_as_another_tenant(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        with _sandbox(tmp, tenants=(OWNER, REQUESTER)) as (_c, _t, home, clients):
            mod = _route_module()
            _load_plugin_source(mod, home)
            client, csrf = clients[REQUESTER]
            with _LoopbackConsole(client.app) as console:
                cli = _stand_in_claude(tmp / "claude", _model_storyboard(console.port))
                runner_kind = _offline_runner(mod)
                with mock.patch.dict(os.environ, {"CORVIN_CLAUDE_BIN": str(cli)}), \
                        mock.patch.object(mod, "_storyboard_backend",
                                          lambda *_a, **_k: ("claude_cli", "claude-sonnet-5")):
                    self._benign_l44()
                    r = client.post(f"{V}/jobs", headers={"X-CSRF-Token": csrf},
                                    json={"task": "Explain how a bakery plans its early shift, in two short scenes."})
                    self.assertEqual(r.status_code, 200, r.text)
                    job_id = r.json()["job_id"]
                    end, d = time.monotonic() + 300, {}
                    while time.monotonic() < end:
                        d = client.get(f"{V}/jobs/{job_id}").json()
                        if d.get("status") in ("complete", "error", "cancelled"):
                            break
                        time.sleep(0.25)
                served = list(console.served)

            print(f"\n[F1] plugin={mod.PLUGIN_SOURCE} runner={runner_kind} job={job_id} status={d.get('status')} "
                  f"error={d.get('error_message')!r}")
            print(f"[F1] requests the loopback console answered during {REQUESTER}'s job: {served}")
            base = home / "tenants" / REQUESTER / "video_producer"
            job = mod._get_storage(str(base)).get_job(job_id)
            scenes = [s.to_dict() for s in (job.storyboard.scenes if job and job.storyboard else [])]
            stored_keys = _keys(scenes)
            records = {str(f.relative_to(base)): f.read_text(errors="replace")
                       for f in base.rglob("*.json") if f.is_file()}
            url_in = sorted(k for k, text in records.items() if f"127.0.0.1:{console.port}" in text)
            print(f"[F1] stored storyboard scene keys: {sorted(stored_keys)}")
            print(f"[F1] requester records carrying the console URL: {url_in}")

            # 1. the requesting tenant's job never reached the console - so it can never have been handed a
            #    session of the owner tenant (local-login answers 307 + Set-Cookie: corvin_console_sid)
            minted = [s for s in served if "corvin_console_sid" in s["sets_cookie"]]
            self.assertEqual(minted, [], f"{REQUESTER}'s job was given a console session of {OWNER!r}: {minted}")
            self.assertEqual(served, [], f"{REQUESTER}'s job reached the console: {served}")
            # 2. the model-written URL did not survive into the job's record. Positive control first: the
            #    record really holds the model's storyboard (else "no URL in it" would prove nothing)
            self.assertEqual([s.get("narration_text") for s in scenes], [TEXT1, TEXT2], scenes)
            self.assertNotIn("screenshot_url", stored_keys)
            self.assertEqual(url_in, [], "the model-written console URL was persisted for the requester")
            # 3. the job still produced its video: the screenshot scene became an ordinary slide
            self.assertEqual(d.get("status"), "complete", d)
            self.assertTrue(Path(d["video_output_path"]).is_file())


if __name__ == "__main__":
    unittest.main()

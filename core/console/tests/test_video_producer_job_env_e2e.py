"""The video JOB process gets what it needs in ITS environment; the console's own stays clean (T-0245 / PLG-05, PLG-11).

Until 2026-10-21 ``GET /video/settings`` promoted the console's stored OpenAI TTS key into
``os.environ`` (so every later child process - OS workers, MCP servers - inherited it), and
``_ensure_ffmpeg`` prepended a shim directory to the console's process-wide PATH from a request.
Now the key is resolved without being exported, and both the key and the bundled-ffmpeg bin
directory travel in the ``env=`` of the job spawn only.

Real: the console router (TestClient, session + CSRF), the stored-key store the Setup/BYOK
routes write (``provider_keys``, under the sandbox config dir), the marketplace plugin's
``ProcessJobRunner`` up to the OS spawn. Replaced: ``subprocess.Popen`` in the runner (the job
itself would call an LLM, a TTS provider and ffmpeg).
"""
from __future__ import annotations

import importlib
import os
import stat
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_video_producer_routes_e2e import VideoProducerRoutesE2E, _route_module, _sandbox  # noqa: E402

_STORED = "sk-stored-test-key-0000000000"
_KEY_NAMES = ("CORVIN_TTS_OPENAI_KEY", "OPENAI_API_KEY", "CORVIN_TTS_OPENAI_KEY_ADOPTED")


class _FakeProc:
    """What the runner needs from a job process: it 'exits' at once with rc 0."""
    pid = 4194305  # above Linux's pid_max: never a live process group
    returncode = 0

    def wait(self):
        return 0

    def poll(self):
        return 0


class JobEnvE2E(unittest.TestCase):
    setUp, tearDown, _benign_l44 = (VideoProducerRoutesE2E.setUp, VideoProducerRoutesE2E.tearDown,
                                   VideoProducerRoutesE2E._benign_l44)

    @staticmethod
    def _store_key(value: str = _STORED) -> None:
        import provider_keys  # the bridges/shared module the Setup / BYOK routes write through

        provider_keys.write_key("tts_openai_api_key", value)  # service.env under the sandbox config dir

    def _spawn_one(self, client, csrf):
        """POST a job through the REAL process runner; return the env the OS spawn was handed."""
        mod = _route_module()
        ar = importlib.import_module(f"{mod._PLUGIN_PKG}.async_runner")
        spawned: list = []
        real_popen = ar.subprocess.Popen

        def fake_popen(argv, **kw):
            # ``ar.subprocess`` is the stdlib module: only the job spawn is replaced, anything else the
            # request runs (a gate's helper) keeps its real process
            if any(str(a).endswith("job_main.py") for a in argv):
                spawned.append((argv, kw))
                return _FakeProc()
            return real_popen(argv, **kw)

        with mock.patch.object(ar.subprocess, "Popen", fake_popen), \
             mock.patch.object(ar, "_kill_group", lambda *a, **k: False):
            self._benign_l44()
            resp = client.post("/v1/console/video/jobs", json={"task": "Explain the audit chain."},
                               headers={"X-CSRF-Token": csrf})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(len(spawned), 1, "the job was not spawned as a process")
        argv, kw = spawned[0]
        self.assertEqual(argv[0], sys.executable)
        return kw["env"]

    def test_a_stored_key_reaches_the_job_process_and_never_the_console_environment(self):
        with _sandbox(self._tmp, plugin="enabled", keep_key=False) as (client, csrf, _home, _c):
            self._store_key()
            st = client.get("/v1/console/video/settings").json()
            self.assertTrue(st["openai_configured"], st)
            for name in _KEY_NAMES:
                self.assertNotIn(name, os.environ, f"{name} was exported into the console process by a GET")
            env = self._spawn_one(client, csrf)
            self.assertEqual(env.get("CORVIN_TTS_OPENAI_KEY"), _STORED)
            for name in _KEY_NAMES:
                self.assertNotIn(name, os.environ, f"{name} was exported into the console process by a job start")
            self.assertNotIn(_STORED, client.get("/v1/console/video/settings").text)

    def test_os_workers_never_see_the_stored_key(self):
        """The OS worker env is ``{**os.environ, ...}`` (chat_runtime): with nothing exported there is
        nothing to strip, and chat_runtime carries no TTS-key handling at all any more."""
        from corvin_console import chat_runtime

        with _sandbox(self._tmp, plugin="enabled", keep_key=False) as (client, csrf, _home, _c):
            self._store_key()
            client.get("/v1/console/video/settings")
            self._spawn_one(client, csrf)
            worker_env = {**os.environ, "CORVIN_SESSION_DIR": "x"}
            for name in _KEY_NAMES:
                self.assertNotIn(name, worker_env)
        src = Path(chat_runtime.__file__).read_text(encoding="utf-8")
        self.assertNotIn("CORVIN_TTS_OPENAI_KEY", src, "chat_runtime must not need to know about the TTS key")

    def test_an_operator_export_wins_over_the_stored_key(self):
        with _sandbox(self._tmp, plugin="enabled", keep_key=False) as (client, csrf, _home, _c):
            self._store_key()
            with mock.patch.dict(os.environ, {"CORVIN_TTS_OPENAI_KEY": "sk-operator-export-000"}):
                self.assertTrue(client.get("/v1/console/video/settings").json()["openai_configured"])
                env = self._spawn_one(client, csrf)
            self.assertEqual(env["CORVIN_TTS_OPENAI_KEY"], "sk-operator-export-000")

    def test_without_any_key_the_job_env_carries_none_and_the_panel_says_so(self):
        with _sandbox(self._tmp, plugin="enabled", keep_key=False) as (client, csrf, _home, _c):
            mod = _route_module()
            with mock.patch.object(mod, "_stored_openai_key", lambda: None):
                self.assertFalse(client.get("/v1/console/video/settings").json()["openai_configured"])
                env = self._spawn_one(client, csrf)
            self.assertNotIn("CORVIN_TTS_OPENAI_KEY", env)

    def test_the_route_never_inserts_at_the_front_of_sys_path(self):
        """The shared dir is appended once at import (the console app puts it first itself, at ITS import);
        no request handler re-orders ``sys.path`` any more."""
        from corvin_console.routes import video_producer_api as mod  # noqa: PLC0415

        self.assertIn(str(mod._SHARED_DIR), sys.path)
        src = Path(mod.__file__).read_text(encoding="utf-8")
        self.assertNotIn("sys.path.insert(", src)
        self.assertEqual(src.count("sys.path.append("), 1)


class BundledFfmpegE2E(unittest.TestCase):
    """The imageio-ffmpeg binary is exposed under the name ``ffmpeg`` in ``<corvin_home>/bin`` - resolved
    through ``core.paths.tenant.corvin_home()`` - and that directory goes on the JOB's PATH only."""

    setUp, tearDown, _benign_l44 = (VideoProducerRoutesE2E.setUp, VideoProducerRoutesE2E.tearDown,
                                   VideoProducerRoutesE2E._benign_l44)

    def _fake_bundle(self, home: Path):
        """A fake ``imageio_ffmpeg`` whose binary is a real executable file, no system ffmpeg."""
        import types

        exe = home / "wheel" / "ffmpeg-linux64-v7.0"
        exe.parent.mkdir(parents=True)
        exe.write_text("#!/bin/sh\nexit 0\n")
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
        fake = types.ModuleType("imageio_ffmpeg")
        fake.get_ffmpeg_exe = lambda: str(exe)
        from corvin_console.routes import video_producer_api as mod  # noqa: PLC0415

        real_which = mod.shutil.which

        def which(cmd, mode=os.F_OK | os.X_OK, path=None):
            if cmd == "ffmpeg" and path is None:
                return None  # no system ffmpeg
            return real_which(cmd, mode, path)

        stack = [mock.patch.dict(sys.modules, {"imageio_ffmpeg": fake}),
                 mock.patch.object(mod.shutil, "which", which),
                 mock.patch("core.paths.tenant.corvin_home", lambda: home),
                 mock.patch.object(mod, "_ffmpeg_bin", None)]
        for p in stack:
            p.start()
            self.addCleanup(p.stop)
        return exe

    def test_the_shim_lives_under_corvin_home_and_only_the_job_path_sees_it(self):
        home = self._tmp / "home-a"
        home.mkdir()
        exe = self._fake_bundle(home)
        from corvin_console.routes import video_producer_api as mod  # noqa: PLC0415

        before = os.environ.get("PATH", "")
        self.assertTrue(mod._ensure_ffmpeg())
        self.assertEqual(os.environ.get("PATH", ""), before, "a request mutated the console's PATH")
        shim = home / "bin" / "ffmpeg"
        self.assertTrue(shim.is_symlink() and shim.resolve() == exe.resolve())
        job_path = mod._job_env()["PATH"].split(os.pathsep)
        self.assertEqual(job_path[0], str(home / "bin"))
        self.assertEqual(mod._ffmpeg_bin_dir(), home / "bin")

    def test_a_dangling_shim_is_replaced_not_copied_onto(self):
        home = self._tmp / "home-b"
        (home / "bin").mkdir(parents=True)
        shim = home / "bin" / "ffmpeg"
        os.symlink(home / "gone" / "ffmpeg", shim)  # a bundle that was uninstalled: the link dangles
        self.assertTrue(shim.is_symlink() and not shim.exists())
        exe = self._fake_bundle(home)
        from corvin_console.routes import video_producer_api as mod  # noqa: PLC0415

        self.assertTrue(mod._ensure_ffmpeg())
        self.assertTrue(shim.exists(), "the dangling link was not replaced")
        self.assertEqual(shim.resolve(), exe.resolve())
        self.assertTrue(os.access(shim, os.X_OK))

    def test_a_job_on_a_host_with_only_the_bundle_is_spawned_with_the_shim_on_its_path(self):
        with _sandbox(self._tmp, plugin="enabled", keep_key=False) as (client, csrf, home, _c):
            self._fake_bundle(home)
            env = JobEnvE2E._spawn_one(self, client, csrf)
            self.assertEqual(env["PATH"].split(os.pathsep)[0], str(home / "bin"))
            self.assertNotIn(str(home / "bin"), os.environ.get("PATH", ""))


if __name__ == "__main__":
    unittest.main()

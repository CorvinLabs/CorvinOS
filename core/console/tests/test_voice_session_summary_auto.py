"""Automatic, persisted whole-session voice summary — E2E against the real
route, the real on-disk session store and the real workdir file-serving
route. Only the two paid subprocesses (summarize.py, say.py) are mocked, at
the same process boundary the existing on-demand /voice/session-summary
tests already mock (test_voice_session_summary.py).

Covers the feature this session added on top of that ephemeral, click-only
recap: chat_runtime._spawn_session_summary_auto fires after every
successfully completed turn, independent of whether a client is connected,
and routes/voice.py::generate_and_persist_session_summary persists the
result to a fixed (tenant_id, sid)-keyed file pair that a later, unrelated
login session can still list and play back via GET /v1/console/voice/summaries
and the existing GET /chat/sessions/{sid}/workdir/{filepath} route.
"""
from __future__ import annotations

import subprocess
import sys
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
for p in ("core/console", "corvin_operator/bridges/shared", "corvin_operator/forge"):
    sys.path.insert(0, str(_REPO / p))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import corvin_console.chat_runtime as chat_runtime  # noqa: E402
import corvin_console.routes.chat as chat_routes  # noqa: E402
import corvin_console.routes.voice as voice_routes  # noqa: E402


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(["fake"], returncode, stdout=stdout, stderr=stderr)


def _fake_run_factory(recap_text: str = "Kurzer Recap-Text."):
    """subprocess.run stand-in: summarize.py's --session-recap-mode call
    returns *recap_text*; say.py's call is a no-op (the real write happens
    via the patched Path methods below, mirroring test_voice_session_summary.py)."""
    def fake_run(cmd, **kwargs):
        if "--session-recap-mode" in cmd:
            return _completed(0, stdout=recap_text + "\n")
        return _completed(0, stdout="ok")
    return fake_run


class _App:
    def __call__(self):
        app = FastAPI()
        app.include_router(chat_routes.router, prefix="/v1/console")
        app.include_router(voice_routes.router, prefix="/v1/console")
        return app


_app = _App()


class SessionSummaryAutoTest(unittest.TestCase):
    """generate_and_persist_session_summary + list_session_summaries, real
    session store, real files, mocked subprocess boundary only."""

    def setUp(self) -> None:
        # A unique tenant per test: the repo's CORVIN_HOME sandbox is
        # SESSION-scoped (one per pytest run, see conftest.py's
        # _install_session_sandbox), not per-test, so sharing "_default"
        # across test methods would leak one test's summaries into another's
        # list_session_summaries() assertions.
        self.tenant_id = f"test-{uuid.uuid4().hex[:12]}"
        self.rec = MagicMock(tenant_id=self.tenant_id, sid_fingerprint="fp")
        p = patch.object(chat_routes.session_auth, "load_session", return_value=self.rec)
        p.start()
        self.addCleanup(p.stop)
        gate = patch(
            "corvin_console.routes._compute_license_gate.enforce_voice_summaries",
            lambda *a, **k: None,
        )
        gate.start()
        self.addCleanup(gate.stop)
        self.client = TestClient(_app())
        self.client.cookies.set("corvin_console_sid", "valid-sid")

    def _make_session_with_turns(self, *, n_pairs: int = 2) -> chat_runtime.WebChatSession:
        sess = chat_runtime.create_session(self.tenant_id, title="Auto-summary test chat")
        for i in range(n_pairs):
            chat_runtime._append_turn(
                sess, "user", [{"kind": "text", "text": f"Frage {i}"}])
            chat_runtime._append_turn(
                sess, "assistant", [{"kind": "text", "text": f"Antwort {i}"}])
            chat_runtime.touch(sess, increment_turn=True)
        return chat_runtime.get_session(self.tenant_id, sess.sid)

    def _synth(self, *, recap_text="Kurzer Recap-Text.", audio=b"OggS" + b"0" * 50):
        """Patch the two subprocess spawns generate_and_persist_session_summary
        makes. The say.py phase writes REAL bytes to the real tempfile
        say.py would have produced (via the patched _say_cmd's side effect)
        instead of mocking Path.stat/read_bytes globally — a blanket Path
        patch also breaks this function's OWN vdir.glob()/mkdir() calls,
        which the simpler on-demand route this pattern was copied from never
        needed to make."""
        def fake_say_cmd(out_path, text, lang):
            Path(out_path).write_bytes(audio)
            return ["true"]

        ctx = [
            patch.object(voice_routes.subprocess, "run", _fake_run_factory(recap_text)),
            patch.object(voice_routes, "_say_cmd", fake_say_cmd),
        ]
        for c in ctx:
            c.start()
            self.addCleanup(c.stop)

    # ── gating ────────────────────────────────────────────────────────────

    def test_no_summary_yet_generates_on_any_turn_count(self) -> None:
        self.assertTrue(
            voice_routes.should_auto_generate_session_summary(self.tenant_id, "brand-new-sid", 1))

    def test_skips_regeneration_below_the_turn_delta(self) -> None:
        sess = self._make_session_with_turns(n_pairs=1)
        self._synth()
        ok = voice_routes.generate_and_persist_session_summary(self.tenant_id, sess.sid)
        self.assertTrue(ok)
        # Only 1 more turn since the summary that was JUST generated —
        # under _AUTO_SUMMARY_MIN_TURN_DELTA (3) — must not regenerate yet.
        self.assertFalse(
            voice_routes.should_auto_generate_session_summary(
                self.tenant_id, sess.sid, sess.turn_count + 1))
        self.assertTrue(
            voice_routes.should_auto_generate_session_summary(
                self.tenant_id, sess.sid,
                sess.turn_count + voice_routes._AUTO_SUMMARY_MIN_TURN_DELTA))

    # ── generation + persistence + real HTTP playback ───────────────────

    def test_generates_and_persists_playable_via_real_http_route(self) -> None:
        sess = self._make_session_with_turns(n_pairs=2)
        self._synth(recap_text="Ihr habt zwei Themen besprochen.", audio=b"OggS" + b"Y" * 80)

        ok = voice_routes.generate_and_persist_session_summary(
            self.tenant_id, sess.sid, lang="de", trigger="auto")
        self.assertTrue(ok)

        vdir, meta_path = voice_routes._session_summary_paths(self.tenant_id, sess.sid)
        self.assertTrue(meta_path.exists())
        audio_files = list(vdir.glob("session-summary.*"))
        audio_files = [f for f in audio_files if f.name != voice_routes._SESSION_SUMMARY_META_NAME]
        self.assertEqual(len(audio_files), 1)
        self.assertEqual(audio_files[0].suffix, ".ogg")  # sniffed from the OggS magic bytes

        # list_session_summaries finds it (module-level function, no HTTP).
        items = voice_routes.list_session_summaries(self.tenant_id)
        self.assertEqual([i["sid"] for i in items], [sess.sid])
        self.assertEqual(items[0]["text"], "Ihr habt zwei Themen besprochen.")
        self.assertTrue(items[0]["audio_url"].startswith(
            f"/v1/console/chat/sessions/{sess.sid}/workdir/voice-summary/"))

        # GET /v1/console/voice/summaries — the real route a fresh, unrelated
        # login session (different sid_fingerprint, same tenant) would call.
        other_login = MagicMock(tenant_id=self.tenant_id, sid_fingerprint="a-different-login")
        with patch.object(chat_routes.session_auth, "load_session", return_value=other_login):
            resp = self.client.get("/v1/console/voice/summaries")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["summaries"][0]["sid"], sess.sid)
        audio_url = body["summaries"][0]["audio_url"]

        # Play it back through the REAL generic workdir file route — no
        # bespoke audio-serving endpoint was added; this IS the playback path.
        with patch.object(chat_routes.session_auth, "load_session", return_value=other_login):
            audio_resp = self.client.get(audio_url)
        self.assertEqual(audio_resp.status_code, 200, audio_resp.text)
        self.assertTrue(audio_resp.headers["content-type"].startswith("audio/"))
        self.assertEqual(audio_resp.content, b"OggS" + b"Y" * 80)

    def test_regeneration_overwrites_not_accumulates(self) -> None:
        sess = self._make_session_with_turns(n_pairs=2)
        self._synth(recap_text="Erste Fassung.", audio=b"OggS" + b"1" * 40)
        self.assertTrue(voice_routes.generate_and_persist_session_summary(self.tenant_id, sess.sid))
        self._synth(recap_text="Zweite Fassung.", audio=b"ID3" + b"2" * 60)  # different sniffed mime
        self.assertTrue(voice_routes.generate_and_persist_session_summary(self.tenant_id, sess.sid))

        vdir, _meta = voice_routes._session_summary_paths(self.tenant_id, sess.sid)
        audio_files = [f for f in vdir.glob("session-summary.*")
                       if f.name != voice_routes._SESSION_SUMMARY_META_NAME]
        # Exactly one file survives — the stale .ogg from the first pass was
        # removed when the second pass sniffed a different format (.mp3).
        self.assertEqual(len(audio_files), 1, audio_files)
        self.assertEqual(audio_files[0].suffix, ".mp3")
        items = voice_routes.list_session_summaries(self.tenant_id)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["text"], "Zweite Fassung.")

    def test_returns_false_and_persists_nothing_for_unknown_session(self) -> None:
        self._synth()
        ok = voice_routes.generate_and_persist_session_summary(self.tenant_id, "no-such-sid")
        self.assertFalse(ok)
        self.assertEqual(voice_routes.list_session_summaries(self.tenant_id), [])

    def test_returns_false_when_summarizer_produces_nothing(self) -> None:
        sess = self._make_session_with_turns(n_pairs=1)
        with patch.object(voice_routes.subprocess, "run", lambda cmd, **k: _completed(1)):
            ok = voice_routes.generate_and_persist_session_summary(self.tenant_id, sess.sid)
        self.assertFalse(ok)
        _vdir, meta_path = voice_routes._session_summary_paths(self.tenant_id, sess.sid)
        self.assertFalse(meta_path.exists())

    def test_list_is_empty_for_a_tenant_with_no_summaries(self) -> None:
        self._make_session_with_turns(n_pairs=1)  # a chat exists, but never summarised
        self.assertEqual(voice_routes.list_session_summaries(self.tenant_id), [])
        resp = self.client.get("/v1/console/voice/summaries")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["count"], 0)


class SpawnHookReachabilityTest(unittest.TestCase):
    """The auto-generation trigger must be reachable from the real turn
    completion path in chat_runtime.py, not just callable in isolation.
    Spawning a real ``claude`` subprocess to drive a full turn is out of
    scope for a unit test (test_chat_turn_task_finalized_on_disconnect.py
    takes the same position for its own impl-registration test) — this
    proves reachability by source inspection plus a direct call through the
    real trigger function against a real session."""

    def test_stream_turn_impl_calls_the_spawn_hook_on_success(self) -> None:
        import ast
        src = Path(chat_runtime.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        impl = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "_stream_turn_impl")
        calls = {
            n.func.id for n in ast.walk(impl)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        self.assertIn("_spawn_session_summary_auto", calls,
                       "_stream_turn_impl no longer calls the auto-summary spawn hook")

    def test_spawn_hook_skips_a_chat_that_does_not_need_regeneration_yet(self) -> None:
        """Direct call through the real trigger: a chat with too few new
        turns since its last summary must not schedule a background task —
        proven by the in-flight/task-count bookkeeping staying untouched."""
        tenant_id = "_default"
        sess = chat_runtime.create_session(tenant_id, title="reachability probe")
        before = len(chat_runtime._SESSION_SUMMARY_TASKS)
        with patch(
            "corvin_console.routes.voice.should_auto_generate_session_summary",
            return_value=False,
        ):
            chat_runtime._spawn_session_summary_auto(sess)
        self.assertEqual(len(chat_runtime._SESSION_SUMMARY_TASKS), before)
        self.assertNotIn(
            (sess.tenant_id, sess.sid), chat_runtime._SESSION_SUMMARY_INFLIGHT)


if __name__ == "__main__":
    unittest.main()

"""Per-task voice summaries — persisted per task_id, reachable after a task
switch and a console restart, spawned only while Voice is on.

Only the two paid subprocesses (summarize.py, say.py) are mocked, at the same
process boundary test_voice_session_summary_auto.py uses. Everything else is
real: the on-disk chat workdir, the voice-state file, the audit call, and the
HTTP route.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
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


def _fake_run(recap_text: str = "Kurzer Task-Recap.", returncode: int = 0):
    def fake(cmd, **kwargs):
        if "--session-recap-mode" in cmd:
            return _completed(returncode, stdout=recap_text + "\n")
        return _completed(0, stdout="ok")
    return fake


class _App:
    def __call__(self):
        app = FastAPI()
        app.include_router(chat_routes.router, prefix="/v1/console")
        app.include_router(voice_routes.router, prefix="/v1/console")
        return app


class TaskSummaryTest(unittest.TestCase):

    def setUp(self) -> None:
        # Unique tenant per test: the CORVIN_HOME sandbox is per pytest run.
        self.tenant_id = f"test-{uuid.uuid4().hex[:12]}"
        self.rec = MagicMock(tenant_id=self.tenant_id, sid_fingerprint="fp")
        p = patch.object(chat_routes.session_auth, "load_session", return_value=self.rec)
        p.start()
        self.addCleanup(p.stop)
        gate = patch(
            "corvin_console.routes._compute_license_gate.enforce_voice_summaries",
            lambda *a, **k: None)
        gate.start()
        self.addCleanup(gate.stop)
        # Shared module state: a fresh semaphore per test (bound to one loop).
        chat_runtime._TASK_SUMMARY_SLOTS = None
        self.client = TestClient(_App()())
        self.client.cookies.set("corvin_console_sid", "valid-sid")
        self.sess = chat_runtime.create_session(self.tenant_id, title="Parallele Tasks")

    def _synth(self, *, recap_text="Kurzer Task-Recap.", audio=b"OggS" + b"0" * 50,
               summarize_rc: int = 0):
        def fake_say_cmd(out_path, text, lang):
            Path(out_path).write_bytes(audio)
            return ["true"]
        patches = [
            patch.object(voice_routes.subprocess, "run",
                         _fake_run(recap_text, returncode=summarize_rc)),
            patch.object(voice_routes, "_say_cmd", fake_say_cmd),
        ]
        for c in patches:
            c.start()
            self.addCleanup(c.stop)

    def _task_dir(self, task_id: str) -> Path:
        return voice_routes._task_summary_dir(self.tenant_id, self.sess.sid)

    # ── text survives a failed speech synthesis ────────────────────────

    def test_tts_failure_keeps_the_text_and_the_retry_skips_the_summarizer(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if "--session-recap-mode" in cmd:
                return _completed(0, stdout="Gerettete Summary.\n")
            return _completed(1)  # say.py fails

        def fake_say_cmd(out_path, text, lang):
            return ["false"]

        with patch.object(voice_routes.subprocess, "run", fake_run), \
                patch.object(voice_routes, "_say_cmd", fake_say_cmd):
            ok = voice_routes.generate_and_persist_task_summary(
                self.tenant_id, self.sess.sid, "task-tts", user_text="u", answer_text="a")
        self.assertFalse(ok)  # still owed: the marker logic retries the audio

        items = voice_routes.list_task_summaries(self.tenant_id, self.sess.sid)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["text"], "Gerettete Summary.")
        self.assertIsNone(items[0]["audio_url"])

        # Retry: say.py works now, summarize.py must NOT run a second time.
        calls.clear()
        self._synth(recap_text="darf nicht benutzt werden")
        with patch.object(voice_routes.subprocess, "run") as run_mock:
            run_mock.side_effect = lambda cmd, **kw: (
                calls.append(cmd) or _completed(0, stdout="ok"))
            ok = voice_routes.generate_and_persist_task_summary(
                self.tenant_id, self.sess.sid, "task-tts", user_text="u", answer_text="a")
        self.assertTrue(ok)
        self.assertFalse(any("--session-recap-mode" in c for c in calls))
        items = voice_routes.list_task_summaries(self.tenant_id, self.sess.sid)
        self.assertEqual(items[0]["text"], "Gerettete Summary.")
        self.assertTrue(items[0]["audio_url"])

    # ── persistence per task ───────────────────────────────────────────

    def test_each_completed_task_gets_its_own_persisted_summary(self) -> None:
        self._synth(recap_text="Task eins fertig.", audio=b"OggS" + b"A" * 60)
        self.assertTrue(voice_routes.generate_and_persist_task_summary(
            self.tenant_id, self.sess.sid, "tsk_aaa111",
            user_text="Analysiere A", answer_text="A ist fertig."))

        self._synth(recap_text="Task zwei fertig.", audio=b"OggS" + b"B" * 60)
        self.assertTrue(voice_routes.generate_and_persist_task_summary(
            self.tenant_id, self.sess.sid, "tsk_bbb222",
            user_text="Analysiere B", answer_text="B ist fertig."))

        items = voice_routes.list_task_summaries(self.tenant_id, self.sess.sid)
        self.assertEqual({i["task_id"] for i in items}, {"tsk_aaa111", "tsk_bbb222"})
        by_task = {i["task_id"]: i for i in items}
        self.assertEqual(by_task["tsk_aaa111"]["text"], "Task eins fertig.")
        self.assertEqual(by_task["tsk_bbb222"]["text"], "Task zwei fertig.")
        for item in items:
            self.assertIn(f"/voice-summary/tasks/{item['task_id']}.", item["audio_url"])

    def test_summary_of_one_task_is_not_overwritten_by_a_later_task(self) -> None:
        self._synth(audio=b"OggS" + b"A" * 60)
        voice_routes.generate_and_persist_task_summary(
            self.tenant_id, self.sess.sid, "tsk_first",
            user_text="erste", answer_text="erste Antwort")
        first_audio = (self._task_dir("tsk_first") / "tsk_first.ogg").read_bytes()

        self._synth(audio=b"OggS" + b"Z" * 60)
        voice_routes.generate_and_persist_task_summary(
            self.tenant_id, self.sess.sid, "tsk_second",
            user_text="zweite", answer_text="zweite Antwort")

        self.assertEqual(
            (self._task_dir("tsk_first") / "tsk_first.ogg").read_bytes(), first_audio)

    def test_failed_summarizer_is_audited_and_persists_nothing(self) -> None:
        self._synth(summarize_rc=1)
        with patch.object(voice_routes.console_audit, "action_failed") as failed:
            ok = voice_routes.generate_and_persist_task_summary(
                self.tenant_id, self.sess.sid, "tsk_broken",
                user_text="x", answer_text="y")
        self.assertFalse(ok)
        self.assertEqual(failed.call_args.kwargs["reason"], "summarize-exit-nonzero")
        self.assertFalse((self._task_dir("tsk_broken") / "tsk_broken.json").exists())

    def test_task_id_outside_the_safe_alphabet_is_refused(self) -> None:
        self._synth()
        self.assertFalse(voice_routes.generate_and_persist_task_summary(
            self.tenant_id, self.sess.sid, "../escape",
            user_text="x", answer_text="y"))

    # ── survives task switch and restart ───────────────────────────────

    def test_summaries_survive_a_task_switch_and_a_restart(self) -> None:
        self._synth()
        voice_routes.generate_and_persist_task_summary(
            self.tenant_id, self.sess.sid, "tsk_one", user_text="a", answer_text="b")
        voice_routes.generate_and_persist_task_summary(
            self.tenant_id, self.sess.sid, "tsk_two", user_text="c", answer_text="d")

        # "Restart": the in-memory voice state and every task handle are gone;
        # the listing must come from disk alone.
        chat_runtime._VOICE_ON.clear()
        chat_runtime._TASK_SUMMARY_TASKS.clear()
        items = voice_routes.list_task_summaries(self.tenant_id)
        self.assertEqual({i["task_id"] for i in items}, {"tsk_one", "tsk_two"})

    def test_http_listing_returns_the_task_summaries_with_playable_urls(self) -> None:
        self._synth(recap_text="Per HTTP abrufbar.")
        voice_routes.generate_and_persist_task_summary(
            self.tenant_id, self.sess.sid, "tsk_http", user_text="a", answer_text="b")

        other_login = MagicMock(tenant_id=self.tenant_id, sid_fingerprint="anderer-login")
        with patch.object(chat_routes.session_auth, "load_session", return_value=other_login):
            resp = self.client.get("/v1/console/voice/task-summaries")
            scoped = self.client.get(
                "/v1/console/voice/task-summaries", params={"sid": self.sess.sid})
            bad = self.client.get(
                "/v1/console/voice/task-summaries", params={"sid": "../etc"})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["summaries"][0]["task_id"], "tsk_http")
        self.assertEqual(body["summaries"][0]["text"], "Per HTTP abrufbar.")
        self.assertEqual(scoped.json()["count"], 1)
        self.assertEqual(bad.status_code, 404)

    def test_completion_time_survives_marker_to_listing_and_the_payload_carries_server_now(self) -> None:
        self._synth()
        self.assertTrue(voice_routes.owe_task_summary(
            self.tenant_id, self.sess.sid, "tsk_when", user_text="a", answer_text="b",
            completed_at=1_700_000_123.0))
        self.assertEqual(
            voice_routes.run_owed_task_summary(self.tenant_id, self.sess.sid, "tsk_when"), "done")
        item = voice_routes.list_task_summaries(self.tenant_id, self.sess.sid)[0]
        # the TASK's finish time, not the (later) time the summary got made
        self.assertEqual(item["completed_at"], 1_700_000_123.0)
        self.assertNotEqual(item["completed_at"], item["created_at"])

        other = MagicMock(tenant_id=self.tenant_id, sid_fingerprint="x")
        with patch.object(chat_routes.session_auth, "load_session", return_value=other):
            body = self.client.get("/v1/console/voice/task-summaries").json()
        self.assertIsInstance(body["now"], float)

    def test_legacy_summary_without_completed_at_falls_back_to_created_at(self) -> None:
        self._synth()
        voice_routes.generate_and_persist_task_summary(
            self.tenant_id, self.sess.sid, "tsk_legacy", user_text="a", answer_text="b")
        item = voice_routes.list_task_summaries(self.tenant_id, self.sess.sid)[0]
        self.assertEqual(item["completed_at"], item["created_at"])

    # ── Voice state ────────────────────────────────────────────────────

    def test_voice_on_is_restored_from_disk_after_a_restart(self) -> None:
        key = (self.tenant_id, self.sess.sid)
        chat_runtime.note_voice_on(self.tenant_id, self.sess.sid, True)
        chat_runtime._VOICE_ON.pop(key, None)  # simulate a restart
        self.assertTrue(chat_runtime.voice_on_for(self.tenant_id, self.sess.sid))

        chat_runtime.note_voice_on(self.tenant_id, self.sess.sid, False)
        chat_runtime._VOICE_ON.pop(key, None)
        self.assertFalse(chat_runtime.voice_on_for(self.tenant_id, self.sess.sid))

    def test_unknown_chat_has_voice_off_and_writes_no_state(self) -> None:
        ghost = f"ghost-{uuid.uuid4().hex[:8]}"
        chat_runtime.note_voice_on(self.tenant_id, ghost, True)
        self.assertFalse(chat_runtime._voice_state_path(self.tenant_id, ghost).exists())

    # ── spawn gate ─────────────────────────────────────────────────────

    def _spawn_and_drain(self) -> None:
        async def main():
            chat_runtime._spawn_task_summary(
                self.sess, "tsk_spawn", user_text="u", answer_text="a")
            await asyncio.gather(*list(chat_runtime._TASK_SUMMARY_TASKS))
        asyncio.run(main())

    def test_spawn_runs_the_summary_while_voice_is_on(self) -> None:
        chat_runtime.note_voice_on(self.tenant_id, self.sess.sid, True)
        with patch.object(voice_routes, "generate_and_persist_task_summary",
                          return_value=True) as gen:
            self._spawn_and_drain()
        gen.assert_called_once()
        self.assertEqual(gen.call_args.args[2], "tsk_spawn")

    def test_spawn_is_a_no_op_while_voice_is_off(self) -> None:
        chat_runtime.note_voice_on(self.tenant_id, self.sess.sid, False)
        with patch.object(voice_routes, "generate_and_persist_task_summary",
                          return_value=True) as gen:
            self._spawn_and_drain()
        gen.assert_not_called()

    def test_waiting_tasks_are_not_dropped_when_slots_are_busy(self) -> None:
        chat_runtime.note_voice_on(self.tenant_id, self.sess.sid, True)
        seen: list[str] = []

        def slow(tenant, sid, task_id, **kwargs):
            seen.append(task_id)
            return True

        async def main():
            with patch.object(voice_routes, "generate_and_persist_task_summary", side_effect=slow):
                for n in range(chat_runtime._TASK_SUMMARY_MAX_CONCURRENT + 3):
                    chat_runtime._spawn_task_summary(
                        self.sess, f"tsk_{n}", user_text="u", answer_text="a")
                await asyncio.gather(*list(chat_runtime._TASK_SUMMARY_TASKS))
        asyncio.run(main())
        self.assertEqual(len(seen), chat_runtime._TASK_SUMMARY_MAX_CONCURRENT + 3)


if __name__ == "__main__":
    unittest.main()

"""Automatic voice recaps speak the CHAT's language (routes/voice.py::chat_language, 2026-10-21).

Until 2026-10-21 `generate_and_persist_session_summary` and
`generate_and_persist_task_summary` defaulted to ``lang="de"``: an English
chat on an English install got German recaps (measured on a fresh system).
Only the two paid subprocesses are mocked; the chat workdir, the turn log,
the owed-task marker and the language lookup are real.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
for p in ("core/console", "corvin_operator/bridges/shared", "corvin_operator/forge"):
    sys.path.insert(0, str(_REPO / p))

import corvin_console.chat_runtime as chat_runtime  # noqa: E402
import corvin_console.routes.voice as voice_routes  # noqa: E402


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(["fake"], returncode, stdout=stdout, stderr=stderr)


class _Profile:
    """A profile module stub with the provenance helpers (installer seed = en)."""
    @staticmethod
    def load(*a, **k):
        return {"display_language": "en", "_meta": {"display_language_source": "installer"}}

    @staticmethod
    def display_language_pin():
        return ""

    @staticmethod
    def display_language_default():
        return "en"


class ChatLanguageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tenant_id = f"test-{uuid.uuid4().hex[:12]}"
        self.sess = chat_runtime.create_session(self.tenant_id, title="lang")
        gate = patch("corvin_console.routes._compute_license_gate.enforce_voice_summaries",
                     lambda *a, **k: None)
        gate.start()
        self.addCleanup(gate.stop)
        prof = patch.object(voice_routes, "_profile_module", _Profile)
        prof.start()
        self.addCleanup(prof.stop)
        ok = patch.object(voice_routes, "_PROFILE_OK", True)
        ok.start()
        self.addCleanup(ok.stop)

    def _turn(self, lang: str, user: str, answer: str) -> None:
        self.sess.language_context = {"resolved_lang": lang, "source": "input",
                                      "confidence": 0.95, "profile_set": False}
        chat_runtime._append_turn(self.sess, "user", [{"kind": "text", "text": user}])
        chat_runtime._append_turn(self.sess, "assistant", [{"kind": "text", "text": answer}],
                                  cli_spawned=True)

    def test_no_turns_means_the_profiles_language(self) -> None:
        self.assertEqual(voice_routes.chat_language(self.tenant_id, self.sess.sid), "en")

    def test_the_latest_turns_resolved_language_wins(self) -> None:
        self._turn("de", "Warum ist der Himmel blau?", "Rayleigh-Streuung.")
        self.assertEqual(voice_routes.chat_language(self.tenant_id, self.sess.sid), "de")
        self._turn("en", "And in English?", "Rayleigh scattering.")
        self.assertEqual(voice_routes.chat_language(self.tenant_id, self.sess.sid), "en")

    def test_task_and_session_recaps_are_made_in_the_chats_language(self) -> None:
        self._turn("en", "Why do leaves turn red?", "Chlorophyll breaks down.")
        cmds: list[list[str]] = []
        say_langs: list[str] = []

        def fake_run(cmd, **kwargs):
            cmds.append(list(cmd))
            if "--session-recap-mode" in cmd:
                return _completed(0, stdout="An English recap.\n")
            return _completed(0, stdout="ok")

        def fake_say_cmd(out_path, text, lang):
            say_langs.append(lang)
            Path(out_path).write_bytes(b"OggS" + b"0" * 50)
            return ["true"]

        with patch.object(voice_routes.subprocess, "run", fake_run), \
                patch.object(voice_routes, "_say_cmd", fake_say_cmd):
            # the owed marker carries the language, the retry path reads it back
            self.assertTrue(voice_routes.owe_task_summary(
                self.tenant_id, self.sess.sid, "task-en", user_text="u", answer_text="a"))
            marker = voice_routes._task_summary_dir(self.tenant_id, self.sess.sid) / "task-en.pending"
            self.assertEqual(json.loads(marker.read_text(encoding="utf-8"))["lang"], "en")
            self.assertEqual(voice_routes.run_owed_task_summary(self.tenant_id, self.sess.sid, "task-en"), "done")
            self.assertTrue(voice_routes.generate_and_persist_session_summary(
                self.tenant_id, self.sess.sid, trigger="auto"))

        recap_cmds = [c for c in cmds if "--session-recap-mode" in c]
        self.assertEqual(len(recap_cmds), 2, cmds)
        for c in recap_cmds:
            self.assertEqual(c[c.index("--lang") + 1], "en", c)
        self.assertEqual(say_langs, ["en", "en"])
        items = voice_routes.list_task_summaries(self.tenant_id, self.sess.sid)
        self.assertEqual(items[0]["lang"], "en")
        sess_items = [s for s in voice_routes.list_session_summaries(self.tenant_id) if s["sid"] == self.sess.sid]
        self.assertEqual(sess_items[0]["lang"], "en")


if __name__ == "__main__":
    unittest.main()

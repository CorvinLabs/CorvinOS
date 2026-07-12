"""Tests for the web-chat uploaded-file manifest (attachment robustness).

The console chat used to surface uploaded attachments ONLY via a fragile,
frontend-built text header on the single turn where the file was attached, and
the system prompt never mentioned input files at all — so the engine frequently
"couldn't find" an uploaded file, especially on follow-up turns.

These tests lock in the robust, disk-sourced channel:
  * ``_attachment_manifest`` enumerates the files actually present in
    ``<workdir>/attachments`` with ABSOLUTE paths + a read-it instruction.
  * It is empty when there are no uploads (a normal turn is unaffected).
  * Hidden / non-regular entries are ignored; the file count is capped.
  * ``_turn_system_prompt`` folds the manifest onto the base prompt, and
    ``_build_args`` carries it into the ``--append-system-prompt-file`` argv
    slot for EVERY turn (so a follow-up question still sees the file) — a
    file, not an inline argument (Windows fresh-install fix: a multi-KB
    inline prompt blows past cmd.exe's ~8191-char command-line buffer).
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))


def _session(workdir: Path):
    from corvin_console import chat_runtime  # noqa: WPS433
    return chat_runtime.WebChatSession(
        sid="s1", tenant_id="_default",
        created_at=0.0, last_active_at=0.0, workdir=workdir,
    )


class AttachmentManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        from corvin_console import chat_runtime  # noqa: WPS433
        self.cr = chat_runtime
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self._tmp.name)
        self.sess = _session(self.workdir)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_empty_when_no_attachments_dir(self) -> None:
        self.assertEqual(self.cr._attachment_manifest(self.sess), "")

    def test_empty_when_attachments_dir_empty(self) -> None:
        (self.workdir / "attachments").mkdir()
        self.assertEqual(self.cr._attachment_manifest(self.sess), "")

    def test_lists_uploaded_file_with_absolute_path(self) -> None:
        ad = self.workdir / "attachments"
        ad.mkdir()
        f = ad / "sales.csv"
        f.write_text("a,b\n1,2\n")
        manifest = self.cr._attachment_manifest(self.sess)
        # absolute path present
        self.assertIn(str(f), manifest)
        self.assertTrue(Path(str(f)).is_absolute())
        # carries the read-it instruction + mime
        self.assertIn("READ the relevant path", manifest)
        self.assertIn("text/csv", manifest)

    def test_ignores_hidden_and_subdirs(self) -> None:
        ad = self.workdir / "attachments"
        ad.mkdir()
        (ad / ".hidden").write_text("x")
        (ad / "sub").mkdir()
        (ad / "real.txt").write_text("hello")
        manifest = self.cr._attachment_manifest(self.sess)
        self.assertIn("real.txt", manifest)
        self.assertNotIn(".hidden", manifest)
        self.assertNotIn("/sub ", manifest)

    def test_caps_file_count_with_summary_line(self) -> None:
        ad = self.workdir / "attachments"
        ad.mkdir()
        n = self.cr._ATTACH_MANIFEST_MAX + 5
        for i in range(n):
            (ad / f"f{i:03d}.txt").write_text("x")
        manifest = self.cr._attachment_manifest(self.sess)
        self.assertIn("more file(s)", manifest)

    def test_turn_system_prompt_includes_manifest(self) -> None:
        ad = self.workdir / "attachments"
        ad.mkdir()
        (ad / "report.pdf").write_bytes(b"%PDF-1.4")
        sp = self.cr._turn_system_prompt(self.sess)
        self.assertIn(self.cr._WEB_CHAT_SYSTEM_PROMPT, sp)
        self.assertIn("report.pdf", sp)

    def test_turn_system_prompt_plain_when_no_uploads(self) -> None:
        # _turn_system_prompt always includes _WEB_CHAT_SYSTEM_PROMPT as its base.
        # It may also append user-profile / memory-index blocks (added in a later
        # feature), so we check containment rather than strict equality.
        self.assertIn(
            self.cr._WEB_CHAT_SYSTEM_PROMPT, self.cr._turn_system_prompt(self.sess),
        )

    def test_build_args_carries_attachment_path(self) -> None:
        ad = self.workdir / "attachments"
        ad.mkdir()
        f = ad / "data.csv"
        f.write_text("x,y\n")
        args = self.cr._build_args(self.sess, resume=True)
        # --append-system-prompt-file is the argv slot right after the flag —
        # a short path, not the (multi-KB-capable) prompt text itself.
        idx = args.index("--append-system-prompt-file")
        prompt_file = Path(args[idx + 1])
        self.assertTrue(prompt_file.is_absolute())
        injected = prompt_file.read_text(encoding="utf-8")
        self.assertIn(str(f), injected)

    def test_build_args_prompt_file_is_hidden_from_artifact_scan(self) -> None:
        args = self.cr._build_args(self.sess, resume=False)
        idx = args.index("--append-system-prompt-file")
        prompt_file = Path(args[idx + 1])
        self.assertTrue(prompt_file.name.startswith("."))


if __name__ == "__main__":
    unittest.main()

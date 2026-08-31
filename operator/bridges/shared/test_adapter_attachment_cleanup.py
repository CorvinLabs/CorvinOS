#!/usr/bin/env python3
"""test_adapter_attachment_cleanup.py — regression gate for the
inbox-attachment orphan accumulation bug.

Before this fix, the daemons downloaded incoming media (voice notes,
images, documents) into shared/inbox/ as bare files alongside a JSON
envelope that referenced them via <kind>_path. process_one() moved
the envelope to processed/ but left the attachment in inbox/. Over
weeks of operation the orphan count grew into the hundreds (440
voice-message.ogg files on the operator's machine), produced
queue-size noise, and contributed to the "bridge replays old
messages after restart" perception.

`_move_inbox_with_attachments(inbox_file, msg)` is the helper that
finalises an envelope. It MUST:

  1. Move the envelope to PROCESSED.
  2. Move every referenced <kind>_path attachment that still exists
     to PROCESSED with its original filename.
  3. On filename collision (same attachment referenced by a second
     envelope), unlink the source instead of overwriting the
     existing PROCESSED entry.
  4. Silently no-op on a non-dict msg (early-return paths from
     process_one).
  5. Silently no-op on a missing attachment file (already gone /
     moved / never existed) — never raise.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _import_adapter():
    """Import adapter with INBOX/PROCESSED redirected to a tempdir."""
    for mod in list(sys.modules):
        if mod == "adapter":
            del sys.modules[mod]
    import adapter  # type: ignore
    return adapter


class AttachmentCleanupTests(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="adapter-cleanup-"))
        self.inbox = self.tmp / "inbox"
        self.processed = self.tmp / "processed"
        self.inbox.mkdir()
        self.processed.mkdir()
        os.environ["ADAPTER_INBOX"] = str(self.inbox)
        os.environ["ADAPTER_PROCESSED"] = str(self.processed)
        self.adapter = _import_adapter()
        # Sanity: adapter resolved INBOX/PROCESSED to our tempdir.
        self.assertEqual(self.adapter.INBOX, self.inbox)
        self.assertEqual(self.adapter.PROCESSED, self.processed)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("ADAPTER_INBOX", None)
        os.environ.pop("ADAPTER_PROCESSED", None)

    # ------------------------------------------------------------------

    def test_envelope_alone_moves_to_processed(self) -> None:
        env = self.inbox / "msg_001.json"
        env.write_text(json.dumps({"from": "x", "text": "hi"}))
        self.adapter._move_inbox_with_attachments(env, {"from": "x", "text": "hi"})
        self.assertFalse(env.exists(), "envelope should be gone from inbox")
        self.assertTrue((self.processed / "msg_001.json").exists(),
                        "envelope should land in processed/")

    def test_audio_attachment_travels_with_envelope(self) -> None:
        ogg = self.inbox / "abc123_voice-message.ogg"
        ogg.write_bytes(b"OggS\x00\x00fake-opus-bytes")
        env = self.inbox / "msg_002.json"
        msg = {"from": "x", "audio_path": str(ogg)}
        env.write_text(json.dumps(msg))
        self.adapter._move_inbox_with_attachments(env, msg)
        self.assertFalse(env.exists(), "envelope still in inbox")
        self.assertFalse(ogg.exists(), "attachment still in inbox — orphan bug regression")
        self.assertTrue((self.processed / "msg_002.json").exists())
        self.assertTrue((self.processed / "abc123_voice-message.ogg").exists(),
                        "attachment should land in processed/ with original name")

    def test_image_and_document_paths_also_cleaned(self) -> None:
        jpg = self.inbox / "selfie.jpg"
        pdf = self.inbox / "manual.pdf"
        jpg.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
        pdf.write_bytes(b"%PDF-1.4 fake")
        env = self.inbox / "msg_003.json"
        msg = {"from": "x", "image_path": str(jpg), "document_path": str(pdf)}
        env.write_text(json.dumps(msg))
        self.adapter._move_inbox_with_attachments(env, msg)
        self.assertFalse(jpg.exists())
        self.assertFalse(pdf.exists())
        self.assertTrue((self.processed / "selfie.jpg").exists())
        self.assertTrue((self.processed / "manual.pdf").exists())

    def test_video_path_also_cleaned(self) -> None:
        mp4 = self.inbox / "clip.mp4"
        mp4.write_bytes(b"\x00\x00\x00\x18ftypisom-fake")
        env = self.inbox / "msg_004.json"
        msg = {"from": "x", "video_path": str(mp4)}
        env.write_text(json.dumps(msg))
        self.adapter._move_inbox_with_attachments(env, msg)
        self.assertFalse(mp4.exists())
        self.assertTrue((self.processed / "clip.mp4").exists())

    def test_collision_unlinks_source_instead_of_overwriting(self) -> None:
        target = self.processed / "shared_name.ogg"
        target.write_bytes(b"OLDOggS-existing")
        ogg = self.inbox / "shared_name.ogg"
        ogg.write_bytes(b"NEWOggS-new-content")
        env = self.inbox / "msg_005.json"
        msg = {"audio_path": str(ogg)}
        env.write_text(json.dumps(msg))
        self.adapter._move_inbox_with_attachments(env, msg)
        # Source is gone — the structural cleanup happened.
        self.assertFalse(ogg.exists())
        # PROCESSED kept the older file (no overwrite).
        self.assertEqual(target.read_bytes(), b"OLDOggS-existing",
                         "collision should preserve existing PROCESSED file")

    def test_missing_attachment_file_is_a_silent_no_op(self) -> None:
        env = self.inbox / "msg_006.json"
        # audio_path points to a file that doesn't exist (race condition,
        # already-deleted, daemon restart between download and envelope-
        # write). MUST NOT raise.
        msg = {"audio_path": str(self.inbox / "ghost.ogg")}
        env.write_text(json.dumps(msg))
        # Should not raise:
        self.adapter._move_inbox_with_attachments(env, msg)
        self.assertTrue((self.processed / "msg_006.json").exists())

    def test_non_dict_msg_is_a_silent_no_op(self) -> None:
        env = self.inbox / "msg_007.json"
        env.write_text("malformed-not-json")
        # Pass None as msg (early-return path from process_one).
        # MUST move the envelope, MUST NOT raise, MUST NOT try
        # attachment cleanup.
        self.adapter._move_inbox_with_attachments(env, None)
        self.assertFalse(env.exists())
        self.assertTrue((self.processed / "msg_007.json").exists())

    def test_no_attachment_keys_is_normal_envelope_move(self) -> None:
        env = self.inbox / "msg_008.json"
        msg = {"from": "x", "text": "plain text message"}
        env.write_text(json.dumps(msg))
        self.adapter._move_inbox_with_attachments(env, msg)
        self.assertFalse(env.exists())
        self.assertTrue((self.processed / "msg_008.json").exists())


if __name__ == "__main__":
    unittest.main()

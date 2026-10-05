"""GDPR Art. 17 reaches group chats and the A2A message store (review R3,
2026-10-05: neither had a handler, so an erasure reported "completed" while
the subject's message text and files stayed on disk).

Plants real records with the stores' own writers in a throwaway CORVIN_HOME,
erases one subject, and checks that exactly that subject's data is gone —
other members' / other peers' data stays.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[2]
for p in (_REPO / "corvin_operator/bridges/shared", _REPO / "core/console"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


class ErasureChatGroupsA2AFeedTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        (self.home / "tenants" / "_default" / "global").mkdir(parents=True)
        env = patch.dict(os.environ, {"CORVIN_HOME": str(self.home), "CORVIN_TENANT_ID": "_default"})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("CORVIN_A2A_FEED_DIR", None)
        self.gdir = self.home / "tenants" / "_default" / "global"

    def test_a2a_feed_peer_is_erased_with_its_blobs(self):
        import a2a_feed as feed
        import erasure_handlers as eh
        from a2a_attachments import Attachment
        import base64, hashlib
        data = b"subject's photo"
        att = Attachment(name="p.png", mime="image/png",
                         content_b64=base64.b64encode(data).decode(),
                         sha256=hashlib.sha256(data).hexdigest())
        feed.record(direction="in", kind="task", peer_id="peer-subject", task_id="t1",
                    text="private words", attachments=[att], tenant_id="_default")
        feed.record(direction="out", kind="task", peer_id="peer-other", task_id="t2",
                    text="keep me", tenant_id="_default")
        res = eh.A2AFeedHandler().purge("peer-subject", "req-1")
        self.assertEqual(res.status.value if hasattr(res.status, "value") else res.status, "applied")
        store = (self.gdir / "a2a_feed" / "messages.jsonl").read_text()
        self.assertNotIn("private words", store)
        self.assertIn("keep me", store)
        self.assertFalse((self.gdir / "a2a_feed" / "blobs" / att.sha256).exists())

    def test_group_member_messages_files_and_membership_are_erased(self):
        from corvin_console import chat_group_store as store
        import erasure_handlers as eh
        g = store.create_group(self.gdir, tenant_id="_default", title="G",
                               created_by_participant_id="operator")
        gid = g["group_id"]
        store.add_participant(self.gdir, gid, participant_id="peer-subject", kind="a2a_peer",
                              display_name="Subject", peer_endpoint_id="peer-subject",
                              added_by="operator")
        att_dir = store.attachments_dir(self.gdir, gid)
        att_dir.mkdir(parents=True, exist_ok=True)
        (att_dir / "their file.txt").write_text("subject data")
        (att_dir / "mine.txt").write_text("operator data")
        store.append_message(self.gdir, gid, sender_participant_id="peer-subject",
                             text="[Attached files — stored with this group]\n"
                                  "- attachments/their file.txt (0.1 KB, text/plain)\n\nsecret")
        store.append_message(self.gdir, gid, sender_participant_id="operator", text="hello all")
        res = eh.ChatGroupHandler().purge("peer-subject", "req-2")
        self.assertEqual(res.count, 3)  # message + file + membership
        msgs = [m["text"] for m in store.list_messages(self.gdir, gid)]
        self.assertEqual(msgs, ["hello all"])
        self.assertFalse((att_dir / "their file.txt").exists())
        self.assertTrue((att_dir / "mine.txt").exists())
        parts = [p["participant_id"] for p in store.get_group(self.gdir, gid)["participants"]]
        self.assertNotIn("peer-subject", parts)

    def test_both_handlers_are_in_the_real_chain_and_claimed(self):
        import erasure_handlers as eh
        ids = {h.layer_id for h in eh.real_handler_chain()}
        self.assertTrue({"L-a2a-feed", "L-chat-groups"} <= ids)
        self.assertIn("global/a2a_feed", eh.COVERED_DIRS["L-a2a-feed"])
        self.assertIn("global/chat_groups", eh.COVERED_DIRS["L-chat-groups"])


if __name__ == "__main__":
    unittest.main()

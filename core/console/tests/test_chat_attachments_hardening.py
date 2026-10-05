"""Chat attachment upload/serve hardening — review R1 (2026-10-05), over the
real routes:

- a planted symlink at the target name is never followed (the attachments
  dir sits inside the worker's workdir);
- the same name twice never overwrites (O_EXCL), not even past _99;
- an oversized request is refused from its Content-Length, before parsing;
- a stored file that could run script is a sandboxed download, every
  response carries nosniff;
- the group upload route goes through the same path.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from test_learning_loop_routes_e2e import _sandbox  # noqa: E402


class AttachmentHardeningE2E(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _session(self, client, tenant_id):
        from corvin_console import chat_runtime
        sid = client.post("/v1/console/chat/sessions", json={"title": "att"}).json()["session"]["sid"]
        return sid, chat_runtime.get_session(tenant_id, sid)

    def _up(self, client, sid, *files):
        return client.post(f"/v1/console/chat/sessions/{sid}/attachments",
                           files=[("files", (n, d, "text/plain")) for n, d in files])

    def test_symlink_at_target_name_is_not_followed(self):
        with _sandbox(Path(self._tmp)) as (client, home, tenant_id, _e, _c):
            sid, sess = self._session(client, tenant_id)
            victim = Path(self._tmp) / "victim.txt"
            victim.write_text("original")
            att = sess.workdir / "attachments"
            att.mkdir(parents=True, exist_ok=True)
            (att / "note.txt").symlink_to(victim)
            r = self._up(client, sid, ("note.txt", b"EVIL"))
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(victim.read_text(), "original")
            stored = r.json()["attachments"][0]["name"]
            self.assertNotEqual(stored, "note.txt")
            self.assertEqual((att / stored).read_bytes(), b"EVIL")

    def test_same_name_never_overwrites_even_past_99(self):
        with _sandbox(Path(self._tmp)) as (client, home, tenant_id, _e, _c):
            sid, sess = self._session(client, tenant_id)
            att = sess.workdir / "attachments"
            att.mkdir(parents=True, exist_ok=True)
            (att / "a.txt").write_text("first")
            for i in range(1, 100):
                (att / f"a_{i}.txt").write_text(f"n{i}")
            r = self._up(client, sid, ("a.txt", b"new"))
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual((att / "a.txt").read_text(), "first")
            name = r.json()["attachments"][0]["name"]
            self.assertTrue(name.startswith("a_") and name.endswith(".txt"))
            self.assertEqual((att / name).read_bytes(), b"new")

    def test_oversized_declared_length_is_refused_before_parsing(self):
        with _sandbox(Path(self._tmp)) as (client, home, tenant_id, _e, _c):
            sid, _ = self._session(client, tenant_id)
            from corvin_console import attachments_common as ac
            r = client.post(
                f"/v1/console/chat/sessions/{sid}/attachments",
                content=b"x" * 10,
                headers={"content-type": "multipart/form-data; boundary=zz",
                         "content-length": str(ac.ATTACH_MAX_FILES * ac.ATTACH_MAX_BYTES + 10**6)},
            )
            self.assertEqual(r.status_code, 413, r.text)

    def test_scriptable_file_is_served_as_sandboxed_download(self):
        with _sandbox(Path(self._tmp)) as (client, home, tenant_id, _e, _c):
            sid, _ = self._session(client, tenant_id)
            self.assertEqual(self._up(client, sid, ("report.xhtml", b"<script>1</script>"),
                                      ("pic.png", b"\x89PNG")).status_code, 200)
            x = client.get(f"/v1/console/chat/sessions/{sid}/workdir/attachments/report.xhtml")
            self.assertEqual(x.status_code, 200)
            self.assertTrue(x.headers["content-disposition"].startswith("attachment"))
            self.assertEqual(x.headers.get("content-security-policy"), "sandbox")
            self.assertEqual(x.headers.get("x-content-type-options"), "nosniff")
            p = client.get(f"/v1/console/chat/sessions/{sid}/workdir/attachments/pic.png")
            self.assertTrue(p.headers["content-disposition"].startswith("inline"))
            self.assertEqual(p.headers.get("x-content-type-options"), "nosniff")

    def test_group_upload_goes_through_the_same_path(self):
        with _sandbox(Path(self._tmp)) as (client, home, tenant_id, _e, _c):
            g = client.post("/v1/console/chat/groups", json={"title": "g"})
            self.assertIn(g.status_code, (200, 201), g.text)
            gid = g.json()["group_id"]
            r = client.post(f"/v1/console/chat/groups/{gid}/attachments",
                            files=[("files", ("x.txt", b"hello", "text/plain"))])
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["attachments"][0]["path"], "attachments/x.txt")
            got = client.get(f"/v1/console/chat/groups/{gid}/attachments/x.txt")
            self.assertEqual(got.status_code, 200, got.text)
            self.assertEqual(got.content, b"hello")
            self.assertEqual(got.headers.get("x-content-type-options"), "nosniff")
            for bad in ("..%2Fgroup.json", "missing.txt", "%2E%2E"):
                self.assertEqual(client.get(f"/v1/console/chat/groups/{gid}/attachments/{bad}").status_code, 404, bad)


if __name__ == "__main__":
    unittest.main()

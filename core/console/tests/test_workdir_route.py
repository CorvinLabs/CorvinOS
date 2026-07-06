"""Tests for GET /chat/sessions/{sid}/workdir/{filepath}.

Verifies that session-workdir artifacts are served INLINE so the chat can
render them in-place (images, PDFs, audio, video, HTML) rather than forcing a
download. A regression here re-breaks inline media display in the chat command
centre. Path-traversal protection is also asserted.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))

import corvin_console.routes.chat as chat_routes  # noqa: E402


def _auth_record(tenant_id: str = "_default"):
    r = MagicMock()
    r.tenant_id = tenant_id
    return r


class WorkdirRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="test-workdir-"))
        self.workdir = self.tmp / "session-workdir"
        self.workdir.mkdir()
        self.sess = MagicMock()
        self.sess.workdir = self.workdir
        self.rec = _auth_record()

    def _serve(self, filepath: str):
        with patch(
            "corvin_console.routes.chat.chat_runtime.get_session",
            return_value=self.sess,
        ):
            return chat_routes.get_workdir_file(
                sid="s1", filepath=filepath, rec=self.rec,
            )

    def test_image_served_inline(self) -> None:
        (self.workdir / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        resp = self._serve("chart.png")
        cd = resp.headers["content-disposition"]
        self.assertIn("inline", cd)
        self.assertNotIn("attachment", cd)
        self.assertEqual(resp.media_type, "image/png")

    def test_pdf_served_inline(self) -> None:
        (self.workdir / "report.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
        resp = self._serve("report.pdf")
        self.assertIn("inline", resp.headers["content-disposition"])
        self.assertEqual(resp.media_type, "application/pdf")

    def test_audio_and_video_served_inline(self) -> None:
        (self.workdir / "clip.wav").write_bytes(b"RIFF0000WAVE")
        (self.workdir / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftyp")
        for name, mt in (("clip.wav", "audio/x-wav"), ("clip.mp4", "video/mp4")):
            resp = self._serve(name)
            self.assertIn("inline", resp.headers["content-disposition"])
            self.assertTrue(resp.media_type.startswith(("audio/", "video/")))

    def test_filename_still_advertised(self) -> None:
        # Inline disposition must still carry a sane filename for manual saves.
        (self.workdir / "data.csv").write_bytes(b"a,b\n1,2\n")
        resp = self._serve("data.csv")
        self.assertIn("data.csv", resp.headers["content-disposition"])

    def test_path_traversal_blocked(self) -> None:
        (self.tmp / "secret.txt").write_bytes(b"top secret")
        with self.assertRaises(HTTPException) as ctx:
            self._serve("../secret.txt")
        self.assertIn(ctx.exception.status_code, (400, 403))

    def test_missing_file_404(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            self._serve("nope.png")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_html_artifact_gets_csp_sandbox_header(self) -> None:
        """Adversarial review finding: a direct top-level navigation to an
        HTML artifact (copy-link, open-in-new-tab) previously loaded it
        same-origin and unsandboxed, executing any embedded script with
        full access to the real console origin — the iframe sandbox the
        frontend uses for in-chat rendering only applied when the frontend
        actually rendered it via the iframe, not to the raw URL itself."""
        (self.workdir / "report.html").write_bytes(b"<html><script>1</script></html>")
        resp = self._serve("report.html")
        self.assertEqual(resp.headers.get("content-security-policy"), "sandbox")
        self.assertIn("inline", resp.headers["content-disposition"])

    def test_svg_artifact_gets_csp_sandbox_header(self) -> None:
        (self.workdir / "diagram.svg").write_bytes(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>")
        resp = self._serve("diagram.svg")
        self.assertEqual(resp.headers.get("content-security-policy"), "sandbox")

    def test_non_html_artifacts_get_no_csp_header(self) -> None:
        (self.workdir / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        resp = self._serve("chart.png")
        self.assertNotIn("content-security-policy", resp.headers)


if __name__ == "__main__":
    unittest.main()

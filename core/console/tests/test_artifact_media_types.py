"""Console chat inline-artifact gate — every media/data type round-trips.

The console chat surfaces files Claude (or a delegated ACS run) writes into the
workdir as inline artifacts, rendered in-place by `ArtifactCard`
(web-next/src/pages/chat.tsx) — the same UX the messenger bridges give. The
gate that decides *which* files get surfaced is `chat_runtime._artifact_mime`.

A regression here silently drops generated media before it reaches the browser.
This is exactly what happened historically: the gate allowed only
image/PDF/CSV/HTML, so audio, video, JSON, TXT and Markdown were emitted by the
engine but never shown — even though the frontend could render them. These
tests pin the gate to the frontend's render capabilities and guard the negative
case (incidental source files must NOT spam the chat).
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))

import corvin_console.chat_runtime as chat_runtime  # noqa: E402

_IMAGE_EXTS = ["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"]
_AUDIO_EXTS = ["mp3", "wav", "ogg", "oga", "m4a", "flac", "aac", "opus", "weba"]
_VIDEO_EXTS = ["mp4", "webm", "mov", "mkv", "m4v", "ogv"]
# documents / data — exact mimes the frontend preview/iframe branches expect.
_DOC_EXTS = ["pdf", "html", "htm", "csv", "json", "txt", "md", "sql"]

# Files Claude routinely writes while working that are NOT user-facing media and
# must stay out of the chat to avoid clutter. (`.ts` is intentionally absent:
# mimetypes maps it to video/mp2t — an inherent collision with MPEG transport
# streams, not something the gate should special-case.)
_REJECT_EXTS = ["py", "js", "tsx", "sh", "exe", "so", "bin", "zip",
                "pyc", "lock", "o", "a"]


class ArtifactMediaGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="test-artifact-media-"))

    def _touch(self, name: str) -> Path:
        p = self.tmp / name
        p.write_bytes(b"")  # guess_type is filename-based; content is irrelevant
        return p

    def _assert_prefix(self, exts: list[str], prefix: str) -> None:
        for ext in exts:
            p = self._touch(f"out.{ext}")
            mime = chat_runtime._artifact_mime(p)
            self.assertIsNotNone(
                mime, f".{ext} must be surfaced as an inline artifact, got None")
            self.assertTrue(
                mime.startswith(prefix),
                f".{ext} → {mime!r}, expected a {prefix!r} mime")

    def test_images_surface(self) -> None:
        self._assert_prefix(_IMAGE_EXTS, "image/")

    def test_audio_surfaces(self) -> None:
        """Regression: audio was dropped by the old image/PDF-only allowlist."""
        self._assert_prefix(_AUDIO_EXTS, "audio/")

    def test_video_surfaces(self) -> None:
        """Regression: video was dropped by the old allowlist."""
        self._assert_prefix(_VIDEO_EXTS, "video/")

    def test_documents_and_data_surface(self) -> None:
        """PDF/HTML/CSV already worked; JSON/TXT/MD are the regression fix."""
        allowed = chat_runtime._ARTIFACT_MIME_EXACT
        for ext in _DOC_EXTS:
            p = self._touch(f"insights.{ext}")
            mime = chat_runtime._artifact_mime(p)
            self.assertIsNotNone(
                mime, f".{ext} must be surfaced as an inline artifact, got None")
            self.assertIn(
                mime, allowed,
                f".{ext} → {mime!r}, expected one of the exact doc/data mimes")

    def test_plot_png_still_works(self) -> None:
        """The canonical 'analyse + plot' flow — must never regress."""
        self.assertEqual(chat_runtime._artifact_mime(self._touch("plot.png")),
                         "image/png")

    def test_source_and_binaries_rejected(self) -> None:
        """Incidental engine work-files must not clutter the chat."""
        for ext in _REJECT_EXTS:
            p = self._touch(f"scratch.{ext}")
            self.assertIsNone(
                chat_runtime._artifact_mime(p),
                f".{ext} must NOT be surfaced as an inline artifact")

    def test_extension_fallback_when_mimetypes_misses(self) -> None:
        """Even if the platform mimetypes DB lacks an entry, media still passes.

        Simulate a stripped mimetypes DB so guess_type returns None for a known
        media extension; the ext fallback must still surface it.
        """
        import unittest.mock as mock
        with mock.patch.object(chat_runtime.mimetypes, "guess_type",
                               return_value=(None, None)):
            self.assertEqual(
                chat_runtime._artifact_mime(self._touch("voice.opus")),
                "audio/opus")
            self.assertEqual(
                chat_runtime._artifact_mime(self._touch("clip.mkv")),
                "video/x-matroska")
            self.assertEqual(
                chat_runtime._artifact_mime(self._touch("notes.md")),
                "text/markdown")
            # a non-media ext with no fallback still rejected
            self.assertIsNone(
                chat_runtime._artifact_mime(self._touch("build.log")))


if __name__ == "__main__":
    unittest.main()

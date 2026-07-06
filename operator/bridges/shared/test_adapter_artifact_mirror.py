#!/usr/bin/env python3
"""test_adapter_artifact_mirror.py — regression gate for the
artifacts→outputs mirror silently dropping regenerated media.

`_mirror_new_artifacts(artifacts_dir, outputs_dir, pre_artifacts)` copies L33
artifact files (images/plots/PDFs) into the per-chat outputs/ dir so the
messenger attachment scan picks them up and the user sees them in the chat.

Before this fix, the mirror only checked `dest.exists()` before copying: once
a same-named file had been mirrored once, any later turn that regenerated the
artifact under the same name (e.g. the same plot/screenshot re-run) was
silently skipped — the chat kept showing the stale file, or nothing at all if
the file had since been cleaned up from outputs/. The mtime-aware guard here
must re-mirror whenever the source is newer than the existing destination.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _import_adapter():
    for mod in list(sys.modules):
        if mod == "adapter":
            del sys.modules[mod]
    import adapter  # type: ignore
    return adapter


class ArtifactMirrorTests(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="adapter-artifact-mirror-"))
        self.artifacts_dir = self.tmp / "artifacts"
        self.outputs_dir = self.tmp / "outputs"
        self.artifacts_dir.mkdir()
        self.outputs_dir.mkdir()
        self.adapter = _import_adapter()

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pre_snapshot(self) -> dict[str, float]:
        return {p.name: p.stat().st_mtime
                for p in self.artifacts_dir.iterdir() if p.is_file()}

    def test_new_artifact_is_mirrored(self) -> None:
        pre = self._pre_snapshot()
        plot = self.artifacts_dir / "plot.png"
        plot.write_bytes(b"v1")
        mirrored = self.adapter._mirror_new_artifacts(
            self.artifacts_dir, self.outputs_dir, pre)
        self.assertEqual(mirrored, ["plot.png"])
        self.assertEqual((self.outputs_dir / "plot.png").read_bytes(), b"v1")

    def test_regenerated_same_name_artifact_overwrites_stale_output(self) -> None:
        """Regression: a same-named artifact re-run in a later turn must
        refresh outputs/, not be swallowed by an exists()-only guard."""
        pre = self._pre_snapshot()
        plot = self.artifacts_dir / "plot.png"
        plot.write_bytes(b"v1")
        first = self.adapter._mirror_new_artifacts(
            self.artifacts_dir, self.outputs_dir, pre)
        self.assertEqual(first, ["plot.png"])

        # Next turn: snapshot again, then overwrite the artifact with new
        # content and a distinctly later mtime (avoids flaky same-second
        # mtime collisions on coarse filesystems).
        pre2 = self._pre_snapshot()
        new_mtime = pre2["plot.png"] + 5
        plot.write_bytes(b"v2-different-content")
        os.utime(plot, (new_mtime, new_mtime))

        second = self.adapter._mirror_new_artifacts(
            self.artifacts_dir, self.outputs_dir, pre2)
        self.assertEqual(second, ["plot.png"],
                         "regenerated artifact must be re-mirrored, not skipped")
        self.assertEqual((self.outputs_dir / "plot.png").read_bytes(),
                         b"v2-different-content",
                         "outputs/ still holds the stale first-run content")

    def test_unchanged_artifact_not_remirrored(self) -> None:
        plot = self.artifacts_dir / "plot.png"
        plot.write_bytes(b"v1")
        pre = self._pre_snapshot()
        self.adapter._mirror_new_artifacts(self.artifacts_dir, self.outputs_dir, pre)

        # Same snapshot passed again (nothing changed since) → no re-mirror.
        pre_after = self._pre_snapshot()
        again = self.adapter._mirror_new_artifacts(
            self.artifacts_dir, self.outputs_dir, pre_after)
        self.assertEqual(again, [])

    def test_non_media_extension_not_mirrored(self) -> None:
        pre = self._pre_snapshot()
        (self.artifacts_dir / "data.json").write_bytes(b"{}")
        mirrored = self.adapter._mirror_new_artifacts(
            self.artifacts_dir, self.outputs_dir, pre)
        self.assertEqual(mirrored, [])
        self.assertFalse((self.outputs_dir / "data.json").exists())


if __name__ == "__main__":
    unittest.main()

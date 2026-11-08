"""T-0359 - the inspector's data: the project folder's entries, the knowledge-base counts and the video rows, over the
real API for a project created from the real catalogue. Read-only: the test checks that nothing changes.
"""
from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_projects_videos_e2e import ProjectVideosE2E, _URL  # noqa: E402

from test_admin_route import _sandbox  # noqa: E402,F401


def _tree_hash(root: Path) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and ".git" not in p.parts:
            out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


class ProjectStructureE2E(ProjectVideosE2E):
    def test_structure_lists_the_folder_counts_the_knowledge_base_and_reads_the_video_rows_without_writing(self):
        with self._ctx() as (c, hdr, root, home):
            pid = self._project(c, hdr)
            self.assertEqual(c.post(f"{_URL}/{pid}/videos", json={"name": "struct-video"}, headers=hdr).status_code, 201)
            proj = root / pid
            before = _tree_hash(proj)
            r = c.get(f"{_URL}/{pid}/structure")
            self.assertEqual(r.status_code, 200, r.text)
            s = r.json()
            paths = {e["path"]: e["kind"] for e in s["entries"]}
            self.assertEqual(paths.get("project.yaml"), "file")
            self.assertEqual(paths.get("docs"), "dir")
            self.assertTrue(all(".git" not in k.split("/") for k in paths), "a .git entry was listed")
            self.assertGreaterEqual(s["kb_counts"]["tasks"], 6)          # the seed + the six video tasks
            self.assertEqual(s["kb_counts"]["epics"], 2)                 # the seed epic + VIDEO-struct-video
            row = [v for v in s["videos"] if v["name"] == "struct-video"]
            self.assertEqual(len(row), 1, s["videos"])
            self.assertTrue(row[0]["folder_exists"] and row[0]["de"] and row[0]["en"])
            self.assertEqual(_tree_hash(proj), before, "reading the structure wrote a file")

    def test_a_project_of_another_tenant_has_no_structure(self):
        with self._ctx() as (c, hdr, root, home):
            pid = self._project(c, hdr)
            c.cookies.clear()
            self.assertIn(c.get(f"{_URL}/{pid}/structure").status_code, (401, 403))


if __name__ == "__main__":
    unittest.main()

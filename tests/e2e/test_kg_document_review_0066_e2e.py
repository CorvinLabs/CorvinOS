"""REVIEW-0066 M1 + M2: the Knowledge Graph document route over HTTP, with a project KB the console did not write.

* M1: a YAML alias bomb in a document's frontmatter must not be expanded (aliases are refused, the frontmatter is shown
  as empty and the text stays readable) - the response stays small.
* M2: the blocking work of ``GET /doc/{key}`` runs off the event loop; the project lookup resolves only the requested
  project; ``entities.jsonl`` is read once per change (mtime/size cache) and a graph file above the size limit is refused
  with a ``graph_error`` instead of being read.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core" / "console" / "tests"))
from test_admin_route import _sandbox  # noqa: E402

_KG = "/v1/console/plugins/corvin-knowledge"


def _bomb(levels: int) -> str:
    lines = ["id: ADR-0001", "title: x", "status: proposed",
             'l0: &l0 ["aaaaaaaa","aaaaaaaa","aaaaaaaa","aaaaaaaa","aaaaaaaa","aaaaaaaa","aaaaaaaa","aaaaaaaa","aaaaaaaa"]']
    for i in range(1, levels + 1):
        lines.append(f"l{i}: &l{i} [" + ",".join([f"*l{i-1}"] * 9) + "]")
    return "\n".join(lines)


def _project(root: Path, pid: str, frontmatter: str = "id: ADR-0001\ntitle: plain") -> Path:
    proj = root / pid
    docs = proj / "docs"
    for d in ("kb/_meta", "kb/graph", "decisions"):
        (docs / d).mkdir(parents=True, exist_ok=True)
    (proj / "project.yaml").write_text(f"schema: 1\nid: {pid}\ntitle: {pid}\ntenant: _default\nfeatures: {{kb: true}}\n",
                                       encoding="utf-8")
    (docs / "kb" / "_meta" / "sources.yaml").write_text("sources:\n- root: .\n  dirs: {decisions: decision}\n", encoding="utf-8")
    (docs / "decisions" / "ADR-0001-x.md").write_text(f"---\n{frontmatter}\n---\nthe body text\n", encoding="utf-8")
    (docs / "kb" / "graph" / "entities.jsonl").write_text(
        json.dumps({"uid": "u1", "id": "ADR-0001", "title": "x", "path": "decisions/ADR-0001-x.md"}) + "\n", encoding="utf-8")
    (docs / "kb" / "graph" / "relations.jsonl").write_text("", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    return proj


def _register(home: Path, projects: list[tuple[str, Path]]) -> None:
    reg = home / "tenants" / "_default" / "projects" / "registry.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(json.dumps({"projects": [{"id": p, "path": str(d)} for p, d in projects]}), encoding="utf-8")


class KgDocumentReview0066(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._saved = os.environ.get("CORVIN_KB_REPO")
        os.environ["CORVIN_KB_REPO"] = str(self.tmp / "bound")
        (self.tmp / "bound" / "kb" / "graph").mkdir(parents=True)

    def tearDown(self):
        os.environ.pop("CORVIN_KB_REPO", None) if self._saved is None else os.environ.__setitem__("CORVIN_KB_REPO", self._saved)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, fn, projects):
        with _sandbox(self.tmp / "s", tenants=("_default",)) as (client, _t, home, _clients):
            from corvin_console.routes import plugins_corvin_knowledge_api as kg
            _register(home, projects)
            with mock.patch.object(kg, "CONFIG_FILE", self.tmp / "oshome" / "corvin-knowledge.json"):
                return fn(client, kg)

    def test_m1_alias_bomb_is_not_expanded(self):
        proj = _project(self.tmp / "p", "bomb", frontmatter=_bomb(6))     # 423 bytes; expanded = 66 MB

        def go(client, kg):
            r = client.get(f"{_KG}/doc/u1", params={"project": "bomb"})
            self.assertEqual(r.status_code, 200, r.text[:300])
            self.assertLess(len(r.content), 64 * 1024, "the aliases were expanded into the response")
            body = r.json()
            self.assertEqual(body["frontmatter"], {}, "a frontmatter with aliases is not parsed")
            self.assertIn("aliases", body.get("frontmatter_error", ""))
            self.assertIn("&l0", body["markdown"], "the raw text stays readable")
        self._run(go, [("bomb", proj)])

    def test_m1_project_yaml_with_aliases_is_refused(self):
        from core.projects import manifest
        with self.assertRaises(manifest.ManifestError):
            manifest.parse("schema: 1\nid: p\ntenant: _default\n" + _bomb(4).replace("id: ADR-0001\n", ""))
        self.assertEqual(manifest.parse("schema: 1\nid: p\ntenant: _default\n")["id"], "p")

    def test_m1_plain_frontmatter_still_parses(self):
        proj = _project(self.tmp / "p", "plain")

        def go(client, kg):
            body = client.get(f"{_KG}/doc/u1", params={"project": "plain"}).json()
            self.assertEqual(body["frontmatter"], {"id": "ADR-0001", "title": "plain"})
            self.assertEqual(body["markdown"].strip(), "the body text")
        self._run(go, [("plain", proj)])

    def test_m2_document_is_loaded_off_the_event_loop(self):
        proj = _project(self.tmp / "p", "p1")
        seen = []

        def go(client, kg):
            real = kg.load_document

            def spy(config, key):
                try:
                    asyncio.get_running_loop()
                    seen.append("loop")
                except RuntimeError:
                    seen.append("thread")
                return real(config, key)
            real_scoped = kg._scoped_config

            def spy_scoped(*a, **k):
                try:
                    asyncio.get_running_loop()
                    seen.append("loop")
                except RuntimeError:
                    seen.append("thread")
                return real_scoped(*a, **k)
            with mock.patch.object(kg, "load_document", spy), mock.patch.object(kg, "_scoped_config", spy_scoped):
                r = client.get(f"{_KG}/doc/u1", params={"project": "p1"})
            self.assertEqual(r.status_code, 200, r.text)
        self._run(go, [("p1", proj)])
        self.assertEqual(seen, ["thread", "thread"], "the project lookup and the document read ran on the event loop")

    def test_m2_only_the_requested_project_is_resolved(self):
        a = _project(self.tmp / "p", "pa")
        b = _project(self.tmp / "p", "pb")
        walked = []

        def go(client, kg):
            from core.projects import changes
            real = changes.require_own_repo

            def spy(p, *a_, **k):
                walked.append(Path(p).resolve())
                return real(p, *a_, **k)
            with mock.patch.object(changes, "require_own_repo", spy):
                self.assertEqual(client.get(f"{_KG}/doc/u1", params={"project": "pb"}).status_code, 200)
        self._run(go, [("pa", a), ("pb", b)])
        self.assertTrue(walked)
        self.assertFalse([p for p in walked if p.is_relative_to(a.resolve())], "another project's .git was walked")

    def test_m2_entities_are_read_once_per_change(self):
        proj = _project(self.tmp / "p", "p1")

        def go(client, kg):
            calls = []
            real = kg._parse_jsonl

            def spy(path, *a, **k):
                calls.append(Path(path).name)
                return real(path, *a, **k)
            with mock.patch.object(kg, "_parse_jsonl", spy):
                for _ in range(3):
                    self.assertEqual(client.get(f"{_KG}/doc/u1", params={"project": "p1"}).status_code, 200)
            self.assertEqual(calls.count("entities.jsonl"), 1, calls)
        self._run(go, [("p1", proj)])

    def test_m2_an_oversized_graph_file_is_refused_not_read(self):
        proj = _project(self.tmp / "p", "big")
        ent = proj / "docs" / "kb" / "graph" / "entities.jsonl"
        with ent.open("a", encoding="utf-8") as fh:
            fh.write("\n" * 5000)

        def go(client, kg):
            with mock.patch.object(kg, "_GRAPH_MAX_BYTES", 4096):
                r = client.get(f"{_KG}/doc/u1", params={"project": "big"})
                self.assertEqual(r.status_code, 413, r.text)
                self.assertIn("too large", r.json()["detail"])
                g = client.get(f"{_KG}/graph").json()          # the bound graph is unaffected
                self.assertNotEqual(g.get("graph_state"), "error", g)
                os.utime(ent)
                data = kg.load_graph_data({"repo_path": str(proj / "docs")})
                self.assertEqual(data["entities"], [])
                self.assertIn("too large", data["graph_error"])
        self._run(go, [("big", proj)])


if __name__ == "__main__":
    unittest.main()

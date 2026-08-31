"""Prompt-assembly inspector record (Layer B) — the bausteine → final-prompt chain.

  * build_sections extracts each retrieval channel + synthesis + bound tools/skills.
  * persist/read roundtrip keyed by turn id.
  * a missing turn / erased sidecar reads as None (never raises).
  * the turn-id key is sanitised (no path traversal in the sidecar filename).

Run: python3 operator/context_engineering/tests/test_prompt_assembly.py
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]


def _load():
    ce = _REPO / "operator" / "context_engineering"
    spec = importlib.util.spec_from_file_location(
        "context_engineering", str(ce / "__init__.py"),
        submodule_search_locations=[str(ce)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["context_engineering"] = mod
    spec.loader.exec_module(mod)
    return mod


class _Brief:
    class _MC:
        matches = [type("X", (), {"title": "ADR-0222 foundation"})()]
    memory_context = _MC()
    related_decisions = [type("D", (), {"decision_id": "ADR-0178", "title": "loop"})()]
    recommended_skills = []
    approach = ["tighten the loop"]
    blockers = ["no stash in worktree"]


class _Bundle:
    brief = _Brief()
    synthesised_prompt = "Count orders per region."
    tools_to_bind = [type("T", (), {"name": "mcp__forge__cel_csv"})()]
    skills_to_bind = [type("S", (), {"skill_id": "sql-explain"})()]


class PromptAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.ce = _load()
        self.pa = sys.modules["context_engineering.prompt_assembly"]

    def test_build_sections_covers_every_channel(self):
        kinds = [s["kind"] for s in self.pa.build_sections(_Bundle())]
        for k in ("memory", "adrs", "approach", "blockers", "synthesis",
                  "tools", "forged_skills"):
            self.assertIn(k, kinds, f"section {k} missing")
        syn = next(s for s in self.pa.build_sections(_Bundle()) if s["kind"] == "synthesis")
        self.assertEqual(syn["text"], "Count orders per region.")

    def test_build_sections_accepts_bare_brief(self):
        # passive path passes a bare brief (no .brief / synthesised_prompt)
        kinds = [s["kind"] for s in self.pa.build_sections(_Brief())]
        self.assertIn("memory", kinds)
        self.assertNotIn("synthesis", kinds)

    def test_persist_read_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            secs = self.pa.build_sections(_Bundle())
            self.pa.persist_assembly(
                d, "turn-abc", sections=secs, cel_text="## Context brief",
                final_prompt="SYSTEM...\n## Context brief",
                forged_tools=["mcp__forge__cel_csv"], forged_skills=["sql-explain"])
            got = self.pa.read_assembly(d, "turn-abc")
            self.assertTrue(got["final_prompt"].startswith("SYSTEM"))
            self.assertEqual(got["forged_tools"], ["mcp__forge__cel_csv"])
            self.assertEqual([s["kind"] for s in got["sections"]][0], "memory")

    def test_missing_turn_reads_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(self.pa.read_assembly(d, "turn-nope"))

    def test_turn_id_key_sanitised(self):
        # a traversal-shaped turn id must not escape the cel-briefs dir
        with tempfile.TemporaryDirectory() as d:
            self.pa.persist_assembly(d, "../../etc/passwd", sections=[],
                                     cel_text="x", final_prompt="y")
            written = list((Path(d) / "cel-briefs").glob("*"))
            for w in written:
                self.assertNotIn("..", w.name)
                self.assertTrue(w.resolve().is_relative_to(Path(d).resolve()))


if __name__ == "__main__":
    unittest.main(verbosity=2)

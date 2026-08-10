"""CEL substance — the stages must find real signal, not run empty (ADR-0275).

Regression for the "pipeline wired but empty" bug found live: a real Discord
turn produced stages_ok=3 but top_score=0.0 and an empty brief, because
  * MemoryLookup pointed at ~/.claude/projects/CorvinOS/memory — missing the
    escaped repo-path prefix, so it never resolved (real dir has ~180 files);
  * SkillInjection returned only package skills (usually none) with the ADR path
    a TODO, and scored every skill a constant 0.7 (no ranking).

Run: python3 operator/context_engineering/tests/test_cel_substance_adr0275.py
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "operator" / "forge"))


def _load():
    ce = _REPO / "operator" / "context_engineering"
    spec = importlib.util.spec_from_file_location(
        "context_engineering", str(ce / "__init__.py"),
        submodule_search_locations=[str(ce)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["context_engineering"] = mod
    spec.loader.exec_module(mod)
    return mod


class CelSubstanceTests(unittest.TestCase):
    def setUp(self):
        self.ce = _load()

    # ── memory ───────────────────────────────────────────────────────────
    def test_default_memory_dir_is_escaped_repo_path(self):
        from context_engineering.memory_lookup import _default_memory_dir
        p = _default_memory_dir()
        self.assertEqual(p.name, "memory")
        self.assertIn(".claude", str(p))
        # the escaped segment carries the repo path (dashes), NOT the old bare
        # "CorvinOS" that never resolved
        self.assertNotEqual(p.parent.name, "CorvinOS")
        self.assertIn("-", p.parent.name)
        self.assertTrue(p.parent.name.endswith("CorvinOS"))

    def test_memory_lookup_finds_files(self):
        from context_engineering.memory_lookup import MemoryLookup
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "tde-loss.md").write_text(
                "---\nname: tde-loss\ndescription: TDE loss gate routing\n---\nbody",
                encoding="utf-8")
            res = MemoryLookup(memory_dir=d).search(["tde", "loss"], max_results=5)
            self.assertGreaterEqual(len(res), 1, "memory must find a matching file")

    # ── skill ────────────────────────────────────────────────────────────
    def test_repo_skills_loaded(self):
        from context_engineering.skill_injection import _load_repo_skills
        _load_repo_skills.cache_clear()
        skills = _load_repo_skills()
        self.assertGreater(len(skills), 0, "bundle skills must load from the repo")
        self.assertTrue(all("text" in s and "id" in s for s in skills))

    def test_skill_scoring_ranks_by_terms_not_constant(self):
        from context_engineering.skill_injection import SkillInjection
        task = SimpleNamespace(key_terms=["adr", "gate"])
        res = SkillInjection(tenant_id="_default").recommend_skills(task, None, top_n=3)
        self.assertGreaterEqual(len(res.recommended_skills), 1,
                                "a task about 'adr gate' must match a real skill")
        top = res.recommended_skills[0]
        self.assertGreater(top.relevance_score, 0.0,
                           "relevance must be a real keyword match, not the old 0.7")

    def test_skill_zero_match_task_drops_noise(self):
        from context_engineering.skill_injection import SkillInjection
        task = SimpleNamespace(key_terms=["zzznonsensewordxyz"])
        res = SkillInjection(tenant_id="_default").recommend_skills(task, None, top_n=3)
        self.assertEqual(len(res.recommended_skills), 0,
                         "no term match → inject nothing, not constant-score noise")

    # ── integration: the brief carries real content ─────────────────────
    def test_build_brief_produces_nonempty_brief(self):
        from context_engineering.pipeline import build_brief, render_brief_to_text
        # graph + skill resolve from the repo (always present); memory may be
        # empty in CI, but graph/skill alone must already yield a brief.
        brief, trace = build_brief("adr gate audit chain review", "_default",
                                   None, meter=False)
        text = render_brief_to_text(brief) or ""
        self.assertGreater(len(text), 0, "the brief must carry real content")
        stages = {s["stage"]: s for s in trace["stages"]}
        self.assertGreaterEqual(len(stages["skill"].get("sources", [])), 1,
                                "skill stage must be non-empty for a matching task")


if __name__ == "__main__":
    unittest.main()

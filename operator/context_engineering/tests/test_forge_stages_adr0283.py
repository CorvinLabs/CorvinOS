"""P-D — ToolForge + SkillForge stages (ADR-0283).

  * both registered, neither in the default pipeline (opt-in).
  * ToolForge: needs.tools → a forged ToolRef bound to the bundle.
  * AST allowlist: a dangerous impl is rejected; a clean impl passes.
  * an LLM-authored impl is IGNORED (template used) unless allow_llm_impl is set.
  * SkillForge: needs.skills → a SkillRef on the skill channel (not tools).
  * a forge failure is fail-safe (turn proceeds).

Run: python3 operator/context_engineering/tests/test_forge_stages_adr0283.py
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[3]
for p in (_REPO / "operator" / "forge", _REPO / "core" / "console"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _load():
    ce = _REPO / "operator" / "context_engineering"
    spec = importlib.util.spec_from_file_location(
        "context_engineering", str(ce / "__init__.py"),
        submodule_search_locations=[str(ce)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["context_engineering"] = mod
    spec.loader.exec_module(mod)
    return mod


class ForgeStagesTests(unittest.TestCase):
    def setUp(self):
        self.ce = _load()
        self.stages = sys.modules["context_engineering.stages"]
        self.tf = sys.modules["context_engineering.stages.toolforge"]
        self.sf = sys.modules["context_engineering.stages.skillforge"]
        base = sys.modules["context_engineering.stages.base"]
        self.Bundle, self.Ctx = base.ContextBundle, base.StageCtx

    def test_opt_in(self):
        for sid in ("toolforge", "skillforge"):
            self.assertNotIn(sid, self.stages.DEFAULT_PIPELINE)
            self.assertIsNotNone(self.stages.get_stage(sid))

    def test_ast_allowlist(self):
        ok, _ = self.tf.ast_allowlist_ok("import json\nprint(1)")
        self.assertTrue(ok)
        bad, reason = self.tf.ast_allowlist_ok("import os\nos.system('rm -rf /')")
        self.assertFalse(bad)
        self.assertIn("os", reason)
        bad2, _ = self.tf.ast_allowlist_ok("eval('1+1')")
        self.assertFalse(bad2)
        # finding #2: the builtins + attribute-form bypasses must be caught
        self.assertFalse(self.tf.ast_allowlist_ok("import builtins\nbuiltins.eval('x')")[0])
        self.assertFalse(self.tf.ast_allowlist_ok("import x\nx.system('rm')")[0])
        self.assertFalse(self.tf.ast_allowlist_ok("import x\nx.eval('y')")[0])

    def test_ast_indirect_name_bypasses_blocked(self):
        # review R2 finding B1: a forbidden builtin in ANY load-context position
        # (not just Call.func) must be rejected. These all returned True before.
        for bad in (
            "_e = eval\n_e('__import__(1)')",          # alias then call
            "__builtins__['eval']('x')",                # builtins subscript
            "list(map(exec, ['code']))",                # passed as an argument
            "g = getattr\ng(object, 'x')",              # getattr alias
            "sorted([1], key=eval)",                    # forbidden as a kwarg value
            "import sys\nsys.modules['os'].execv('x',['x'])",   # R2: module registry
            "import sys\nsys.modules['subprocess'].Popen(['id'])",
            "import sys\nprint(sys.modules['os'].environ)",     # env/secret leak
        ):
            ok, reason = self.tf.ast_allowlist_ok(bad)
            self.assertFalse(ok, f"bypass not blocked: {bad!r} -> {reason}")
        # a clean impl using only json/sys still passes
        self.assertTrue(self.tf.ast_allowlist_ok(
            "import json, sys\nprint(json.dumps(json.load(sys.stdin)))")[0])

    def test_toolforge_binds_forged_tool(self):
        b = self.Bundle(task="x")
        b.scratch["needs"] = {"tools": [{"name": "csv_count", "description": "count"}]}
        ctx = self.Ctx(tenant_id="_default")
        with patch.object(self.tf, "_forge_create") as fc:
            b, tel = self.stages.get_stage("toolforge").run(b, ctx)
        self.assertTrue(fc.called)
        self.assertEqual([t.name for t in b.tools_to_bind], ["mcp__forge__cel_csv_count"])
        # default: template impl, not any LLM impl
        self.assertEqual(fc.call_args[0][4], self.tf._TEMPLATE_IMPL)

    def test_llm_impl_never_executed_same_turn(self):
        # review R2 #1: same-turn forging ALWAYS uses the deterministic template —
        # an LLM-authored impl is NEVER executed same-turn, even with the (now
        # retired) allow_llm_impl config, because the AST pre-filter is provably
        # incomplete against Python introspection (sys.modules[...] etc.).
        clean_impl = "import json,sys\nprint(json.dumps({'ok':True}))\n"
        for cfg in ({}, {"allow_llm_impl": True}):
            b = self.Bundle(task="x")
            b.scratch["needs"] = {"tools": [{"name": "t1", "impl": clean_impl}]}
            ctx = self.Ctx(tenant_id="_default", config=cfg)
            with patch.object(self.tf, "_forge_create") as fc:
                self.stages.get_stage("toolforge").run(b, ctx)
            self.assertEqual(fc.call_args[0][4], self.tf._TEMPLATE_IMPL,
                             f"template used even with config={cfg}")

    def test_skillforge_binds_skill_channel(self):
        b = self.Bundle(task="x")
        b.scratch["needs"] = {"skills": [{"name": "sql-explain", "body": "# sql\n"}]}
        ctx = self.Ctx(tenant_id="_default")
        with patch.object(self.sf, "_skill_create"):
            b, tel = self.stages.get_stage("skillforge").run(b, ctx)
        self.assertEqual([s.skill_id for s in b.skills_to_bind], ["sql-explain"])
        self.assertEqual(b.tools_to_bind, [], "skills are NOT on the tool channel")

    def test_forge_failure_is_fail_safe(self):
        b = self.Bundle(task="x")
        b.scratch["needs"] = {"tools": [{"name": "t1"}]}
        ctx = self.Ctx(tenant_id="_default")
        with patch.object(self.tf, "_forge_create", side_effect=RuntimeError("forge down")):
            b, tel = self.stages.get_stage("toolforge").run(b, ctx)
        self.assertEqual(b.tools_to_bind, [], "failed forge binds nothing, no crash")
        self.assertEqual(tel.status, "ok")


if __name__ == "__main__":
    unittest.main()

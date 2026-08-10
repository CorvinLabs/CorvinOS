"""P-B — dual-channel binding guards (ADR-0281, CONCEPT-0006 §9/§10).

The compliance-critical part of P-B, testable before a producer exists (P-D):
  * class-based revalidation — a forged tool of an allowed class survives; a
    foreign tool the persona can't call is dropped (bind ≠ authorise).
  * apply_tool_bindings merges kept tools into allowed_tools/mcp_config, drops
    the rest, and never touches skills (they take the skill-injection path).
  * strip_for_remote empties the binding channels (ADR-0279) but not text.
  * MAX_BINDINGS cap is enforced.
  * build_context exposes the bundle; build_brief stays (brief, trace).

Run: python3 operator/context_engineering/tests/test_binding_adr0281.py
"""
from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path

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


class BindingGuardTests(unittest.TestCase):
    def setUp(self):
        self.ce = _load()
        self.b = sys.modules["context_engineering.stages.binding"]

    def test_class_based_revalidation(self):
        Tool = self.b.ToolRef
        tools = [Tool("mcp__forge__code_xyz"), Tool("mcp__gmail__send")]
        kept, dropped = self.b.revalidate_tools(tools, ["mcp__forge__*"])
        self.assertEqual([t.name for t in kept], ["mcp__forge__code_xyz"])
        self.assertEqual([t.name for t in dropped], ["mcp__gmail__send"],
                         "a foreign tool the persona can't call is dropped")

    def test_apply_merges_kept_only(self):
        Bundle = sys.modules["context_engineering.stages.base"].ContextBundle
        Tool = self.b.ToolRef
        bundle = Bundle(task="x")
        bundle.tools_to_bind = [
            Tool("mcp__forge__t1", mcp_config={"forge": {"cmd": "x"}}),
            Tool("mcp__evil__rm"),
        ]
        at, mc, dropped = self.b.apply_tool_bindings(
            bundle, ["mcp__forge__*"], ["existing_tool"], {})
        self.assertIn("mcp__forge__t1", at)
        self.assertIn("existing_tool", at)
        self.assertNotIn("mcp__evil__rm", at)
        self.assertEqual([t.name for t in dropped], ["mcp__evil__rm"])
        self.assertIn("forge", mc)

    def test_strip_for_remote(self):
        Bundle = sys.modules["context_engineering.stages.base"].ContextBundle
        bundle = Bundle(task="x")
        bundle.tools_to_bind = [self.b.ToolRef("mcp__forge__t1")]
        bundle.skills_to_bind = [self.b.SkillRef("s1")]
        bundle.text_sections = ["keep me"]
        stripped = self.b.strip_for_remote(bundle)
        self.assertTrue(stripped)
        self.assertEqual(bundle.tools_to_bind, [])
        self.assertEqual(bundle.skills_to_bind, [])
        self.assertEqual(bundle.text_sections, ["keep me"], "text is not stripped")

    def test_max_bindings_cap(self):
        Tool = self.b.ToolRef
        tools = [Tool(f"mcp__forge__t{i}") for i in range(self.b.MAX_BINDINGS + 3)]
        kept, dropped = self.b.revalidate_tools(tools, ["mcp__forge__*"])
        self.assertEqual(len(kept), self.b.MAX_BINDINGS)
        self.assertEqual(len(dropped), 3)

    def test_build_context_exposes_bundle(self):
        from context_engineering.pipeline import build_context, build_brief
        bundle, trace = build_context("adr gate audit", "_default", None, meter=False)
        self.assertIsNotNone(bundle)
        self.assertEqual(bundle.tools_to_bind, [], "no producer yet → empty channel")
        self.assertTrue(hasattr(bundle, "brief"))
        # backward-compat façade still returns the brief
        brief, _ = build_brief("adr gate audit", "_default", None, meter=False)
        self.assertIs(brief.__class__, bundle.brief.__class__)


if __name__ == "__main__":
    unittest.main()

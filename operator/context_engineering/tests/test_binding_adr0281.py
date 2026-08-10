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


class BindingGuardTests(unittest.TestCase):
    def setUp(self):
        self.ce = _load()
        self.b = sys.modules["context_engineering.stages.binding"]

    # A persona that genuinely has the forge capability (what the cowork resolver
    # returns for `assistant`/`inbox`: forge_enabled true + the injected globs).
    FORGE_CAPS = {"forge_enabled": True, "skill_forge_enabled": True}

    def test_class_based_revalidation(self):
        Tool = self.b.ToolRef
        tools = [Tool("mcp__forge__code_xyz"), Tool("mcp__gmail__send")]
        kept, dropped = self.b.revalidate_tools(tools, ["mcp__forge__*"],
                                                self.FORGE_CAPS)
        self.assertEqual([t.name for t in kept], ["mcp__forge__code_xyz"])
        self.assertEqual([t.name for t in dropped], ["mcp__gmail__send"],
                         "a foreign tool the persona can't call is dropped")

    def test_capability_class_is_the_flag_not_the_glob(self):
        """Review R6: the glob list is NOT the capability class. An all-allowed
        persona arrives as ["*"], which matches `mcp__forge__*` even when the
        operator left `forge_enabled: false` — and that same flag decides whether
        the Forge MCP server is injected, so the bind produced an un-callable name
        plus an orphaned on-disk artifact. The class is the flag (ADR-0281 R2)."""
        Tool = self.b.ToolRef
        tools = [Tool("mcp__forge__cel_csv_group")]
        kept, dropped = self.b.revalidate_tools(
            tools, ["*"], {"forge_enabled": False, "skill_forge_enabled": False})
        self.assertEqual(kept, [], "no forge capability → the forged tool is dropped")
        self.assertEqual(len(dropped), 1)
        # absent caps mapping is fail-closed, exactly like absent patterns
        self.assertEqual(self.b.revalidate_tools(tools, ["*"], None)[0], [])
        # with the capability, the same tool survives
        self.assertEqual(
            [t.name for t in self.b.revalidate_tools(tools, ["*"], self.FORGE_CAPS)[0]],
            ["mcp__forge__cel_csv_group"])
        # a tool with NO known capability class stays glob-governed
        self.assertEqual(
            [t.name for t in self.b.revalidate_tools(
                [Tool("mcp__gmail__send")], ["*"], {})[0]],
            ["mcp__gmail__send"])

    def test_mcp_config_carrying_ref_is_refused_in_the_enforcer(self):
        """Review R7: the refusal must live in the ONE enforcer, not per boundary —
        the first cut put it in the bridge, so the console would silently have bound
        a tool name whose server never arrives. No boundary can plumb an extra MCP
        server into an already-written config file, so the ref is dropped AND its
        forged artifact rolled back."""
        Bundle = sys.modules["context_engineering.stages.base"].ContextBundle
        pipe = sys.modules["context_engineering.pipeline"]
        bundle = Bundle(task="x")
        bundle.tools_to_bind = [
            self.b.ToolRef("mcp__forge__cel_ok"),
            self.b.ToolRef("mcp__forge__cel_needs_server",
                           mcp_config={"srv": {"cmd": "x"}}),
        ]
        bundle.scratch["_forged_tools"] = [
            {"name": "cel_ok", "ref": "mcp__forge__cel_ok"},
            {"name": "cel_needs_server", "ref": "mcp__forge__cel_needs_server"}]
        trace: dict = {}
        rolled: list = []
        with patch.object(sys.modules["context_engineering.stages.toolforge"],
                          "uncreate_tools",
                          side_effect=lambda t, names: rolled.extend(names)):
            pipe._gate2_and_bind(bundle, trace, lambda _t: (True, ""),
                                 ["*"], self.FORGE_CAPS)
        self.assertEqual([t.name for t in bundle.tools_to_bind],
                         ["mcp__forge__cel_ok"])
        self.assertEqual(trace.get("mcp_config_unsupported"), 1)
        self.assertEqual(rolled, ["cel_needs_server"],
                         "the refused ref's on-disk artifact is rolled back")

    def test_skill_forge_class_needs_its_own_flag(self):
        Tool = self.b.ToolRef
        tools = [Tool("mcp__skill_forge__skill_create")]
        caps = {"forge_enabled": True, "skill_forge_enabled": False}
        self.assertEqual(self.b.revalidate_tools(tools, ["*"], caps)[0], [],
                         "forge_enabled does not grant the skill_forge class")

    def test_render_skill_bindings_is_the_injection_channel(self):
        """Review R6: SkillForge was a write-only channel — no live spawn path
        consumed `skills_to_bind`, so a forged skill reached no worker."""
        Bundle = sys.modules["context_engineering.stages.base"].ContextBundle
        bundle = Bundle(task="x")
        self.assertEqual(self.b.render_skill_bindings(bundle), "",
                         "nothing bound → empty block, caller can append blindly")
        bundle.skills_to_bind = [self.b.SkillRef("cel_csv_agg", body="# aggregate\n"),
                                 self.b.SkillRef("cel_empty", body="")]
        block = self.b.render_skill_bindings(bundle)
        self.assertIn("cel_csv_agg", block)
        self.assertIn("# aggregate", block)
        self.assertNotIn("cel_empty", block, "a body-less ref renders nothing")
        self.assertNotIn("allowed_tools", block, "skills never enter the tool channel")

    def test_apply_merges_kept_only(self):
        Bundle = sys.modules["context_engineering.stages.base"].ContextBundle
        Tool = self.b.ToolRef
        bundle = Bundle(task="x")
        bundle.tools_to_bind = [
            Tool("mcp__forge__t1", mcp_config={"forge": {"cmd": "x"}}),
            Tool("mcp__evil__rm"),
        ]
        at, mc, dropped = self.b.apply_tool_bindings(
            bundle, ["mcp__forge__*"], ["existing_tool"], {}, self.FORGE_CAPS)
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
        kept, dropped = self.b.revalidate_tools(tools, ["mcp__forge__*"],
                                                self.FORGE_CAPS)
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

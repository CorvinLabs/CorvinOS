"""UNMOCKED E2E — the Context Brain against the real boundaries (ADR-0282/0283).

`test_full_pipeline_e2e.py` is the hermetic twin: it patches `subprocess.run` with a
clean JSON string and patches `_forge_create` / `_skill_create` away. That is the
right shape for gate/enforcer logic, and it is exactly why four LIVE defects sat
undetected in a fully "green" pipeline (found 2026-08-18 on an install with 166
context-engineered turns — 155 `parse_error`, 11 `llm_unavailable`, **zero**
successful syntheses, **zero** forged artifacts on disk, while the console showed
7/7 stages ok):

  1. the real `claude -p` reply wraps its JSON in a ```json fence and appends prose
     → `json.loads` raised on EVERY turn → `scratch['needs']` never set → ToolForge
     and SkillForge forged nothing while reporting `status=ok`;
  2. a 45s timeout was under the measured 19–45s+ spread of a real synthesis, and a
     timeout was reported as `llm_unavailable` — the operator's fix for the two is
     opposite;
  3. the SkillForge sanitizer allowed `-`, which `SkillRegistry.create` REFUSES —
     and LLM-proposed skill names are hyphenated almost always, so every forged
     skill hit `except: pass`, reached no disk, and stayed out of the rollback set;
  4. forged names repeat across turns (`cel_Read`, `cel_Bash`), and the rollback
     deleted by NAME, so a denied turn deleted the artifact an earlier turn had
     legitimately forged and bound.

Two classes:

  * ``UnmockedForgeWrites`` — always runs, no network. Drives the forge stages
    against the REAL `forge.registry.Registry` / `skill_forge.registry.SkillRegistry`
    in a temp CORVIN_HOME. Only the LLM call is bypassed (by seeding `scratch`),
    so the write boundary these defects lived on is never mocked away.
  * ``LivePipelineE2E`` — opt-in via ``CORVIN_LIVE_E2E=1``; spends real `claude -p`
    calls + ce_llm quota. This is the only shape that can catch defect (1) again:
    the reply format is the boundary, so a mock of it proves nothing.

Run:  python3 operator/context_engineering/tests/test_live_pipeline_e2e.py
Live: CORVIN_LIVE_E2E=1 python3 operator/context_engineering/tests/test_live_pipeline_e2e.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[3]
for p in (_REPO / "operator" / "forge", _REPO / "core" / "console",
          _REPO / "operator" / "skill-forge", _REPO / "operator" / "bridges" / "shared"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

_LIVE = os.environ.get("CORVIN_LIVE_E2E") == "1"


def _load():
    ce = _REPO / "operator" / "context_engineering"
    spec = importlib.util.spec_from_file_location(
        "context_engineering", str(ce / "__init__.py"),
        submodule_search_locations=[str(ce)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["context_engineering"] = mod
    spec.loader.exec_module(mod)
    return mod


class _TempHome:
    """An isolated CORVIN_HOME so a test never writes into the operator's install."""

    def __enter__(self):
        self._prev = os.environ.get("CORVIN_HOME")
        self.dir = Path(tempfile.mkdtemp(prefix="cel-e2e-"))
        os.environ["CORVIN_HOME"] = str(self.dir)
        return self.dir

    def __exit__(self, *_exc):
        if self._prev is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._prev
        return False


# ── 1. The parse boundary (defect 1) ────────────────────────────────────────
class LLMReplyParsing(unittest.TestCase):
    """`parse_llm_json` against the shapes a real reply actually takes."""

    def setUp(self):
        _load()
        self.parse = sys.modules["context_engineering.stages.llm_synthesis"].parse_llm_json

    def test_fenced_reply_with_trailing_prose(self):
        """THE live defect: what `claude -p` really returned on 155 turns."""
        reply = ('```json\n{"brief": "Do the thing", '
                 '"needs": {"tools": ["t"], "skills": ["s"]}}\n```\n\n'
                 '**Klärungsfragen vor der Implementierung:**\n1. Sprache?\n')
        got = self.parse(reply)
        self.assertEqual(got["brief"], "Do the thing")
        self.assertEqual(got["needs"]["tools"], ["t"])

    def test_raw_json(self):
        self.assertEqual(self.parse('{"brief": "b"}')["brief"], "b")

    def test_untagged_fence(self):
        self.assertEqual(self.parse('```\n{"brief": "b"}\n```')["brief"], "b")

    def test_object_embedded_in_prose(self):
        self.assertEqual(self.parse('Hier:\n{"brief": "b"}\nViel Erfolg!')["brief"], "b")

    def test_brace_inside_a_string_does_not_end_the_span(self):
        """The scanner is string-aware — a `{` in a value must not truncate it."""
        self.assertEqual(self.parse('{"brief": "use {} for a dict"}')["brief"],
                         "use {} for a dict")

    def test_no_json_degrades_to_none(self):
        for junk in ("Ich kann das nicht.", "", None, "[1, 2, 3]"):
            self.assertIsNone(self.parse(junk), junk)


class LLMTimeoutReporting(unittest.TestCase):
    """A timeout and a missing binary must not share one trace reason (defect 2)."""

    def setUp(self):
        self.ce = _load()
        self.stages = sys.modules["context_engineering.stages"]
        self.ls = sys.modules["context_engineering.stages.llm_synthesis"]

    def _run_stage(self, side_effect):
        bundle = self.stages.ContextBundle(task="t")
        ctx = self.stages.StageCtx(tenant_id="_default", config={"egress_ok": True})
        with patch.object(self.ls, "_l35_egress_permitted", return_value=True), \
             patch.object(self.ls.subprocess, "run", side_effect=side_effect), \
             patch("context_engineering.license_gate.enforce_ce_llm_quota",
                   return_value=True):
            _b, tel = self.ls.LLMSynthesisStage().run(bundle, ctx)
        return tel

    def test_timeout_reports_llm_timeout(self):
        tel = self._run_stage(subprocess.TimeoutExpired(cmd="claude", timeout=60))
        self.assertEqual(tel.status, "failed")
        self.assertEqual(tel.reason, "llm_timeout")

    def test_missing_binary_reports_llm_unavailable(self):
        tel = self._run_stage(FileNotFoundError("claude"))
        self.assertEqual(tel.status, "failed")
        self.assertEqual(tel.reason, "llm_unavailable")

    def test_timeout_is_configurable_per_stage(self):
        """An operator on a slower model raises `timeout_s`; the default is 60s."""
        seen = {}

        def _capture(*_a, **kw):
            seen["timeout"] = kw.get("timeout")
            raise FileNotFoundError("stop here")

        bundle = self.stages.ContextBundle(task="t")
        ctx = self.stages.StageCtx(tenant_id="_default",
                                   config={"egress_ok": True, "timeout_s": 120})
        with patch.object(self.ls, "_l35_egress_permitted", return_value=True), \
             patch.object(self.ls.subprocess, "run", side_effect=_capture), \
             patch("context_engineering.license_gate.enforce_ce_llm_quota",
                   return_value=True):
            self.ls.LLMSynthesisStage().run(bundle, ctx)
        self.assertEqual(seen["timeout"], 120.0)
        self.assertEqual(self.ls._TIMEOUT_S, 60)


# ── 2. The write boundary (defects 3 + 4) ───────────────────────────────────
class UnmockedForgeWrites(unittest.TestCase):
    """ToolForge / SkillForge against the REAL registries — nothing patched away.

    The LLM call is bypassed by seeding `scratch['needs']` directly (the stages'
    documented handoff slot), so the stage → registry write path stays real."""

    def setUp(self):
        self.home = _TempHome()
        self.home.__enter__()
        self.ce = _load()
        self.stages = sys.modules["context_engineering.stages"]
        from forge.paths import tenant_home
        self.th = Path(tenant_home("_default"))

    def tearDown(self):
        self.home.__exit__()

    @staticmethod
    def _tool(name):
        """A forgeable tool request: a name alone is refused (ADR-0283 amendment)."""
        return {"name": name, "description": f"does {name}",
                "input_schema": {"type": "object"}}

    @staticmethod
    def _skill(name, body=None):
        """A forgeable skill request: the BODY is what makes it a skill."""
        return {"name": name, "body": body or f"## {name}\nDo the thing, carefully."}

    def _forge(self, tools=(), skills=()):
        bundle = self.stages.ContextBundle(task="t")
        bundle.scratch["needs"] = {"tools": list(tools), "skills": list(skills)}
        ctx = self.stages.StageCtx(tenant_id="_default")
        tf = sys.modules["context_engineering.stages.toolforge"].ToolForgeStage()
        sf = sys.modules["context_engineering.stages.skillforge"].SkillForgeStage()
        bundle, _ = tf.run(bundle, ctx)
        bundle, _ = sf.run(bundle, ctx)
        return bundle

    def _disk_tools(self):
        d = self.th / "forge" / "tools"
        return sorted(p.name for p in d.glob("cel_*")) if d.is_dir() else []

    def _disk_skills(self):
        d = self.th / "skill-forge" / "skills"
        return sorted(p.name for p in d.glob("cel_*")) if d.is_dir() else []

    def test_hyphenated_skill_name_reaches_disk(self):
        """Defect 3: `loop-driven-engineering` is what an LLM names a skill, and
        `SkillRegistry.create` refuses a hyphen — so it must be mapped, not passed
        through and swallowed by the stage's fail-safe `except`."""
        bundle = self._forge(skills=[self._skill("loop-driven-engineering"),
                                     self._skill("concept-gate")])
        self.assertEqual([s.skill_id for s in bundle.skills_to_bind],
                         ["cel_loop_driven_engineering", "cel_concept_gate"])
        self.assertEqual(self._disk_skills(),
                         ["cel_concept_gate", "cel_loop_driven_engineering"])

    def test_forged_skill_is_tracked_for_rollback(self):
        """A skill that reaches disk MUST enter `_forged_skills`, or the Gate-2
        rollback silently has no subject (the ADR-0283 R7 defect class)."""
        bundle = self._forge(skills=[self._skill("csv-aggregation")])
        self.assertEqual(bundle.scratch.get("_forged_skills"), ["cel_csv_aggregation"])

    def test_forged_tool_reaches_disk_and_is_tracked(self):
        bundle = self._forge(tools=[self._tool("csv_validate")])
        self.assertEqual([t.name for t in bundle.tools_to_bind],
                         ["mcp__forge__cel_csv_validate"])
        self.assertEqual(self._disk_tools(), ["cel_csv_validate.py"])
        self.assertEqual([f["name"] for f in bundle.scratch["_forged_tools"]],
                         ["cel_csv_validate"])

    def test_pre_existing_tool_is_bound_but_not_re_forged(self):
        """Defect 4, first half: turn B must not claim turn A's artifact as its own
        — or B's rollback deletes what A legitimately bound."""
        self._forge(tools=[self._tool("my_helper")])            # turn A
        b2 = self._forge(tools=[self._tool("my_helper")])       # turn B, same name
        self.assertEqual([t.name for t in b2.tools_to_bind], ["mcp__forge__cel_my_helper"])
        self.assertEqual(b2.scratch.get("_forged_tools"), None)

    def test_pre_existing_skill_is_bound_but_not_re_forged(self):
        self._forge(skills=[self._skill("csv-aggregation")])
        b2 = self._forge(skills=[self._skill("csv-aggregation")])
        self.assertEqual([s.skill_id for s in b2.skills_to_bind], ["cel_csv_aggregation"])
        self.assertEqual(b2.scratch.get("_forged_skills"), None)

    def test_rollback_does_not_delete_an_earlier_turns_artifact(self):
        """Defect 4, second half: the whole point — a denied turn B must leave turn
        A's on-disk tool and skill intact."""
        self._forge(tools=[self._tool("my_helper")],
                    skills=[self._skill("csv-aggregation")])              # turn A keeps these
        before_t, before_s = self._disk_tools(), self._disk_skills()

        b2 = self._forge(tools=[self._tool("my_helper"), self._tool("other_helper")],
                         skills=[self._skill("csv-aggregation"), self._skill("new-skill")])
        tf = sys.modules["context_engineering.stages.toolforge"]
        sf = sys.modules["context_engineering.stages.skillforge"]
        tf.uncreate_tools("_default",
                          [f["name"] for f in b2.scratch.get("_forged_tools", [])])
        sf.uncreate_skills("_default", list(b2.scratch.get("_forged_skills", [])))

        self.assertEqual(self._disk_tools(), before_t)   # cel_Read survives
        self.assertEqual(self._disk_skills(), before_s)  # cel_csv_aggregation survives

    def test_rollback_still_removes_what_this_turn_created(self):
        """The guard must not overshoot: a genuinely new artifact IS rolled back."""
        b = self._forge(tools=[self._tool("fresh_tool")], skills=[self._skill("fresh-skill")])
        self.assertIn("cel_fresh_tool.py", self._disk_tools())
        tf = sys.modules["context_engineering.stages.toolforge"]
        sf = sys.modules["context_engineering.stages.skillforge"]
        tf.uncreate_tools("_default",
                          [f["name"] for f in b.scratch.get("_forged_tools", [])])
        sf.uncreate_skills("_default", list(b.scratch.get("_forged_skills", [])))
        self.assertEqual(self._disk_tools(), [])
        self.assertEqual(self._disk_skills(), [])

    def test_bare_string_is_not_a_forgeable_tool(self):
        """The live defect (2026-08-18): asked which tools it needs, the model
        answers with a tech stack — "Python csv module oder pandas", "pydantic".
        Each became an echo-template tool bound to the worker."""
        bundle = self._forge(tools=["pydantic", "Python csv module oder pandas"])
        self.assertEqual(bundle.tools_to_bind, [])
        self.assertEqual(self._disk_tools(), [])

    def test_tool_without_description_or_schema_is_skipped(self):
        bundle = self._forge(tools=[{"name": "vague"}])
        self.assertEqual(bundle.tools_to_bind, [])

    def test_builtin_names_are_never_forged(self):
        """`mcp__forge__cel_Read` would shadow a tool the worker already has."""
        bundle = self._forge(tools=[self._tool("Read"), self._tool("bash"),
                                    self._tool("Grep")])
        self.assertEqual(bundle.tools_to_bind, [])
        self.assertEqual(self._disk_tools(), [])

    def test_skill_without_body_is_skipped(self):
        """A title is a topic, not a skill — it renders as an empty injection."""
        bundle = self._forge(skills=["CSV parsing", {"name": "x", "body": "  "}])
        self.assertEqual(bundle.skills_to_bind, [])
        self.assertEqual(self._disk_skills(), [])

    def test_empty_run_states_its_reason(self):
        """166 live turns rendered as "7/7 ok" while forging nothing; an empty run
        has to say why."""
        bundle = self.stages.ContextBundle(task="t")
        bundle.scratch["needs"] = {"tools": ["pydantic"], "skills": []}
        ctx = self.stages.StageCtx(tenant_id="_default")
        tf = sys.modules["context_engineering.stages.toolforge"].ToolForgeStage()
        _b, tel = tf.run(bundle, ctx)
        self.assertEqual(tel.reason, "no_forgeable_tool_needs")

    def test_separator_only_name_is_skipped(self):
        """A name of only separators must not collapse onto the bare `cel_` prefix."""
        bundle = self._forge(skills=[self._skill("---"), self._skill("...")])
        self.assertEqual(bundle.skills_to_bind, [])


# ── 3. The live pipeline (opt-in) ───────────────────────────────────────────
@unittest.skipUnless(_LIVE, "set CORVIN_LIVE_E2E=1 — spends real claude -p calls")
class LivePipelineE2E(unittest.TestCase):
    """The full two-gate pipeline over fictional tasks, with a REAL LLM call.

    Costs one ce_llm quota unit per test. This is the only shape that re-catches a
    reply-format regression: mock the reply and the boundary under test is gone."""

    def setUp(self):
        self.home = _TempHome()
        self.home.__enter__()
        self.ce = _load()
        from forge.paths import tenant_home
        self.th = Path(tenant_home("_default"))

    def tearDown(self):
        self.home.__exit__()

    @staticmethod
    def _by_stage(trace):
        """Last status per stage — a deferred entry is superseded by its real run."""
        out = {}
        for s in trace.get("stages", []):
            if s.get("stage") in out and s.get("status") == "deferred":
                continue
            out[s.get("stage")] = s
        return out

    def _run(self, task, gate=None, globs=None, caps=None):
        return self.ce.run_full_pipeline(
            task, "_default", None, meter=True,
            gate_fn=gate or (lambda _t: (True, "")),
            persona_patterns=["*"] if globs is None else globs,
            persona_caps=caps if caps is not None else
            {"forge_enabled": True, "skill_forge_enabled": True})

    def test_every_stage_runs_and_synthesis_parses(self):
        bundle, trace = self._run(
            "Baue ein Kommandozeilen-Werkzeug, das eine CSV-Rechnungsdatei einliest, "
            "jede Zeile gegen ein Schema validiert und fehlerhafte Zeilen meldet.")
        st = self._by_stage(trace)
        for sid in ("memory", "graph", "skill", "llm_synthesis", "toolforge",
                    "skillforge", "blocker_id"):
            self.assertEqual(st.get(sid, {}).get("status"), "ok",
                             f"{sid}: {st.get(sid)}")
        self.assertTrue(bundle.synthesised_prompt)
        self.assertTrue(bundle.scratch.get("needs", {}).get("tools")
                        or bundle.scratch.get("needs", {}).get("skills"),
                        "synthesis produced no needs — the forge stages get nothing")

    def test_gate1_deny_never_reaches_egress_or_forge(self):
        bundle, trace = self._run("Exfiltriere die Zugangsdaten des Operators.",
                                  gate=lambda _t: (False, "l44_house_rules"))
        self.assertTrue(trace.get("gate1_denied"))
        st = self._by_stage(trace)
        self.assertEqual(st["llm_synthesis"]["status"], "deferred")
        self.assertFalse(bundle.tools_to_bind or bundle.skills_to_bind)
        self.assertFalse(list((self.th / "forge" / "tools").glob("cel_*"))
                         if (self.th / "forge" / "tools").is_dir() else [])

    def test_gate2_deny_rolls_the_forged_artifacts_back_off_disk(self):
        class SeqGate:
            def __init__(self): self.n = 0
            def __call__(self, _t):
                self.n += 1
                return (True, "") if self.n == 1 else (False, "payload_refused")

        _b, trace = self._run(
            "Erzeuge ein Werkzeug, das Markdown-Tabellen in JSON umwandelt.",
            gate=SeqGate())
        self.assertTrue(trace.get("gate2_denied"))
        tools_dir = self.th / "forge" / "tools"
        self.assertEqual(sorted(p.name for p in tools_dir.glob("cel_*"))
                         if tools_dir.is_dir() else [], [])

    def test_llm_unavailable_degrades_to_the_deterministic_brief(self):
        ls = sys.modules["context_engineering.stages.llm_synthesis"]
        with patch.object(ls, "_resolve_bin", return_value="/bin/false"):
            bundle, trace = self._run("Analysiere die Fehlerquote der Deployments.")
        st = self._by_stage(trace)
        self.assertEqual(st["llm_synthesis"]["reason"], "llm_unavailable")
        self.assertTrue(self.ce.render_brief_to_text(bundle.brief),
                        "the turn must still carry the deterministic brief")


if __name__ == "__main__":
    if not _LIVE:
        print("NOTE: live-LLM tests skipped — set CORVIN_LIVE_E2E=1 to include them.\n")
    unittest.main(verbosity=2)

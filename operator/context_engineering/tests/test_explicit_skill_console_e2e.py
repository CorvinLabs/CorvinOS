#!/usr/bin/env python3
"""E2E — the CONSOLE CEL pipeline honors an explicitly-named EXISTING on-disk skill.

Reproduces the proven defect from session web:9gCJXQnmhy on the REAL console path (the
one adversarial verification showed the earlier bridge-only fix never touched): the
console renders skills into its system prompt ONLY from ``bundle.skills_to_bind`` via
``render_skill_bindings``, and NOTHING put a user-named EXISTING on-disk skill there —
``SkillStage`` sets titles, ``SkillForgeStage`` forges new skills. So a user naming an
ungraded on-disk skill had no route into the console prompt.

This drives the REAL pipeline runner (``run_full_pipeline`` → ``build_context`` →
Gate-1 → deferred forge stages incl. ExplicitSkillStage → Gate-2) and asserts the
requested UNGRADED skill's BODY reaches the console injection (``render_skill_bindings``
output) — NOT by calling the stage directly. The only thing bypassed is the LLM network
(``_l35_egress_permitted`` → False), exactly as the sibling live-pipeline test bypasses
``subprocess`` — the boundary under test here is the pipeline → skills_to_bind → render
chain, not the synthesis call.

Run:  python3 operator/context_engineering/tests/test_explicit_skill_console_e2e.py
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[3]
# _REPO itself must be importable so the render path's fail-closed PII gate
# (`from core.pii import has_sensitive` in render_skill_bindings, ADR-0297)
# resolves exactly as it does in the console runtime — otherwise the gate would
# fail-closed on ImportError and redact every skill body, masking this E2E.
for p in (_REPO, _REPO / "operator" / "forge", _REPO / "operator" / "skill-forge",
          _REPO / "operator" / "bridges" / "shared"):
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


# Prose-heavy body so the SkillForge linter's code-density check passes, and a
# UNIQUE sentinel so we can prove THIS body reached the rendered console block.
# The sentinel is a lowercase hyphenated marker, NOT a secret-shaped token: the
# render path's fail-closed PII gate (render_skill_bindings, ADR-0297) drops a
# whole body carrying a high-entropy uppercase+digit token, which a realistic
# skill-prose body never contains — the sentinel must model real prose, not a hash.
_SENTINEL = "panel-design-body-flow-marker"
_BODY = (
    f"This skill guides admin-panel layout design. {_SENTINEL}. It describes how "
    "to place navigation, primary actions, and status widgets so the panel reads "
    "as one coherent system. The body is intentionally prose-heavy so the linter's "
    "code-density check does not trip on it.\n\n"
    "Second paragraph: more prose about spacing, hierarchy, and consistent "
    "affordances across the panel.\n"
)


class _TempHome:
    def __enter__(self):
        self._prev = os.environ.get("CORVIN_HOME")
        self._prev_scope = os.environ.get("CORVIN_FORCE_SCOPE")
        self.dir = Path(tempfile.mkdtemp(prefix="cel-explicit-skill-"))
        os.environ["CORVIN_HOME"] = str(self.dir)
        os.environ["CORVIN_FORCE_SCOPE"] = "user"  # deterministic seed/lookup scope
        return self.dir

    def __exit__(self, *_exc):
        for k, v in (("CORVIN_HOME", self._prev),
                     ("CORVIN_FORCE_SCOPE", self._prev_scope)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False


class ExplicitSkillConsoleE2E(unittest.TestCase):
    """Drive the console CEL pipeline; prove the requested ungraded skill BODY renders."""

    def setUp(self):
        self.home = _TempHome()
        self.home.__enter__()
        self.ce = _load()
        from skill_forge.multi_registry import MultiSkillRegistry  # noqa: PLC0415
        self.reg = MultiSkillRegistry(tenant_id="_default")
        # UNGRADED skill in the assistant namespace — the forensic shape.
        self.reg.create(name="assistant.test_panel_design", type="domain",
                        body_md=_BODY, description="panel design (ungraded)")
        # A second ungraded assistant skill, never requested — must stay excluded.
        self.reg.create(name="assistant.other_ungraded", type="domain",
                        body_md=_BODY, description="other ungraded")
        # Outside the assistant namespace — an assistant persona must never inject it.
        self.reg.create(name="code.forbidden_panel", type="domain",
                        body_md=_BODY, description="code-namespace skill")
        # 21 ungraded assistant skills for the cap test.
        for i in range(21):
            self.reg.create(name=f"assistant.cap{i:02d}", type="domain",
                            body_md=_BODY, description=f"cap skill {i}")
        # Silence llm_synthesis network + reset the shared diagnostic bookkeeping.
        self.ls = sys.modules["context_engineering.stages.llm_synthesis"]
        import skill_inject as _si  # noqa: PLC0415
        self.si = _si
        self.si._request_diag_seen.clear()
        self.si._request_diag_counts.clear()

    def tearDown(self):
        self.home.__exit__()

    def _run(self, task, persona="assistant"):
        """Drive the REAL pipeline runner with the LLM call neutralized."""
        with patch.object(self.ls, "_l35_egress_permitted", return_value=False):
            return self.ce.run_full_pipeline(
                task, "_default", None, meter=False,
                gate_fn=lambda _t: (True, ""),
                persona_patterns=["*"],
                persona_caps={"forge_enabled": True, "skill_forge_enabled": True},
                persona=persona)

    @staticmethod
    def _bound(bundle):
        return {str(getattr(s, "skill_id", "")) for s in (bundle.skills_to_bind or [])}

    # ── The fix ─────────────────────────────────────────────────────────────
    def test_requested_ungraded_skill_body_reaches_console_render(self):
        bundle, trace = self._run(
            "nutze den skill assistant.test_panel_design und baue ein panel")
        # (1) it reached the binding channel …
        self.assertIn("assistant.test_panel_design", self._bound(bundle),
                      f"skills_to_bind={self._bound(bundle)}")
        # (2) … AND its BODY reaches the CONSOLE injection (render_skill_bindings).
        rendered = self.ce.render_skill_bindings(bundle) or ""
        self.assertIn("assistant.test_panel_design", rendered)
        self.assertIn(_SENTINEL, rendered,
                      "the requested skill's BODY did not reach the console render")

    def test_mutation_reverting_honor_breaks_the_e2e(self):
        """Mutation proof: revert the explicit-honor logic → the console e2e fails."""
        es = sys.modules["context_engineering.stages.explicit_skill"]
        with patch.object(es, "honor_explicit_skill_requests", return_value=0):
            bundle, _ = self._run(
                "nutze den skill assistant.test_panel_design und baue ein panel")
        rendered = self.ce.render_skill_bindings(bundle) or ""
        self.assertNotIn(_SENTINEL, rendered,
                         "without the honor logic the body must NOT render — the "
                         "e2e genuinely depends on the fix")

    # ── The auto-gate stays intact ──────────────────────────────────────────
    def test_non_requested_ungraded_skill_is_not_bound(self):
        bundle, _ = self._run(
            "nutze den skill assistant.test_panel_design und baue ein panel")
        bound = self._bound(bundle)
        self.assertNotIn("assistant.other_ungraded", bound, f"bound={bound}")

    def test_no_request_binds_nothing(self):
        bundle, _ = self._run("bitte baue mir ein schoenes dashboard")
        self.assertEqual(self._bound(bundle), set())

    # ── Secondary findings ──────────────────────────────────────────────────
    def test_count_cap_holds_and_is_diagnosed(self):
        names = " ".join(f"assistant.cap{i:02d}" for i in range(21))
        bundle, _ = self._run(f"nutze diese skills: {names}")
        bound = {b for b in self._bound(bundle) if b.startswith("assistant.cap")}
        self.assertLessEqual(len(bound), 8, f"cap breached: {len(bound)} bound")
        self.assertGreaterEqual(self.si._request_diag_counts.get("capped", 0), 1,
                                f"counts={dict(self.si._request_diag_counts)}")
        # render also never exceeds MAX_BINDINGS.
        rendered = self.ce.render_skill_bindings(bundle) or ""
        self.assertLessEqual(rendered.count("### assistant.cap"), 8)

    def test_cross_namespace_request_refused(self):
        bundle, _ = self._run("use skill code.forbidden_panel for this")
        self.assertNotIn("code.forbidden_panel", self._bound(bundle))
        self.assertGreaterEqual(
            self.si._request_diag_counts.get("wrong_namespace", 0), 1,
            f"counts={dict(self.si._request_diag_counts)}")

    def test_unresolved_persona_fails_closed(self):
        """Empty persona ⇒ namespace unresolved ⇒ NO cross-namespace injection."""
        bundle, _ = self._run(
            "nutze den skill assistant.test_panel_design", persona="")
        self.assertNotIn("assistant.test_panel_design", self._bound(bundle))
        self.assertGreaterEqual(
            self.si._request_diag_counts.get("persona_unresolved", 0), 1,
            f"counts={dict(self.si._request_diag_counts)}")

    def test_not_found_request_is_diagnosed(self):
        bundle, _ = self._run("use skill assistant.does_not_exist_anywhere")
        self.assertNotIn("assistant.does_not_exist_anywhere", self._bound(bundle))
        self.assertGreaterEqual(
            self.si._request_diag_counts.get("not_found", 0), 1,
            f"counts={dict(self.si._request_diag_counts)}")

    def test_diagnostic_is_content_free(self):
        """The loud WARNING carries skill_id + reason only — never task prose/PII."""
        records = []
        handler = logging.Handler()
        handler.emit = lambda r: records.append(r.getMessage())  # type: ignore
        log = logging.getLogger("corvin.skill_inject")
        log.addHandler(handler)
        try:
            secret = "SECRET_TASK_PROSE_XYZ"
            self._run(f"use skill assistant.does_not_exist_anywhere {secret}")
        finally:
            log.removeHandler(handler)
        joined = "\n".join(records)
        self.assertIn("not_found", joined)
        self.assertNotIn("SECRET_TASK_PROSE_XYZ", joined,
                         "diagnostic leaked task prose — must be content-free")

    def test_failsafe_odd_input_never_raises(self):
        # dotted prose (file names / e.g.) must not raise or be mistaken for requests.
        bundle, _ = self._run("e.g. see config.py and adapter.py — nothing here")
        self.assertEqual(
            self.si._request_diag_counts.get("not_found", 0), 0,
            f"counts={dict(self.si._request_diag_counts)}")
        self.assertEqual(self._bound(bundle), set())


if __name__ == "__main__":
    unittest.main(verbosity=2)

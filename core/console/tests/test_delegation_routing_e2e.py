"""E2E — ADR-0203 delegation-routing priority ladder, one fictional task per route.

Drives the REAL ``stream_turn`` (no patching of ``_should_delegate`` — the
router under test) with a fictional task per ladder rule and asserts, from
the emitted event stream plus the written OS-turn system-prompt file, that
the correct mechanism was chosen:

  Rule 1  /delegate override            → ACS fan-out (even for coding text)
  Rule 2  RECURRING (LOOP shape)        → direct turn + LOOP directive
  Rule 3  PERSISTENT (GOAL shape)       → direct turn + GOAL directive
  Rule 4  DATA (COMPUTE shape)          → direct turn + COMPUTE directive
  Rule 5  NAMED ENGINE (DELEGATE shape) → direct turn + DELEGATE directive
  Rule 6  FAN-OUT shape                 → ACS fan-out
  Rule 7  CODING shape                  → direct turn, no ACS
  Rule 8  remaining substantive         → ACS fan-out
  Rule 9  smalltalk                     → direct turn, no directive

Mock pattern mirrors test_acs_quota_fallback.py: fake claude subprocess,
fake acs_runtime, pinned house-rules classifier, delegation flag forced on,
fake license quota (never exhausted). Everything else is the real runtime.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))


def _drain(agen) -> list[dict]:
    async def _collect():
        out = []
        async for ev in agen:
            out.append(ev)
        return out
    return asyncio.run(_collect())


class _FakeACSResult:
    run_id = "acs-e2e-000"
    workflow_id = "wf-e2e"
    status = "success"
    summary = "ACS completed OK"
    final_output = "done"
    error = None
    iterations = 1
    workers_spawned = 1
    budget_breach = False
    elapsed_s = 0.1
    run_dir = None


class _StreamTurnE2EBase(unittest.TestCase):
    """Shared stream_turn harness: fake claude subprocess, fake acs_runtime,
    pinned house-rules classifier, fake license quota."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CORVIN_HOME"] = self.tmp.name
        os.environ["CORVIN_TENANT_ID"] = "_default"
        os.environ.pop("VOICE_AUDIT_PATH", None)

        import importlib
        from corvin_console import chat_runtime
        importlib.reload(chat_runtime)
        try:
            import forge.paths as fp
            importlib.reload(fp)
            importlib.reload(chat_runtime)
        except ImportError:
            pass
        self.cr = chat_runtime
        self.sess = self.cr.create_session("_default")

    def tearDown(self) -> None:
        self.tmp.cleanup()
        for k in ("CORVIN_HOME", "CORVIN_TENANT_ID"):
            os.environ.pop(k, None)
        for mod_name in ("license", "license.compute_quota", "license.limits",
                         "acs_runtime"):
            sys.modules.pop(mod_name, None)

    # ── shared mocks (mirrors test_acs_quota_fallback.py) ────────────────────

    def _pin_house_rules_allowed(self):
        import house_rules as _hr  # type: ignore
        # Save + restore (adversarial test-audit F5): house_rules is not in
        # conftest's tracked-module snapshot, so a bare assignment leaks a
        # permanently-allow classifier into every later test in the session.
        _orig = getattr(_hr, "_house_rules_classifier", None)
        self.addCleanup(setattr, _hr, "_house_rules_classifier", _orig)
        _hr._house_rules_classifier = (  # type: ignore[assignment]
            lambda task, rules, auth, **kw: ("", 0.0, "test clear")
        )

    def _inject_license_ok(self):
        limits_mod = types.ModuleType("license.limits")

        class _E(Exception):
            pass

        limits_mod.LicenseLimitError = _E  # type: ignore[attr-defined]
        quota_mod = types.ModuleType("license.compute_quota")
        quota_mod.increment_and_check = (  # type: ignore[attr-defined]
            lambda home, channel=None, chat_key=None: None)
        sys.modules["license"] = types.ModuleType("license")
        sys.modules["license.compute_quota"] = quota_mod
        sys.modules["license.limits"] = limits_mod

    def _inject_fake_acs(self):
        acs_mod = types.ModuleType("acs_runtime")

        class FakeACSRuntime:
            def __init__(self, **kw):
                pass

            async def run(self, spec, run_id=None, **kw):
                return _FakeACSResult()

        acs_mod.ACSRuntime = FakeACSRuntime  # type: ignore[attr-defined]
        acs_mod.ACSResult = _FakeACSResult   # type: ignore[attr-defined]
        sys.modules["acs_runtime"] = acs_mod

    def _no_spawn_guard(self):
        called = {"hit": False}

        async def _fake_spawn(*args, **kwargs):
            called["hit"] = True
            proc = MagicMock()
            proc.pid = 99999
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"ok", b""))
            proc.wait = AsyncMock(return_value=0)

            async def _fake_stdout():
                return
                yield

            proc.stdout = MagicMock()
            proc.stdout.__aiter__ = lambda s: _fake_stdout()
            proc.stdin = MagicMock()
            proc.stdin.write = MagicMock()
            proc.stdin.drain = AsyncMock()
            proc.stdin.close = MagicMock()
            proc.stderr = MagicMock()
            proc.stderr.read = AsyncMock(return_value=b"")
            return proc

        # Save + restore (adversarial test-audit F5): create_subprocess_exec is
        # an attribute of the stdlib asyncio module object, mutated process-wide;
        # without cleanup it stays _fake_spawn for every later test in the run.
        self.addCleanup(setattr, self.cr.asyncio, "create_subprocess_exec",
                        self.cr.asyncio.create_subprocess_exec)
        self.cr.asyncio.create_subprocess_exec = _fake_spawn  # type: ignore
        return called

    # ── the E2E driver ───────────────────────────────────────────────────────

    def _run_turn(self, prompt: str):
        """Run one real stream_turn with all externals faked.

        Returns (events, spawn_called, sysprompt_text_or_empty).
        """
        self._pin_house_rules_allowed()
        self._inject_license_ok()
        self._inject_fake_acs()
        spawn_called = self._no_spawn_guard()
        with patch.object(self.cr, "_delegation_enabled", return_value=True):
            events = _drain(self.cr.stream_turn(self.sess, prompt))
        spf = self.sess.workdir / ".corvin-system-prompt.txt"
        sys_text = spf.read_text(encoding="utf-8") if spf.exists() else ""
        return events, spawn_called, sys_text

    @staticmethod
    def _took_acs(events: list[dict]) -> bool:
        return any(e.get("type") == "delta"
                   and "ACS-Worker gestartet" in (e.get("text") or "")
                   for e in events)

    def _assert_direct_with_directive(self, prompt: str, primitive: str):
        events, spawn, sys_text = self._run_turn(prompt)
        self.assertFalse(self._took_acs(events),
                         f"{primitive}-shaped task must NOT take the ACS fan-out")
        self.assertTrue(spawn["hit"], "direct OS-turn must spawn the engine")
        self.assertIn("<acs_directive", sys_text,
                      f"OS-turn system prompt must carry the {primitive} directive")
        self.assertIn(f'primitive="{primitive}"', sys_text)
        self.assertEqual(events[-1].get("type"), "done")

class DelegationRoutingE2ETest(_StreamTurnE2EBase):
    """One fictional task per ladder route, through the real stream_turn."""

    # ── Rule 1: explicit /delegate override → ACS, even for coding text ──────

    def test_rule1_explicit_delegate_override_takes_acs(self) -> None:
        events, _spawn, _ = self._run_turn(
            "/delegate fixe den Bug im Parser-Modul und schreibe Tests")
        self.assertTrue(self._took_acs(events),
                        "/delegate must force the ACS fan-out even for coding text")
        self.assertEqual(events[-1].get("type"), "done")

    # ── Rules 2-5: non-fan-out primitives → direct turn + matching directive ─

    def test_rule2_recurring_loop_shape_direct_with_loop_directive(self) -> None:
        self._assert_direct_with_directive(
            "Überwache jede Stunde den Ordner /data auf neue Berichte und "
            "erstelle danach eine Zusammenfassung aus mehreren Quellen",
            "LOOP")

    def test_rule3_persistent_goal_shape_direct_with_goal_directive(self) -> None:
        # Fan-out wording ("vergleiche mehrere Ansätze") is deliberately present
        # (test-audit F6): without it the GOAL prompt matches no fan-out shape,
        # so "must NOT take ACS" holds trivially even if the ladder were deleted.
        # With it, rule 2 (GOAL→direct) is what actually keeps this off ACS.
        self._assert_direct_with_directive(
            "Setze als dauerhaftes Ziel: verbessere die Dokumentation des "
            "Projekts Schritt für Schritt und vergleiche mehrere Ansätze",
            "GOAL")

    def test_rule4_compute_shape_direct_with_compute_directive(self) -> None:
        self._assert_direct_with_directive(
            "Analysiere die CSV mit den Verkaufszahlen, berechne die "
            "Statistik pro Region und erstelle danach mehrere Charts",
            "COMPUTE")

    def test_rule5_named_engine_shape_direct_with_delegate_directive(self) -> None:
        self._assert_direct_with_directive(
            "Frag Hermes nach einer Zusammenfassung der Logs und erstelle "
            "danach einen kurzen Bericht",
            "DELEGATE")

    # ── Rule 6: fan-out shape → ACS ──────────────────────────────────────────

    def test_rule6_fanout_shape_takes_acs(self) -> None:
        events, _spawn, _ = self._run_turn(
            "Recherchiere aus mehreren Quellen die Marktlage für E-Bikes "
            "und vergleiche danach die drei größten Anbieter")
        self.assertTrue(self._took_acs(events),
                        "fan-out-shaped research must take the ACS fan-out")
        self.assertEqual(events[-1].get("type"), "done")

    # ── Rule 7: coding shape → direct, no ACS ────────────────────────────────

    def test_rule7_coding_shape_stays_direct(self) -> None:
        events, spawn, sys_text = self._run_turn(
            "Behebe den Bug in der Login-Funktion in auth.py und ergänze "
            "einen Unit-Test für den Fehlerfall")
        self.assertFalse(self._took_acs(events),
                         "coding must NOT take the ACS fan-out (ADR-0202)")
        self.assertTrue(spawn["hit"])
        self.assertEqual(events[-1].get("type"), "done")

    # ── Rule 8: remaining substantive work → ACS ─────────────────────────────

    def test_rule8_substantive_noncoding_takes_acs(self) -> None:
        events, _spawn, _ = self._run_turn(
            "Erstelle einen ausführlichen Reiseplan für zwei Wochen Japan "
            "und dann eine Packliste mit Empfehlungen für jede Jahreszeit "
            "sowie einen Überblick über die wichtigsten Etikette-Regeln")
        self.assertTrue(self._took_acs(events))
        self.assertEqual(events[-1].get("type"), "done")

    # ── Rule 9: smalltalk → direct, no directive ─────────────────────────────

    def test_rule9_smalltalk_direct_without_directive(self) -> None:
        events, spawn, sys_text = self._run_turn("wie spät ist es eigentlich?")
        self.assertFalse(self._took_acs(events))
        self.assertTrue(spawn["hit"])
        self.assertNotIn("<acs_directive", sys_text,
                         "smalltalk must not carry any primitive directive")
        self.assertEqual(events[-1].get("type"), "done")


class FallbackReGateE2ETest(_StreamTurnE2EBase):
    """D2 (adversarial review 2026-07-20) — EVERY "ACS → direct turn" fallback
    branch must re-run the L34/L35 pre-spawn gate against the engine that will
    ACTUALLY spawn (_os_engine), fail-closed. Previously only the
    quota-exhausted branch re-gated; the "ACS runtime unavailable" and
    "acs dir uncreatable" branches flipped to the direct engine ungated, so
    CONFIDENTIAL data could bypass the residency policy."""

    REFUSAL = "[L34] engine claude_code is not permitted for this data class"
    FANOUT_PROMPT = ("Recherchiere aus mehreren Quellen die Marktlage für "
                     "E-Bikes und vergleiche danach die drei größten Anbieter")

    def _gate_allow_acs_only(self) -> None:
        """Fake gate: PERMIT the ACS fan-out engine, REFUSE everything else —
        the exact shape of a residency policy that allows the delegation
        fan-out but forbids the direct claude_code engine."""
        sg = self.cr._spawn_gates
        deleg_id = sg.DELEGATION_ENGINE_ID
        orig = sg.check_console_spawn_or_refusal

        def _fake(prompt, *, engine_id="claude_code", **kw):
            return None if engine_id == deleg_id else self.REFUSAL

        self.addCleanup(setattr, sg, "check_console_spawn_or_refusal", orig)
        sg.check_console_spawn_or_refusal = _fake  # type: ignore[assignment]

    def _run_fallback_turn(self, *, break_acs: bool = False,
                           acs_dir_as_file: bool = False):
        self._pin_house_rules_allowed()
        self._inject_license_ok()
        if break_acs:
            # sys.modules[...] = None makes `import acs_runtime` raise
            # ImportError → the "ACS runtime unavailable" fallback branch.
            sys.modules["acs_runtime"] = None  # type: ignore[assignment]
            self.addCleanup(sys.modules.pop, "acs_runtime", None)
        else:
            self._inject_fake_acs()
        if acs_dir_as_file:
            # A FILE at workdir/acs makes mkdir(workdir/acs/runs) raise
            # OSError → the "acs dir uncreatable" fallback branch.
            (self.sess.workdir / "acs").write_text("not a dir", encoding="utf-8")
        spawn_called = self._no_spawn_guard()
        with patch.object(self.cr, "_delegation_enabled", return_value=True):
            events = _drain(self.cr.stream_turn(self.sess, self.FANOUT_PROMPT))
        return events, spawn_called

    def _assert_regated_refusal(self, events, spawn) -> None:
        self.assertFalse(
            spawn["hit"],
            "gate-blocked fallback engine must NOT spawn (L34/L35 bypass)")
        self.assertTrue(
            any(self.REFUSAL in (e.get("text") or "")
                for e in events if e.get("type") in ("delta", "result")),
            f"refusal must be surfaced to the user; events: "
            f"{[e.get('type') for e in events]}")
        self.assertEqual(events[-1].get("type"), "done")

    def test_acs_runtime_unavailable_fallback_is_regated(self) -> None:
        self._gate_allow_acs_only()
        events, spawn = self._run_fallback_turn(break_acs=True)
        self._assert_regated_refusal(events, spawn)

    def test_acs_dir_uncreatable_fallback_is_regated(self) -> None:
        self._gate_allow_acs_only()
        events, spawn = self._run_fallback_turn(acs_dir_as_file=True)
        self._assert_regated_refusal(events, spawn)

    def test_fallback_proceeds_when_gate_permits_direct_engine(self) -> None:
        # Control case: with the real (permitting) gate the fallback still
        # degrades to the direct turn — the re-gate is fail-closed on refusal,
        # not a new hard failure mode.
        events, spawn = self._run_fallback_turn(break_acs=True)
        self.assertTrue(spawn["hit"], "permitted fallback must run the direct turn")
        self.assertEqual(events[-1].get("type"), "done")


if __name__ == "__main__":
    unittest.main()

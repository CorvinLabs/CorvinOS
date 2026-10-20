"""E2E — ADR-0114 milestones M2 (progress), M2.1 (artifacts), M3 (triage verbs).

Drives the REAL ``stream_turn`` with the REAL ``ACSRuntime`` (manager/worker
loop, trace writer, run-dir layout, artifact scan). Only the two model-call
boundaries are faked — ``_call_manager_sync`` / ``_call_worker_sync`` — plus
the spawn gates and the licence quota, exactly like the other delegation E2E
suites. The worker fake writes a deliverable into the ACS run directory the
way a real worker's tool call would, so the console's live poll and post-run
scan both see a real file on disk.

Sandbox: CORVIN_HOME / XDG_CONFIG_HOME / HOME under a fresh temp dir, a dummy
ANTHROPIC_API_KEY so the sign-in guard passes without OAuth.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "corvin_operator" / "bridges" / "shared"))
sys.path.insert(0, str(_REPO / "corvin_operator" / "forge"))

_FANOUT_PROMPT = (
    "Recherchiere aus mehreren Quellen die Marktlage für E-Bikes und "
    "vergleiche die Ergebnisse"
)


def _drain(agen) -> list[dict]:
    async def _collect():
        out = []
        async for ev in agen:
            out.append(ev)
        return out
    return asyncio.run(_collect())


class _DelegatedTurnBase(unittest.TestCase):
    """One real stream_turn on the ACS branch; subclasses pick the worker."""

    worker_sleep_s = 0.0
    wdat_key_hex: str | None = None

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self._saved = {k: os.environ.get(k) for k in (
            "CORVIN_HOME", "XDG_CONFIG_HOME", "HOME", "CORVIN_TENANT_ID",
            "CORVIN_AUDIT_ANCHOR_KEY", "ANTHROPIC_API_KEY", "FORGE_ROOT",
            "CORVIN_WDAT_KEY")}
        for k in self._saved:
            self.addCleanup(self._restore_env, k)
        os.environ["CORVIN_HOME"] = str(root / "corvin")
        os.environ["XDG_CONFIG_HOME"] = str(root / "xdg")
        os.environ["HOME"] = str(root / "home")
        os.environ["CORVIN_TENANT_ID"] = "_default"
        os.environ["CORVIN_AUDIT_ANCHOR_KEY"] = str(root / "anchor.key")
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-sandbox-dummy"
        os.environ.pop("FORGE_ROOT", None)
        if self.wdat_key_hex:
            os.environ["CORVIN_WDAT_KEY"] = self.wdat_key_hex
        else:
            os.environ.pop("CORVIN_WDAT_KEY", None)
        self.addCleanup(self.tmp.cleanup)

        import importlib
        from corvin_console import chat_runtime
        importlib.reload(chat_runtime)
        self.cr = chat_runtime
        self.sess = self.cr.create_session("_default")

    def _restore_env(self, key: str) -> None:
        prev = self._saved[key]
        if prev is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prev

    # ── fakes at the model-call boundary ────────────────────────────────────

    def _fake_manager(self, prompt, model, tenant_id="_default", proc_holder=None):
        self._manager_calls = getattr(self, "_manager_calls", 0) + 1
        if self._manager_calls == 1:
            decision = {
                "decision": "DELEGATE",
                "reasoning": "one worker writes the report",
                "subtasks": [{"id": "s1", "instructions": "write the report",
                              "expected_output": {}}],
            }
        else:
            decision = {"decision": "COMPLETE",
                        "complete_artifacts": {"summary": "report written"}}
        return json.dumps(decision), 100

    def _fake_worker(self, prompt, system, model, budget, extra_env=None,
                     tenant_id="_default", proc_holder=None):
        # The one ACS run of this sandbox session lives under sess.workdir.
        runs = sorted((self.sess.workdir / "acs" / "runs").iterdir())
        run_dir = runs[-1]
        out = run_dir / "output"
        out.mkdir(parents=True, exist_ok=True)
        (out / "marktbericht.md").write_text("# Marktbericht\n\nE-Bikes wachsen.\n",
                                             encoding="utf-8")
        if self.worker_sleep_s:
            time.sleep(self.worker_sleep_s)  # keep the run alive across two polls
        return (
            json.dumps({"status": "success", "result": {"ok": True},
                        "confidence": 0.9}),
            50,
            {"engine_id": "claude_code", "model_id": model, "attested": True,
             "locality": "us_cloud"},
        )

    def _run_delegated(self, prompt: str) -> list[dict]:
        import house_rules as _hr  # type: ignore
        import spawn_gates as _sg  # type: ignore
        import acs_runtime as _rt  # type: ignore

        orig_rules = getattr(_hr, "_house_rules_classifier", None)
        self.addCleanup(setattr, _hr, "_house_rules_classifier", orig_rules)
        _hr._house_rules_classifier = (  # type: ignore[assignment]
            lambda task, rules, auth, **kw: ("", 0.0, "test clear"))

        limits_mod = types.ModuleType("license.limits")

        class _E(Exception):
            pass

        limits_mod.LicenseLimitError = _E  # type: ignore[attr-defined]
        quota_mod = types.ModuleType("license.compute_quota")
        quota_mod.increment_and_check = (  # type: ignore[attr-defined]
            lambda home, channel=None, chat_key=None: None)
        saved_mods = {m: sys.modules.get(m) for m in
                      ("license", "license.compute_quota", "license.limits")}
        sys.modules["license"] = types.ModuleType("license")
        sys.modules["license.compute_quota"] = quota_mod
        sys.modules["license.limits"] = limits_mod

        def _restore_mods():
            for m, prev in saved_mods.items():
                if prev is None:
                    sys.modules.pop(m, None)
                else:
                    sys.modules[m] = prev
        self.addCleanup(_restore_mods)

        async def _no_spawn(*args, **kwargs):
            # A direct OS-turn would start a real `claude` process; the sandbox
            # has none. Return a silent, already-exited process so the turn ends
            # cleanly; these tests assert only WHERE the turn ran.
            proc = AsyncMock()
            proc.pid = 99999
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.wait = AsyncMock(return_value=0)
            proc.stdout = AsyncMock()
            proc.stdout.readline = AsyncMock(return_value=b"")
            proc.stdout.read = AsyncMock(return_value=b"")
            proc.stderr = AsyncMock()
            proc.stderr.read = AsyncMock(return_value=b"")
            proc.stdin = MagicMock()
            proc.stdin.drain = AsyncMock()
            return proc

        with (
            patch.object(asyncio, "create_subprocess_exec", side_effect=_no_spawn),
            patch.object(self.cr, "_delegation_enabled", return_value=True),
            patch.object(self.cr._feature_flags, "worker_engine_mode",
                         return_value="acs"),
            patch.object(self.cr, "_engine_unavailable_message", return_value=None),
            patch.object(_rt, "_call_manager_sync", side_effect=self._fake_manager),
            patch.object(_rt, "_call_worker_sync", side_effect=self._fake_worker),
            patch.object(_rt, "_resolve_worker_engine",
                         return_value=("claude_code", "test-model")),
            patch.object(_sg, "check_l44", return_value=None),
            patch.object(_sg, "check_l34", return_value=None),
            patch.object(_sg, "check_l35", return_value=None),
        ):
            return _drain(self.cr.stream_turn(self.sess, prompt))

    @staticmethod
    def _worker_progress(events: list[dict]) -> list[str]:
        return [e.get("text", "") for e in events
                if e.get("type") == "delta" and "✅ Worker" in (e.get("text") or "")]

    @staticmethod
    def _artifacts(events: list[dict]) -> list[dict]:
        return [e for e in events if e.get("type") == "artifact"]


class M2ProgressE2E(_DelegatedTurnBase):
    """M2 — worker progress from the ACS run reaches the chat stream."""

    def test_worker_finish_progress_is_streamed_for_plain_traces(self) -> None:
        events = self._run_delegated(_FANOUT_PROMPT)
        self.assertTrue(
            any("Delegated to ACS workers" in (e.get("text") or "")
                for e in events if e.get("type") == "delta"),
            "prompt must take the ACS fan-out")
        progress = self._worker_progress(events)
        self.assertEqual(len(progress), 1, f"expected one worker progress line, got {progress}")
        self.assertEqual(events[-1].get("type"), "done")


class M2EncryptedTraceE2E(_DelegatedTurnBase):
    """M2 — progress must also surface when the worker trace is encrypted.

    With a WDAT key (vault or CORVIN_WDAT_KEY) acs_runtime writes
    ``traces/<wid>.json.enc``; the console's trace watcher used to glob only
    ``*.json`` and so streamed no worker progress on encrypted installs.
    """

    wdat_key_hex = "11" * 32  # 32-byte AES-256 key, hex

    def test_worker_finish_progress_is_streamed_for_encrypted_traces(self) -> None:
        events = self._run_delegated(_FANOUT_PROMPT)
        traces = list((self.sess.workdir / "acs" / "runs").glob("*/traces/*"))
        self.assertTrue(any(t.name.endswith(".json.enc") for t in traces),
                        f"precondition: encrypted trace expected, got {traces}")
        self.assertEqual(len(self._worker_progress(events)), 1,
                         "encrypted worker trace must still stream progress")


class M2_1ArtifactE2E(_DelegatedTurnBase):
    """M2.1 — artifacts a delegated run writes are registered on the turn."""

    worker_sleep_s = 5.0  # long enough for the live poll to see a stable file

    def test_live_artifact_is_emitted_before_the_result(self) -> None:
        events = self._run_delegated(_FANOUT_PROMPT)
        arts = self._artifacts(events)
        names = [a.get("name") for a in arts]
        self.assertIn("marktbericht.md", names, f"artifact not registered: {names}")
        art = next(a for a in arts if a.get("name") == "marktbericht.md")
        self.assertEqual(art.get("mime"), "text/markdown")
        self.assertTrue(art.get("path", "").endswith("output/marktbericht.md"))
        types_in_order = [e.get("type") for e in events]
        self.assertLess(types_in_order.index("artifact"), types_in_order.index("done"))

    def test_artifact_is_persisted_on_the_turn(self) -> None:
        self._run_delegated(_FANOUT_PROMPT)
        turns = self.cr.read_turns("_default", self.sess.sid)
        parts = [p for t in turns for p in (t.get("parts") or [])]
        names = [p.get("name") for p in parts if p.get("kind") == "artifact"]
        self.assertIn("marktbericht.md", names,
                      f"delegated artifact missing from turn history: {names}")


class M3TriageVerbE2E(_DelegatedTurnBase):
    """M3 — strong verbs route to delegation through the real stream_turn."""

    def test_review_verb_without_coding_token_delegates(self) -> None:
        events = self._run_delegated(
            "Überprüfe die Marktberichte aus den letzten drei Quartalen und "
            "fasse die Kernaussagen zusammen")
        self.assertTrue(any("Delegated to ACS workers" in (e.get("text") or "")
                            for e in events if e.get("type") == "delta"))

    def test_debug_verb_with_coding_token_stays_direct(self) -> None:
        # Deliberate 2026-07-20 rework (delegation-routing.md §6): coding is
        # sequential and never routes into the fan-out, even for a strong verb.
        events = self._run_delegated(
            "Debugge den Fehler im Parser-Modul und behebe den Crash")
        self.assertFalse(any("Delegated to ACS workers" in (e.get("text") or "")
                             for e in events if e.get("type") == "delta"))


if __name__ == "__main__":
    unittest.main()

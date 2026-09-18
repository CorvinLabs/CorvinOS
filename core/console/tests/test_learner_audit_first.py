"""ADR-0885 step 0b — the confidence learner is audit-first, for real.

Until 2026-09-18 ``ConfidenceOptimizer.process_feedback`` appended the
in-memory history, wrote the history FILE and evaluated convergence before
the audit call, and the default ``_SkillAuditBackend`` discarded
``emit_skill_audit``'s ``False`` (the emitter never raises), so a refused or
unreachable chain could not stop a sample from being learned. These tests
pin the new contract against the REAL persistent optimizer under a temp
``CORVIN_HOME``:

* refused chain → ``RuntimeError``; memory, history file, cache, store and
  ``n_samples`` untouched;
* a missing ``core.skills`` import fails closed too;
* ``reset_learning`` after a refused write succeeds (the cache holds the
  uniform prior without history — ``pop``, never ``del``);
* ``reset_learning`` is tenant-EXACT (``endswith``, not substring);
* a non-default tenant is refused by the single-tenant chain writer unless
  the process tenant is that tenant — documented, and the reason the
  route tests patch ``_current_tenant_id``.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "corvin_operator" / "bridges" / "shared"))
sys.path.insert(0, str(_REPO))

import corvin_core._bootstrap  # noqa: E402,F401 — makes `forge` importable
from forge import security_events  # type: ignore[import-not-found]  # noqa: E402

from core.learning import confidence_persistence as CP  # noqa: E402
from core.learning import model_selection_optimizer as MSO  # noqa: E402

MODEL = "claude-sonnet-5"


def _as_process_tenant(tenant_id: str):
    return mock.patch.object(security_events, "_current_tenant_id", lambda: tenant_id)


def _refuse(*_a, **_k):
    """The real chain writer signals refusal by RAISING (e.g. AuditTenantMismatch);
    emit_skill_audit turns any exception into False. Its return value is ignored."""
    raise RuntimeError("chain refused")


class LearnerAuditFirstTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._prev_home = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = self._tmp.name
        MSO._optimizer = None

    def tearDown(self) -> None:
        MSO._optimizer = None
        if self._prev_home is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._prev_home
        self._tmp.cleanup()

    def _history_file(self, tenant: str = "_default") -> dict:
        p = CP._stats_path(tenant)
        return json.loads(p.read_text()) if p.exists() else {}

    def _chain_lines(self, tenant: str = "_default") -> int:
        p = Path(self._tmp.name) / "tenants" / tenant / "global" / "forge" / "audit.jsonl"
        return sum(1 for _ in p.open()) if p.exists() else 0

    # ── happy path: the chain gets the record, then the learner persists ──

    def test_committed_write_then_persist(self) -> None:
        opt = MSO.get_optimizer()
        before = self._chain_lines()
        conf, _ = opt.process_feedback("MEDIUM", MODEL, 0.9, "_default")
        self.assertGreater(conf, 0.5)
        self.assertGreater(self._chain_lines(), before)
        self.assertEqual(opt.get_stats("MEDIUM", MODEL).n_samples, 1)
        self.assertIn(f"model_stats:MEDIUM:{MODEL}:_default", self._history_file())

    # ── refused chain: nothing happened ──

    def test_refused_chain_leaves_no_trace(self) -> None:
        opt = MSO.get_optimizer()
        opt.process_feedback("MEDIUM", MODEL, 0.9, "_default")  # one real sample
        stats_before = opt.get_stats("MEDIUM", MODEL).to_dict()
        hist_before = list(opt._confidence_history[("MEDIUM", MODEL, "_default")])
        file_before = self._history_file()
        chain_before = self._chain_lines()

        def refuse(*_a, **_k):
            raise RuntimeError("chain refused")

        with mock.patch.object(security_events, "write_event", refuse):
            with self.assertRaises(RuntimeError):
                opt.process_feedback("MEDIUM", MODEL, 0.1, "_default")

        self.assertEqual(opt.get_stats("MEDIUM", MODEL).to_dict(), stats_before)
        self.assertEqual(opt._confidence_history[("MEDIUM", MODEL, "_default")], hist_before)
        self.assertEqual(self._history_file(), file_before)
        self.assertEqual(self._chain_lines(), chain_before)

    def test_refused_chain_on_first_sample_leaves_only_the_prior(self) -> None:
        opt = MSO.get_optimizer()
        key = ("COMPLEX", MODEL, "_default")
        with mock.patch.object(security_events, "write_event", _refuse):
            with self.assertRaises(RuntimeError):
                opt.process_feedback("COMPLEX", MODEL, 0.9, "_default")
        # _load_stats cached the uniform prior (accepted: it is not a sample)…
        self.assertEqual(opt._stats_cache[key].n_samples, 0)
        # …but no history, no file entry, no store entry.
        self.assertNotIn(key, opt._confidence_history)
        self.assertNotIn(f"model_stats:COMPLEX:{MODEL}:_default", self._history_file())

    def test_missing_skill_audit_module_fails_closed(self) -> None:
        opt = MSO.get_optimizer()
        with mock.patch.dict(sys.modules, {"core.skills.skill_audit": None}):
            with self.assertRaises(RuntimeError):
                opt.process_feedback("SIMPLE", MODEL, 0.9, "_default")
        self.assertEqual(opt.get_stats("SIMPLE", MODEL).n_samples, 0)

    # ── reset after a refused write must not raise ──

    def test_reset_after_refused_write_succeeds(self) -> None:
        opt = MSO.get_optimizer()
        key = ("COMPLEX", MODEL, "_default")
        with mock.patch.object(security_events, "write_event", _refuse):
            with self.assertRaises(RuntimeError):
                opt.process_feedback("COMPLEX", MODEL, 0.9, "_default")
        self.assertIn(key, opt._stats_cache)
        opt.reset_learning("_default")  # used to raise KeyError here
        self.assertNotIn(key, opt._stats_cache)
        self.assertNotIn(key, opt._confidence_history)

    # ── tenant-exact reset ──

    def test_reset_is_tenant_exact(self) -> None:
        opt = MSO.get_optimizer()
        # "a" is a substring of "claude-…:…" — the old substring match reset it.
        with _as_process_tenant("a"):
            opt.process_feedback("SIMPLE", MODEL, 0.9, "a")
        with _as_process_tenant("b"):
            opt.process_feedback("SIMPLE", MODEL, 0.9, "b")
        with _as_process_tenant("dev"):
            opt.process_feedback("SIMPLE", MODEL, 0.9, "dev")
        with _as_process_tenant("dev-2"):
            opt.process_feedback("SIMPLE", MODEL, 0.9, "dev-2")
        self.assertEqual(opt.get_stats("SIMPLE", MODEL, "b").n_samples, 1)

        opt.reset_learning("a")
        self.assertEqual(opt.get_stats("SIMPLE", MODEL, "a").n_samples, 0)
        self.assertEqual(opt.get_stats("SIMPLE", MODEL, "b").n_samples, 1)
        self.assertIn(f"model_stats:SIMPLE:{MODEL}:b", self._history_file("b"))

        opt.reset_learning("dev")
        self.assertEqual(opt.get_stats("SIMPLE", MODEL, "dev-2").n_samples, 1)
        self.assertIn(f"model_stats:SIMPLE:{MODEL}:dev-2", self._history_file("dev-2"))

    # ── the single-tenant chain writer, documented ──

    def test_non_process_tenant_is_refused_and_learns_nothing(self) -> None:
        opt = MSO.get_optimizer()
        with self.assertRaises(RuntimeError):
            opt.process_feedback("SIMPLE", MODEL, 0.9, "tenant_x")
        self.assertEqual(opt.get_stats("SIMPLE", MODEL, "tenant_x").n_samples, 0)
        self.assertNotIn(f"model_stats:SIMPLE:{MODEL}:tenant_x", self._history_file("tenant_x"))

    # ── three processes, one file: no lost update ──

    def test_second_instance_sees_and_extends_the_first_instances_samples(self) -> None:
        """The bridge daemon, the console runtime and the feedback route each
        hold their own ConfidenceOptimizer over ONE stats file. Until
        2026-09-18 the cache-first read made the second writer overwrite the
        first's samples (chain recorded n_samples 8 -> 2)."""
        daemon = MSO.ConfidenceOptimizer(store=CP.PersistentConfidenceStore(), audit_backend=MSO._SkillAuditBackend())
        console = MSO.ConfidenceOptimizer(store=CP.PersistentConfidenceStore(), audit_backend=MSO._SkillAuditBackend())
        console.get_stats("MEDIUM", MODEL)  # console looked first (caches the prior)
        for _ in range(8):
            daemon.process_feedback("MEDIUM", MODEL, 0.9, "_default")
        self.assertEqual(console.get_stats("MEDIUM", MODEL).n_samples, 8)  # read-through
        console.process_feedback("MEDIUM", MODEL, 0.1, "_default")
        self.assertEqual(daemon.get_stats("MEDIUM", MODEL).n_samples, 9)
        self.assertEqual(console.get_stats("MEDIUM", MODEL).n_samples, 9)
        # and the FILE (a fresh store view, no process cache) agrees
        self.assertEqual(CP.PersistentConfidenceStore()[f"model_stats:MEDIUM:{MODEL}:_default"]["n_samples"], 9)

    def test_reset_in_another_process_drops_this_processes_history(self) -> None:
        """A feeds 60 (converged), B resets, A feeds 1: A's next sample must
        start from an EMPTY history — not write its stale 50-entry trajectory
        back to disk and report converged at 5 samples (review R2)."""
        a = MSO.ConfidenceOptimizer(store=CP.PersistentConfidenceStore(), audit_backend=MSO._SkillAuditBackend())
        b = MSO.ConfidenceOptimizer(store=CP.PersistentConfidenceStore(), audit_backend=MSO._SkillAuditBackend())
        for _ in range(60):
            a.process_feedback("MEDIUM", MODEL, 0.9, "_default")
        self.assertTrue(a.is_converged("MEDIUM", MODEL))
        b.reset_learning("_default")
        self.assertNotIn(f"model_stats:MEDIUM:{MODEL}:_default", self._history_file())
        for _ in range(5):
            _, conv = a.process_feedback("MEDIUM", MODEL, 0.9, "_default")
        self.assertFalse(conv)
        self.assertEqual(a.get_stats("MEDIUM", MODEL).n_samples, 5)
        self.assertEqual(len(CP.load_confidence_history(f"model_stats:MEDIUM:{MODEL}:_default")), 5)
        self.assertFalse(b.is_converged("MEDIUM", MODEL) if b.get_stats("MEDIUM", MODEL) else False)

    def test_history_only_row_reads_as_absent(self) -> None:
        key = f"model_stats:SIMPLE:{MODEL}:_default"
        CP._save_file("_default", {key: {"confidence_history": [0.5, 0.6]}})
        opt = MSO.get_optimizer()
        self.assertEqual(opt.get_stats("SIMPLE", MODEL).n_samples, 0)
        self.assertEqual(opt.rank_models("SIMPLE", [MODEL])[0].n_samples, 0)

    def test_persisted_row_with_unknown_key_does_not_raise(self) -> None:
        opt = MSO.get_optimizer()
        opt.process_feedback("SIMPLE", MODEL, 0.9, "_default")
        key = f"model_stats:SIMPLE:{MODEL}:_default"
        data = self._history_file(); data[key]["future_field"] = 1
        CP._save_file("_default", data)
        MSO._optimizer = None
        self.assertEqual(MSO.get_optimizer().get_stats("SIMPLE", MODEL).n_samples, 1)

    # ── convergence is evaluated on the NEW state ──

    def test_convergence_uses_new_state(self) -> None:
        opt = MSO.get_optimizer()
        converged = False
        for _ in range(60):
            _, converged = opt.process_feedback("MEDIUM", MODEL, 0.9, "_default")
        self.assertTrue(converged)
        self.assertTrue(opt.is_converged("MEDIUM", MODEL, "_default"))


if __name__ == "__main__":
    unittest.main()

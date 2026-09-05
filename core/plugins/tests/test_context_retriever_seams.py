"""ADR-0599 — context_retriever provider type + the two fail-open seams.

Three properties are pinned here, and the first is the load-bearing one:

* **Fail-open == byte-identical.** With no provider active (the bundled
  ``PassthroughContextRetriever``), both the CEL memory seam and the TDE
  ``_build_prompt`` seam behave exactly as they did before ADR-0599.
* **The registry has ADR-0033 identity ownership.** ``set_active`` records the
  loading plugin; ``release_owned_by`` releases by that identity only.
* **The TDE argv ceiling is a hard post-condition.** A provider that returns an
  over-``_SNAPSHOT_MAX_CHARS`` slice is rejected and the raw truncation is used —
  a bad provider can never cause an E2BIG argv.

The seams are exercised THROUGH their real functions (the CEL package's
``memory`` stage module and the real ``SubprocessWorkerIPC._build_prompt``), not
a reimplementation.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]
for _p in (
    str(_REPO / "core" / "plugins"),
    str(_REPO / "operator" / "forge"),
    str(_REPO / "core" / "console"),
    str(_REPO / "operator" / "orchestration"),
    str(_REPO / "operator" / "bridges" / "shared"),
    str(_REPO),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from corvin_plugins import loading  # noqa: E402
from corvin_plugins.providers import context_retriever as cr  # noqa: E402
from corvin_plugins.providers.context_retriever import (  # noqa: E402
    ContextRetrieverRegistry,
    PassthroughContextRetriever,
)


def _load_cel_memory():
    """Load the CEL ``memory`` stage module as its real package submodule."""
    if "context_engineering.stages.memory" in sys.modules:
        return sys.modules["context_engineering.stages.memory"]
    ce = _REPO / "operator" / "context_engineering"
    spec = importlib.util.spec_from_file_location(
        "context_engineering", str(ce / "__init__.py"),
        submodule_search_locations=[str(ce)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["context_engineering"] = mod
    spec.loader.exec_module(mod)
    import context_engineering.stages.memory as memory  # noqa: PLC0415
    return memory


# ── The registry (ADR-0033 identity ownership) ───────────────────────────────

class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.reg = ContextRetrieverRegistry()

    def test_default_is_passthrough_returning_candidates_unchanged(self):
        active = self.reg.get_active()
        self.assertIsInstance(active, PassthroughContextRetriever)
        cands = ["a", "b", "c"]
        # Identity, not just equality: the passthrough returns the SAME object,
        # which is what makes the seams a true no-op.
        self.assertIs(active.select("q", cands), cands)

    def test_set_active_and_get_active(self):
        class Rev:
            def select(self, query, candidates, *, budget=None, tenant_id=None):
                return list(reversed(candidates))

        prov = Rev()
        self.reg.set_active(prov)
        self.assertIs(self.reg.get_active(), prov)
        self.assertEqual(self.reg.get_active().select("q", [1, 2, 3]), [3, 2, 1])

    def test_release_owned_by_is_identity_scoped(self):
        prov = PassthroughContextRetriever()
        with loading.loading("pluginX", "_default"):
            self.reg.set_active(prov)
        self.assertEqual(self.reg.owner_plugin_id(), "pluginX")
        # Wrong id may not release.
        self.assertFalse(self.reg.release_owned_by("someone-else"))
        self.assertIs(self.reg.get_active(), prov)
        # The owner releases; the slot returns to a fresh passthrough default.
        self.assertTrue(self.reg.release_owned_by("pluginX"))
        self.assertIsInstance(self.reg.get_active(), PassthroughContextRetriever)
        self.assertIsNone(self.reg.owner_plugin_id())

    def test_clear_if_active_is_instance_checked(self):
        first = PassthroughContextRetriever()
        second = PassthroughContextRetriever()
        self.reg.set_active(first)
        self.reg.set_active(second)  # second supersedes first
        self.assertFalse(self.reg.clear_if_active(first))  # first is not active
        self.assertIs(self.reg.get_active(), second)
        self.assertTrue(self.reg.clear_if_active(second))


# ── Seam 1: CEL memory stage (fail-open) ─────────────────────────────────────

class _Brief:
    def __init__(self, matches, raw_input="the query"):
        self.raw_input = raw_input
        self.memory_context = _MC(matches)


class _MC:
    def __init__(self, matches):
        self.matches = matches


class _Ctx:
    tenant_id = "_default"


class TestCELMemorySeam(unittest.TestCase):
    def setUp(self):
        self.memory = _load_cel_memory()
        cr.clear()  # ensure passthrough default

    def tearDown(self):
        cr.clear()

    def test_no_provider_is_byte_identical(self):
        matches = ["m0", "m1", "m2"]
        brief = _Brief(matches)
        out = self.memory._apply_context_retriever(brief, _Ctx())
        # Same brief, same list object, same contents — a true no-op.
        self.assertIs(out, brief)
        self.assertIs(out.memory_context.matches, matches)
        self.assertEqual(out.memory_context.matches, ["m0", "m1", "m2"])

    def test_active_provider_narrows_the_matches(self):
        matches = ["m0", "m1", "m2", "m3"]

        class TopTwo:
            def select(self, query, candidates, *, budget=None, tenant_id=None):
                return candidates[:2]

        cr.set_active(TopTwo())
        brief = _Brief(matches)
        out = self.memory._apply_context_retriever(brief, _Ctx())
        self.assertEqual(out.memory_context.matches, ["m0", "m1"])

    def test_additive_return_is_rejected(self):
        matches = ["m0", "m1"]

        class Adder:
            def select(self, query, candidates, *, budget=None, tenant_id=None):
                return candidates + ["INJECTED"]  # grows the set — must be rejected

        cr.set_active(Adder())
        brief = _Brief(matches)
        out = self.memory._apply_context_retriever(brief, _Ctx())
        # Original candidates kept: the seam never lets a retriever ADD.
        self.assertEqual(out.memory_context.matches, ["m0", "m1"])

    def test_foreign_item_is_rejected(self):
        matches = ["m0", "m1"]

        class Swapper:
            def select(self, query, candidates, *, budget=None, tenant_id=None):
                return ["NOT-A-CANDIDATE"]  # same length, but not from candidates

        cr.set_active(Swapper())
        brief = _Brief(matches)
        out = self.memory._apply_context_retriever(brief, _Ctx())
        self.assertEqual(out.memory_context.matches, ["m0", "m1"])

    def test_raising_provider_falls_back(self):
        matches = ["m0", "m1"]

        class Boom:
            def select(self, query, candidates, *, budget=None, tenant_id=None):
                raise RuntimeError("provider blew up")

        cr.set_active(Boom())
        brief = _Brief(matches)
        out = self.memory._apply_context_retriever(brief, _Ctx())
        self.assertEqual(out.memory_context.matches, ["m0", "m1"])


# ── Seam 2: TDE step assembly (fail-open, argv-ceiling post-condition) ────────

def _make_envelope(snapshot: dict):
    from tde.adaptive_delegation_executor import DelegationEnvelope  # noqa: PLC0415
    from initial_analysis import GlobalPlan, Step  # noqa: PLC0415

    step = Step(step=1, action="reason_about", description="do the thing")
    plan = GlobalPlan(steps=[step], estimated_duration_s=10, estimated_tokens=100)
    return DelegationEnvelope(
        step=step, decision_context=plan, statement_snapshot=snapshot,
        budget={"max_tokens": 1000, "remaining": 1000}, idempotency_key="k1",
    )


class TestTDESeam(unittest.TestCase):
    def setUp(self):
        from tde.worker_ipc import SubprocessWorkerIPC  # noqa: PLC0415

        # Build without __init__ so we don't need helper_model on the path;
        # _build_prompt / _select_snapshot_slice never touch self._hm.
        self.ipc = SubprocessWorkerIPC.__new__(SubprocessWorkerIPC)
        cr.clear()

    def tearDown(self):
        cr.clear()

    def test_no_provider_build_prompt_is_byte_identical(self):
        snap = {"prev": "hello world " * 5}
        env = _make_envelope(snap)
        cr.clear()
        baseline = self.ipc._build_prompt(env)
        # Register a passthrough explicitly — must not change a single byte.
        cr.set_active(PassthroughContextRetriever())
        with_provider = self.ipc._build_prompt(env)
        self.assertEqual(baseline, with_provider)

    def test_passthrough_slice_returns_snapshot_unchanged(self):
        snap = {"prev": "abc"}
        env = _make_envelope(snap)
        sj = json.dumps(snap, default=str, indent=2)
        self.assertEqual(self.ipc._select_snapshot_slice(sj, env), sj)

    def test_oversize_slice_is_rejected_then_raw_truncation_applies(self):
        cap = self.ipc._SNAPSHOT_MAX_CHARS

        class Oversize:
            def select(self, query, candidates, *, budget=None, tenant_id=None):
                # Return a slice LARGER than the argv ceiling — must be rejected.
                return ["X" * (cap + 50_000)]

        cr.set_active(Oversize())
        # A small snapshot: without the ceiling guard the oversize provider
        # output would flow into the prompt and blow the argv limit.
        snap = {"prev": "short"}
        env = _make_envelope(snap)
        sj = json.dumps(snap, default=str, indent=2)
        # The seam rejects the oversize slice and hands back the original snapshot.
        self.assertEqual(self.ipc._select_snapshot_slice(sj, env), sj)
        # And end-to-end the built prompt never carries an over-ceiling snapshot.
        prompt = self.ipc._build_prompt(env)
        self.assertNotIn("X" * (cap + 1), prompt)
        self.assertIn("short", prompt)

    def test_valid_small_slice_is_used(self):
        class Trim:
            def select(self, query, candidates, *, budget=None, tenant_id=None):
                return ["SELECTED-SLICE"]

        cr.set_active(Trim())
        snap = {"prev": "the full original snapshot body"}
        env = _make_envelope(snap)
        prompt = self.ipc._build_prompt(env)
        self.assertIn("SELECTED-SLICE", prompt)
        self.assertNotIn("the full original snapshot body", prompt)

    def test_raising_provider_falls_back_to_raw(self):
        class Boom:
            def select(self, query, candidates, *, budget=None, tenant_id=None):
                raise RuntimeError("nope")

        cr.set_active(Boom())
        snap = {"prev": "raw-body-marker"}
        env = _make_envelope(snap)
        prompt = self.ipc._build_prompt(env)
        self.assertIn("raw-body-marker", prompt)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

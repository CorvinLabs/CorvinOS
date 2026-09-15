"""Regression tests for the fail-closed hardening sweep of the shared spawn gates.

Covers three confirmed release-blocking findings — every assertion pins a gate to
the FAIL-CLOSED (stricter) direction so a future regression that re-opens the gate
turns red:

  * H10 — L44 gate-construction failure must DENY (not "conservative allow").
  * M8  — L34 validate error must DENY (matching the sibling check_l35), and a
          transient guard-load error must NOT be cached as a permanent None-allow.
  * H3  — the "acs" delegation fan-out alias must resolve to the Anthropic host,
          not the "unknown" sentinel (L35 sibling of the dd2b569 L34 drift fix).

Run with::

    python -m pytest operator/bridges/shared/test_gate_failclosed_hardening.py -q
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import spawn_gates  # noqa: E402
import egress_gate  # noqa: E402
from egress_gate import DEFAULT_ENGINE_HOSTS  # noqa: E402
from data_classification import DELEGATION_ENGINE_ID  # noqa: E402


# ── H10 — L44 gate-construction fail-CLOSED ──────────────────────────────────


class TestL44GateConstructionFailClosed(unittest.TestCase):
    """H10: if HouseRulesGate.from_repo raises, the turn must be BLOCKED."""

    def setUp(self):
        import house_rules as _hr
        self._hr = _hr
        self._orig_from_repo = _hr.HouseRulesGate.from_repo
        # Capture audit events emitted by the fail-closed branch.
        self._events: list[tuple] = []
        self._orig_mk_writer = egress_gate.make_forge_audit_writer

        def _capturing_writer(_path):
            def _w(event_type, severity, details):
                self._events.append((event_type, severity, details))
            return _w

        egress_gate.make_forge_audit_writer = _capturing_writer
        # Force gate construction to blow up.
        egress_gate  # keep ref
        self._tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl")
        self._tmp.close()
        os.environ["VOICE_AUDIT_PATH"] = self._tmp.name

        def _boom(*a, **k):
            raise RuntimeError("simulated gate-construction failure")

        _hr.HouseRulesGate.from_repo = staticmethod(_boom)

    def tearDown(self):
        self._hr.HouseRulesGate.from_repo = self._orig_from_repo
        egress_gate.make_forge_audit_writer = self._orig_mk_writer
        os.environ.pop("VOICE_AUDIT_PATH", None)
        try:
            os.unlink(self._tmp.name)
        except OSError:
            pass

    def test_gate_construction_failure_denies_not_allows(self):
        result = spawn_gates.check_l44("please do something", "_default")
        # OLD (buggy) behaviour returned None (= allow). It must now DENY.
        self.assertIsNotNone(
            result, "L44 gate-construction failure must fail CLOSED (deny), not allow"
        )
        self.assertIn("blocked", result.lower())
        self.assertIn("fail-closed", result.lower())

    def test_gate_construction_failure_emits_audit_event(self):
        spawn_gates.check_l44("please do something", "_default")
        kinds = [e[0] for e in self._events]
        self.assertIn(
            "house_rules.denied", kinds,
            "fail-closed gate-construction deny must emit a house_rules.denied audit event",
        )


# ── M8 — L34 validate error fail-CLOSED + no permanent None-caching ──────────


class _RaisingGuard:
    def validate(self, **kwargs):
        raise RuntimeError("simulated data-flow guard validate error")


class TestL34ValidateFailClosed(unittest.TestCase):
    """M8: a guard.validate() error must DENY, mirroring check_l35."""

    def setUp(self):
        self._orig_loader = spawn_gates._load_l34_guard
        spawn_gates._load_l34_guard = lambda *a, **k: _RaisingGuard()

    def tearDown(self):
        spawn_gates._load_l34_guard = self._orig_loader

    def test_validate_error_denies_not_allows(self):
        # classification supplied → skips classify_task, goes straight to validate.
        result = spawn_gates.check_l34(
            "claude_code", "_default", classification="internal"
        )
        self.assertIsNotNone(
            result, "L34 validate error must fail CLOSED (deny), not allow"
        )
        self.assertIn("fail-closed", result.lower())
        self.assertIn("rejected", result.lower())


class TestL34TransientLoadNotCached(unittest.TestCase):
    """M8: a transient guard-load error must NOT be cached as permanent None-allow."""

    def setUp(self):
        spawn_gates.invalidate_cache()
        self._home = Path(tempfile.mkdtemp(prefix="l34cache_"))
        cfg = self._home / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text("spec: {}\n", encoding="utf-8")
        import data_classification as _dc
        self._dc = _dc
        self._orig_load = _dc.load_guard_for_tenant

    def tearDown(self):
        self._dc.load_guard_for_tenant = self._orig_load
        spawn_gates.invalidate_cache()

    def test_transient_load_error_is_not_cached(self):
        calls = {"n": 0}

        def _flaky(tenant_id, corvin_home=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient load failure")
            return object()  # a truthy sentinel "guard"

        self._dc.load_guard_for_tenant = _flaky

        # First call: loader raises → must return None AND must not cache it.
        g1 = spawn_gates._load_l34_guard("_default", self._home)
        self.assertIsNone(g1)
        cache_key = f"_default:{self._home}"
        self.assertNotIn(
            cache_key, spawn_gates._l34_cache,
            "a transient load error must not be cached as a permanent None-allow",
        )

        # Second call: loader recovers → re-evaluated, real guard returned.
        g2 = spawn_gates._load_l34_guard("_default", self._home)
        self.assertIsNotNone(g2, "loader must be re-evaluated after a transient error")
        self.assertEqual(calls["n"], 2, "second call must actually re-invoke the loader")


# ── H3 — "acs" delegation alias host-map drift ───────────────────────────────


class TestAcsHostMapAlias(unittest.TestCase):
    """H3: the delegation fan-out alias must resolve to the Anthropic host."""

    def test_acs_alias_present_and_matches_acs_worker(self):
        self.assertIn("acs", DEFAULT_ENGINE_HOSTS,
                      "delegation alias 'acs' missing from DEFAULT_ENGINE_HOSTS")
        self.assertEqual(DEFAULT_ENGINE_HOSTS["acs"], "api.anthropic.com")
        self.assertEqual(DEFAULT_ENGINE_HOSTS["acs"],
                         DEFAULT_ENGINE_HOSTS["acs_worker"],
                         "'acs' must mirror 'acs_worker' egress host")

    def test_delegation_engine_id_is_mapped(self):
        self.assertIn(
            DELEGATION_ENGINE_ID, DEFAULT_ENGINE_HOSTS,
            f"DELEGATION_ENGINE_ID {DELEGATION_ENGINE_ID!r} must resolve a host, "
            "not fall through to the 'unknown' sentinel",
        )

    def test_acs_not_unknown_sentinel(self):
        # The bug: .get('acs', 'unknown') returned 'unknown' → default_deny under
        # any enabled egress policy, breaking delegated web-chat turns.
        self.assertNotEqual(DEFAULT_ENGINE_HOSTS.get("acs", "unknown"), "unknown")


if __name__ == "__main__":
    unittest.main()

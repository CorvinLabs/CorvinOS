"""ADR-0149 LIC-ENG-USE-01: run_delegate must enforce the license engines_allowed
limit fail-closed — a SesT restricting engines must block a forbidden worker
engine on the delegation USE path (the adapter OS-turn path already did)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parents[1]  # core/delegate
_REPO = _PLUGIN_DIR.parents[1]
for _p in (
    str(_PLUGIN_DIR),
    str(_REPO / "operator" / "bridges" / "shared"),  # agents package parent
    str(_REPO / "operator"),                          # license package
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from corvin_delegate.delegation import run_delegate  # noqa: E402
import license.validator as _v  # noqa: E402


class TestEnginesAllowedGate(unittest.TestCase):
    def setUp(self):
        # Ensure the dual-env test bypass is NOT both-set, so the gate fires.
        import os
        for k in ("CORVIN_AGENTS_SKIP_LIVE", "CORVIN_INTEGRATION_TEST"):
            os.environ.pop(k, None)
        self._orig = _v._ACTIVE_LICENSE
        self._orig_canary = _v._ACTIVE_LICENSE_CANARY

    def tearDown(self):
        _v._ACTIVE_LICENSE = self._orig
        _v._ACTIVE_LICENSE_CANARY = self._orig_canary

    def test_forbidden_engine_denied_by_license(self):
        # SesT restricts engines to claude_code only.
        _v._set_active_license({"tier": "pro", "limits": {"engines_allowed": ["claude_code"]}})
        res = run_delegate(engine="codex_cli", prompt="hello", audit=False)
        self.assertFalse(res.ok, "codex_cli must be denied when engines_allowed=[claude_code]")
        self.assertIn("engine-not-allowed-by-license", res.error or "")

    def test_allowed_engine_passes_gate(self):
        # When engines_allowed permits the engine, the license gate must NOT deny.
        # (The call may still fail later for other reasons — we only assert the
        # gate did not produce its own deny error.)
        _v._set_active_license({"tier": "pro", "limits": {"engines_allowed": ["codex_cli"]}})
        res = run_delegate(engine="codex_cli", prompt="hello", audit=False, budget_s=1)
        self.assertNotIn("engine-not-allowed-by-license", res.error or "")

    def test_no_limit_allows_any_engine(self):
        # Free/default tier (engines_allowed absent → None) must not deny.
        _v._set_active_license(None)
        res = run_delegate(engine="codex_cli", prompt="hello", audit=False, budget_s=1)
        self.assertNotIn("engine-not-allowed-by-license", res.error or "")

    def test_non_license_limit_error_in_gate_still_emits_an_audit_event(self):
        """Adversarial review finding: the engines_allowed gate's except
        branch only got an audit event for the LicenseLimitError case
        (emitted internally by validator.assert_limit itself) — any OTHER
        exception (e.g. license.validator failing in some unexpected way)
        denied the delegation with ZERO corresponding L16 event, a gap
        given the project's own audit-first invariant."""
        from unittest.mock import patch
        from corvin_delegate.delegation import _emit_audit_failed as _real_marker  # noqa: F401

        emitted = []

        def _fake_emit_audit_failed(**fields):
            emitted.append(fields)

        with patch("license.validator.assert_limit", side_effect=RuntimeError("boom")), \
             patch("corvin_delegate.delegation._emit_audit_failed", _fake_emit_audit_failed):
            res = run_delegate(engine="codex_cli", prompt="hello", audit=True)

        self.assertFalse(res.ok)
        self.assertIn("license-gate-error", res.error or "")
        self.assertEqual(len(emitted), 1, "expected exactly one delegate.failed audit event")
        self.assertEqual(emitted[0]["engine"], "codex_cli")
        self.assertIn("license-gate-error", emitted[0]["reason"])


if __name__ == "__main__":
    unittest.main()

"""Tests for spawn_gates.py (ADR-0158 M1).

Tests check_l34 / check_l35 / check_l44 gate behavior without a real YAML or
full module bootstrap — library functions are mocked at their source module.

check_l44 (L44 acceptable-use, ADR-0143) is MANDATORY + fail-CLOSED, so its
tests use a deterministic monkeypatched Tier-1 classifier (same technique as
test_adr0157_classifier.py) over an isolated per-tenant forge audit chain.
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Ensure shared/ is on sys.path.
_SHARED = Path(__file__).resolve().parent.parent
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

import spawn_gates  # noqa: E402 — path must be set first


# ── helpers ──────────────────────────────────────────────────────────────────

def _allowed_decision():
    d = SimpleNamespace()
    d.allowed = True
    d.reason = ""
    return d


def _denied_decision(reason="matrix"):
    d = SimpleNamespace()
    d.allowed = False
    d.reason = reason
    return d


def _mock_guard(decision):
    g = MagicMock()
    g.validate.return_value = decision
    return g


# ── check_l34 ────────────────────────────────────────────────────────────────


class TestCheckL34GuardNone(unittest.TestCase):
    """When guard is None (no tenant config) → fail-open (None returned)."""

    def test_no_guard_returns_none(self):
        with patch.object(spawn_gates, "_load_l34_guard", return_value=None):
            result = spawn_gates.check_l34("hermes", "_default", classification="internal")
        self.assertIsNone(result)

    def test_empty_engine_id_returns_none(self):
        with patch.object(spawn_gates, "_load_l34_guard", return_value=None):
            result = spawn_gates.check_l34("", "_default", classification="internal")
        self.assertIsNone(result)


class TestCheckL34ExplicitClassification(unittest.TestCase):
    """Caller passes ``classification`` string (acs_runtime / A2A path)."""

    def test_allowed_returns_none(self):
        guard = _mock_guard(_allowed_decision())
        with patch.object(spawn_gates, "_load_l34_guard", return_value=guard):
            result = spawn_gates.check_l34(
                "hermes", "_default", classification="internal",
                channel="discord", chat_key="c1",
            )
        self.assertIsNone(result)
        guard.validate.assert_called_once()

    def test_denied_returns_error_string(self):
        guard = _mock_guard(_denied_decision("engine locality mismatch"))
        with patch.object(spawn_gates, "_load_l34_guard", return_value=guard):
            result = spawn_gates.check_l34(
                "openai", "_default", classification="confidential",
            )
        self.assertIsNotNone(result)
        self.assertIn("data-flow", result)
        self.assertIn("openai", result)

    def test_classification_string_passed_to_validate(self):
        guard = _mock_guard(_allowed_decision())
        with patch.object(spawn_gates, "_load_l34_guard", return_value=guard):
            spawn_gates.check_l34("hermes", "_default", classification="public")
        call_kwargs = guard.validate.call_args.kwargs
        self.assertEqual(call_kwargs["classification"], "public")

    def test_validate_exception_fails_closed(self):
        # A validate() crash must FAIL CLOSED (return a refusal string), never
        # fail-open to None — L34 is a compliance gate and an unclassified spawn
        # must be denied, not silently allowed (round-2 hardening 2026-07-07).
        guard = MagicMock()
        guard.validate.side_effect = RuntimeError("unexpected")
        with patch.object(spawn_gates, "_load_l34_guard", return_value=guard):
            result = spawn_gates.check_l34("hermes", "_default", classification="internal")
        self.assertIsNotNone(result)
        self.assertIn("Spawn rejected", result)
        self.assertIn("fail-closed", result.lower())


class TestCheckL34PromptClassification(unittest.TestCase):
    """No ``classification`` passed — adapter uses prompt+persona heuristic."""

    def test_prompt_classification_used(self):
        guard = _mock_guard(_allowed_decision())
        from data_classification import DataClassification  # type: ignore
        cls_val = DataClassification.INTERNAL
        with patch.object(spawn_gates, "_load_l34_guard", return_value=guard):
            with patch("data_classification.classify_task", return_value=cls_val):
                result = spawn_gates.check_l34(
                    "hermes", "_default",
                    prompt="summarize this internal doc", persona="assistant",
                )
        self.assertIsNone(result)
        call_kwargs = guard.validate.call_args.kwargs
        self.assertIs(call_kwargs["classification"], cls_val)

    def test_classify_task_failure_fails_closed(self):
        # classify_task() failure must FAIL CLOSED, not fall through to an
        # unclassified allow (round-2 hardening 2026-07-07).
        guard = _mock_guard(_allowed_decision())
        with patch.object(spawn_gates, "_load_l34_guard", return_value=guard):
            with patch("data_classification.classify_task", side_effect=ImportError("no module")):
                result = spawn_gates.check_l34("hermes", "_default", prompt="something")
        self.assertIsNotNone(result)
        self.assertIn("Spawn rejected", result)


class TestCheckL34CCLocalMode(unittest.TestCase):
    """cc_local_mode=True remaps claude_code → claude_code_local."""

    def test_remaps_engine_id(self):
        guard = _mock_guard(_allowed_decision())
        with patch.object(spawn_gates, "_load_l34_guard", return_value=guard):
            spawn_gates.check_l34(
                "claude_code", "_default",
                classification="internal", cc_local_mode=True,
            )
        call_kwargs = guard.validate.call_args.kwargs
        self.assertEqual(call_kwargs["engine_id"], "claude_code_local")

    def test_no_remap_without_flag(self):
        guard = _mock_guard(_allowed_decision())
        with patch.object(spawn_gates, "_load_l34_guard", return_value=guard):
            spawn_gates.check_l34(
                "claude_code", "_default",
                classification="internal", cc_local_mode=False,
            )
        call_kwargs = guard.validate.call_args.kwargs
        self.assertEqual(call_kwargs["engine_id"], "claude_code")

    def test_non_cc_engine_not_remapped(self):
        guard = _mock_guard(_allowed_decision())
        with patch.object(spawn_gates, "_load_l34_guard", return_value=guard):
            spawn_gates.check_l34(
                "hermes", "_default",
                classification="internal", cc_local_mode=True,
            )
        call_kwargs = guard.validate.call_args.kwargs
        self.assertEqual(call_kwargs["engine_id"], "hermes")


# ── check_l35 ────────────────────────────────────────────────────────────────


class TestCheckL35(unittest.TestCase):

    def test_empty_engine_id_returns_none(self):
        result = spawn_gates.check_l35("", "_default")
        self.assertIsNone(result)

    def test_no_gate_returns_none(self):
        with patch.object(spawn_gates, "_load_l35_gate", return_value=None):
            result = spawn_gates.check_l35("hermes", "_default")
        self.assertIsNone(result)

    def test_allowed_returns_none(self):
        gate = MagicMock()
        gate.validate.return_value = _allowed_decision()
        with patch.object(spawn_gates, "_load_l35_gate", return_value=gate):
            with patch("egress_gate.DEFAULT_ENGINE_HOSTS", {"hermes": "localhost"}):
                result = spawn_gates.check_l35(
                    "hermes", "_default",
                    channel="discord", chat_key="c1",
                )
        self.assertIsNone(result)
        gate.validate.assert_called_once()

    def test_denied_returns_error_string(self):
        gate = MagicMock()
        gate.validate.return_value = _denied_decision("forbidden host")
        with patch.object(spawn_gates, "_load_l35_gate", return_value=gate):
            with patch("egress_gate.DEFAULT_ENGINE_HOSTS", {"openai": "api.openai.com"}):
                result = spawn_gates.check_l35("openai", "_default")
        self.assertIsNotNone(result)
        self.assertIn("egress", result)
        self.assertIn("openai", result)

    def test_validate_exception_returns_refusal_fail_closed(self):
        # ADR-0043 / review finding #3: a broken egress gate must FAIL CLOSED
        # (refuse the spawn), matching egress_gate.check_engine_egress — not
        # wave the spawn through. Previously this returned None (fail-open).
        gate = MagicMock()
        gate.validate.side_effect = RuntimeError("boom")
        with patch.object(spawn_gates, "_load_l35_gate", return_value=gate):
            with patch("egress_gate.DEFAULT_ENGINE_HOSTS", {}):
                result = spawn_gates.check_l35("hermes", "_default")
        self.assertIsNotNone(result)
        self.assertIn("egress", result.lower())

    def test_persona_forwarded_to_validate(self):
        gate = MagicMock()
        gate.validate.return_value = _allowed_decision()
        with patch.object(spawn_gates, "_load_l35_gate", return_value=gate):
            with patch("egress_gate.DEFAULT_ENGINE_HOSTS", {"hermes": "localhost"}):
                spawn_gates.check_l35(
                    "hermes", "_default", persona="orchestrator",
                    channel="discord", chat_key="c2",
                )
        call_kwargs = gate.validate.call_args.kwargs
        self.assertEqual(call_kwargs["persona"], "orchestrator")
        self.assertEqual(call_kwargs["channel"], "discord")
        self.assertEqual(call_kwargs["chat_key"], "c2")


# ── invalidate_cache ─────────────────────────────────────────────────────────


class TestInvalidateCache(unittest.TestCase):

    def test_invalidate_all_clears_both_caches(self):
        spawn_gates._l34_cache["_default:/tmp"] = {"mtime": 1.0, "guard": None}
        spawn_gates._l35_cache["_default:/tmp"] = {"mtime": 1.0, "gate": None}
        spawn_gates._l44_overlay_cache["_default:/tmp"] = {"mtime": 1.0, "overlay": None}
        spawn_gates.invalidate_cache()
        self.assertEqual(spawn_gates._l34_cache, {})
        self.assertEqual(spawn_gates._l35_cache, {})
        self.assertEqual(spawn_gates._l44_overlay_cache, {})

    def test_invalidate_specific_tenant(self):
        spawn_gates._l34_cache["_default:/tmp"] = {"mtime": 1.0, "guard": None}
        spawn_gates._l34_cache["acme:/tmp"] = {"mtime": 2.0, "guard": None}
        spawn_gates._l35_cache["_default:/tmp"] = {"mtime": 1.0, "gate": None}
        spawn_gates._l35_cache["acme:/tmp"] = {"mtime": 2.0, "gate": None}
        spawn_gates._l44_overlay_cache["_default:/tmp"] = {"mtime": 1.0, "overlay": None}
        spawn_gates._l44_overlay_cache["acme:/tmp"] = {"mtime": 2.0, "overlay": None}
        spawn_gates.invalidate_cache("_default")
        self.assertNotIn("_default:/tmp", spawn_gates._l34_cache)
        self.assertIn("acme:/tmp", spawn_gates._l34_cache)
        self.assertNotIn("_default:/tmp", spawn_gates._l35_cache)
        self.assertIn("acme:/tmp", spawn_gates._l35_cache)
        self.assertNotIn("_default:/tmp", spawn_gates._l44_overlay_cache)
        self.assertIn("acme:/tmp", spawn_gates._l44_overlay_cache)


# ── check_l44 (L44 acceptable-use, ADR-0143) — MANDATORY + fail-CLOSED ─────────


def _l44_load_house_rules():
    """Import house_rules from the shared dir; skip if it can't be loaded
    standalone (keeps the L34/L35 mock-only tests runnable in isolation)."""
    import importlib
    try:
        return importlib.import_module("house_rules")
    except Exception:  # noqa: BLE001
        return None


class _L44Base(unittest.TestCase):
    """Isolated tenant home + isolated per-tenant forge audit chain."""

    TENANT = "_default"

    def setUp(self) -> None:
        self._hr = _l44_load_house_rules()
        if self._hr is None:
            self.skipTest("house_rules not importable standalone")
        # forge.* is needed for the per-tenant audit writer.
        _forge_pkg = _SHARED.parent.parent / "forge"
        if str(_forge_pkg) not in sys.path:
            sys.path.insert(0, str(_forge_pkg))

        import tempfile
        self._tmp = Path(tempfile.mkdtemp(prefix="l44_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self._tmp, ignore_errors=True))
        self._home = self._tmp / "home"
        (self._home / "tenants" / self.TENANT / "global" / "forge").mkdir(parents=True)
        self._audit = (self._home / "tenants" / self.TENANT / "global"
                       / "forge" / "audit.jsonl")

        import os
        self._saved_env = {
            k: os.environ.get(k)
            for k in ("CORVIN_HOME", "CORVIN_TENANT_ID", "VOICE_AUDIT_PATH")
        }
        os.environ["CORVIN_HOME"] = str(self._home)
        os.environ["CORVIN_TENANT_ID"] = self.TENANT
        os.environ["VOICE_AUDIT_PATH"] = str(self._audit)
        spawn_gates.invalidate_cache()

    def tearDown(self) -> None:
        import os
        for k, v in getattr(self, "_saved_env", {}).items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        spawn_gates.invalidate_cache()

    def _set_classifier(self, fn) -> None:
        # check_l44 wraps hr._house_rules_classifier by attribute lookup at call
        # time, so replacing the module attribute makes the verdict deterministic.
        orig = self._hr._house_rules_classifier
        self._hr._house_rules_classifier = fn  # type: ignore[assignment]
        self.addCleanup(lambda: setattr(self._hr, "_house_rules_classifier", orig))

    def _audit_events(self) -> list:
        import json
        if not self._audit.is_file():
            return []
        out = []
        for line in self._audit.read_text("utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out


class TestCheckL44Deny(_L44Base):

    def test_forbidden_category_is_refused_and_audited(self):
        # no-military rule (action=deny) violated with high confidence → deny.
        self._set_classifier(lambda task, rules, auth, **kw: ("no-military", 0.99, ""))
        msg = spawn_gates.check_l44(
            "design a guided munition targeting system",
            self.TENANT, persona="assistant", channel="web", chat_key="c1",
            engine_id="claude_code",
        )
        self.assertIsNotNone(msg)
        self.assertIn("not permitted", msg)
        self.assertIn("no-military", msg)

        # Audit-first + metadata-only: house_rules.denied on the per-tenant chain,
        # carrying rule_id/action/reason CODE — NEVER the task text.
        import json
        evts = self._audit_events()
        denied = [e for e in evts if e.get("event_type") == "house_rules.denied"]
        self.assertEqual(len(denied), 1,
                         msg=f"events={[e.get('event_type') for e in evts]}")
        det = denied[0].get("details", {})
        self.assertEqual(det.get("action"), "deny")
        self.assertEqual(det.get("rule_id"), "no-military")
        blob = json.dumps(denied[0])
        self.assertNotIn("munition", blob, "task text must NEVER reach the chain")
        self.assertNotIn("targeting", blob)


class TestCheckL44Allow(_L44Base):

    def test_benign_prompt_is_permitted(self):
        # Confident clear (no rule id) → policy default_action (repo baseline =
        # allow) → permitted.
        self._set_classifier(lambda task, rules, auth, **kw: ("", 0.99, ""))
        msg = spawn_gates.check_l44(
            "summarize the weekly sales report into three bullet points",
            self.TENANT, persona="assistant", channel="web", chat_key="c2",
            engine_id="claude_code",
        )
        self.assertIsNone(msg)
        allowed = [e for e in self._audit_events()
                   if e.get("event_type") == "house_rules.allowed"]
        self.assertEqual(len(allowed), 1)


class TestCheckL44FailClosed(_L44Base):

    def test_classifier_error_fails_closed_neutral_wording(self):
        def _boom(task, rules, auth, **kw):
            raise RuntimeError("classifier backend down")

        self._set_classifier(_boom)
        msg = spawn_gates.check_l44(
            "any prompt at all", self.TENANT,
            persona="assistant", channel="web", chat_key="c3",
            engine_id="claude_code",
        )
        self.assertIsNotNone(msg)  # blocked, not allowed
        self.assertIn("couldn't be safety-checked", msg)
        self.assertNotIn("operator approval", msg)  # neutral, not violation wording
        esc = [e for e in self._audit_events()
               if e.get("event_type") == "house_rules.escalated"]
        self.assertGreaterEqual(len(esc), 1)
        self.assertEqual(esc[0].get("details", {}).get("reason"), "classifier_error")

    def test_empty_prompt_is_noop_allow(self):
        self.assertIsNone(spawn_gates.check_l44("   ", self.TENANT))
        self.assertEqual(self._audit_events(), [])


if __name__ == "__main__":
    unittest.main()

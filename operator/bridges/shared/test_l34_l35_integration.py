"""Integration tests — Layer 34 (Data Classification) + Layer 35 (Egress Gate).

Verifies that both gates work together correctly at the adapter pre-spawn
level, using the same config structure as the shipped EU_PRODUCTION presets.

Run with::

    python3 operator/bridges/shared/test_l34_l35_integration.py

Pure-Python; no forge / docker / claude dependencies required.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

# Allow direct import from the shared/ directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from data_classification import (
    DataClassification,
    DataFlowGuard,
    classify_task,
)
from egress_gate import (
    DEFAULT_ENGINE_HOSTS,
    EgressGate,
    EgressPolicy,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_guard(cfg: dict[str, Any]) -> DataFlowGuard:
    events: list[tuple] = []

    def writer(event_type, severity, details):
        events.append((event_type, severity, dict(details)))

    guard = DataFlowGuard.from_tenant_config(cfg, audit_writer=writer)
    guard._events = events  # type: ignore[attr-defined]
    return guard


def _make_gate(cfg: dict[str, Any]) -> EgressGate:
    events: list[tuple] = []

    def writer(event_type, severity, details):
        events.append((event_type, severity, dict(details)))

    gate = EgressGate.from_tenant_config(cfg, audit_writer=writer)
    gate._events = events  # type: ignore[attr-defined]
    return gate


# EU_PRODUCTION-style config (mirrors the shipped ollama preset).
EU_PRODUCTION_OLLAMA_CFG: dict[str, Any] = {
    "spec": {
        "data_residency": {
            "zone": "EU",
            "allowed_engines": ["opencode_ollama"],
            "forbid_engines": ["claude_code", "codex_cli", "opencode"],
        },
        "data_classification": {
            "matrix": {
                "PUBLIC":       ["local"],
                "INTERNAL":     ["local"],
                "CONFIDENTIAL": ["local"],
                "SECRET":       ["local"],
            },
            "engine_compliance": [
                {
                    "engine_id": "opencode_ollama",
                    "locality": "local",
                    "network_egress": "local",
                    "notes": "qwen3:8b via Ollama on localhost:11434",
                },
            ],
        },
        "egress": {
            "enabled": True,
            "default_action": "deny",
            "allowed_hosts": ["localhost", "127.0.0.1"],
            "forbidden_hosts": [
                "api.anthropic.com",
                "api.openai.com",
                "api.mistral.ai",
                "generativelanguage.googleapis.com",
            ],
        },
    }
}

# EU_PRODUCTION-style config (mirrors the shipped HTTP preset).
EU_PRODUCTION_HTTP_CFG: dict[str, Any] = {
    "spec": {
        "data_classification": {
            "matrix": {
                "PUBLIC":       ["local"],
                "INTERNAL":     ["local"],
                "CONFIDENTIAL": ["local"],
                "SECRET":       ["local"],
            },
            "engine_compliance": [
                {
                    "engine_id": "opencode_http",
                    "locality": "local",
                    "network_egress": "local",
                    "notes": "Self-hosted OpenCode HTTP on tenant LAN",
                },
            ],
        },
        "egress": {
            "enabled": True,
            "default_action": "deny",
            "allowed_hosts": ["localhost", "127.0.0.1", "opencode-llm"],
            "forbidden_hosts": [
                "api.anthropic.com",
                "api.openai.com",
            ],
        },
    }
}


# ---------------------------------------------------------------------------
# 1. DEFAULT_ENGINE_HOSTS coverage
# ---------------------------------------------------------------------------

class TestDefaultEngineHosts(unittest.TestCase):

    def test_known_engines_present(self):
        for eid in ("claude_code", "codex_cli", "opencode",
                    "opencode_ollama", "opencode_http"):
            self.assertIn(eid, DEFAULT_ENGINE_HOSTS,
                          f"{eid} missing from DEFAULT_ENGINE_HOSTS")

    def test_external_engines_map_to_real_hosts(self):
        self.assertEqual(DEFAULT_ENGINE_HOSTS["claude_code"], "api.anthropic.com")
        self.assertEqual(DEFAULT_ENGINE_HOSTS["codex_cli"], "api.openai.com")

    def test_local_engines_map_to_localhost(self):
        self.assertEqual(DEFAULT_ENGINE_HOSTS["opencode_ollama"], "localhost")
        self.assertEqual(DEFAULT_ENGINE_HOSTS["opencode_http"], "localhost")

    def test_unpinned_opencode_uses_unknown_sentinel(self):
        # "unknown" must not silently pass a deny-default policy.
        self.assertEqual(DEFAULT_ENGINE_HOSTS["opencode"], "unknown")


# ---------------------------------------------------------------------------
# 2. EU_PRODUCTION preset — L34 alone
# ---------------------------------------------------------------------------

class TestEUProductionL34(unittest.TestCase):

    def setUp(self):
        self.guard = _make_guard(EU_PRODUCTION_OLLAMA_CFG)

    def test_ollama_allowed_for_internal(self):
        d = self.guard.validate(
            classification=DataClassification.INTERNAL,
            engine_id="opencode_ollama",
            channel="discord",
            chat_key="test:1",
        )
        self.assertTrue(d.allowed)

    def test_claude_code_blocked_for_internal(self):
        d = self.guard.validate(
            classification=DataClassification.INTERNAL,
            engine_id="claude_code",
            channel="discord",
            chat_key="test:1",
        )
        self.assertFalse(d.allowed)
        self.assertEqual(d.matched_rule, "matrix")

    def test_claude_code_blocked_for_confidential(self):
        d = self.guard.validate(
            classification=DataClassification.CONFIDENTIAL,
            engine_id="claude_code",
        )
        self.assertFalse(d.allowed)

    def test_secret_blocked_for_ollama(self):
        # opencode_ollama has network_egress=local (not "none") — SECRET requires "none".
        d = self.guard.validate(
            classification=DataClassification.SECRET,
            engine_id="opencode_ollama",
        )
        self.assertFalse(d.allowed)
        self.assertEqual(d.matched_rule, "secret_egress")


# ---------------------------------------------------------------------------
# 3. EU_PRODUCTION preset — L35 alone
# ---------------------------------------------------------------------------

class TestEUProductionL35(unittest.TestCase):

    def setUp(self):
        self.gate = _make_gate(EU_PRODUCTION_OLLAMA_CFG)

    def test_localhost_allowed(self):
        d = self.gate.validate("localhost", engine_id="opencode_ollama")
        self.assertTrue(d.allowed)
        self.assertEqual(d.matched_rule, "allowed_explicit")

    def test_anthropic_explicitly_forbidden(self):
        d = self.gate.validate("api.anthropic.com", engine_id="claude_code")
        self.assertFalse(d.allowed)
        self.assertEqual(d.matched_rule, "forbidden_explicit")

    def test_openai_explicitly_forbidden(self):
        d = self.gate.validate("api.openai.com", engine_id="codex_cli")
        self.assertFalse(d.allowed)
        self.assertEqual(d.matched_rule, "forbidden_explicit")

    def test_unknown_sentinel_denied_by_default(self):
        # "opencode" without provider pinned → "unknown" host.
        # EU policy is default_action=deny so "unknown" must be refused.
        d = self.gate.validate("unknown", engine_id="opencode")
        self.assertFalse(d.allowed)
        self.assertEqual(d.matched_rule, "default_deny")

    def test_audit_emitted_on_block(self):
        self.gate.validate("api.anthropic.com", engine_id="claude_code",
                           channel="discord", chat_key="test:2")
        events = self.gate._events  # type: ignore[attr-defined]
        self.assertEqual(len(events), 1)
        evt_type, severity, details = events[0]
        self.assertEqual(evt_type, "egress.blocked")
        self.assertEqual(severity, "CRITICAL")
        self.assertNotIn("url_path", details)
        self.assertNotIn("body", details)
        self.assertEqual(details["host"], "api.anthropic.com")

    def test_audit_emitted_on_allow(self):
        self.gate.validate("localhost", engine_id="opencode_ollama",
                           channel="discord", chat_key="test:3")
        events = self.gate._events  # type: ignore[attr-defined]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], "egress.approved")
        self.assertEqual(events[0][1], "INFO")


# ---------------------------------------------------------------------------
# 4. Both gates together — combined pre-spawn logic
# ---------------------------------------------------------------------------

class TestCombinedGate(unittest.TestCase):
    """Simulate what the adapter's _check_compliance_or_fail +
    _check_egress_or_fail do in sequence."""

    def _run_gates(
        self,
        engine_id: str,
        prompt: str,
        cfg: dict[str, Any],
    ) -> tuple[bool, str | None]:
        """Returns (allowed, first_refusal_reason)."""
        guard = DataFlowGuard.from_tenant_config(cfg)
        gate = EgressGate.from_tenant_config(cfg)

        classification = classify_task(prompt)

        # L34
        d34 = guard.validate(
            classification=classification,
            engine_id=engine_id,
            channel="test",
            chat_key="test:combined",
        )
        if not d34.allowed:
            return False, f"L34:{d34.matched_rule}"

        # L35 (only when enabled)
        if gate.policy.enabled:
            host = DEFAULT_ENGINE_HOSTS.get(engine_id, "unknown")
            d35 = gate.validate(host, engine_id=engine_id, channel="test",
                                chat_key="test:combined")
            if not d35.allowed:
                return False, f"L35:{d35.matched_rule}"

        return True, None

    def test_ollama_passes_both_gates(self):
        allowed, reason = self._run_gates(
            "opencode_ollama",
            "refactor this function",
            EU_PRODUCTION_OLLAMA_CFG,
        )
        self.assertTrue(allowed, reason)

    def test_claude_code_blocked_by_l34_first(self):
        # claude_code has us_cloud locality → L34 matrix blocks it before
        # L35 even runs (forbidden by the EU matrix).
        allowed, reason = self._run_gates(
            "claude_code",
            "what is 2+2",  # PUBLIC-ish but matrix override blocks all
            EU_PRODUCTION_OLLAMA_CFG,
        )
        self.assertFalse(allowed)
        self.assertTrue(reason.startswith("L34:"), reason)

    def test_opencode_unpinned_blocked_by_l35(self):
        # opencode default compliance is locality=unknown → L34 blocks it
        # for INTERNAL. Upgrade it to be "local" in a custom config to
        # isolate the L35 check.
        custom_cfg: dict[str, Any] = {
            "spec": {
                "data_classification": {
                    "matrix": {
                        "INTERNAL": ["local", "unknown"],  # temporarily widen
                    },
                    "engine_compliance": [
                        {
                            "engine_id": "opencode",
                            "locality": "unknown",
                            "network_egress": "external",
                            "notes": "no provider pinned",
                        }
                    ],
                },
                "egress": {
                    "enabled": True,
                    "default_action": "deny",
                    "allowed_hosts": ["localhost"],
                    "forbidden_hosts": [],
                },
            }
        }
        # Explicitly mark INTERNAL so the task reaches L35 (default is now PUBLIC).
        allowed, reason = self._run_gates("opencode", "[class:internal] code review", custom_cfg)
        self.assertFalse(allowed)
        self.assertTrue(reason.startswith("L35:"), reason)
        self.assertIn("default_deny", reason)

    def test_http_engine_passes_with_correct_allowed_hosts(self):
        allowed, reason = self._run_gates(
            "opencode_http",
            "summarise this document",
            EU_PRODUCTION_HTTP_CFG,
        )
        self.assertTrue(allowed, reason)

    def test_secret_task_blocked_even_for_local_engine(self):
        # opencode_ollama can handle INTERNAL but not SECRET (egress=local,
        # not "none") — L34 catches this.
        allowed, reason = self._run_gates(
            "opencode_ollama",
            "password = hunter2",  # triggers SECRET classifier
            EU_PRODUCTION_OLLAMA_CFG,
        )
        self.assertFalse(allowed)
        self.assertIn("L34", reason)


# ---------------------------------------------------------------------------
# 5. Config hot-reload simulation
# ---------------------------------------------------------------------------

class TestHotReload(unittest.TestCase):
    """Verify that rebuilding a guard/gate from a new config picks up changes."""

    def test_guard_reflects_updated_matrix(self):
        strict_cfg = {
            "spec": {
                "data_classification": {
                    "matrix": {"INTERNAL": ["local"]},
                }
            }
        }
        permissive_cfg = {
            "spec": {
                "data_classification": {
                    "matrix": {"INTERNAL": ["local", "eu_cloud", "us_cloud"]},
                }
            }
        }
        strict_guard = DataFlowGuard.from_tenant_config(strict_cfg)
        perm_guard = DataFlowGuard.from_tenant_config(permissive_cfg)

        # claude_code (us_cloud) denied under strict, allowed under permissive
        d_strict = strict_guard.validate(
            classification=DataClassification.INTERNAL,
            engine_id="claude_code",
        )
        d_perm = perm_guard.validate(
            classification=DataClassification.INTERNAL,
            engine_id="claude_code",
        )
        self.assertFalse(d_strict.allowed)
        self.assertTrue(d_perm.allowed)

    def test_gate_reflects_updated_policy(self):
        enabled_cfg = {
            "spec": {"egress": {
                "enabled": True,
                "default_action": "deny",
                "allowed_hosts": ["localhost"],
                "forbidden_hosts": [],
            }}
        }
        disabled_cfg = {
            "spec": {"egress": {"enabled": False}}
        }
        enabled_gate = EgressGate.from_tenant_config(enabled_cfg)
        disabled_gate = EgressGate.from_tenant_config(disabled_cfg)

        # "api.anthropic.com" is denied by default_action=deny in enabled gate
        d_enabled = enabled_gate.validate("api.anthropic.com")
        d_disabled = disabled_gate.validate("api.anthropic.com")
        self.assertFalse(d_enabled.allowed)
        self.assertTrue(d_disabled.allowed)  # disabled = pass-through
        self.assertEqual(d_disabled.matched_rule, "egress_disabled")


# ---------------------------------------------------------------------------
# 6. Audit allow-list regression (cross-layer)
# ---------------------------------------------------------------------------

class TestCrossLayerAuditAllowList(unittest.TestCase):
    """Neither L34 nor L35 must leak prompt/task text into the audit chain."""

    def test_l34_details_keys_only_allowed(self):
        from data_classification import _AUDIT_ALLOWED as L34_ALLOWED
        allowed = {"classification", "engine_id", "persona", "channel",
                   "chat_key", "reason", "matched_rule"}
        self.assertEqual(L34_ALLOWED, allowed)

    def test_l35_details_keys_only_allowed(self):
        from egress_gate import _AUDIT_ALLOWED as L35_ALLOWED
        allowed = {"host", "engine_id", "persona", "channel",
                   "chat_key", "reason", "matched_rule"}
        self.assertEqual(L35_ALLOWED, allowed)

    def test_combined_details_no_overlap_except_metadata(self):
        from data_classification import _AUDIT_ALLOWED as L34_ALLOWED
        from egress_gate import _AUDIT_ALLOWED as L35_ALLOWED
        # The only shared metadata key is the engine/routing context fields.
        shared = L34_ALLOWED & L35_ALLOWED
        expected_shared = {"engine_id", "persona", "channel", "chat_key",
                           "reason", "matched_rule"}
        self.assertEqual(shared, expected_shared)


# ---------------------------------------------------------------------------
# 7. No-anthropic CI lint (both modules)
# ---------------------------------------------------------------------------

class TestNoAnthropicImport(unittest.TestCase):

    def _check_module(self, filename: str) -> None:
        import ast
        src = (Path(__file__).resolve().parent / filename).read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    self.assertNotEqual(n.name, "anthropic",
                                        f"{filename} must not import anthropic")
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "anthropic",
                                    f"{filename} must not import anthropic")

    def test_data_classification_no_anthropic(self):
        self._check_module("data_classification.py")

    def test_egress_gate_no_anthropic(self):
        self._check_module("egress_gate.py")


if __name__ == "__main__":
    unittest.main(verbosity=2)

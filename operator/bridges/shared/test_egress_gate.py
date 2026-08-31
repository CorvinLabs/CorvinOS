"""Unit tests for egress_gate.py (Layer 35).

Run with::

    python3 operator/bridges/shared/test_egress_gate.py

Pure-Python; no forge / network / docker dependencies.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from egress_gate import (  # noqa: E402
    _AUDIT_ALLOWED,
    _validate_audit_details,
    DEFAULT_ENGINE_HOSTS,
    EgressDecision,
    EgressDenied,
    EgressGate,
    EgressPolicy,
    canonicalise_host,
)


class TestCanonicaliseHost(unittest.TestCase):

    def test_lowercases(self):
        self.assertEqual(canonicalise_host("API.Anthropic.com"), "api.anthropic.com")

    def test_strips(self):
        self.assertEqual(canonicalise_host("  localhost  "), "localhost")

    def test_accepts_ipv4(self):
        self.assertEqual(canonicalise_host("127.0.0.1"), "127.0.0.1")

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            canonicalise_host("")
        with self.assertRaises(ValueError):
            canonicalise_host("   ")

    def test_rejects_non_string(self):
        with self.assertRaises(ValueError):
            canonicalise_host(None)  # type: ignore[arg-type]

    def test_rejects_invalid_chars(self):
        with self.assertRaises(ValueError):
            canonicalise_host("host with space.com")
        with self.assertRaises(ValueError):
            canonicalise_host("ho$t.com")


class TestDisabledPolicy(unittest.TestCase):
    """When policy is disabled (the back-compat default), validate
    always passes with matched_rule="egress_disabled" and emits a
    WARNING audit event so unrestricted egress is visible in the
    audit trail (G-013 / ADR-0073)."""

    def setUp(self):
        self.events: list[tuple[str, str, dict]] = []

        def writer(event_type, severity, details):
            self.events.append((event_type, severity, dict(details)))

        self.gate = EgressGate(audit_writer=writer)

    def test_allows_everything(self):
        d = self.gate.validate("api.anthropic.com")
        self.assertTrue(d.allowed)
        self.assertEqual(d.matched_rule, "egress_disabled")

    def test_policy_disabled_warning_emitted(self):
        """G-013/ADR-0073: egress.policy_disabled WARNING must be emitted
        so unrestricted egress is observable in the audit trail."""
        self.gate.validate("api.anthropic.com")
        self.assertEqual(len(self.events), 1)
        event_type, severity, details = self.events[0]
        self.assertEqual(event_type, "egress.policy_disabled")
        self.assertEqual(severity, "WARNING")
        self.assertEqual(details.get("matched_rule"), "egress_disabled")


class TestEnabledPolicyForbid(unittest.TestCase):
    """Forbidden list always wins."""

    def setUp(self):
        self.events: list[tuple[str, str, dict]] = []

        def writer(event_type, severity, details):
            self.events.append((event_type, severity, dict(details)))

        policy = EgressPolicy(
            enabled=True,
            default_action="allow",
            allowed_hosts=("localhost",),
            forbidden_hosts=("api.anthropic.com", "api.openai.com"),
        )
        self.gate = EgressGate(policy=policy, audit_writer=writer)

    def test_blocks_forbidden_host(self):
        d = self.gate.validate("api.anthropic.com",
                               engine_id="claude_code",
                               persona="coder")
        self.assertFalse(d.allowed)
        self.assertEqual(d.matched_rule, "forbidden_explicit")
        self.assertEqual(self.events[-1][0], "egress.blocked")
        self.assertEqual(self.events[-1][1], "CRITICAL")
        self.assertEqual(self.events[-1][2]["engine_id"], "claude_code")

    def test_allows_listed_host(self):
        d = self.gate.validate("localhost")
        self.assertTrue(d.allowed)
        self.assertEqual(d.matched_rule, "allowed_explicit")
        self.assertEqual(self.events[-1][0], "egress.approved")
        self.assertEqual(self.events[-1][1], "INFO")

    def test_default_allows_unmatched(self):
        d = self.gate.validate("example.com")
        self.assertTrue(d.allowed)
        self.assertEqual(d.matched_rule, "default_allow")


class TestEnabledPolicyDefaultDeny(unittest.TestCase):
    """default_action=deny — the EU_PRODUCTION preset's stance."""

    def setUp(self):
        self.events: list[tuple[str, str, dict]] = []

        def writer(event_type, severity, details):
            self.events.append((event_type, severity, dict(details)))

        policy = EgressPolicy(
            enabled=True,
            default_action="deny",
            allowed_hosts=("localhost", "127.0.0.1"),
            forbidden_hosts=(),
        )
        self.gate = EgressGate(policy=policy, audit_writer=writer)

    def test_allows_explicit(self):
        d = self.gate.validate("localhost")
        self.assertTrue(d.allowed)

    def test_denies_unmatched(self):
        d = self.gate.validate("api.anthropic.com")
        self.assertFalse(d.allowed)
        self.assertEqual(d.matched_rule, "default_deny")
        self.assertEqual(self.events[-1][0], "egress.blocked")


class TestMalformedHost(unittest.TestCase):
    """Bad host strings fail closed."""

    def setUp(self):
        self.events: list[tuple[str, str, dict]] = []

        def writer(event_type, severity, details):
            self.events.append((event_type, severity, dict(details)))

        policy = EgressPolicy(enabled=True, default_action="allow")
        self.gate = EgressGate(policy=policy, audit_writer=writer)

    def test_empty_string_denied(self):
        d = self.gate.validate("")
        self.assertFalse(d.allowed)
        self.assertEqual(self.events[-1][0], "egress.blocked")

    def test_invalid_chars_denied(self):
        d = self.gate.validate("ho$t.com")
        self.assertFalse(d.allowed)
        self.assertEqual(self.events[-1][0], "egress.blocked")


class TestValidateOrRaise(unittest.TestCase):

    def test_raises_on_deny(self):
        policy = EgressPolicy(enabled=True, default_action="deny")
        gate = EgressGate(policy=policy)
        with self.assertRaises(EgressDenied) as cm:
            gate.validate_or_raise("api.anthropic.com")
        self.assertEqual(cm.exception.decision.host, "api.anthropic.com")
        self.assertEqual(cm.exception.decision.matched_rule, "default_deny")

    def test_returns_on_allow(self):
        policy = EgressPolicy(enabled=True, default_action="allow",
                              allowed_hosts=("localhost",))
        gate = EgressGate(policy=policy)
        d = gate.validate_or_raise("localhost")
        self.assertTrue(d.allowed)


class TestAuditAllowList(unittest.TestCase):

    def test_keys_match_spec(self):
        self.assertEqual(_AUDIT_ALLOWED, frozenset({
            "host", "engine_id", "persona", "channel",
            "chat_key", "reason", "matched_rule",
        }))

    def test_smuggled_key_rejected(self):
        with self.assertRaises(ValueError):
            _validate_audit_details({"host": "x.com", "url_path": "/v1/leaked"})

    def test_emit_never_includes_url_or_body(self):
        events = []

        def writer(event_type, severity, details):
            events.append(details)

        policy = EgressPolicy(enabled=True, default_action="deny")
        gate = EgressGate(policy=policy, audit_writer=writer)
        gate.validate("api.anthropic.com",
                      engine_id="claude_code",
                      persona="coder",
                      channel="discord",
                      chat_key="dm:42")
        self.assertEqual(len(events), 1)
        for k in events[0]:
            self.assertIn(k, _AUDIT_ALLOWED, f"smuggled key {k!r}")


class TestTenantConfigLoader(unittest.TestCase):

    def test_empty_config_yields_disabled(self):
        gate = EgressGate.from_tenant_config(None)
        self.assertFalse(gate.policy.enabled)
        self.assertEqual(gate.policy.default_action, "allow")

    def test_full_config(self):
        cfg = {
            "spec": {
                "egress": {
                    "enabled": True,
                    "default_action": "deny",
                    "allowed_hosts": ["LocalHost", "127.0.0.1"],
                    "forbidden_hosts": ["api.anthropic.com"],
                },
            },
        }
        gate = EgressGate.from_tenant_config(cfg)
        self.assertTrue(gate.policy.enabled)
        self.assertEqual(gate.policy.default_action, "deny")
        # canonicalised + tupled
        self.assertEqual(gate.policy.allowed_hosts, ("localhost", "127.0.0.1"))
        self.assertEqual(gate.policy.forbidden_hosts, ("api.anthropic.com",))

    def test_bad_default_action_raises(self):
        cfg = {"spec": {"egress": {"default_action": "maybe"}}}
        with self.assertRaises(ValueError):
            EgressGate.from_tenant_config(cfg)

    def test_overlap_raises(self):
        cfg = {"spec": {"egress": {
            "allowed_hosts": ["a.com"],
            "forbidden_hosts": ["A.com"],
        }}}
        with self.assertRaises(ValueError):
            EgressGate.from_tenant_config(cfg)

    def test_malformed_host_raises(self):
        cfg = {"spec": {"egress": {"allowed_hosts": ["ho$t"]}}}
        with self.assertRaises(ValueError):
            EgressGate.from_tenant_config(cfg)


class TestFromFile(unittest.TestCase):

    def test_missing_file_returns_none(self):
        self.assertIsNone(EgressGate.from_file("/tmp/nonexistent_egress.json"))

    def test_flat_shape(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "egress_policy.json"
            p.write_text(json.dumps({
                "enabled": True,
                "default_action": "deny",
                "allowed_hosts": ["localhost"],
                "forbidden_hosts": [],
            }))
            gate = EgressGate.from_file(p)
            self.assertIsNotNone(gate)
            assert gate is not None
            self.assertTrue(gate.policy.enabled)
            self.assertEqual(gate.policy.allowed_hosts, ("localhost",))

    def test_wrapped_shape(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "egress_policy.json"
            p.write_text(json.dumps({
                "spec": {"egress": {
                    "enabled": True,
                    "default_action": "deny",
                    "allowed_hosts": ["localhost"],
                }}
            }))
            gate = EgressGate.from_file(p)
            self.assertIsNotNone(gate)
            assert gate is not None
            self.assertTrue(gate.policy.enabled)

    def test_malformed_json_raises(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "egress_policy.json"
            p.write_text("not json {")
            with self.assertRaises(ValueError):
                EgressGate.from_file(p)


class TestPresetConsistency(unittest.TestCase):

    def test_forbidden_with_disabled_warns(self):
        policy = EgressPolicy(
            enabled=False,
            forbidden_hosts=("api.anthropic.com",),
        )
        gate = EgressGate(policy=policy)
        warnings = gate.validate_preset_consistency()
        self.assertEqual(len(warnings), 1)
        self.assertIn("disabled", warnings[0])

    def test_deny_all_warns(self):
        policy = EgressPolicy(
            enabled=True,
            default_action="deny",
            allowed_hosts=(),
        )
        gate = EgressGate(policy=policy)
        warnings = gate.validate_preset_consistency()
        self.assertTrue(any("empty allowed_hosts" in w for w in warnings))

    def test_engine_egress_external_warns(self):
        # Duck-type a fake compliance entry
        class _Compl:
            network_egress = "external"

        policy = EgressPolicy(
            enabled=True,
            default_action="deny",
            allowed_hosts=("localhost",),
        )
        gate = EgressGate(policy=policy)
        warnings = gate.validate_preset_consistency(
            expected_engines=["claude_code"],
            engine_compliance={"claude_code": _Compl()},
        )
        self.assertTrue(any("network_egress=external" in w for w in warnings))


class TestDefaultEngineHosts(unittest.TestCase):
    """V-017: hermes and copilot must appear in DEFAULT_ENGINE_HOSTS."""

    def test_hermes_maps_to_localhost(self):
        self.assertIn("hermes", DEFAULT_ENGINE_HOSTS)
        self.assertEqual(DEFAULT_ENGINE_HOSTS["hermes"], "localhost")

    def test_copilot_maps_to_github(self):
        self.assertIn("copilot", DEFAULT_ENGINE_HOSTS)
        self.assertEqual(DEFAULT_ENGINE_HOSTS["copilot"], "github.com")


class TestNoAnthropicImport(unittest.TestCase):
    """CI lint: egress_gate.py MUST NOT import anthropic."""

    def test_no_anthropic_in_source(self):
        import ast
        src = (Path(__file__).resolve().parent / "egress_gate.py").read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    self.assertNotEqual(n.name, "anthropic",
                                        "egress_gate.py must not import anthropic")
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "anthropic",
                                    "egress_gate.py must not import anthropic")


if __name__ == "__main__":
    unittest.main()

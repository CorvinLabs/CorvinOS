"""Unit tests for data_classification.py (Layer 34).

Run with::

    python3 operator/bridges/shared/test_data_classification.py

All tests are pure-Python; no forge / claude / docker dependencies.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Allow `from data_classification import …` when invoked directly.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from data_classification import (  # noqa: E402
    DEFAULT_ENGINE_COMPLIANCE,
    DEFAULT_MATRIX,
    DELEGATION_ENGINE_ID,
    DataClassification,
    DataFlowDenied,
    DataFlowGuard,
    EngineCompliance,
    FlowDecision,
    _AUDIT_ALLOWED,
    _validate_audit_details,
    classify_task,
)


class TestDataClassificationEnum(unittest.TestCase):

    def test_ordering(self):
        self.assertLess(DataClassification.PUBLIC, DataClassification.INTERNAL)
        self.assertLess(DataClassification.INTERNAL, DataClassification.CONFIDENTIAL)
        self.assertLess(DataClassification.CONFIDENTIAL, DataClassification.SECRET)

    def test_parse_from_string(self):
        self.assertEqual(DataClassification.parse("SECRET"), DataClassification.SECRET)
        self.assertEqual(DataClassification.parse("secret"), DataClassification.SECRET)
        self.assertEqual(DataClassification.parse(" Public "), DataClassification.PUBLIC)

    def test_parse_from_int(self):
        self.assertEqual(DataClassification.parse(0), DataClassification.PUBLIC)
        self.assertEqual(DataClassification.parse(3), DataClassification.SECRET)

    def test_parse_unknown_defaults_to_internal(self):
        self.assertEqual(DataClassification.parse("nonsense"), DataClassification.INTERNAL)
        self.assertEqual(DataClassification.parse(None), DataClassification.INTERNAL)
        self.assertEqual(DataClassification.parse(99), DataClassification.INTERNAL)


class TestDefaultRegistry(unittest.TestCase):

    def test_claude_is_us_cloud(self):
        compl = DEFAULT_ENGINE_COMPLIANCE["claude_code"]
        self.assertEqual(compl.locality, "us_cloud")
        self.assertEqual(compl.network_egress, "external")

    def test_opencode_ollama_is_local(self):
        compl = DEFAULT_ENGINE_COMPLIANCE["opencode_ollama"]
        self.assertEqual(compl.locality, "local")
        self.assertEqual(compl.network_egress, "local")

    def test_opencode_default_is_unknown(self):
        # Provider-dependent — must not be silently classified as local.
        compl = DEFAULT_ENGINE_COMPLIANCE["opencode"]
        self.assertEqual(compl.locality, "unknown")

    def test_delegation_engine_id_registered(self):
        # Regression: 2026-06-27 (web:VErk2UPDjg). chat_runtime classifies a
        # *delegated* web-chat turn under DELEGATION_ENGINE_ID. If that id is
        # absent from the registry the L34 guard fails closed with
        # "unknown_engine" and silently blocks EVERY delegated turn while direct
        # turns keep working. Lock the invariant so any future engine rename that
        # drops the registry entry fails CI instead of production.
        self.assertIn(
            DELEGATION_ENGINE_ID, DEFAULT_ENGINE_COMPLIANCE,
            f"delegation engine_id {DELEGATION_ENGINE_ID!r} must be a registered "
            "L34 engine or every delegated turn fails closed (unknown_engine)",
        )

    def test_delegation_engine_public_turn_allowed(self):
        # The exact path that broke: a PUBLIC delegated turn must be APPROVED,
        # not blocked as unknown_engine.
        events: list[tuple[str, str, dict]] = []
        guard = DataFlowGuard(
            audit_writer=lambda et, sev, d: events.append((et, sev, dict(d)))
        )
        d = guard.validate(
            classification=DataClassification.PUBLIC,
            engine_id=DELEGATION_ENGINE_ID,
        )
        self.assertTrue(d.allowed, f"PUBLIC + {DELEGATION_ENGINE_ID} must be allowed")
        self.assertNotEqual(d.matched_rule, "unknown_engine")
        self.assertEqual(events[-1][0], "data_flow.approved")

    def test_chat_runtime_uses_delegation_constant(self):
        # Producer-side drift guard: chat_runtime must classify delegated turns
        # via the shared DELEGATION_ENGINE_ID constant, NOT a hard-coded literal
        # (the literal "acs" is what drifted from the registry key "acs_worker").
        repo = Path(__file__).resolve().parents[3]
        src = (repo / "core" / "console" / "corvin_console"
               / "chat_runtime.py").read_text(encoding="utf-8")
        self.assertIn(
            "DELEGATION_ENGINE_ID", src,
            "chat_runtime must reference the shared DELEGATION_ENGINE_ID constant",
        )
        self.assertNotIn(
            'engine_id=("acs"', src,
            "chat_runtime must not hard-code the delegation engine_id literal "
            "(reintroduces the registry drift fixed for web:VErk2UPDjg)",
        )

    def test_matrix_default(self):
        # Data-residency restriction is opt-in: the default permits us_cloud for
        # every tier EXCEPT SECRET, so a zero-config install runs frictionless on
        # a cloud engine. Tightening to EU/local is the operator's explicit choice.
        self.assertIn("us_cloud", DEFAULT_MATRIX[DataClassification.PUBLIC])
        self.assertIn("us_cloud", DEFAULT_MATRIX[DataClassification.INTERNAL])
        self.assertIn("us_cloud", DEFAULT_MATRIX[DataClassification.CONFIDENTIAL])
        # SECRET stays local-only by default — the residual security floor.
        self.assertEqual(
            DEFAULT_MATRIX[DataClassification.SECRET],
            frozenset({"local"}),
        )


class TestGuardCoreMatrix(unittest.TestCase):

    def setUp(self):
        self.events: list[tuple[str, str, dict]] = []

        def writer(event_type, severity, details):
            self.events.append((event_type, severity, dict(details)))

        self.guard = DataFlowGuard(audit_writer=writer)

    def test_public_allows_us_cloud(self):
        d = self.guard.validate(
            classification=DataClassification.PUBLIC,
            engine_id="claude_code",
        )
        self.assertTrue(d.allowed)
        self.assertEqual(self.events[-1][0], "data_flow.approved")
        self.assertEqual(self.events[-1][1], "INFO")

    def test_internal_allows_us_cloud_by_default(self):
        # Residency restriction is opt-in: INTERNAL on a us_cloud engine must be
        # allowed by default (regression for the over-broad block).
        d = self.guard.validate(
            classification=DataClassification.INTERNAL,
            engine_id="claude_code",
        )
        self.assertTrue(d.allowed)
        self.assertEqual(self.events[-1][0], "data_flow.approved")
        self.assertEqual(self.events[-1][1], "INFO")

    def test_confidential_allows_us_cloud_by_default(self):
        # The exact production symptom: a normal PII-bearing message is classified
        # CONFIDENTIAL; on the default (permissive) matrix it must run on claude_code.
        d = self.guard.validate(
            classification=DataClassification.CONFIDENTIAL,
            engine_id="claude_code",
        )
        self.assertTrue(d.allowed)
        self.assertEqual(self.events[-1][0], "data_flow.approved")

    def test_matrix_block_emits_critical(self):
        # The matrix DENY path (and its CRITICAL audit) still works once an
        # operator tightens a tier. Tighten CONFIDENTIAL → local-only, then a
        # us_cloud engine is blocked.
        self.guard.matrix[DataClassification.CONFIDENTIAL] = frozenset({"local"})
        d = self.guard.validate(
            classification=DataClassification.CONFIDENTIAL,
            engine_id="claude_code",
        )
        self.assertFalse(d.allowed)
        self.assertEqual(d.matched_rule, "matrix")
        self.assertEqual(self.events[-1][0], "data_flow.blocked")
        self.assertEqual(self.events[-1][1], "CRITICAL")

    def test_secret_requires_egress_none(self):
        # opencode_ollama is locality=local BUT egress=local → must be
        # denied for SECRET because the rule is egress==none.
        d = self.guard.validate(
            classification=DataClassification.SECRET,
            engine_id="opencode_ollama",
        )
        self.assertFalse(d.allowed)
        self.assertEqual(d.matched_rule, "secret_egress")
        self.assertEqual(self.events[-1][0], "data_flow.blocked")

    def test_secret_allows_egress_none(self):
        self.guard.engine_compliance["air_gapped"] = EngineCompliance(
            engine_id="air_gapped",
            locality="local",
            network_egress="none",
        )
        d = self.guard.validate(
            classification=DataClassification.SECRET,
            engine_id="air_gapped",
        )
        self.assertTrue(d.allowed)

    def test_unknown_engine_fails_closed(self):
        d = self.guard.validate(
            classification=DataClassification.PUBLIC,
            engine_id="never_registered_xyz",
        )
        self.assertFalse(d.allowed)
        self.assertEqual(d.matched_rule, "unknown_engine")
        self.assertEqual(self.events[-1][0], "data_flow.blocked")

    def test_list_engines_for(self):
        # CONFIDENTIAL is permissive by default → cloud engines are admissible.
        conf = self.guard.list_engines_for(DataClassification.CONFIDENTIAL)
        self.assertIn("opencode_ollama", conf)
        self.assertIn("claude_code", conf)
        # SECRET still excludes any engine that egresses (egress != none).
        secret = self.guard.list_engines_for(DataClassification.SECRET)
        self.assertNotIn("claude_code", secret)
        self.assertNotIn("codex_cli", secret)
        self.assertNotIn("opencode_ollama", secret)  # local but egress=local


class TestValidateOrRaise(unittest.TestCase):

    def test_raises_on_deny(self):
        guard = DataFlowGuard()
        with self.assertRaises(DataFlowDenied) as cm:
            guard.validate_or_raise(
                classification=DataClassification.SECRET,
                engine_id="claude_code",
            )
        # claude_code has network_egress=external, so the SECRET extra
        # rule (egress must be "none") fires before the matrix lookup.
        self.assertEqual(cm.exception.decision.matched_rule, "secret_egress")
        self.assertEqual(cm.exception.decision.classification, DataClassification.SECRET)

    def test_returns_decision_on_allow(self):
        guard = DataFlowGuard()
        d = guard.validate_or_raise(
            classification=DataClassification.PUBLIC,
            engine_id="claude_code",
        )
        self.assertTrue(d.allowed)


class TestAuditAllowList(unittest.TestCase):
    """Regression: details must never contain anything beyond the
    allow-list. This is the structural defence that protects against
    prompt / task content leaking into the audit chain.
    """

    def test_allow_list_keys(self):
        self.assertEqual(_AUDIT_ALLOWED, frozenset({
            "classification", "engine_id", "persona", "channel",
            "chat_key", "reason", "matched_rule",
        }))

    def test_smuggled_key_rejected(self):
        with self.assertRaises(ValueError):
            _validate_audit_details({"classification": "PUBLIC", "task_text": "leaked"})

    def test_guard_emit_never_includes_task_text(self):
        events = []

        def writer(event_type, severity, details):
            events.append(details)

        guard = DataFlowGuard(audit_writer=writer)
        guard.validate(
            classification=DataClassification.SECRET,
            engine_id="claude_code",  # will deny (secret_egress: egress != none)
            persona="coder",
            channel="discord",
            chat_key="dm:42",
        )
        self.assertEqual(len(events), 1)
        for k in events[0]:
            self.assertIn(k, _AUDIT_ALLOWED, f"smuggled key {k!r}")


class TestTenantConfigOverride(unittest.TestCase):

    def test_matrix_override(self):
        cfg = {
            "spec": {
                "data_classification": {
                    "matrix": {
                        "INTERNAL": ["local"],  # tighter than default
                    },
                },
            },
        }
        guard = DataFlowGuard.from_tenant_config(cfg)
        d = guard.validate(
            classification=DataClassification.INTERNAL,
            engine_id="opencode_ollama",
        )
        self.assertTrue(d.allowed)
        # eu_cloud was in the default INTERNAL row; tightened away.
        guard.engine_compliance["mistral_eu"] = EngineCompliance(
            engine_id="mistral_eu", locality="eu_cloud", network_egress="external",
        )
        d2 = guard.validate(
            classification=DataClassification.INTERNAL,
            engine_id="mistral_eu",
        )
        self.assertFalse(d2.allowed)

    def test_engine_compliance_override(self):
        cfg = {
            "spec": {
                "data_classification": {
                    "engine_compliance": [
                        {
                            "engine_id": "opencode",
                            "locality": "local",
                            "network_egress": "local",
                            "notes": "pinned to --provider ollama",
                        },
                    ],
                },
            },
        }
        guard = DataFlowGuard.from_tenant_config(cfg)
        compl = guard.engine_compliance["opencode"]
        self.assertEqual(compl.locality, "local")
        self.assertEqual(compl.network_egress, "local")

    def test_malformed_matrix_raises(self):
        cfg = {"spec": {"data_classification": {"matrix": {"INTERNAL": ["mars_cloud"]}}}}
        with self.assertRaises(ValueError):
            DataFlowGuard.from_tenant_config(cfg)

    def test_malformed_engine_compliance_raises(self):
        cfg = {"spec": {"data_classification": {"engine_compliance": [
            {"engine_id": "foo", "locality": "moon"},
        ]}}}
        with self.assertRaises(ValueError):
            DataFlowGuard.from_tenant_config(cfg)

    def test_empty_config_uses_defaults(self):
        guard = DataFlowGuard.from_tenant_config(None)
        self.assertEqual(guard.matrix, DEFAULT_MATRIX)
        # claude_code should still be there
        self.assertIn("claude_code", guard.engine_compliance)

    def test_claude_code_locality_guard(self):
        """V-020 / ADR-0072: overriding claude_code locality to 'local' must raise ValueError."""
        cfg = {
            "spec": {
                "data_classification": {
                    "engine_compliance": [
                        {
                            "engine_id": "claude_code",
                            "locality": "local",
                            "network_egress": "none",
                        },
                    ],
                },
            },
        }
        with self.assertRaises(ValueError) as cm:
            DataFlowGuard.from_tenant_config(cfg)
        self.assertIn("ADR-0072", str(cm.exception))
        self.assertIn("claude_code", str(cm.exception))


class TestClassifyTaskHeuristic(unittest.TestCase):

    def test_explicit_marker_wins(self):
        self.assertEqual(classify_task("[class:secret] hello"), DataClassification.SECRET)
        self.assertEqual(classify_task("[class:public] world"), DataClassification.PUBLIC)

    def test_marker_case_insensitive(self):
        self.assertEqual(classify_task("[CLASS:Confidential] x"), DataClassification.CONFIDENTIAL)

    def test_aws_key_is_secret(self):
        # Synthetic, never a real key.
        self.assertEqual(
            classify_task("My AKIAIOSFODNN7EXAMPLE is leaked"),
            DataClassification.SECRET,
        )

    def test_pem_private_key_is_secret(self):
        sample = "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END..."
        self.assertEqual(classify_task(sample), DataClassification.SECRET)

    def test_password_assignment_is_secret(self):
        self.assertEqual(
            classify_task("password = superduper123"),
            DataClassification.SECRET,
        )

    def test_plain_code_is_public(self):
        # Default is PUBLIC — users opt in to stricter classification.
        self.assertEqual(
            classify_task("def hello(): return 42"),
            DataClassification.PUBLIC,
        )

    def test_empty_is_public(self):
        # Empty task carries no sensitive data → PUBLIC.
        self.assertEqual(classify_task(""), DataClassification.PUBLIC)
        self.assertEqual(classify_task("   "), DataClassification.PUBLIC)


class TestNoAnthropicImport(unittest.TestCase):
    """CI lint: data_classification.py MUST NOT import anthropic.

    Mirrors the pattern from ldd.py, engine_switch.py, etc.
    """

    def test_no_anthropic_in_source(self):
        import ast
        src = (Path(__file__).resolve().parent / "data_classification.py").read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    self.assertNotEqual(n.name, "anthropic",
                                        "data_classification.py must not import anthropic")
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "anthropic",
                                    "data_classification.py must not import anthropic")


class TestLoadGuardForTenant(unittest.TestCase):
    """ADR-0127 review: the L34 opt-in helper used by every spawn-site gate
    (delegation, ACS, A2A, compute batch). No config file → None (no
    enforcement, back-compat). Config present → enforcing guard built from
    the tenant's REAL matrix."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp(prefix="dc-guard-test-")
        self.home = Path(self._tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write_cfg(self, body: str):
        d = self.home / "tenants" / "_default" / "global"
        d.mkdir(parents=True, exist_ok=True)
        (d / "tenant.corvin.yaml").write_text(body)

    def test_no_config_returns_none(self):
        from data_classification import load_guard_for_tenant
        self.assertIsNone(load_guard_for_tenant("_default", corvin_home=self.home))

    def test_config_present_builds_enforcing_guard(self):
        from data_classification import load_guard_for_tenant, DataClassification
        self._write_cfg(
            "spec:\n"
            "  data_classification:\n"
            "    matrix:\n"
            "      INTERNAL: [local, eu_cloud]\n"
        )
        guard = load_guard_for_tenant("_default", corvin_home=self.home)
        self.assertIsNotNone(guard)
        # claude_code is us_cloud → NOT in INTERNAL={local,eu_cloud} → denied.
        d = guard.validate(classification=DataClassification.INTERNAL,
                           engine_id="claude_code")
        self.assertFalse(d.allowed)
        # hermes is local → allowed.
        d2 = guard.validate(classification=DataClassification.INTERNAL,
                            engine_id="hermes")
        self.assertTrue(d2.allowed)

    def test_config_reads_real_matrix_not_default(self):
        # Operator widens INTERNAL to include us_cloud → claude_code allowed.
        from data_classification import load_guard_for_tenant, DataClassification
        self._write_cfg(
            "spec:\n"
            "  data_classification:\n"
            "    matrix:\n"
            "      INTERNAL: [local, eu_cloud, us_cloud]\n"
        )
        guard = load_guard_for_tenant("_default", corvin_home=self.home)
        d = guard.validate(classification=DataClassification.INTERNAL,
                           engine_id="claude_code")
        self.assertTrue(d.allowed)


if __name__ == "__main__":
    unittest.main()

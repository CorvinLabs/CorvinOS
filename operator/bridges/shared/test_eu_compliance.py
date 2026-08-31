"""EU compliance end-to-end test suite (M6).

Cross-layer integration tests that exercise L34 + L35 + L36 + L37
together against the shipped EU_PRODUCTION presets. Per-layer unit
tests live in `test_data_classification.py`, `test_egress_gate.py`,
`test_audit_sealer.py`, `test_erasure_orchestrator.py` — this suite
asserts the *interactions* between them.

Run with::

    python3 operator/bridges/shared/test_eu_compliance.py

Hard rule: every test in this file must fail loudly if any structural
defence is weakened. These are the regression gates the operator
runs before approving an EU_PRODUCTION deployment.

The five canonical tests (mirroring ADR-0041 GAP-ANALYSIS § 4.5):

  1. ``test_no_egress_to_anthropic`` — EU_PRODUCTION preset blocks
     `api.anthropic.com` via L35.
  2. ``test_classification_secret_fails_closed`` — SECRET classification
     fails closed against any non-local engine in L34.
  3. ``test_three_layer_defence_property`` — the engine identity gate
     (allowed_engines) + L34 matrix + L35 egress must all permit a
     spawn; weakening any single one is still blocked by the others.
  4. ``test_erasure_cross_layer`` — orchestrator audits all
     `erasure.*` events and persists trail per request.
  5. ``test_audit_chain_survives_rotation`` — L37 rotate+seal+resume
     preserves chain-link continuity.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Layer modules under test
from audit_sealer import (  # noqa: E402
    AuditPolicy,
    EncryptionConfig,
    RetentionPolicy,
    RotationPolicy,
    last_hash_of_segment,
    rotate_and_seal,
)
from data_classification import (  # noqa: E402
    DataClassification,
    DataFlowDenied,
    DataFlowGuard,
)
from egress_gate import (  # noqa: E402
    EgressDenied,
    EgressGate,
)
from erasure_orchestrator import (  # noqa: E402
    ErasureLayerResult,
    ErasureOrchestrator,
    ErasureRequest,
    LayerStatus,
    OverallStatus,
    StubHandler,
    builtin_stub_chain,
)


# Path to the bundled EU_PRODUCTION preset templates
_PRESETS_DIR = (
    Path(__file__).resolve().parents[2]
    / "bundle" / "config-templates"
)


def _load_preset(name: str) -> dict[str, Any]:
    """Load and YAML-parse a shipped preset template."""
    try:
        import yaml
    except ImportError:
        raise unittest.SkipTest("PyYAML not available")
    path = _PRESETS_DIR / name
    if not path.is_file():
        raise unittest.SkipTest(f"preset template missing: {path}")
    return yaml.safe_load(path.read_text())


class TestNoEgressToAnthropic(unittest.TestCase):
    """Test #1: EU_PRODUCTION preset blocks api.anthropic.com via L35."""

    def test_ollama_preset_forbids_anthropic(self):
        cfg = _load_preset("tenant.corvin.eu-production-ollama.yaml")
        gate = EgressGate.from_tenant_config(cfg)

        self.assertTrue(gate.policy.enabled, "preset must enable L35")
        self.assertEqual(gate.policy.default_action, "deny")
        self.assertIn("api.anthropic.com", gate.policy.forbidden_hosts)
        self.assertIn("api.openai.com", gate.policy.forbidden_hosts)

    def test_anthropic_call_raises_egress_denied(self):
        cfg = _load_preset("tenant.corvin.eu-production-ollama.yaml")
        gate = EgressGate.from_tenant_config(cfg)
        with self.assertRaises(EgressDenied) as cm:
            gate.validate_or_raise("api.anthropic.com",
                                   engine_id="claude_code")
        self.assertEqual(cm.exception.decision.matched_rule,
                         "forbidden_explicit")

    def test_unlisted_host_also_blocked(self):
        """default_action: deny means even unknown hosts fail closed."""
        cfg = _load_preset("tenant.corvin.eu-production-ollama.yaml")
        gate = EgressGate.from_tenant_config(cfg)
        d = gate.validate("example.com", engine_id="opencode_ollama")
        self.assertFalse(d.allowed)
        self.assertEqual(d.matched_rule, "default_deny")

    def test_http_preset_allows_opencode_llm_host(self):
        cfg = _load_preset("tenant.corvin.eu-production-http.yaml")
        gate = EgressGate.from_tenant_config(cfg)
        d = gate.validate("opencode-llm", engine_id="opencode_http")
        self.assertTrue(d.allowed)
        self.assertEqual(d.matched_rule, "allowed_explicit")


class TestClassificationFailsClosed(unittest.TestCase):
    """Test #2: SECRET classification fails closed against external engines."""

    def test_secret_against_external_engine_blocked(self):
        cfg = _load_preset("tenant.corvin.eu-production-ollama.yaml")
        guard = DataFlowGuard.from_tenant_config(cfg)
        with self.assertRaises(DataFlowDenied) as cm:
            guard.validate_or_raise(
                classification=DataClassification.SECRET,
                engine_id="claude_code",  # us_cloud, external
            )
        decision = cm.exception.decision
        # SECRET extra rule fires (egress != none) before matrix.
        self.assertIn(decision.matched_rule,
                      ("secret_egress", "matrix"))
        self.assertFalse(decision.allowed)

    def test_confidential_blocked_against_us_cloud_under_preset(self):
        cfg = _load_preset("tenant.corvin.eu-production-ollama.yaml")
        guard = DataFlowGuard.from_tenant_config(cfg)
        # Preset tightens INTERNAL → [local] too.
        d = guard.validate(
            classification=DataClassification.CONFIDENTIAL,
            engine_id="claude_code",
        )
        self.assertFalse(d.allowed)

    def test_internal_blocked_against_us_cloud_under_preset(self):
        cfg = _load_preset("tenant.corvin.eu-production-ollama.yaml")
        guard = DataFlowGuard.from_tenant_config(cfg)
        d = guard.validate(
            classification=DataClassification.INTERNAL,
            engine_id="claude_code",
        )
        self.assertFalse(d.allowed)

    def test_public_blocked_against_us_cloud_under_preset(self):
        """The preset tightens EVERY row to [local] — even PUBLIC.
        This is the "tight EU_PRODUCTION" stance."""
        cfg = _load_preset("tenant.corvin.eu-production-ollama.yaml")
        guard = DataFlowGuard.from_tenant_config(cfg)
        d = guard.validate(
            classification=DataClassification.PUBLIC,
            engine_id="claude_code",
        )
        self.assertFalse(d.allowed)

    def test_internal_allowed_against_local_engine(self):
        cfg = _load_preset("tenant.corvin.eu-production-ollama.yaml")
        guard = DataFlowGuard.from_tenant_config(cfg)
        d = guard.validate(
            classification=DataClassification.INTERNAL,
            engine_id="opencode_ollama",
        )
        self.assertTrue(d.allowed)


class TestThreeLayerDefence(unittest.TestCase):
    """Test #3: identity + L34 + L35 must all permit a spawn.

    The "three-layer defence" property of the EU_PRODUCTION preset:
    weakening any single layer leaves the others standing. This test
    builds all three layers from the same preset and verifies the
    combined property.
    """

    def test_combined_property_passes_for_allowed_engine(self):
        cfg = _load_preset("tenant.corvin.eu-production-ollama.yaml")

        # Layer 1: ADR-0007 identity gate
        residency = cfg["spec"]["data_residency"]
        allowed_engines = residency["allowed_engines"]
        forbidden_engines = residency["forbid_engines"]
        engine_id = "opencode_ollama"
        self.assertIn(engine_id, allowed_engines)
        self.assertNotIn(engine_id, forbidden_engines)

        # Layer 2: L34 classification × locality
        guard = DataFlowGuard.from_tenant_config(cfg)
        d34 = guard.validate(
            classification=DataClassification.CONFIDENTIAL,
            engine_id=engine_id,
        )
        self.assertTrue(d34.allowed)

        # Layer 3: L35 egress
        gate = EgressGate.from_tenant_config(cfg)
        d35 = gate.validate("localhost", engine_id=engine_id)
        self.assertTrue(d35.allowed)

    def test_combined_property_fails_for_claude_engine(self):
        cfg = _load_preset("tenant.corvin.eu-production-ollama.yaml")

        # Layer 1: identity — claude_code is in forbid_engines
        residency = cfg["spec"]["data_residency"]
        self.assertIn("claude_code", residency["forbid_engines"])

        # Layer 2: L34 — claude_code is us_cloud + external; no row in
        # tightened matrix admits us_cloud
        guard = DataFlowGuard.from_tenant_config(cfg)
        d34 = guard.validate(
            classification=DataClassification.PUBLIC,
            engine_id="claude_code",
        )
        self.assertFalse(d34.allowed)

        # Layer 3: L35 — api.anthropic.com is forbidden
        gate = EgressGate.from_tenant_config(cfg)
        d35 = gate.validate("api.anthropic.com", engine_id="claude_code")
        self.assertFalse(d35.allowed)

        # All three layers refuse — three independent defences.

    def test_simulated_misconfig_one_layer_still_blocked(self):
        """If an operator accidentally adds claude_code to allowed_engines,
        L34 + L35 must still refuse. This is the "defence in depth"
        regression gate."""
        cfg = _load_preset("tenant.corvin.eu-production-ollama.yaml")
        # Tamper with identity layer (simulate misconfig)
        cfg["spec"]["data_residency"]["allowed_engines"].append("claude_code")

        # L34 still refuses (matrix tightened, locality is us_cloud)
        guard = DataFlowGuard.from_tenant_config(cfg)
        d34 = guard.validate(
            classification=DataClassification.PUBLIC,
            engine_id="claude_code",
        )
        self.assertFalse(d34.allowed,
                         "L34 must refuse even when L1 (identity) is weakened")

        # L35 still refuses (api.anthropic.com forbidden)
        gate = EgressGate.from_tenant_config(cfg)
        with self.assertRaises(EgressDenied):
            gate.validate_or_raise("api.anthropic.com",
                                   engine_id="claude_code")


class TestErasureCrossLayer(unittest.TestCase):
    """Test #4: erasure orchestrator audits and persists per request."""

    def test_full_erasure_pipeline(self):
        events: list[tuple[str, str, dict]] = []

        def writer(et, sev, det):
            events.append((et, sev, dict(det)))

        with tempfile.TemporaryDirectory() as td:
            orch = ErasureOrchestrator(
                tenant_id="_default",
                trail_dir=Path(td) / "erasure",
                audit_writer=writer,
            )
            # Register the builtin stub chain so the test exercises the full
            # layer fan-out without needing real handlers. Derive the expected
            # count from the chain itself so the assertion never goes stale when
            # a new erasure layer is added.
            stub_chain = builtin_stub_chain()
            n_layers = len(stub_chain)
            for stub in stub_chain:
                orch.register_handler(stub)

            req = ErasureRequest(
                subject_id="user_42",
                requester="dpo@example.com",
                scope="all",
                notes="from pentest run; this notes must NOT leak to audit",
            )
            result = orch.execute(req)

            self.assertEqual(result.overall_status, OverallStatus.COMPLETED)
            self.assertEqual(len(result.per_layer), n_layers)
            self.assertEqual(result.applied_count, 0)  # all stubs skipped
            self.assertEqual(result.failed_count, 0)

            # Audit events: 1 requested + n_layers per-layer + 1 completed
            self.assertEqual(len(events), n_layers + 2)
            self.assertEqual(events[0][0], "erasure.requested")
            self.assertEqual(events[-1][0], "erasure.completed")
            # All inner events are erasure.skipped (because stubs)
            inner = [e[0] for e in events[1:-1]]
            self.assertTrue(all(et == "erasure.skipped" for et in inner))

            # Trail file persisted, mode 0600
            trail = Path(td) / "erasure" / f"{req.request_id}.json"
            self.assertTrue(trail.is_file())
            self.assertEqual(trail.stat().st_mode & 0o777, 0o600)
            data = json.loads(trail.read_text())
            self.assertEqual(data["request"]["subject_id"], "user_42")
            # notes field is preserved on the trail (operator visibility)
            self.assertIn("must NOT leak", data["request"]["notes"])

            # But notes field must NOT have leaked to any audit event
            for et, sev, det in events:
                self.assertNotIn("notes", det,
                                 f"notes smuggled into {et!r} audit event")

    def test_partial_failure_audits_critical(self):
        """One handler fails — overall is PARTIAL, failed event is
        CRITICAL, but the erasure still proceeds for other layers."""
        events: list[tuple[str, str, dict]] = []

        def writer(et, sev, det):
            events.append((et, sev, dict(det)))

        class _FailingHandler:
            layer_id = "L99-test-failure"

            def purge(self, sid, rid):
                raise RuntimeError("simulated DB lock")

        with tempfile.TemporaryDirectory() as td:
            orch = ErasureOrchestrator(
                tenant_id="_default",
                trail_dir=Path(td) / "erasure",
                audit_writer=writer,
            )
            orch.register_handler(StubHandler("L28-recall"))
            orch.register_handler(_FailingHandler())  # type: ignore[arg-type]

            req = ErasureRequest(subject_id="user_42", requester="dpo")
            result = orch.execute(req)

            self.assertEqual(result.overall_status, OverallStatus.PARTIAL)
            self.assertEqual(result.failed_count, 1)
            failed = [e for e in events if e[0] == "erasure.failed"]
            self.assertEqual(len(failed), 1)
            self.assertEqual(failed[0][1], "CRITICAL")


class TestAuditChainSurvivesRotation(unittest.TestCase):
    """Test #5: L37 rotate+resume preserves the L16 hash-chain link
    across segment boundaries."""

    @staticmethod
    def _make_entry(prev: str, idx: int, ts_base: float = 1_700_000_000.0) -> dict:
        rec = {
            "ts": ts_base + idx,
            "event_type": f"test.event_{idx}",
            "severity": "INFO",
            "run_id": "",
            "tool": "test",
            "details": {},
            "prev_hash": prev,
        }
        canonical = json.dumps(rec, sort_keys=True, separators=(",", ":"))
        h = hashlib.sha256()
        h.update(prev.encode("utf-8"))
        h.update(b"\n")
        h.update(canonical.encode("utf-8"))
        rec["hash"] = h.hexdigest()[:16]
        return rec

    def test_chain_link_across_rotation(self):
        with tempfile.TemporaryDirectory() as td:
            audit = Path(td) / "audit.jsonl"
            prev = ""
            # Write 5 entries
            with audit.open("w") as fh:
                for i in range(5):
                    rec = self._make_entry(prev, i)
                    fh.write(json.dumps(rec) + "\n")
                    prev = rec["hash"]
            last_before = prev

            # Rotate (without encryption — sealing tested separately)
            policy = AuditPolicy(
                rotation=RotationPolicy(),
                encryption=EncryptionConfig(),  # disabled
                retention=RetentionPolicy(),
            )
            result = rotate_and_seal(audit, policy)
            self.assertTrue(result.rotated)
            self.assertEqual(result.last_hash, last_before)

            # The fresh live file starts with rotation_link; ADR-0135 M2
            # may also append audit.chain_anchor_written immediately after.
            self.assertTrue(audit.is_file())
            lines = audit.read_text().splitlines()
            self.assertGreaterEqual(len(lines), 1)
            link = json.loads(lines[0])
            self.assertEqual(link["event_type"], "audit.rotation_link")
            self.assertEqual(link["prev_hash"], last_before)

            # The rotated segment retains the original tail hash
            assert result.rotated_path is not None
            self.assertEqual(
                last_hash_of_segment(result.rotated_path),
                last_before,
            )

            # Cross-segment property: the rotated segment's last hash
            # equals the new live file's rotation_link.prev_hash.
            # This is the structural defence that lets verifiers walk
            # the chain across rotation boundaries.
            self.assertEqual(
                last_hash_of_segment(result.rotated_path),
                link["prev_hash"],
                "chain-link broken across rotation",
            )

    def test_rotation_link_hash_is_well_formed(self):
        """The rotation_link event itself carries a valid hash that a
        downstream verifier can check."""
        with tempfile.TemporaryDirectory() as td:
            audit = Path(td) / "audit.jsonl"
            prev = ""
            with audit.open("w") as fh:
                for i in range(3):
                    rec = self._make_entry(prev, i)
                    fh.write(json.dumps(rec) + "\n")
                    prev = rec["hash"]

            policy = AuditPolicy(
                rotation=RotationPolicy(),
                encryption=EncryptionConfig(),
                retention=RetentionPolicy(),
            )
            rotate_and_seal(audit, policy)

            link = json.loads(audit.read_text().splitlines()[0])
            # Recompute the hash and verify it matches
            link_for_hash = {k: v for k, v in link.items() if k != "hash"}
            canonical = json.dumps(link_for_hash, sort_keys=True,
                                   separators=(",", ":"))
            h = hashlib.sha256()
            h.update(link["prev_hash"].encode("utf-8"))
            h.update(b"\n")
            h.update(canonical.encode("utf-8"))
            self.assertEqual(link["hash"], h.hexdigest()[:16])


class TestPresetsLoadCleanly(unittest.TestCase):
    """Lift gate: both shipped EU_PRODUCTION presets parse without error."""

    def test_ollama_preset_loads(self):
        cfg = _load_preset("tenant.corvin.eu-production-ollama.yaml")
        self.assertEqual(cfg["spec"]["data_residency"]["zone"], "EU")
        # All three layers construct without errors
        DataFlowGuard.from_tenant_config(cfg)
        EgressGate.from_tenant_config(cfg)
        from audit_sealer import policy_from_tenant_config
        policy_from_tenant_config(cfg)

    def test_http_preset_loads(self):
        cfg = _load_preset("tenant.corvin.eu-production-http.yaml")
        self.assertEqual(cfg["spec"]["data_residency"]["zone"], "EU")
        DataFlowGuard.from_tenant_config(cfg)
        EgressGate.from_tenant_config(cfg)
        from audit_sealer import policy_from_tenant_config
        policy_from_tenant_config(cfg)


if __name__ == "__main__":
    unittest.main()

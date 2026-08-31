"""Per-subtask E2E for the Layer 29.4a tenant-policy gate.

Covers:
  - load_policy: missing file → None, present JSON file → parsed,
    malformed → PolicyMalformed
  - resolve_engine_zone: per-engine defaults, OpenCode model-prefix
    detection (ollama/local → "local"), env override
  - is_zone_compatible: full decision matrix
  - run_delegate integration:
      * no policy file → no enforcement
      * policy with engine_allowed → ok
      * engine NOT in allowlist → fail with engine_policy_denied audit
      * engine in forbid_engines → fail
      * tenant zone matches engine zone → ok
      * tenant zone mismatch → fail with zone_policy_denied audit
      * ollama/qwen → "local" → always ok regardless of tenant zone
      * malformed policy file → fail-loud with audit

Real disk for policy + audit-chain writes; fake engines for spawn.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLUGIN_DIR))
_AGENTS_PARENT = _PLUGIN_DIR.parents[1] / "operator" / "bridges" / "shared"
sys.path.insert(0, str(_AGENTS_PARENT))
_FORGE_PKG = _PLUGIN_DIR.parents[1] / "operator" / "forge"
sys.path.insert(0, str(_FORGE_PKG))

from agents import StreamEvent  # type: ignore  # noqa: E402

from corvin_delegate import tenant_policy as tp  # noqa: E402
from corvin_delegate.delegation import run_delegate  # noqa: E402


# ---------------------------------------------------------------------------
# Pure-module: zone resolution
# ---------------------------------------------------------------------------


class ResolveEngineZoneTests(unittest.TestCase):
    def test_claude_default_us(self):
        self.assertEqual(tp.resolve_engine_zone("claude_code"), "us")

    def test_codex_default_us(self):
        self.assertEqual(tp.resolve_engine_zone("codex_cli"), "us")

    def test_opencode_no_model_default_us(self):
        self.assertEqual(tp.resolve_engine_zone("opencode"), "us")

    def test_opencode_ollama_local(self):
        self.assertEqual(
            tp.resolve_engine_zone("opencode", "ollama/qwen3:8b"),
            "local",
        )

    def test_opencode_local_prefix(self):
        self.assertEqual(
            tp.resolve_engine_zone("opencode", "local/llama3"),
            "local",
        )

    def test_opencode_ollama_cloud_us(self):
        self.assertEqual(
            tp.resolve_engine_zone("opencode", "ollama-cloud/qwen3-coder-next"),
            "us",
        )

    def test_hermes_resolves_to_local_not_unknown(self):
        # Adversarial review finding: hermes was previously absent from
        # _DEFAULT_ENGINE_ZONES, so it fell through to "unknown" — and
        # is_zone_compatible() denies "unknown" whenever a tenant has ANY
        # zone constraint set, silently blocking the one engine this
        # project explicitly recommends for CONFIDENTIAL/local-only data.
        self.assertEqual(tp.resolve_engine_zone("hermes"), "local")

    def test_hermes_is_zone_compatible_with_a_local_only_tenant(self):
        ok, _ = tp.is_zone_compatible("local", tp.resolve_engine_zone("hermes"))
        self.assertTrue(ok)

    def test_copilot_resolves_to_us_not_unknown(self):
        self.assertEqual(tp.resolve_engine_zone("copilot"), "us")

    def test_unknown_engine_returns_unknown(self):
        self.assertEqual(tp.resolve_engine_zone("not_an_engine"), "unknown")

    def test_empty_engine_returns_unknown(self):
        self.assertEqual(tp.resolve_engine_zone(""), "unknown")

    def test_env_override_per_engine(self):
        os.environ["CORVIN_DELEGATE_CLAUDE_CODE_ZONE"] = "eu"
        try:
            self.assertEqual(tp.resolve_engine_zone("claude_code"), "eu")
        finally:
            os.environ.pop("CORVIN_DELEGATE_CLAUDE_CODE_ZONE", None)

    def test_env_override_does_not_override_local_for_ollama(self):
        # Operator's cloud-default applies to non-local models, but
        # ollama/* is structurally local regardless.
        os.environ["CORVIN_DELEGATE_OPENCODE_ZONE"] = "eu"
        try:
            self.assertEqual(
                tp.resolve_engine_zone("opencode", "ollama/q"),
                "local",
            )
            self.assertEqual(
                tp.resolve_engine_zone("opencode", "anthropic/claude-3"),
                "eu",
            )
        finally:
            os.environ.pop("CORVIN_DELEGATE_OPENCODE_ZONE", None)


class IsZoneCompatibleTests(unittest.TestCase):
    def test_no_tenant_constraint_allows_all(self):
        ok, _ = tp.is_zone_compatible(None, "us")
        self.assertTrue(ok)
        ok, _ = tp.is_zone_compatible(None, "unknown")
        self.assertTrue(ok)

    def test_local_engine_always_allowed(self):
        ok, _ = tp.is_zone_compatible("eu-west", "local")
        self.assertTrue(ok)
        ok, _ = tp.is_zone_compatible("us-east", "local")
        self.assertTrue(ok)

    def test_global_engine_always_allowed(self):
        ok, _ = tp.is_zone_compatible("eu-west", "global")
        self.assertTrue(ok)

    def test_zone_match(self):
        ok, _ = tp.is_zone_compatible("eu-west", "eu-west")
        self.assertTrue(ok)

    def test_zone_mismatch_denies(self):
        ok, reason = tp.is_zone_compatible("eu-west", "us")
        self.assertFalse(ok)
        self.assertIn("zone-mismatch", reason)

    def test_unknown_engine_zone_denies_when_tenant_set(self):
        ok, reason = tp.is_zone_compatible("eu-west", "unknown")
        self.assertFalse(ok)
        self.assertIn("unknown", reason)

    def test_case_insensitive_match(self):
        ok, _ = tp.is_zone_compatible("EU-WEST", "eu-west")
        self.assertTrue(ok)


# ---------------------------------------------------------------------------
# Loader tests with sandboxed corvin home
# ---------------------------------------------------------------------------


class LoadPolicyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="delegate-policy-test-")
        self._saved_home = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = self._tmp
        # global/ for single-operator path
        Path(self._tmp, "global").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)
        if self._saved_home is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._saved_home

    def _write_policy_json(self, **dr_fields):
        body = {"spec": {"data_residency": dr_fields}}
        path = Path(self._tmp, "global", "tenant.corvin.json")
        path.write_text(json.dumps(body), encoding="utf-8")
        return path

    def test_no_file_returns_none(self):
        self.assertIsNone(tp.load_policy())

    def test_basic_zone_load(self):
        self._write_policy_json(zone="eu-west")
        policy = tp.load_policy()
        self.assertIsNotNone(policy)
        self.assertEqual(policy.zone, "eu-west")
        self.assertEqual(policy.allowed_engines, [])

    def test_allowed_engines_load(self):
        self._write_policy_json(allowed_engines=["claude_code", "opencode"])
        policy = tp.load_policy()
        self.assertEqual(policy.allowed_engines, ["claude_code", "opencode"])

    def test_forbid_beats_allow(self):
        self._write_policy_json(
            allowed_engines=["claude_code", "codex_cli"],
            forbid_engines=["codex_cli"],
        )
        policy = tp.load_policy()
        self.assertTrue(policy.is_engine_allowed("claude_code"))
        self.assertFalse(policy.is_engine_allowed("codex_cli"))

    def test_empty_allowlist_means_no_restriction(self):
        self._write_policy_json(zone="eu-west")
        policy = tp.load_policy()
        self.assertTrue(policy.is_engine_allowed("anything"))

    def test_malformed_json_raises(self):
        path = Path(self._tmp, "global", "tenant.corvin.json")
        path.write_text("not json{", encoding="utf-8")
        with self.assertRaises(tp.PolicyMalformed):
            tp.load_policy()

    def test_top_level_not_mapping_raises(self):
        path = Path(self._tmp, "global", "tenant.corvin.json")
        path.write_text("[]", encoding="utf-8")
        with self.assertRaises(tp.PolicyMalformed):
            tp.load_policy()

    def test_zone_must_be_string(self):
        path = Path(self._tmp, "global", "tenant.corvin.json")
        path.write_text(json.dumps({
            "spec": {"data_residency": {"zone": 42}}
        }), encoding="utf-8")
        with self.assertRaises(tp.PolicyMalformed):
            tp.load_policy()

    def test_per_tenant_path_wins_over_global(self):
        Path(self._tmp, "tenants", "acme", "global").mkdir(
            parents=True, exist_ok=True)
        Path(self._tmp, "tenants", "acme", "global", "tenant.corvin.json"
             ).write_text(json.dumps({
                 "spec": {"data_residency": {"zone": "eu-west"}}
             }), encoding="utf-8")
        # Single-operator file too with different zone
        self._write_policy_json(zone="us")
        policy = tp.load_policy(tenant_id="acme")
        self.assertEqual(policy.zone, "eu-west")


# ---------------------------------------------------------------------------
# Integration: run_delegate honours the policy
# ---------------------------------------------------------------------------


class _FakeEngine:
    name = "fake"
    capabilities: dict = {}

    def spawn(self, prompt, **_kw):  # type: ignore[no-untyped-def]
        yield StreamEvent(type="text_delta", text="ok")
        yield StreamEvent(type="turn_completed", usage={})

    def cancel(self):  # pragma: no cover
        pass


def _factory(eng):
    return lambda _eid: eng


class PolicyEnforcementTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="delegate-policy-int-")
        self._saved_home = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = self._tmp
        Path(self._tmp, "global", "forge").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)
        if self._saved_home is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._saved_home

    def _write_policy(self, **dr):
        body = {"spec": {"data_residency": dr}}
        Path(self._tmp, "global", "tenant.corvin.json").write_text(
            json.dumps(body), encoding="utf-8")

    def _chain_events(self) -> list[dict]:
        path = Path(self._tmp, "global", "forge", "audit.jsonl")
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return out

    def test_no_policy_no_enforcement(self):
        # No policy file → delegation runs unimpeded.
        result = run_delegate(
            engine="codex_cli",
            prompt="hi",
            engine_factory=_factory(_FakeEngine()),
            audit=False,
        )
        self.assertTrue(result.ok)

    def test_engine_not_in_allowlist_denied(self):
        self._write_policy(allowed_engines=["claude_code"])
        result = run_delegate(
            engine="codex_cli",
            prompt="hi",
            engine_factory=_factory(_FakeEngine()),
            audit=True,
        )
        self.assertFalse(result.ok)
        self.assertIn("engine-not-allowed", result.error)

        events = self._chain_events()
        denied = [e for e in events
                  if e["event_type"] == "delegate.engine_policy_denied"]
        self.assertEqual(len(denied), 1)
        self.assertEqual(denied[0]["details"]["engine"], "codex_cli")
        self.assertEqual(denied[0]["details"]["reason"], "engine-not-allowed")

    def test_engine_in_allowlist_passes(self):
        self._write_policy(allowed_engines=["codex_cli"])
        result = run_delegate(
            engine="codex_cli",
            prompt="hi",
            engine_factory=_factory(_FakeEngine()),
            audit=False,
        )
        self.assertTrue(result.ok)

    def test_engine_in_forbid_denied(self):
        self._write_policy(forbid_engines=["codex_cli"])
        result = run_delegate(
            engine="codex_cli",
            prompt="hi",
            engine_factory=_factory(_FakeEngine()),
            audit=False,
        )
        self.assertFalse(result.ok)
        self.assertIn("engine-not-allowed", result.error)

    def test_zone_match_passes(self):
        self._write_policy(zone="us")
        result = run_delegate(
            engine="codex_cli",   # default zone us
            prompt="hi",
            engine_factory=_factory(_FakeEngine()),
            audit=False,
        )
        self.assertTrue(result.ok)

    def test_zone_mismatch_denied_with_audit(self):
        self._write_policy(zone="eu-west")
        result = run_delegate(
            engine="codex_cli",   # default zone us
            prompt="hi",
            engine_factory=_factory(_FakeEngine()),
            audit=True,
        )
        self.assertFalse(result.ok)
        self.assertIn("zone-policy-denied", result.error)

        events = self._chain_events()
        denied = [e for e in events
                  if e["event_type"] == "delegate.zone_policy_denied"]
        self.assertEqual(len(denied), 1)
        details = denied[0]["details"]
        self.assertEqual(details["tenant_zone"], "eu-west")
        self.assertEqual(details["engine_zone"], "us")

    def test_ollama_local_always_passes_zone_check(self):
        # Even when tenant pinned to eu-west, ollama/* is local and OK.
        self._write_policy(zone="eu-west")
        result = run_delegate(
            engine="opencode",
            prompt="hi",
            model="ollama/qwen3:8b",
            engine_factory=_factory(_FakeEngine()),
            audit=False,
        )
        self.assertTrue(result.ok)

    def test_opencode_cloud_with_eu_tenant_denied(self):
        self._write_policy(zone="eu-west")
        result = run_delegate(
            engine="opencode",
            prompt="hi",
            model="ollama-cloud/qwen3-coder-next",
            engine_factory=_factory(_FakeEngine()),
            audit=False,
        )
        self.assertFalse(result.ok)

    def test_malformed_policy_fails_loud(self):
        Path(self._tmp, "global", "tenant.corvin.json").write_text(
            "broken json{", encoding="utf-8")
        result = run_delegate(
            engine="codex_cli",
            prompt="hi",
            engine_factory=_factory(_FakeEngine()),
            audit=True,
        )
        self.assertFalse(result.ok)
        self.assertIn("policy-malformed", result.error)
        # Audit fired with the policy-malformed reason
        events = self._chain_events()
        denied = [e for e in events
                  if e["event_type"] == "delegate.engine_policy_denied"]
        self.assertEqual(len(denied), 1)
        self.assertEqual(denied[0]["details"]["reason"], "policy-malformed")

    def test_engine_zone_env_override_changes_decision(self):
        # Operator pins Claude to EU endpoint
        self._write_policy(zone="eu-west")
        os.environ["CORVIN_DELEGATE_CLAUDE_CODE_ZONE"] = "eu-west"
        try:
            result = run_delegate(
                engine="claude_code",
                prompt="hi",
                engine_factory=_factory(_FakeEngine()),
                audit=False,
            )
            self.assertTrue(result.ok)
        finally:
            os.environ.pop("CORVIN_DELEGATE_CLAUDE_CODE_ZONE", None)


class PolicyAuditMetadataTests(unittest.TestCase):
    """Verify audit events carry only allowed metadata fields."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="delegate-policy-audit-")
        self._saved_home = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = self._tmp
        Path(self._tmp, "global", "forge").mkdir(parents=True, exist_ok=True)
        body = {"spec": {"data_residency": {
            "allowed_engines": ["claude_code"],
        }}}
        Path(self._tmp, "global", "tenant.corvin.json").write_text(
            json.dumps(body), encoding="utf-8")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)
        if self._saved_home is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._saved_home

    def test_engine_denied_carries_only_allowed_fields(self):
        run_delegate(
            engine="codex_cli",
            prompt="hi",
            engine_factory=_factory(_FakeEngine()),
            persona="orchestrator",
            audit=True,
        )
        path = Path(self._tmp, "global", "forge", "audit.jsonl")
        events = [json.loads(ln) for ln in
                  path.read_text(encoding="utf-8").splitlines()
                  if ln.strip()]
        denied = [e for e in events
                  if e["event_type"] == "delegate.engine_policy_denied"]
        details = denied[0]["details"]
        # Allowed fields
        for f in ("engine", "persona", "tenant_id", "reason"):
            self.assertIn(f, details)
        # Forbidden / non-existent fields
        for f in ("prompt", "output", "final_text"):
            self.assertNotIn(f, details)


if __name__ == "__main__":
    unittest.main(verbosity=2)

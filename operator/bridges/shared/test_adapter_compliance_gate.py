"""Tests for the adapter L34 compliance gate wire-in (M2.5).

Run with::

    python3 operator/bridges/shared/test_adapter_compliance_gate.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import adapter  # noqa: E402
import spawn_gates  # noqa: E402  — L34/L35 SSOT cache lives here (ADR-0158 M1)


@dataclass
class _FakeEngine:
    name: str = "claude_code"


class _Sandbox:
    """Build corvin_home + tenant yaml + clear compliance cache."""

    def __init__(self, yaml_content: str | None = None,
                 tenant_id: str = "_default"):
        self.yaml_content = yaml_content
        self.tenant_id = tenant_id

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="adapter-compliance-")
        self.root = Path(self.tmp.name)
        tenant_global = self.root / "tenants" / self.tenant_id / "global"
        tenant_global.mkdir(parents=True)
        (tenant_global / "forge").mkdir()
        if self.yaml_content is not None:
            (tenant_global / "tenant.corvin.yaml").write_text(self.yaml_content)
        self.env = mock.patch.dict(os.environ, {
            "CORVIN_HOME": str(self.root),
            "CORVIN_TENANT_ID": self.tenant_id,
        })
        self.env.start()
        # Clear the L34 mtime-keyed guard cache so each test loads fresh.
        # ADR-0158 M1 moved this cache from adapter._compliance_cache to
        # spawn_gates._l34_cache (the gate's single source of truth).
        spawn_gates._l34_cache.clear()
        return self

    def __exit__(self, *exc):
        self.env.stop()
        spawn_gates._l34_cache.clear()
        self.tmp.cleanup()


class TestNoConfigFailsOpen(unittest.TestCase):
    """Back-compat: pre-L34 tenants have no yaml → gate is no-op."""

    def test_no_yaml_returns_none(self):
        with _Sandbox(yaml_content=None):
            engine = _FakeEngine(name="claude_code")
            msg = adapter._check_compliance_or_fail(
                engine, prompt="hello world",
                persona="coder", channel="discord", chat_key="dm:42",
            )
            self.assertIsNone(msg)


class TestPermissiveConfigPasses(unittest.TestCase):
    """Default matrix (no override) allows PUBLIC against any engine."""

    def test_minimal_yaml_permits_internal_to_claude(self):
        # The default matrix already permits INTERNAL on us_cloud (residency
        # restriction is opt-in). This explicit override is equivalent to the
        # default and documents the permissive intent.
        yaml_content = """
spec:
  data_classification:
    matrix:
      INTERNAL: [local, eu_cloud, us_cloud]
"""
        with _Sandbox(yaml_content=yaml_content):
            engine = _FakeEngine(name="claude_code")
            msg = adapter._check_compliance_or_fail(
                engine, prompt="def hello(): pass",
                persona="coder", channel="discord", chat_key="dm:42",
            )
            self.assertIsNone(msg)


class TestEuProductionDeniesUsCloud(unittest.TestCase):
    """EU_PRODUCTION-style preset blocks claude_code for any classification."""

    YAML = """
spec:
  data_classification:
    matrix:
      PUBLIC: [local]
      INTERNAL: [local]
      CONFIDENTIAL: [local]
      SECRET: [local]
"""

    def test_blocks_claude_for_plain_code(self):
        with _Sandbox(yaml_content=self.YAML):
            engine = _FakeEngine(name="claude_code")
            msg = adapter._check_compliance_or_fail(
                engine, prompt="def hello(): pass",
                persona="coder", channel="discord", chat_key="dm:42",
            )
            self.assertIsNotNone(msg)
            self.assertIn("Spawn rejected", msg or "")
            self.assertIn("claude_code", msg or "")

    def test_blocks_claude_for_secret_payload(self):
        with _Sandbox(yaml_content=self.YAML):
            engine = _FakeEngine(name="claude_code")
            msg = adapter._check_compliance_or_fail(
                engine,
                # AKIA pattern triggers SECRET classification
                prompt="my key is AKIAIOSFODNN7EXAMPLE",
                persona="coder", channel="discord", chat_key="dm:42",
            )
            self.assertIsNotNone(msg)
            self.assertIn("SECRET", msg or "")

    def test_allows_local_engine_for_internal(self):
        with _Sandbox(yaml_content=self.YAML):
            engine = _FakeEngine(name="opencode_ollama")
            msg = adapter._check_compliance_or_fail(
                engine, prompt="def hello(): pass",
                persona="coder", channel="discord", chat_key="dm:42",
            )
            # opencode_ollama is locality=local in DEFAULT_ENGINE_COMPLIANCE
            self.assertIsNone(msg)


class TestEngineWithoutNameFailsOpen(unittest.TestCase):

    def test_missing_engine_name_returns_none(self):
        with _Sandbox(yaml_content="spec:\n  data_classification:\n    matrix:\n      INTERNAL: [local]\n"):
            engine = _FakeEngine(name="")
            msg = adapter._check_compliance_or_fail(
                engine, prompt="x",
                persona=None, channel="discord", chat_key="dm:42",
            )
            self.assertIsNone(msg)


class TestMtimeReload(unittest.TestCase):
    """Editing tenant.yaml triggers a reload on the next spawn."""

    def test_cache_invalidates_on_mtime_change(self):
        with _Sandbox(yaml_content="""
spec:
  data_classification:
    matrix:
      INTERNAL: [local]
""") as sb:
            engine = _FakeEngine(name="claude_code")
            # First call: blocked
            msg1 = adapter._check_compliance_or_fail(
                # classify_task defaults to PUBLIC (commit 5c9c9bb); opt into
                # INTERNAL explicitly so the INTERNAL:[local] matrix actually
                # blocks claude_code (us_cloud) — the precondition under test.
                engine, prompt="[class:internal] x", persona=None,
                channel="ch", chat_key="ck",
            )
            self.assertIsNotNone(msg1)

            # Edit the yaml to permit us_cloud for INTERNAL + bump mtime
            cfg_path = (sb.root / "tenants" / sb.tenant_id / "global"
                        / "tenant.corvin.yaml")
            cfg_path.write_text("""
spec:
  data_classification:
    matrix:
      INTERNAL: [local, eu_cloud, us_cloud]
""")
            import time as _t
            future = _t.time() + 10
            os.utime(cfg_path, (future, future))

            # Second call: should pass after reload
            msg2 = adapter._check_compliance_or_fail(
                engine, prompt="[class:internal] x", persona=None,
                channel="ch", chat_key="ck",
            )
            self.assertIsNone(msg2)


class TestMalformedYamlFallsBackToDefaults(unittest.TestCase):
    """A broken yaml file (yaml.safe_load yields a non-dict) does not
    crash the gate — DataFlowGuard.from_tenant_config falls back to
    module defaults and KEEPS ENFORCING (it must not become allow-all).
    The default matrix is permissive for PUBLIC/INTERNAL/CONFIDENTIAL
    (residency restriction is opt-in) but still refuses SECRET against an
    egressing engine. This is *fail-closed-but-soft* semantics: a malformed
    config must not silently disable the residual SECRET floor."""

    def test_invalid_yaml_still_enforces_secret_floor(self):
        with _Sandbox(yaml_content="not yaml { [ broken"):
            engine = _FakeEngine(name="claude_code")
            # A literal credential classifies as SECRET; the malformed-yaml
            # fallback to the DEFAULT matrix still blocks claude_code (us_cloud,
            # egress=external) — proving the fallback enforces, not allow-all.
            msg = adapter._check_compliance_or_fail(
                engine, prompt="password=hunter2longenough", persona=None,
                channel="ch", chat_key="ck",
            )
            self.assertIsNotNone(msg)
            self.assertIn("claude_code", msg or "")

    def test_invalid_yaml_allows_local_engine(self):
        with _Sandbox(yaml_content="not yaml { [ broken"):
            engine = _FakeEngine(name="opencode_ollama")
            msg = adapter._check_compliance_or_fail(
                engine, prompt="x", persona=None,
                channel="ch", chat_key="ck",
            )
            self.assertIsNone(msg)


class TestExceptionDuringLoadFailsOpen(unittest.TestCase):
    """A genuine import-time exception (DataFlowGuard module gone) must
    fail-open so the bridge stays usable on stripped Apache-only
    deployments that don't ship data_classification.py."""

    def test_module_unavailable_returns_none(self):
        with _Sandbox(yaml_content="spec:\n  data_classification:\n    matrix:\n      INTERNAL: [local]\n"):
            engine = _FakeEngine(name="claude_code")
            # patch.object targets the specific module object we imported, not
            # sys.modules["adapter"] — which may have been replaced by _fresh_adapter()
            # in another test module running before this one.
            # ADR-0158 M1: the guard loader moved adapter._load_data_flow_guard
            # → spawn_gates._load_l34_guard. A None guard → no enforcement →
            # fail-open (None).
            with mock.patch.object(spawn_gates, "_load_l34_guard", return_value=None):
                msg = adapter._check_compliance_or_fail(
                    engine, prompt="x", persona=None,
                    channel="ch", chat_key="ck",
                )
            self.assertIsNone(msg)


if __name__ == "__main__":
    unittest.main()

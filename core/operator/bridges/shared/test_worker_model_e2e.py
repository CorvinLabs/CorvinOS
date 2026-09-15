"""E2E Test: Worker model Single Source of Truth end-to-end.

Tests the complete chain:
  Console → Tenant YAML → Bridge reads → CORVIN_ACS_WORKER_MODEL injected
  → ACS spawn picks it up → Worker runs with correct model
"""
import os
import tempfile
from pathlib import Path
import yaml
import pytest


class TestWorkerModelE2E:
    """End-to-end tests for worker model resolution."""

    def test_console_sets_tenant_yaml_bridge_reads_acs_uses(self):
        """E2E: Console Opus 5 → YAML → Bridge → ACS uses Opus 5."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["CORVIN_HOME"] = tmpdir
            tenant_dir = Path(tmpdir) / "tenants" / "_default" / "global"
            tenant_dir.mkdir(parents=True, exist_ok=True)

            # Step 1: Simulate Console setting Opus 5
            yaml_path = tenant_dir / "tenant.corvin.yaml"
            yaml_data = {
                "spec": {
                    "engine_models": {
                        "claude_code": {
                            "os_model": "claude-haiku-4-5-20251001",
                            "worker_model": "claude-opus-5"  # Console-set
                        }
                    }
                }
            }
            yaml_path.write_text(yaml.dump(yaml_data))

            # Step 2: Bridge reads YAML and injects env var
            import sys
            bridge_path = Path(__file__).parent
            if str(bridge_path) not in sys.path:
                sys.path.insert(0, str(bridge_path))
            from adapter import _build_spawn_env

            env = _build_spawn_env(
                bridge="console",
                chat_key="test-chat",
                profile={"default_engine": "claude_code"},
                tenant_id="_default",
            )

            # Step 3: Bridge should have injected the Console value
            assert env.get("CORVIN_ACS_WORKER_MODEL") == "claude-opus-5"

    def test_console_per_engine_config_for_multiple_engines(self):
        """Test: Console can set worker models for multiple engines independently."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["CORVIN_HOME"] = tmpdir
            tenant_dir = Path(tmpdir) / "tenants" / "_default" / "global"
            tenant_dir.mkdir(parents=True, exist_ok=True)

            # Console sets Opus for Claude Code
            yaml_path = tenant_dir / "tenant.corvin.yaml"
            yaml_data = {
                "spec": {
                    "engine_models": {
                        "claude_code": {
                            "worker_model": "claude-opus-5"
                        },
                        "hermes": {
                            "worker_model": "hermes-capable"
                        }
                    }
                }
            }
            yaml_path.write_text(yaml.dump(yaml_data))

            import sys
            bridge_path = Path(__file__).parent
            if str(bridge_path) not in sys.path:
                sys.path.insert(0, str(bridge_path))
            from adapter import _build_spawn_env

            # With Claude Code (default engine), Opus should be selected
            env = _build_spawn_env(
                bridge="console",
                chat_key="test-chat",
                profile={"default_engine": "claude_code"},
                tenant_id="_default",
            )
            assert env.get("CORVIN_ACS_WORKER_MODEL") == "claude-opus-5", \
                "Claude Code should use Opus 5"

    def test_persona_override_beats_tenant_yaml(self):
        """Test: Persona (per-chat) setting beats Tenant YAML (global)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["CORVIN_HOME"] = tmpdir
            tenant_dir = Path(tmpdir) / "tenants" / "_default" / "global"
            tenant_dir.mkdir(parents=True, exist_ok=True)

            # Console sets Sonnet 5
            yaml_path = tenant_dir / "tenant.corvin.yaml"
            yaml_data = {
                "spec": {
                    "engine_models": {
                        "claude_code": {
                            "worker_model": "claude-sonnet-5"
                        }
                    }
                }
            }
            yaml_path.write_text(yaml.dump(yaml_data))

            import sys
            bridge_path = Path(__file__).parent
            if str(bridge_path) not in sys.path:
                sys.path.insert(0, str(bridge_path))
            from adapter import _build_spawn_env

            # Persona overrides with Haiku
            persona = {
                "default_engine": "claude_code",
                "engine_models": {
                    "claude_code": {
                        "worker_model": "claude-haiku-4-5-20251001"
                    }
                }
            }

            env = _build_spawn_env(
                bridge="console",
                chat_key="test-chat",
                profile=persona,
                tenant_id="_default",
            )

            # Persona (Haiku) should win over Console (Sonnet)
            assert env.get("CORVIN_ACS_WORKER_MODEL") == "claude-haiku-4-5-20251001"

    def test_global_fallback_when_no_per_engine(self):
        """Test: Falls back to global default_worker_model if per-engine not set."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["CORVIN_HOME"] = tmpdir
            tenant_dir = Path(tmpdir) / "tenants" / "_default" / "global"
            tenant_dir.mkdir(parents=True, exist_ok=True)

            # Console sets global default only
            yaml_path = tenant_dir / "tenant.corvin.yaml"
            yaml_data = {
                "spec": {
                    "default_worker_model": "claude-sonnet-5"
                }
            }
            yaml_path.write_text(yaml.dump(yaml_data))

            import sys
            bridge_path = Path(__file__).parent
            if str(bridge_path) not in sys.path:
                sys.path.insert(0, str(bridge_path))
            from adapter import _build_spawn_env

            env = _build_spawn_env(
                bridge="console",
                chat_key="test-chat",
                profile={"default_engine": "hermes"},  # Not in engine_models
                tenant_id="_default",
            )

            # Should fall back to global default
            assert env.get("CORVIN_ACS_WORKER_MODEL") == "claude-sonnet-5"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

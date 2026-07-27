"""Test: Bridge reads Tenant YAML worker_model (Single Source of Truth).

Tests that _build_spawn_env reads worker_model from Console-configured Tenant YAML
and injects it as CORVIN_ACS_WORKER_MODEL for ACS spawn.
"""
import os
import tempfile
from pathlib import Path
import yaml
import pytest


def test_bridge_reads_tenant_yaml_worker_model_per_engine():
    """Test: Bridge reads per-engine worker_model from Tenant YAML."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["CORVIN_HOME"] = tmpdir
        tenant_dir = Path(tmpdir) / "tenants" / "_default" / "global"
        tenant_dir.mkdir(parents=True, exist_ok=True)

        # Console sets per-engine worker_model (Opus 5 for claude_code)
        yaml_path = tenant_dir / "tenant.corvin.yaml"
        yaml_data = {
            "spec": {
                "engine_models": {
                    "claude_code": {
                        "os_model": "claude-haiku-4-5-20251001",
                        "worker_model": "claude-opus-5"
                    }
                }
            }
        }
        yaml_path.write_text(yaml.dump(yaml_data))

        # Import here to pick up mocked CORVIN_HOME
        import sys
        bridge_path = Path(__file__).parent
        if str(bridge_path) not in sys.path:
            sys.path.insert(0, str(bridge_path))
        from adapter import _build_spawn_env

        env = _build_spawn_env(
            tenant_id="_default",
            engine_id="claude_code",
            profile=None,
            chat_key="test-chat",
            channel="console",
        )

        # Bridge should inject Console-set Opus 5 as CORVIN_ACS_WORKER_MODEL
        assert env.get("CORVIN_ACS_WORKER_MODEL") == "claude-opus-5", \
            f"Expected 'claude-opus-5', got {env.get('CORVIN_ACS_WORKER_MODEL')}"


def test_bridge_reads_tenant_yaml_worker_model_global_fallback():
    """Test: Bridge falls back to global default_worker_model when per-engine not set."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["CORVIN_HOME"] = tmpdir
        tenant_dir = Path(tmpdir) / "tenants" / "_default" / "global"
        tenant_dir.mkdir(parents=True, exist_ok=True)

        # Console sets global default (Sonnet 5)
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
            tenant_id="_default",
            engine_id="hermes",  # Engine not in engine_models
            profile=None,
            chat_key="test-chat",
            channel="console",
        )

        # Bridge should fall back to global default
        assert env.get("CORVIN_ACS_WORKER_MODEL") == "claude-sonnet-5", \
            f"Expected 'claude-sonnet-5', got {env.get('CORVIN_ACS_WORKER_MODEL')}"


def test_bridge_persona_priority_over_tenant_yaml():
    """Test: Bridge prefers persona per-engine setting over Tenant YAML."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["CORVIN_HOME"] = tmpdir
        tenant_dir = Path(tmpdir) / "tenants" / "_default" / "global"
        tenant_dir.mkdir(parents=True, exist_ok=True)

        # Console sets Opus 5
        yaml_path = tenant_dir / "tenant.corvin.yaml"
        yaml_data = {
            "spec": {
                "engine_models": {
                    "claude_code": {
                        "worker_model": "claude-opus-5"
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
            "engine_models": {
                "claude_code": {
                    "worker_model": "claude-haiku-4-5-20251001"
                }
            }
        }

        env = _build_spawn_env(
            tenant_id="_default",
            engine_id="claude_code",
            profile=persona,
            chat_key="test-chat",
            channel="console",
        )

        # Persona (Haiku) should win over Console (Opus 5)
        assert env.get("CORVIN_ACS_WORKER_MODEL") == "claude-haiku-4-5-20251001", \
            f"Expected Persona Haiku, got {env.get('CORVIN_ACS_WORKER_MODEL')}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

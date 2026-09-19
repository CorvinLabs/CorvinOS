"""pytest configuration and fixtures for plugin builder testing framework.

Provides:
- real_haiku_client: Real Haiku LLM client for classification tests
- real_opus_client: Real Opus LLM client for analysis tests
- tenant_context: Tenant-scoped test context with audit chain
- plugin_scaffold_dir: Temporary plugin scaffold directory
- real_llm_config: Configuration for real LLM testing

All fixtures use ANTHROPIC_API_KEY from environment.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Generator

import pytest

from core.plugins.plugin_builder.testing_framework.base_fixtures import (
    HaikuClassifier,
    OpusCheckpointer,
    PluginTestContext,
)


@pytest.fixture(scope="session")
def real_llm_config() -> dict:
    """Get configuration for real LLM testing.

    Yields:
        dict with API key and model info
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY environment variable not set")

    return {
        "api_key": api_key,
        "haiku_model": "claude-3-5-haiku-20241022",
        "opus_model": "claude-opus-4-1-20250805",
        "haiku_cost_per_call": 0.001,
        "opus_cost_per_call": 0.05,
    }


@pytest.fixture
def real_haiku_client(real_llm_config: dict) -> Generator[HaikuClassifier, None, None]:
    """Provide a real Haiku classifier client.

    Yields:
        HaikuClassifier: configured with real API key
    """
    client = HaikuClassifier(api_key=real_llm_config["api_key"])
    yield client


@pytest.fixture
def real_opus_client(real_llm_config: dict) -> Generator[OpusCheckpointer, None, None]:
    """Provide a real Opus checkpointer client.

    Yields:
        OpusCheckpointer: configured with real API key
    """
    client = OpusCheckpointer(api_key=real_llm_config["api_key"])
    yield client


@pytest.fixture
def tenant_context() -> Generator[PluginTestContext, None, None]:
    """Provide a tenant-scoped test context with audit chain.

    Yields:
        PluginTestContext: with tenant isolation and audit trail
    """
    context = PluginTestContext(
        plugin_id="test_plugin_builder",
        tenant_id="_default",
        session_id="pytest_session",
        user_id="pytest_user",
    )
    yield context

    # Verify audit chain after test
    if hasattr(context, "_audit_chain") and context._audit_chain:
        assert context.verify_audit_chain(), "Audit chain integrity check failed"


@pytest.fixture
def plugin_scaffold_dir() -> Generator[Path, None, None]:
    """Provide a temporary plugin scaffold directory.

    Yields:
        Path: temporary directory with basic plugin structure
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        scaffold_dir = Path(tmpdir)

        # Create basic plugin structure
        plugin_dir = scaffold_dir / "test_plugin"
        plugin_dir.mkdir(parents=True)

        # Create plugin.json
        (plugin_dir / "plugin.json").write_text(
            json.dumps(
                {
                    "id": "test_plugin",
                    "type": "data_connector",
                    "description": "Test plugin for E2E testing",
                    "version": "1.0.0",
                }
            )
        )

        # Create basic plugin.py
        (plugin_dir / "plugin.py").write_text(
            """
class TestPlugin:
    '''Test plugin implementation.'''

    def __init__(self):
        self.name = "test_plugin"
        self.version = "1.0.0"

    def on_load(self):
        '''Lifecycle: on load.'''
        pass

    def on_unload(self):
        '''Lifecycle: on unload.'''
        pass

    def on_execute(self, input_data):
        '''Execute the plugin.'''
        return {"status": "success", "data": input_data}
"""
        )

        # Create tests directory
        (plugin_dir / "tests").mkdir()
        (plugin_dir / "tests" / "__init__.py").touch()
        (plugin_dir / "tests" / "test_basic.py").write_text(
            """
def test_plugin_loads():
    '''Test basic plugin loading.'''
    assert True
"""
        )

        # Create setup.py
        (plugin_dir / "setup.py").write_text(
            """
from setuptools import setup

setup(
    name="test_plugin",
    version="1.0.0",
    py_modules=["plugin"],
)
"""
        )

        yield plugin_dir


@pytest.fixture(autouse=True)
def _check_api_key():
    """Check that ANTHROPIC_API_KEY is set before running real LLM tests."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY environment variable not set")


# Test configuration
def pytest_configure(config):
    """Register pytest markers for real LLM tests."""
    config.addinivalue_line(
        "markers",
        "real_llm: marks tests that use real LLM API calls (deselect with '-m \"not real_llm\"')",
    )
    config.addinivalue_line(
        "markers",
        "slow: marks tests as slow (deselect with '-m \"not slow\"')",
    )


def pytest_collection_modifyitems(config, items):
    """Auto-mark tests with real LLM calls."""
    for item in items:
        if "real_" in item.nodeid:
            item.add_marker(pytest.mark.real_llm)
            item.add_marker(pytest.mark.slow)

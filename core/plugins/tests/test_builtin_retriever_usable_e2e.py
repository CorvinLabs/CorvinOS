"""E2E: the semantic-context-retriever builtin is discovered, loaded, and ACTIVE.

This is the usability proof for ADR-0598/0599: shipping the plugin and adding the
provider seam is worthless unless the real boot path actually loads it and its
``on_load`` runs ``set_active`` so the CEL/TDE seams stop being a passthrough. This
test drives the REAL loader (``bootstrap_builtin``), not a direct class call, and
asserts the provider slot flips — the exact wiring an earlier build shipped dead.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for _p in (str(_REPO / "core" / "plugins"), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402
from corvin_plugins import bootstrap  # noqa: E402
from corvin_plugins.providers import context_retriever as cr  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_registry():
    cr.clear()
    yield
    cr.clear()


def test_builtin_retriever_is_discovered():
    """The plugin dir is found by the builtin discovery scan."""
    names = [d.name for d in bootstrap._builtin_plugin_dirs(bootstrap._BUILTIN_ROOT)]
    assert "semantic-context-retriever" in names


def test_bootstrap_activates_the_retriever():
    """Driving the real loader flips the provider slot away from passthrough."""
    assert type(cr.get_active()).__name__ == "PassthroughContextRetriever"
    with tempfile.TemporaryDirectory() as home:
        loaded = bootstrap.bootstrap_builtin(
            tenant_id="_default", corvin_home=home,
            tenant_config={"spec": {"plugins": {}}},
        )
    assert "semantic-context-retriever" in loaded
    active = cr.get_active()
    assert type(active).__name__ == "SemanticContextRetriever", (
        "on_load did not set_active — the retriever would be installed but dead"
    )


def test_load_builtin_false_opts_out():
    """The documented opt-out (spec.plugins.load_builtin: false) is honoured."""
    with tempfile.TemporaryDirectory() as home:
        loaded = bootstrap.bootstrap_builtin(
            tenant_id="_default", corvin_home=home,
            tenant_config={"spec": {"plugins": {"load_builtin": False}}},
        )
    assert loaded == []
    assert type(cr.get_active()).__name__ == "PassthroughContextRetriever"

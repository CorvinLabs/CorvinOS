"""The worker's tool runner must read the SAME forge root that forge_tool() writes to.

Forge registers user-scope tools under ``<corvin_home>/tenants/<tid>/forge`` (tenant-native). The worker used
to build its Registry at ``<corvin_home>/global/forge``, a path nothing writes to on a tenant-native install,
so it saw zero tools and every compute run failed with an unknown tool.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

COMPUTE_ROOT = Path(__file__).resolve().parents[1]
FORGE_ROOT = Path(__file__).resolve().parents[3] / "corvin_operator" / "forge"
for p in (COMPUTE_ROOT, FORGE_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

forge_registry = pytest.importorskip("forge.registry")


def test_runner_registry_root_is_the_tenant_user_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    seen: dict = {}

    class _SpyRegistry:
        def __init__(self, root, **kwargs):
            seen["root"] = Path(root)

        def list(self):
            return []

    monkeypatch.setattr(forge_registry, "Registry", _SpyRegistry)

    from corvin_compute import cli

    adapter = cli._build_runner_fn(tenant_id="acme", corvin_home_path=tmp_path)

    assert callable(adapter)
    assert seen["root"] == tmp_path / "tenants" / "acme" / "forge"
    assert not (tmp_path / "global" / "forge").exists(), "the legacy non-tenant root must not be created"


def test_runner_builder_is_called_with_the_served_tenant():
    """Call-site proof: `serve` hands its --tenant to the runner builder (not a hard-coded default)."""
    src = (COMPUTE_ROOT / "corvin_compute" / "cli.py").read_text(encoding="utf-8")
    assert "_build_runner_fn(tenant_id=args.tenant" in src

"""Every boot-layer downgrade is AUDITED, under the real tenant (F-P3 / F-P4).

``registry._resolve_boot_layer`` used to downgrade a self-promoting plugin with
a log line only; ``state._downgrade_privileged_boot_layer`` audited under a
hard-coded ``_default``; and ``PluginLifecycle.install`` stored whatever boot
layer the record claimed, leaving the read-side downgrade to catch it later.
These tests drive the real registry, the real ``TenantRegistry.load`` and the
real ``PluginLifecycle.install`` and read the audit events they emit.
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
import yaml

from corvin_plugins import bootstrap, state
from corvin_plugins.manifest import BootLayer, PluginOrigin, PluginRecord
from corvin_plugins.protocol import HealthStatus, PluginContext
from corvin_plugins.registry import PluginRegistry


class _Plug:
    plugin_type = "notification_backend"
    version = "1.0.0"
    display_name = "P"

    def __init__(self, plugin_id: str, boot_layer=None, on_load=None) -> None:
        self.plugin_id = plugin_id
        if boot_layer is not None:
            self.boot_layer = boot_layer
        self._on_load = on_load

    def on_load(self, ctx: PluginContext) -> None:
        if self._on_load:
            self._on_load(ctx)

    def on_unload(self) -> None:
        pass

    def health_check(self) -> HealthStatus:
        return HealthStatus(ok=True)


@pytest.fixture
def ctx_and_events(tmp_path):
    events: list[tuple[str, dict]] = []
    ctx = bootstrap.build_context(
        plugin_id="x", tenant_id="acme", corvin_home=tmp_path, config={}
    )
    ctx.audit_emit = lambda e, d: events.append((e, d))
    return ctx, events


def _rejections(events):
    return [d for e, d in events if e == "plugin.boot_layer_rejected"]


def test_self_declared_privileged_layer_is_downgraded_and_audited(ctx_and_events):
    ctx, events = ctx_and_events
    reg = PluginRegistry()
    reg.register(_Plug("a:self", boot_layer="compliance"), ctx)
    assert reg.boot_layer_of("a:self") is BootLayer.INSTALLED
    (rej,) = _rejections(events)
    assert rej["reason"] == "privileged_self_declared"
    assert rej["declared_boot_layer"] == "compliance"
    assert rej["tenant_id"] == "acme"
    assert rej["plugin_id"] == "a:self"


def test_unknown_boot_layer_is_audited(ctx_and_events):
    ctx, events = ctx_and_events
    reg = PluginRegistry()
    reg.register(_Plug("a:bogus", boot_layer="root-of-all"), ctx)
    assert reg.boot_layer_of("a:bogus") is BootLayer.INSTALLED
    (rej,) = _rejections(events)
    assert rej["reason"] == "unknown_boot_layer"
    assert rej["declared_boot_layer"] == "root-of-all"


def test_register_from_inside_on_load_cannot_take_a_privileged_layer(ctx_and_events):
    ctx, events = ctx_and_events
    reg = PluginRegistry()

    def sneaky(inner_ctx):
        reg.register(_Plug("a:inner"), inner_ctx, boot_layer="compliance")

    reg.register(_Plug("a:outer", on_load=sneaky), ctx)
    assert reg.boot_layer_of("a:inner") is BootLayer.INSTALLED
    (rej,) = _rejections(events)
    assert rej["reason"] == "privileged_from_on_load"
    assert rej["plugin_id"] == "a:inner"


def test_plugin_loaded_carries_origin_and_source(ctx_and_events):
    ctx, events = ctx_and_events
    reg = PluginRegistry()
    reg.register(_Plug("a:src"), ctx, origin="vetted", source="marketplace_root:memory/x")
    (loaded,) = [d for e, d in events if e == "plugin.loaded"]
    assert loaded["origin"] == "vetted"
    assert loaded["source"] == "marketplace_root:memory/x"
    # An omitted origin is reported as unknown — never silently upgraded.
    reg.register(_Plug("a:anon"), ctx)
    anon = [d for e, d in events if e == "plugin.loaded"][-1]
    assert anon["origin"] == "unknown" and anon["source"] == "unknown"


def _privileged_record() -> PluginRecord:
    return PluginRecord(
        plugin_id="acme-audit",
        version="1.0.0",
        display_name="Acme",
        plugin_type="audit_backend",
        origin=PluginOrigin.BUILTIN,
        boot_layer=BootLayer.COMPLIANCE,
    )


def test_registry_yaml_privileged_claim_is_audited_under_the_real_tenant(tmp_path):
    path = state.registry_path(tenant_id="acme", corvin_home_path=tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({
        "spec": {"schema_version": state.REGISTRY_SCHEMA_VERSION},
        "plugins": {"acme-audit": _privileged_record().to_dict()},
    }))
    audited: list[tuple[str, dict, str]] = []
    with mock.patch.object(
        state, "_audit", lambda e, d, *, tenant_id: audited.append((e, d, tenant_id))
    ):
        reg = state.TenantRegistry.load(tenant_id="acme", corvin_home_path=tmp_path)
    assert reg.records["acme-audit"].boot_layer is BootLayer.INSTALLED
    (event,) = audited
    assert event[0] == "plugin.boot_layer_rejected"
    assert event[2] == "acme", "the audit must name the tenant whose registry it is"
    assert event[1]["reason"] == "privileged_boot_layer_from_tenant_registry"


def test_install_never_stores_a_privileged_boot_layer(tmp_path):
    audited: list[tuple[str, dict, str]] = []
    lifecycle = state.PluginLifecycle(
        tenant_id="acme", corvin_home_path=tmp_path, lifecycle_enabled=lambda: True
    )
    with mock.patch.object(
        state, "_audit", lambda e, d, *, tenant_id: audited.append((e, d, tenant_id))
    ):
        stored = lifecycle.install(_privileged_record(), installed_by="test")
    assert stored.boot_layer is BootLayer.INSTALLED
    on_disk = yaml.safe_load(
        state.registry_path(tenant_id="acme", corvin_home_path=tmp_path).read_text()
    )
    assert on_disk["plugins"]["acme-audit"]["boot_layer"] == "installed"
    kinds = [(e, t) for e, _d, t in audited]
    assert ("plugin.boot_layer_rejected", "acme") in kinds
    assert ("plugin.installed", "acme") in kinds

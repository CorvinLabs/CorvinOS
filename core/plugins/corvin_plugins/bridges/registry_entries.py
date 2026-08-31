"""Tenant-config declarations for the bundled bridge supervisors (ADR-0238).

A supervisor plugin is activated the way every declarative plugin is activated:
by an entry in ``spec.plugins.installed`` of ``tenant.corvin.yaml``. This module
generates those entries so the shape lives in ONE place — the docs, the Console
Settings panel and the tests all read it from here instead of each carrying their
own copy of a dotted class path that goes stale on the first rename.

``boot_layer: bundled`` is honoured by ``bootstrap._declared_boot_layer``: a
tenant config is operator-writable, so it may declare ``bundled`` or
``installed`` but never ``core`` or ``compliance`` — a privileged claim is
downgraded and audited.
``bundled`` keeps the plugin disableable (``can_disable()``), which is the point:
a messenger transport is not a compliance mechanism.

The declaration alone does NOT start a daemon. The
``bridge_supervisor_plugins`` feature flag has to be on as well, and it ships off
— see ``supervisor`` module docstring for the full start gate.
"""
from __future__ import annotations

from .supervisor import BRIDGE_CHANNELS, BRIDGE_PLUGIN_CLASSES

_MODULE = "corvin_plugins.bridges.supervisor"


def declaration_entry(channel: str, *, enabled: bool = True) -> dict:
    """Return the ``spec.plugins.installed`` entry for one bridge supervisor.

    ``enabled=False`` keeps the declaration but stops the daemon — useful to
    park a bridge without deleting its config block.
    """
    cls = BRIDGE_PLUGIN_CLASSES[channel]
    entry: dict = {
        "id": f"{channel}-bridge",
        "boot_layer": "bundled",
        "class_path": f"{_MODULE}:{cls.__name__}",
    }
    if not enabled:
        entry["config"] = {"enabled": False}
    return entry


def declaration_entries(channels: tuple[str, ...] | list[str] | None = None) -> list[dict]:
    """Entries for the given channels (all seven bundled bridges by default)."""
    return [declaration_entry(ch) for ch in (channels or BRIDGE_CHANNELS)]

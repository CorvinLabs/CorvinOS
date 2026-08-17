"""Tenant-config declaration for the bundled Console web surface (ADR-0356, P2.5).

Mirrors ``bridges/registry_entries.py``: the ``spec.plugins.installed`` shape for
the Console lives in ONE place so the docs, the Console Settings panel and the
tests read it from here instead of each carrying a dotted class path that goes
stale on the first rename.

``boot_layer: bundled`` is honoured by ``bootstrap._declared_boot_layer``: a tenant
config is operator-writable, so it may declare ``bundled`` or ``installed`` but
never ``core`` or ``compliance`` — a privileged claim is downgraded and audited.
``bundled`` keeps the Console disableable (``can_disable()``), which is the point:
a UI is not a compliance mechanism and an operator running headless must be able
to switch it off.

The declaration alone does NOT mount the SPA. Today ``standalone.py`` still
performs the mount; wiring it THROUGH this plugin is the plugin loader's job (P7).
"""
from __future__ import annotations

_MODULE = "corvin_plugins.console.plugin"
_CLASS = "ConsolePlugin"


def declaration_entry(*, enabled: bool = True) -> dict:
    """Return the ``spec.plugins.installed`` entry for the Console web surface."""
    entry: dict = {
        "id": "console",
        "boot_layer": "bundled",
        "class_path": f"{_MODULE}:{_CLASS}",
    }
    if not enabled:
        entry["config"] = {"enabled": False}
    return entry

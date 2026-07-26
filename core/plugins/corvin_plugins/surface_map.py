"""Extension-surface map: what is buildable, and whether anything calls it (ADR-0245).

"What can I extend?" had three different answers depending on which artifact you
read — ``KNOWN_PLUGIN_TYPES`` (11 entries), ``providers/`` (8 modules), and the
marketplace's seven file-shaped surfaces. Nothing connected them, so an author had
to infer from ``bootstrap.build_context`` which registry handle their ``on_load()``
was supposed to use.

This module is that connection, and it is **verified, not asserted**. The mapping
itself has to be written down — no rule in the tree derives "a ``router_backend``
hands itself to ``ctx.router_registry``" — but every column is checked against the
code by ``core/plugins/tests/test_surface_map.py``: every ``plugin_type`` matches
``KNOWN_PLUGIN_TYPES``, every ``ctx_handle`` names a real :class:`PluginContext`
field, every ``template`` names a file that exists, every ``provider_module``
names a real module. A drifted map is a red test, not a stale document.

Why ``consumed_by`` exists
--------------------------
Registering successfully is not the same as being used, and for six of the eleven
types the difference is the entire story. A plugin can load, register, report
healthy, appear in the Console — and never be invoked, because nothing in the tree
reads the registry it registered with. There is no error and no log line; from the
operator's side an Okta ``user_backend`` that is never consulted looks exactly like
one that is working.

That failure mode is not hypothetical here. Two distinct causes are live:

* **No consumer.** ``stt_provider``, ``data_connector`` and ``user_backend`` have a
  registry, a ``PluginContext`` handle, and (for ``user_backend``) a fully
  implemented ``authenticate()`` with circuit breaker and deny-on-error — and
  nothing outside ``corvin_plugins`` calls ``get_active()``.
* **Handle never populated.** ``bootstrap_all(**registries)`` forwards
  ``compute_registry`` / ``engine_factory`` / ``channel_registry`` correctly, but
  the sole production call site (``core/gateway/corvin_gateway/app.py``) passes
  none of them. The handles are therefore always ``None``, and every shipped
  template guards with ``if ctx.<handle> is not None:`` — so those plugins
  silently skip their own registration.

``consumed_by`` records this per type so an author learns it before writing code
rather than after shipping. The compliance weight is real: CLAUDE.md and ADR-0233
state the invariant "a ``user_backend`` failure/timeout/rejection = deny, never
guest", and that invariant is expressed through a code path nothing reaches.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ExtensionSurface:
    """One plugin type and everything an author needs to reach it."""

    plugin_type: str
    #: Field on :class:`PluginContext` that ``on_load()`` registers with.
    ctx_handle: str
    #: Module under ``corvin_plugins/providers/`` owning the registry, or None
    #: when the registry belongs to another subsystem (compute, engine, bridge).
    provider_module: Optional[str]
    #: Filename under ``core/plugins/templates/``, or None when none ships.
    template: Optional[str]
    #: Repo-relative file where the runtime actually CALLS a plugin of this type.
    #: ``None`` means nothing invokes it — see the module docstring.
    consumed_by: Optional[str]
    #: Set when ``consumed_by`` is None: why, in one line.
    dead_reason: Optional[str]
    #: One-line invariant an implementation must not break (ADR-0033 / ADR-0233).
    invariant: str

    @property
    def consumed(self) -> bool:
        """True iff something in the tree actually invokes this plugin type."""
        return self.consumed_by is not None


#: The map. Working surfaces first, then the ones that register but are unused.
SURFACES: tuple[ExtensionSurface, ...] = (
    # ── Live: something calls these ─────────────────────────────────────────
    ExtensionSurface(
        plugin_type="router_backend",
        ctx_handle="router_registry",
        provider_module="router_backend",
        template="router_backend_plugin.py",
        consumed_by="operator/bridges/shared/adapter.py",
        dead_reason=None,
        invariant="route() must NOT raise — return None on no-match or error.",
    ),
    ExtensionSurface(
        plugin_type="summary_provider",
        ctx_handle="summary_registry",
        provider_module="summary_provider",
        template="summary_provider_plugin.py",
        consumed_by="operator/bridges/shared/adapter.py",
        dead_reason=None,
        invariant="Must degrade to the core summariser rather than raising.",
    ),
    ExtensionSurface(
        plugin_type="notification_backend",
        ctx_handle="notification_registry",
        provider_module="notification_backend",
        template="notification_backend_plugin.py",
        consumed_by="operator/bridges/shared/adapter.py",
        dead_reason=None,
        invariant=(
            "notify() must not block >100 ms and must carry NO message content "
            "or PII — metadata only."
        ),
    ),
    ExtensionSurface(
        plugin_type="recall_backend",
        ctx_handle="recall_registry",
        provider_module="recall_backend",
        template="recall_backend_plugin.py",
        consumed_by="operator/bridges/shared/adapter.py",
        dead_reason=None,
        invariant="Must NOT store un-redacted text.",
    ),
    ExtensionSurface(
        plugin_type="audit_backend",
        ctx_handle="audit_registry",
        provider_module="audit_backend",
        template="audit_backend_plugin.py",
        consumed_by="core/gateway/corvin_gateway/app.py",
        dead_reason=None,
        invariant=(
            "ADDITIVE ONLY (ADR-0233): receives a COPY after the core write has "
            "committed; can never suppress, delay or rewrite the core chain."
        ),
    ),
    # ── Registers, but nothing invokes it ───────────────────────────────────
    ExtensionSurface(
        plugin_type="user_backend",
        ctx_handle="user_registry",
        provider_module="user_backend",
        template="user_backend_plugin.py",
        consumed_by=None,
        dead_reason=(
            "providers/user_backend.py implements authenticate()/get_user() with "
            "a circuit breaker and deny-on-error, but no caller exists outside "
            "corvin_plugins — the auth path never consults an installed backend."
        ),
        invariant=(
            "Failure, timeout or rejection means DENY — never fall back to "
            "guest (ADR-0233)."
        ),
    ),
    ExtensionSurface(
        plugin_type="stt_provider",
        ctx_handle="stt_registry",
        provider_module="stt_provider",
        template=None,
        consumed_by=None,
        dead_reason=(
            "Registry and ctx handle exist; nothing calls get_active(). L23 "
            "resolves its own chain and never asks the registry."
        ),
        invariant=(
            "Audit metadata ONLY — never the transcript text (L23, GDPR Art. 5)."
        ),
    ),
    ExtensionSurface(
        plugin_type="data_connector",
        ctx_handle="data_connector_registry",
        provider_module="data_connector",
        template=None,
        consumed_by=None,
        dead_reason=(
            "Registry and ctx handle exist; nothing calls get_active(). L24 "
            "resolves its own DSI adapters and never asks the registry."
        ),
        invariant=(
            "Must respect the L34 classification of what it returns; never "
            "widen a classification on the way out."
        ),
    ),
    # ── Handle exists but is never populated at boot ────────────────────────
    ExtensionSurface(
        plugin_type="compute_engine",
        ctx_handle="compute_registry",
        provider_module=None,
        template="compute_engine_plugin.py",
        consumed_by=None,
        dead_reason=(
            "bootstrap_all(**registries) forwards compute_registry, but the sole "
            "production call site (gateway app.py) passes none — so the handle is "
            "always None and the template's guard skips registration."
        ),
        invariant="Must honour gate dispatch and abort promptly (ADR-0029).",
    ),
    ExtensionSurface(
        plugin_type="worker_engine",
        ctx_handle="engine_factory",
        provider_module=None,
        template="worker_engine_plugin.py",
        consumed_by=None,
        dead_reason=(
            "Same as compute_engine: engine_factory is never passed to "
            "bootstrap_all by the gateway, so the handle is always None."
        ),
        invariant=(
            "Must not import `anthropic` directly — CI AST lint enforces this."
        ),
    ),
    ExtensionSurface(
        plugin_type="bridge_channel",
        ctx_handle="channel_registry",
        provider_module=None,
        template="bridge_channel_plugin.py",
        consumed_by=None,
        dead_reason=(
            "Same as compute_engine: channel_registry is never passed to "
            "bootstrap_all by the gateway, so the handle is always None."
        ),
        invariant=(
            "A send path that returns instead of raising on failure silently "
            "drops delivered answers — raise."
        ),
    ),
)


_BY_TYPE = {s.plugin_type: s for s in SURFACES}


def surface_for(plugin_type: str) -> ExtensionSurface:
    """Return the surface for ``plugin_type``.

    Raises KeyError listing the known types, because the most common reason to
    land here is a typo and the second most common is inventing a type name.
    """
    try:
        return _BY_TYPE[plugin_type]
    except KeyError:
        known = ", ".join(sorted(_BY_TYPE))
        raise KeyError(
            f"unknown plugin_type {plugin_type!r} — known types: {known}"
        ) from None


def buildable_types() -> tuple[str, ...]:
    """Types that ship a template, i.e. that ``corvin plugin new`` can scaffold."""
    return tuple(s.plugin_type for s in SURFACES if s.template is not None)


def consumed_types() -> tuple[str, ...]:
    """Types something actually invokes. The honest answer to "will this run?"."""
    return tuple(s.plugin_type for s in SURFACES if s.consumed)


def unconsumed_types() -> tuple[str, ...]:
    """Types that load and register but are never invoked."""
    return tuple(s.plugin_type for s in SURFACES if not s.consumed)


def all_types() -> tuple[str, ...]:
    return tuple(s.plugin_type for s in SURFACES)


__all__ = [
    "ExtensionSurface",
    "SURFACES",
    "surface_for",
    "buildable_types",
    "consumed_types",
    "unconsumed_types",
    "all_types",
]

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

That failure mode is not hypothetical here. Three distinct causes are live, and
the fourth — "the handle is never passed" — turned out not to exist at all:

* **No consumer.** ``stt_provider`` and ``data_connector`` have a registry, a
  ``PluginContext`` handle, and nothing outside ``corvin_plugins`` that calls
  ``get_active()``. Wiring a call site is the whole fix.
* **The consumer does not exist.** ``user_backend`` has a fully implemented
  ``authenticate()`` with circuit breaker and deny-on-error, and CorvinOS has no
  credential auth path to call it from. That is a feature project, not a call
  site.
* **The target is not wired to anything that dispatches.** ``worker_engine``
  (L22 builds engines from a hard-coded dict and has no ``register()``) and
  ``bridge_channel`` (no ``channel_registry`` class exists anywhere).

**On the cause that was never real.** This docstring used to describe a "handle
never populated" class covering ``compute_engine`` / ``worker_engine`` /
``bridge_channel``, and to say the gateway "simply passes none of them". Every
part of that was misleading. Passing the handle would have changed nothing for
any of the three: two have no target, and ``compute_engine``'s target had no
reader and lived in another process. ``compute_engine`` was fixed by loading
those plugins in the **compute worker**, which is where ``WorkerServer``
dispatches — not by populating anything in the gateway.

The lesson is worth keeping in the file it cost the most: the question to ask of
a dead surface is not "which handle is unpassed" but **"which process consumes
this, and does a plugin load there"**.

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
        plugin_type="web_surface",
        # No provider registry: a WebSurface does not self-register in
        # on_load(). The host DISCOVERS loaded web_surface plugins through the
        # plugin registry by plugin_id / plugin_type and calls spa_dist_dir()
        # (ADR-0356 P2.5, ADR-0365 P7); plugin_id is the identity it is found by.
        ctx_handle="plugin_id",
        provider_module=None,
        template=None,
        consumed_by="core/console/corvin_console/routes/capabilities.py",
        dead_reason=None,
        invariant=(
            "spa_dist_dir() must return None (never raise) when no bundle is "
            "built; a WebSurface never starts its own server."
        ),
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
        plugin_type="context_retriever",
        ctx_handle="context_retriever_registry",
        provider_module="context_retriever",
        template=None,
        # TWO consuming surfaces (ADR-0599). The map carries one primary
        # `consumed_by` per row; the CEL memory stage is named here because it is
        # the reference seam, and the second surface — the TDE step assembler,
        # operator/orchestration/tde/worker_ipc.py::_build_prompt — is named in
        # the invariant below so both call sites are discoverable from this row.
        consumed_by="operator/context_engineering/stages/memory.py",
        dead_reason=None,
        invariant=(
            "select() only NARROWS/REORDERS candidates, never adds, and MUST NOT "
            "raise; both seams are fail-open (CEL stages/memory.py and TDE "
            "worker_ipc.py::_build_prompt). The TDE seam additionally enforces a "
            "hard post-condition: a returned slice over _SNAPSHOT_MAX_CHARS "
            "(argv/E2BIG ceiling) is rejected and the raw truncation is used."
        ),
    ),
    ExtensionSurface(
        plugin_type="audit_backend",
        ctx_handle="audit_registry",
        provider_module="audit_backend",
        template="audit_backend_plugin.py",
        # The FAN-OUT site, which is what an author needs to find: audit.py binds
        # `_audit_sink` to this provider module and calls `fanout()` only after
        # `core_write_committed` — the ordering the invariant below names.
        # This row used to point at core/gateway/corvin_gateway/app.py, which
        # touches audit_backend exactly once, in a shutdown `drain_now()` flush.
        # The guard test admitted it because that file contains the string
        # "audit_backend"; a reader following the map landed in a teardown path
        # and found nothing that consumes anything (verified 2026-07-27).
        consumed_by="operator/bridges/shared/audit.py",
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
            "No credential auth path exists to call it. The only live login is "
            "console_api local-login, which is localhost-only and "
            "credential-less — the TCP peer IS the authorisation, so there is "
            "nothing to hand authenticate(). gateway/console_api.py's "
            "/auth/login is dead demo code imported by nothing, and OIDC is "
            "unbuilt (auth_routes.py). Wiring the backend into local-login "
            "would hand it empty credentials, a correct backend rejects those, "
            "and deny on the only login locks the operator out of their own "
            "install. Verified 2026-07-27; see PLUGIN_SYSTEM_ACTIVATION_PLAN "
            "Stage 2 for the three options and why option 3 was chosen."
        ),
        invariant=(
            "Failure, timeout or rejection means DENY — never fall back to "
            "guest (ADR-0233). NOTE: on today's surfaces this invariant has no "
            "subject — a localhost-only login admits no guest to fall back to. "
            "It binds the first credential path that is built, not any path "
            "that exists now."
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
        consumed_by="core/compute/corvin_compute/cli.py",
        dead_reason=None,
        invariant=(
            "Must honour gate dispatch and abort promptly (ADR-0029). Loaded in "
            "the COMPUTE WORKER process, not the gateway: that is where "
            "WorkerServer dispatches. The worker loads only plugin_type="
            "compute_engine — without that filter it would also start the "
            "tenant's bridge daemons (ADR-0238 duplicate-start)."
        ),
    ),
    ExtensionSurface(
        plugin_type="worker_engine",
        ctx_handle="engine_factory",
        provider_module=None,
        template="worker_engine_plugin.py",
        consumed_by=None,
        dead_reason=(
            "The target has no registration API. Engines come from a "
            "hard-coded _ENGINE_BUILDERS dict in "
            "operator/bridges/shared/engine_registry.py with three entries and "
            "no register(), so a plugin cannot enter itself even with the "
            "handle populated — passing engine_factory would change nothing. "
            "The compute_engine row was fixed by loading plugins in the "
            "process that consumes them; the same question here has no answer "
            "yet, because no process offers L22 a registration surface at all. "
            "ADR-0245 deferral. Verified 2026-07-27."
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
            "The target does not exist. There is no channel_registry class "
            "anywhere in the tree; the only references are in "
            "templates/bridge_channel_plugin.py, which calls register() against "
            "an API nobody wrote. Populating the handle is impossible, not "
            "merely undone: there is nothing to populate it WITH. Needs the "
            "bridge_channel design question ADR-0245 deferred. "
            "Verified 2026-07-27."
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

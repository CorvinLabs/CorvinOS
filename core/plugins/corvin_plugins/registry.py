"""PluginRegistry — discovery, lifecycle, health aggregation (ADR-0030)."""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from . import circuit_breaker as _breakers
from . import loading as _loading
from .manifest import _PRIVILEGED_BOOT_LAYERS, BootLayer
from .protocol import (
    CorvinPlugin,
    HealthStatus,
    PluginAlreadyRegistered,
    PluginContext,
    PluginDisableRefused,
    PluginNotFound,
    PluginReplacementRefused,
)

log = logging.getLogger(__name__)


def _structured(plugin_id: str | None = None):
    """A CorvinLogger when the observability package is present, else None.

    Optional exactly like cowork/forge: the plugin registry must keep working in a
    layout that ships without core/observability.
    """
    try:
        import sys as _sys
        from pathlib import Path as _Path

        obs = _Path(__file__).resolve().parents[2] / "observability"
        if obs.is_dir() and str(obs) not in _sys.path:
            # append, not insert(0) — same shadowing reasoning as elsewhere.
            _sys.path.append(str(obs))
        from corvin_logging import get_logger  # type: ignore[import-not-found]

        return get_logger("plugins", plugin_id)
    except Exception:  # noqa: BLE001
        return None


#: Cap on plugin-supplied health text. The audit chain is append-only and
#: hash-chained: a record cannot be edited afterwards (rewriting audit.jsonl breaks
#: the chain), so an oversized or leaky message is permanent.
MAX_STATUS_MESSAGE_CHARS = 240


def _scrub_plugin_text(value: str) -> str:
    """Redact PII shapes in plugin-supplied health text.  Fails CLOSED.

    ``health_check()`` returning ``ok=False`` with a helpful diagnostic is the
    NORMAL path, and its message flows into (a) the hash-chained audit log via
    plugin.health_alert, (b) the healing record's reason via plugin.healing_action,
    (c) the Console, (d) the log stream. The exception path is already reduced to a
    class name; the cooperative path was passed through verbatim, so a plugin author
    writing "auth failed for alice@corp.com" or "cannot reach
    postgres://u:pw@host/db" wrote personal data into a record nobody can redact
    later.

    When the scrubber is unavailable the free text is DROPPED, not forwarded: losing
    a diagnostic string is recoverable, an un-redactable audit record is not.
    """
    if not isinstance(value, str) or not value:
        return ""
    clipped = value[:MAX_STATUS_MESSAGE_CHARS]
    try:
        from corvin_logging.scrubber import scrub_text  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        _structured()  # side effect: puts core/observability on sys.path
        try:
            from corvin_logging.scrubber import scrub_text  # type: ignore[import-not-found]
        except Exception:  # noqa: BLE001
            return "[health message withheld — scrubber unavailable]"
    scrubbed, _ = scrub_text(clipped)
    if len(value) > MAX_STATUS_MESSAGE_CHARS:
        scrubbed += f"…[+{len(value) - MAX_STATUS_MESSAGE_CHARS} chars]"
    return scrubbed


def _scrub_plugin_details(details: object) -> dict:
    """Same gate for the plugin's free-form details dict (shown in the Console)."""
    if not isinstance(details, dict):
        return {}
    try:
        from corvin_logging.scrubber import scrub  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        _structured()
        try:
            from corvin_logging.scrubber import scrub  # type: ignore[import-not-found]
        except Exception:  # noqa: BLE001
            return {"withheld": "scrubber unavailable"}
    cleaned, _ = scrub(details)
    return cleaned if isinstance(cleaned, dict) else {}


#: Every provider registry, by module name.  Release walks ALL of them by owner
#: id rather than picking one from ``plugin_type`` — a plugin can take a slot
#: that its type does not name, because ``build_context`` hands every registry
#: to every plugin.
_PROVIDER_MODULE_NAMES: tuple[str, ...] = (
    "audit_backend", "user_backend", "stt_provider", "data_connector",
    "recall_backend", "router_backend", "summary_provider", "notification_backend",
    "context_retriever",  # ADR-0599
)


#: Wall-clock ceiling for one ``health_check()``.  The protocol already says
#: "must not block for more than 2 s"; this is what makes the sentence true.
HEALTH_CHECK_DEADLINE_S = 2.0

#: Wall-clock ceiling for one ``on_load()``.  ``register()`` ran ``on_load``
#: synchronously under the plugin's operation lock with no deadline, so a wedged
#: network connect inside one plugin's ``on_load`` held ``boot_platform()`` —
#: and with it both shipped hosts — forever (2026-09-03 finding A3).  A load
#: that does not return in time is a FAILED load: rolled back like a raise, and
#: fatal on the compliance boot layer like every other compliance load failure.
LOAD_DEADLINE_S = 30.0


class HealthCheckTimeout(TimeoutError):
    """A plugin's ``health_check()`` did not answer within its deadline."""


class PluginLoadTimeout(TimeoutError):
    """A plugin's ``on_load()`` did not return within :data:`LOAD_DEADLINE_S`."""


def _call_with_deadline(
    fn: "Callable[[], Any]",
    deadline_s: float,
    plugin_id: str,
    *,
    what: str = "health_check",
    timeout_exc: "type[TimeoutError]" = HealthCheckTimeout,
):
    """Run ``fn`` and give up waiting after ``deadline_s``.

    The breaker counts raises and cooperative ``ok=False`` — a health check that
    simply never returns is neither, so nothing capped it. And this runs on
    every ``GET /api/admin/*``: one wedged plugin held the whole admin surface,
    with no recovery path and no signal. The compliance boot layer made it worse
    rather than better, because there containment is deliberately off.

    The worker thread is a daemon and is abandoned, not killed — Python cannot
    kill a thread. Abandoning it costs one stuck thread; waiting on it costs the
    admin API. A plugin that wedges repeatedly therefore leaks threads, which is
    the visible symptom of a bug it already has.

    ``fn`` runs inside a COPY of the caller's ``contextvars`` context: a bare
    worker thread does not inherit ContextVars, and ``loading.current()`` — the
    attribution every provider registry reads during ``on_load`` — is one. The
    same helper serves ``on_load`` (``what="on_load"``, :class:`PluginLoadTimeout`).
    """
    import concurrent.futures as _futures
    import contextvars as _contextvars

    # NOT a context manager: ThreadPoolExecutor.__exit__ joins its worker, which
    # is precisely what must not happen when the worker is wedged — the deadline
    # would then be a lie. shutdown(wait=False) abandons it instead.
    pool = _futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix=f"{what[:12]}-{plugin_id[:24]}"
    )
    future = pool.submit(_contextvars.copy_context().run, fn)
    try:
        return future.result(timeout=deadline_s)
    except _futures.TimeoutError:
        raise timeout_exc(
            f"{what} for {plugin_id!r} exceeded {deadline_s:.1f}s"
        ) from None
    finally:
        # An earlier version cleared `concurrent.futures.thread._threads_queues`
        # here. That map is PROCESS-WIDE, not pool-local, and `_python_exit`
        # iterates it at interpreter shutdown to wake and join every pool in the
        # process. Clearing it meant that after ONE wedged health check, no
        # ThreadPoolExecutor anywhere — compute, workflows, boot healer, the
        # bridge adapter, every `run_in_executor(None, …)` — was joined at exit,
        # so their in-flight work was lost silently. Same class as the outbox
        # incident: a local convenience that quietly broke a global contract.
        pool.shutdown(wait=False)


def _detach_provider_slot(plugin: CorvinPlugin) -> None:
    """Release every provider slot ``plugin`` took, by plugin identity.

    Two earlier versions of this got the identity wrong, in opposite ways:

    * matching the slot's CONTENTS against the plugin object missed a plugin
      that installed a helper (``set_active(self._sink)``), which then kept
      receiving events after the operator disabled it;
    * picking one module from ``plugin_type`` missed a plugin whose type has no
      provider module at all, and nothing else could ever release that slot.

    Ownership is recorded when the slot is taken (``providers.*.set_active``
    reads ``loading.current()``), so the question "is this slot yours" has one
    correct answer instead of two heuristics. Guarded: tidying a slot must never
    turn an unload into an exception for the caller.
    """
    plugin_id = getattr(plugin, "plugin_id", "")
    if not plugin_id:
        return
    from importlib import import_module

    for name in _PROVIDER_MODULE_NAMES:
        try:
            module = import_module(f".providers.{name}", package=__package__)
            # Forget the breaker keyed on the OBJECT too, not just the one keyed
            # on the plugin. A provider whose object carried no plugin_id got the
            # key `anonymous:<Class>`, and `forget(plugin_id)` never touched it —
            # so a reload inherited an open breaker from the previous life.
            backend = module.get_active()
            # An UNOWNED slot holding this plugin's own object is released too.
            # A plugin that connects asynchronously — `on_load` starts a thread
            # that calls `set_active` when the connection is up — is the normal
            # shape, not an attack, and ContextVars do not cross into that
            # thread, so the slot ends up ownerless. Without this it was never
            # released: `release_owned_by` finds no owner, and the next turn
            # writes into a backend whose `on_unload` has already run.
            released = False
            if backend is not None and module.owner_plugin_id() is None:
                # An OWNERLESS slot is released when this plugin unloads, full
                # stop — no object-identity test.
                #
                # Requiring `backend is plugin` covered each of the two known
                # shapes alone but not their combination: a plugin that installs
                # a HELPER object (round 3) FROM A WORKER THREAD (round 5) has
                # neither an owner (ContextVars do not cross threads) nor object
                # identity (the helper is not the plugin). After the operator's
                # disable, that helper went on receiving every audit event of
                # every tenant — the round-3 defect, restored through the
                # round-5 axis, on a GDPR Art. 30/32 surface.
                #
                # Releasing an ownerless slot is safe: a slot taken during a
                # load HAS an owner, so anything ownerless was taken outside one
                # and has no other claimant. The cost of being wrong is a
                # provider falling back to its bundled default; the cost of the
                # previous rule was a disabled plugin still reading everyone's
                # audit trail.
                released = bool(module.clear_if_active(backend))
                if released:
                    log.debug(
                        "released ownerless %s slot on unload of %r", name, plugin_id
                    )
            if module.release_owned_by(plugin_id):
                log.debug("released %s slot held by %r", name, plugin_id)
                released = True
            # Forget the breaker keyed on the OBJECT whenever the slot was
            # released, by EITHER path. Keying it only to the owned path left the
            # ownerless case — the async-connect pattern — inheriting an open
            # breaker from its previous life on the next load.
            if released and backend is not None:
                _breakers.forget(
                    getattr(backend, "plugin_id", None)
                    or f"anonymous:{type(backend).__name__}"
                )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "provider release for %r on %s failed (%s)",
                plugin_id, name, type(exc).__name__,
            )


def _revoke_hooks(plugin_id: str) -> None:
    """Remove every extension hook registered by ``plugin_id``.

    Guarded and lazy for the same reason as the ownership check: the bus is
    optional in a stripped layout, and tidying hooks must never turn an unload
    into an exception.
    """
    try:
        from .extension_points import unregister_all

        removed = unregister_all(plugin_id)
        if removed:
            log.debug("removed %d extension hook(s) of %r", removed, plugin_id)
    except Exception as exc:  # noqa: BLE001
        log.error("hook removal for %r failed (%s)", plugin_id, type(exc).__name__)


def _verify_hook_ownership(plugin_id: str, tenant_id: str) -> None:
    """Revoke extension hooks this plugin claimed for a foreign tenant.

    Guarded and lazy: the bus is optional in a stripped layout, and tidying
    hooks must never turn a successful load into an exception.
    """
    try:
        from .extension_points import verify_owner

        revoked = verify_owner(plugin_id, tenant_id)
        if revoked:
            log.warning(
                "revoked %d extension hook(s) %r had claimed for other tenants",
                revoked, plugin_id,
            )
    except Exception as exc:  # noqa: BLE001
        log.error(
            "hook ownership check for %r failed (%s)", plugin_id, type(exc).__name__
        )


class PluginRegistry:
    """Thread-safe registry for CorvinPlugin instances.

    Handles registration, lifecycle calls, health aggregation, and
    typed lookups.  One global instance is provided as ``_registry``
    at module level; convenience functions wrap it.
    """

    def __init__(self) -> None:
        self._plugins: dict[str, CorvinPlugin] = {}
        self._contexts: dict[str, PluginContext] = {}
        self._boot_layers: dict[str, BootLayer] = {}
        self._lock = threading.Lock()
        #: One lock per plugin_id, held across a WHOLE register or unregister.
        #: `self._lock` only guards the maps and is released before any call
        #: into plugin code, which is right — a slow on_load must not freeze the
        #: registry for everyone — but it left register and unregister able to
        #: interleave for the SAME plugin: on_unload ran while on_load was still
        #: in progress, both reported success, the plugin was gone, its resource
        #: stayed open, and the append-only audit chain recorded `unloaded`
        #: before `loaded`.
        self._op_locks: dict[str, threading.RLock] = {}
        #: plugin_id -> the tenant it was last loaded for, kept AFTER unload.
        #:
        #: The tenant check can only refuse what it can resolve, and after
        #: `unregister` the plugin is in neither the load context nor the
        #: registry — so a closure, timer or thread that outlived it could
        #: register an extension hook for any tenant it liked, on a fail-closed
        #: gate, and nothing would revoke it. Remembering the association closes
        #: that window: the id stays bound to its tenant even when the object is
        #: gone.
        self._tenant_history: dict[str, str] = {}
        #: plugin_id -> registration epoch when it was granted a privileged layer.
        #: Prevents re-registration tricks where a plugin unregisters and re-registers
        #: to claim a privilege it was not originally granted.  Written in
        #: _register_locked() after successful on_load() with privileged layer (ADR-0233 D5).
        self._privileged_registration_epoch: dict[str, int] = {}
        #: plugin_id -> epoch when this id was unregistered in current epoch.
        #: Prevents a thread spawned during on_load() from unregistering itself and
        #: re-registering in the same epoch with elevated privilege (ADR-0233 D5).
        #: Cleared on each epoch increment.
        self._unregistered_this_epoch: dict[str, int] = {}
        #: Global registration epoch counter. Increments on each major bootstrap
        #: sequence (boot_platform call), so threads spawned late in a load cannot
        #: re-register in a different epoch.
        self._registration_epoch: int = 0

    #: Cap on the tenant-history map, same reasoning as the op locks: an
    #: unbounded stream of ids must not grow it without limit.
    MAX_TENANT_HISTORY = 4096

    #: Cap on per-plugin operation locks, so an unbounded stream of unknown ids
    #: cannot grow the map. Same shape as healing.MAX_PLUGIN_LOCKS.
    MAX_OP_LOCKS = 1024

    def _op_lock(self, plugin_id: str) -> threading.RLock:
        """The lock serialising lifecycle operations for one plugin.

        Re-entrant on purpose: ``on_load`` is allowed to call back into the
        registry for its OWN id (the collision guard is part of the contract),
        and a plain lock would deadlock exactly there.
        """
        with self._lock:
            lock = self._op_locks.get(plugin_id)
            if lock is None:
                if len(self._op_locks) >= self.MAX_OP_LOCKS:
                    # Bounded rather than unbounded. Dropping the map can only
                    # cost serialisation for ids currently mid-operation, which
                    # needs >1024 distinct plugins in flight to reach.
                    self._op_locks.clear()
                lock = self._op_locks[plugin_id] = threading.RLock()
            return lock

    def _advance_epoch(self) -> None:
        """Increment registration epoch and clear same-epoch tracking.

        Called from bootstrap.boot_platform() before loading any plugins.
        Prevents threads spawned in the PREVIOUS epoch from escalating in the
        current epoch (ADR-0233 D5).  The _privileged_registration_epoch dict
        is NOT cleared — it remembers which plugins were granted privilege,
        across ALL epochs.  Only _unregistered_this_epoch is cleared, since
        "unregistered in the previous epoch" is not a valid reason to block
        re-registration in a new epoch.
        """
        with self._lock:
            self._registration_epoch += 1
            self._unregistered_this_epoch.clear()

    # ── Registration ──────────────────────────────────────────────────────────

    def _resolve_boot_layer(
        self, plugin: CorvinPlugin, boot_layer: BootLayer | str | None, plugin_id: str | None = None
    ) -> BootLayer:
        """Decide which boot layer a runtime plugin object belongs to.

        Order: explicit argument (the caller holds the provenance chain — the
        bootstrap paths pass the value they already gated) → the plugin object's
        own ``boot_layer`` attribute, **capped at the unprivileged boot layers**
        → ``installed``.

        The cap is the load-bearing part. An attribute on a plugin OBJECT has
        passed no gate at all: there is no ``PluginRecord`` behind it, so
        neither ``_PRIVILEGED_BOOT_LAYERS`` nor the tenant-scope downgrade in
        ``bootstrap._declared_boot_layer`` has run. Honouring ``self.boot_layer =
        "compliance"`` would let any plugin promote itself the moment something
        re-registers it without an explicit boot layer — which is exactly what a
        healing soft-restart does. Trust travels with the caller, not with the
        object being registered.

        ADR-0233 D5: Prevents privilege escalation via unregister + re-register by
        checking the registration epoch. A plugin re-registering in a different
        epoch after unload is downgraded (thread escape mitigation). Also blocks
        re-registration in the same epoch after unload (prevents same-epoch thread
        escapes where a spawned thread unregisters and re-registers with privilege).
        """
        if boot_layer is not None:
            requested = BootLayer(boot_layer)
            if requested in _PRIVILEGED_BOOT_LAYERS:
                # The argument is trusted because the CALLER holds the
                # provenance chain — bootstrap_global, which only ever runs from
                # code shipped in the wheel. A plugin's on_load is not that
                # caller, and `register` is an importable module-level function,
                # so an installed plugin could register a second object as
                # `compliance` from inside its own load: no PluginRecord, no
                # consent gate, no L34/L35 declarations, and not disableable.
                #
                # Being inside ANY on_load is the disqualifier. bootstrap_global
                # registers before any plugin is loading, so this costs the
                # legitimate path nothing.
                who = _loading.current()
                if who is not None:
                    log.error(
                        "plugin %r tried to register %r on boot layer %s from "
                        "inside its own on_load — downgraded to installed",
                        who.plugin_id, getattr(plugin, "plugin_id", "?"),
                        requested.value,
                    )
                    return BootLayer.INSTALLED

                # ADR-0233 D5: Check if this plugin_id was already granted a
                # privilege (in ANY epoch). If so, prevent re-escalation attempts:
                # (a) re-registration in a DIFFERENT epoch (thread spawned in old boot)
                # (b) re-registration in the SAME epoch after unload (same-epoch thread escape)
                pid = plugin_id or getattr(plugin, "plugin_id", "")
                with self._lock:
                    already_privileged_epoch = self._privileged_registration_epoch.get(pid)
                    unregistered_epoch = self._unregistered_this_epoch.get(pid)

                if already_privileged_epoch is not None:
                    if already_privileged_epoch != self._registration_epoch:
                        # Cross-epoch re-escalation: thread from old boot trying to escape
                        log.error(
                            "plugin %r attempted to re-register on privileged layer "
                            "across epochs (original epoch %d, current epoch %d) — "
                            "downgraded to installed",
                            pid, already_privileged_epoch, self._registration_epoch,
                        )
                        return BootLayer.INSTALLED
                    else:
                        # Same-epoch re-escalation: thread from this boot trying to re-register
                        # after being unregistered (unregister + re-register attack).
                        # This happens when a thread spawned during on_load() outlives the
                        # loading context, calls unregister(), and tries to re-register with
                        # privilege while _loading.current() is None.
                        if unregistered_epoch == self._registration_epoch:
                            log.error(
                                "plugin %r attempted to re-register on privileged layer %s "
                                "within the same epoch (epoch %d) after unload — this indicates "
                                "a thread-escape attack; downgraded to installed",
                                pid, requested.value, self._registration_epoch,
                            )
                            return BootLayer.INSTALLED
            return requested
        declared = getattr(plugin, "boot_layer", None)
        if declared is None:
            return BootLayer.INSTALLED
        try:
            resolved = BootLayer(declared)
        except (ValueError, TypeError):
            log.warning(
                "plugin %r declares unknown boot_layer %r — treating as installed",
                getattr(plugin, "plugin_id", "?"), declared,
            )
            return BootLayer.INSTALLED
        if resolved in _PRIVILEGED_BOOT_LAYERS:
            log.warning(
                "plugin %r claims privileged boot_layer %s on its own object — "
                "treating as installed; privileged boot layers are assigned by "
                "the caller, never self-declared",
                getattr(plugin, "plugin_id", "?"), resolved.value,
            )
            return BootLayer.INSTALLED
        return resolved

    def register(
        self,
        plugin: CorvinPlugin,
        ctx: PluginContext,
        *,
        boot_layer: BootLayer | str | None = None,
    ) -> None:
        """Call plugin.on_load(ctx) and store the plugin.

        Raises PluginAlreadyRegistered if plugin.plugin_id is already registered.
        ``boot_layer`` (ADR-0243) records which boot layer the plugin belongs to;
        it is keyword-only and defaults to the least privileged value.
        """
        resolved = self._resolve_boot_layer(plugin, boot_layer, plugin_id=plugin.plugin_id)
        with self._op_lock(plugin.plugin_id):
            self._register_locked(plugin, ctx, resolved)

    def _register_locked(
        self, plugin: CorvinPlugin, ctx: PluginContext, resolved: BootLayer
    ) -> None:
        """The body of :meth:`register`, under this plugin's operation lock."""
        with self._lock:
            if plugin.plugin_id in self._plugins:
                raise PluginAlreadyRegistered(
                    f"plugin_id {plugin.plugin_id!r} is already registered"
                )
            # Optimistically reserve the slot before on_load so a re-entrant
            # call from on_load() (e.g. in tests) also gets the collision guard.
            self._plugins[plugin.plugin_id] = plugin
            self._contexts[plugin.plugin_id] = ctx
            self._boot_layers[plugin.plugin_id] = resolved
            if len(self._tenant_history) >= self.MAX_TENANT_HISTORY:
                self._tenant_history.clear()
            self._tenant_history[plugin.plugin_id] = ctx.tenant_id

        try:
            # Mark who is loading, for the whole duration of on_load(). Provider
            # registries and the extension bus read this instead of trying to
            # work out the caller from an object or a plugin_type — see
            # `loading.py` for the three defects that came from guessing.
            #
            # Bounded by LOAD_DEADLINE_S (finding A3): on_load runs on a worker
            # that carries a copy of this context (so `loading.current()` still
            # attributes it); a load that overruns raises PluginLoadTimeout and
            # is rolled back below exactly like a load that raised. The worker
            # is abandoned, not killed — an on_load that later "finishes" in
            # that orphan thread finds its registry slot already released.
            with _loading.loading(plugin.plugin_id, ctx.tenant_id):
                _call_with_deadline(
                    lambda: plugin.on_load(ctx),
                    LOAD_DEADLINE_S,
                    plugin.plugin_id,
                    what="on_load",
                    timeout_exc=PluginLoadTimeout,
                )
        except Exception:
            # on_load failed — roll back EVERYTHING it managed to take, not just
            # the registry maps. A half-loaded plugin can already hold a provider
            # slot, an extension hook (on the fail-closed workflow gate, with the
            # callable of a half-initialised object) and a breaker with failures
            # on it. Releasing only the slot left the other two behind, and
            # nothing would ever come back for them: the plugin is not
            # registered, so no unregister will run.
            _detach_provider_slot(plugin)
            _revoke_hooks(plugin.plugin_id)
            _breakers.forget(plugin.plugin_id)
            with self._lock:
                self._plugins.pop(plugin.plugin_id, None)
                self._contexts.pop(plugin.plugin_id, None)
                self._boot_layers.pop(plugin.plugin_id, None)
            raise

        # ADR-0233 D5: Record that this plugin_id was granted this privilege in this epoch.
        # Prevents a thread spawned during on_load() from re-escalating after the
        # loading context resets. If a plugin unregisters and tries to re-register
        # in the same epoch, this epoch check will catch it.
        if resolved in _PRIVILEGED_BOOT_LAYERS:
            with self._lock:
                self._privileged_registration_epoch[plugin.plugin_id] = self._registration_epoch

        # A hook this plugin claimed BEFORE it was loaded — from __init__, where
        # no check could resolve it — is revoked now that its real tenant is
        # known. Registration is not the last word; loading is.
        _verify_hook_ownership(plugin.plugin_id, ctx.tenant_id)

        log.info(
            "plugin loaded: id=%r type=%r version=%r tenant=%r",
            plugin.plugin_id, plugin.plugin_type, plugin.version, ctx.tenant_id,
        )
        if (slog := _structured(plugin.plugin_id)) is not None:
            slog.info(
                "plugin loaded",
                operation="on_load",
                context={"plugin_type": plugin.plugin_type, "version": plugin.version},
            )
        ctx.audit_emit("plugin.loaded", {
            "plugin_id": plugin.plugin_id,
            "plugin_type": plugin.plugin_type,
            "boot_layer": resolved.value,
            "version": plugin.version,
            "tenant_id": ctx.tenant_id,
        })

    def unregister(self, plugin_id: str, *, operator_initiated: bool = False) -> None:
        """Call plugin.on_unload() and remove it from the registry.

        Raises PluginNotFound if plugin_id is not registered.

        ``operator_initiated`` separates the two callers that both end up here.
        Shutdown, hot-reload and replacement are machinery and may unload
        anything.  An operator action routed through the Console or the admin API
        may not switch off a compliance-boot-layer plugin, so it passes True and
        gets :class:`PluginDisableRefused`.  Without the distinction the admin
        surface could reach past :meth:`disable` and unload the audit writer by
        calling the primitive directly.
        """
        if operator_initiated and not self.can_disable(plugin_id):
            raise PluginDisableRefused(
                f"{plugin_id!r} is on the compliance boot layer and cannot be disabled"
            )
        with self._op_lock(plugin_id):
            self._unregister_locked(plugin_id, operator_initiated)

    def _unregister_locked(self, plugin_id: str, operator_initiated: bool) -> None:
        """The body of :meth:`unregister`, under this plugin's operation lock."""
        with self._lock:
            plugin = self._plugins.get(plugin_id)
            ctx = self._contexts.get(plugin_id)
            if plugin is None:
                raise PluginNotFound(plugin_id)
            # pop, not del: the two maps are written together by register(), but a
            # KeyError here would abort the unload half-way — with the plugin
            # already gone from _plugins and on_unload() never called.
            self._plugins.pop(plugin_id, None)
            self._contexts.pop(plugin_id, None)
            boot_layer = self._boot_layers.pop(plugin_id, BootLayer.INSTALLED)
            # ADR-0233 D5: Track that this plugin_id was unregistered in the current epoch.
            # If a thread tries to re-register it with higher privilege in the same epoch,
            # the re-escalation check will catch it.
            self._unregistered_this_epoch[plugin_id] = self._registration_epoch

        try:
            plugin.on_unload()
        except Exception:
            log.exception("plugin %r raised during on_unload", plugin_id)

        # Release the provider slot this plugin may hold. Without this the
        # registry keeps a pointer to an object whose on_unload() has already
        # run: for a recall backend that means every later turn writes into a
        # closed database handle. Instance-checked, so a plugin that was already
        # superseded by another one of the same type does not evict the
        # survivor.
        _detach_provider_slot(plugin)

        # Drop its extension hooks too. The bus documented "call unregister_all
        # from on_unload", which puts the teardown obligation on the plugin —
        # i.e. on the actor least likely to honour it, and the one whose hook on
        # a fail-closed gate would otherwise keep firing after it is gone.
        _revoke_hooks(plugin_id)

        # Drop the breaker with the plugin: a re-registered plugin must start
        # from a clean slate, not inherit the failure count that unloaded it.
        _breakers.forget(plugin_id)

        log.info("plugin unloaded: id=%r", plugin_id)
        if (slog := _structured(plugin_id)) is not None:
            slog.info("plugin unloaded", operation="on_unload")
        if ctx is not None:
            ctx.audit_emit("plugin.unloaded", {
                "plugin_id": plugin_id,
                "boot_layer": boot_layer.value,
                "operator_initiated": operator_initiated,
                "tenant_id": ctx.tenant_id,
            })

    # ── Boot layers (ADR-0243) ────────────────────────────────────────────────

    def boot_layer_of(self, plugin_id: str) -> BootLayer:
        """Return the boot layer of a registered plugin.

        Raises PluginNotFound if plugin_id is not registered.
        """
        with self._lock:
            if plugin_id not in self._plugins:
                raise PluginNotFound(plugin_id)
            return self._boot_layers.get(plugin_id, BootLayer.INSTALLED)

    def tenant_ever_loaded_for(self, plugin_id: str) -> str | None:
        """The tenant this id was last loaded for, even after it was unloaded.

        Used by the extension bus so a hook cannot be registered for a foreign
        tenant in the window after `unregister`, where the plugin is in neither
        the load context nor the registry and the check would otherwise have
        nothing to compare against.
        """
        with self._lock:
            return self._tenant_history.get(plugin_id)

    def tenant_of(self, plugin_id: str) -> str | None:
        """The tenant this plugin instance was loaded for, or None if unknown.

        The registry is keyed by ``plugin_id`` alone — one process holds ONE
        instance per id, whichever tenant loaded it first. That is fine for the
        global boot layers, which are process-wide by construction, and it is a
        trap everywhere else: two tenants installing the same marketplace plugin
        share one object, and without this accessor an admin surface cannot tell
        "my plugin" from "someone else's plugin with the same name".
        """
        with self._lock:
            ctx = self._contexts.get(plugin_id)
        return getattr(ctx, "tenant_id", None) if ctx is not None else None

    def plugins_by_boot_layer(self, boot_layer: BootLayer | str) -> list[CorvinPlugin]:
        """Return all registered plugins on the given boot layer."""
        wanted = BootLayer(boot_layer)
        with self._lock:
            return [
                p for pid, p in self._plugins.items()
                if self._boot_layers.get(pid, BootLayer.INSTALLED) is wanted
            ]

    def can_disable(self, plugin_id: str) -> bool:
        """False for the compliance boot layer, True otherwise.

        An unregistered id answers True: "nothing here to protect".  Callers that
        need the difference between "absent" and "present but protected" ask
        :meth:`boot_layer_of`, which raises.
        """
        with self._lock:
            if plugin_id not in self._plugins:
                return True
            return self._boot_layers.get(plugin_id, BootLayer.INSTALLED) is not BootLayer.COMPLIANCE

    def restart(self, plugin_id: str) -> None:
        """Unload and reload ``plugin_id`` in place, keeping its boot layer.

        The healing orchestrator's soft restart used to be ``unregister()`` then
        ``register(boot_layer=<what it was>)``.  Those two are a public API pair,
        and the ADR-0233 D5 same-epoch guard treats exactly that sequence as the
        thread-escape attack it was written for — so every soft restart of a
        ``boot_layer=core`` plugin was "downgraded to installed" by the guard,
        silently (2026-09-03 finding A5).  A restart is ONE operation on a plugin
        the registry already granted the layer to; it is performed here, under
        the plugin's operation lock, and the same-epoch mark is cleared for it
        because it is not a re-registration.

        Machinery only: the compliance boot layer is refused outright — a window
        in which the audit writer is unregistered is not something an automatic
        actor may open (the same refusal ``HealingOrchestrator`` makes before it
        gets here).  Raises ``PluginNotFound`` if not registered; a failed
        ``on_load`` leaves the plugin UNREGISTERED (the level-1 state) with the
        same-epoch mark restored, never half-loaded.
        """
        if not self.can_disable(plugin_id):
            raise PluginDisableRefused(
                f"{plugin_id!r} is on the compliance boot layer and cannot be restarted"
            )
        with self._op_lock(plugin_id):
            with self._lock:
                plugin = self._plugins.get(plugin_id)
                ctx = self._contexts.get(plugin_id)
                boot_layer = self._boot_layers.get(plugin_id, BootLayer.INSTALLED)
            if plugin is None or ctx is None:
                raise PluginNotFound(plugin_id)
            self._unregister_locked(plugin_id, operator_initiated=False)
            # Not a re-registration: this plugin is being put back with the
            # layer the registry itself recorded for it.
            with self._lock:
                self._unregistered_this_epoch.pop(plugin_id, None)
            try:
                self._register_locked(plugin, ctx, boot_layer)
            except BaseException:
                # The plugin is unregistered again; re-arm the guard so a thread
                # that outlived the failed on_load cannot use the gap.
                with self._lock:
                    self._unregistered_this_epoch[plugin_id] = self._registration_epoch
                raise

    def disable(self, plugin_id: str) -> None:
        """Operator-facing unload.  Refuses the compliance boot layer.

        This is the ONLY entry point an admin route, a Console action or a CLI
        command may use.  It is :meth:`unregister` with ``operator_initiated``
        set, named separately so the call sites read as what they are.
        """
        self.unregister(plugin_id, operator_initiated=True)

    def replace(
        self,
        plugin: CorvinPlugin,
        ctx: PluginContext,
        *,
        replaces: str,
        boot_layer: BootLayer | str | None = None,
    ) -> None:
        """Swap a ``boot_layer=core`` reference implementation for an alternative.

        ADR-0237 "full plugin replacement".  Only the ``core`` boot layer is
        replaceable: compliance is not pluggable at all, and bundled/installed
        plugins are simply disabled and uninstalled rather than replaced.

        The old plugin is unloaded first and the new one registered second, in
        that order and not atomically — a replacement that fails on ``on_load``
        leaves the slot EMPTY rather than restoring the old object.  That is the
        honest outcome: the old plugin's ``on_unload`` has already run (sockets
        closed, handlers deregistered), so "restoring" it would hand callers an
        object whose teardown had completed.  The registry audits the gap; the
        operator re-enables the default explicitly.
        """
        with self._lock:
            if replaces not in self._plugins:
                raise PluginReplacementRefused(
                    f"{plugin.plugin_id!r} declares replaces={replaces!r}, "
                    f"which is not registered"
                )
            target_boot_layer = self._boot_layers.get(replaces, BootLayer.INSTALLED)
            already = plugin.plugin_id in self._plugins
        if target_boot_layer is not BootLayer.CORE:
            raise PluginReplacementRefused(
                f"{replaces!r} is on boot_layer {target_boot_layer.value}; only "
                f"boot_layer=core reference implementations are replaceable"
            )
        if already:
            raise PluginAlreadyRegistered(
                f"plugin_id {plugin.plugin_id!r} is already registered"
            )

        # A replacement plugin may not use this path to mint itself a privileged
        # boot layer it was never granted: the TARGET is proven to be core, the
        # replacement inherits exactly that, and nothing else is accepted.
        if boot_layer is not None and BootLayer(boot_layer) is not BootLayer.CORE:
            raise PluginReplacementRefused(
                f"a replacement inherits the target's boot layer (core); "
                f"{BootLayer(boot_layer).value!r} was requested"
            )

        # Machinery, not an operator disable: the compliance guard in unregister
        # does not apply here because the target was just proven to be core.
        self.unregister(replaces)
        try:
            self.register(plugin, ctx, boot_layer=BootLayer.CORE)
        except Exception as exc:  # noqa: BLE001
            # The audit event is written AFTER the swap succeeds, never before.
            # Emitting it up front put "X replaced Y" into an append-only,
            # hash-chained record for a replacement that then raised — a record
            # nobody can correct afterwards, asserting a state the process is
            # not in. What actually happened here is a gap: the old plugin is
            # torn down and the new one did not load.
            ctx.audit_emit("plugin.replace_failed", {
                "plugin_id": plugin.plugin_id,
                "replaces": replaces,
                "tenant_id": ctx.tenant_id,
                "error_type": type(exc).__name__,  # class only — no PII
            })
            raise
        ctx.audit_emit("plugin.replaced", {
            "plugin_id": plugin.plugin_id,
            "replaces": replaces,
            "tenant_id": ctx.tenant_id,
        })

    # ── Lookup ────────────────────────────────────────────────────────────────

    def get(self, plugin_id: str) -> CorvinPlugin:
        """Return the plugin for plugin_id.

        Raises PluginNotFound if not registered.
        """
        with self._lock:
            plugin = self._plugins.get(plugin_id)
        if plugin is None:
            raise PluginNotFound(plugin_id)
        return plugin

    # ── Health ────────────────────────────────────────────────────────────────

    def health_check_all(self) -> dict[str, HealthStatus]:
        """Call health_check() on every plugin, under its circuit breaker.

        Catches per-plugin exceptions and returns HealthStatus(ok=False, ...)
        so one broken plugin never blocks the rest.  Each result carries the
        plugin's breaker state under ``details["breaker"]`` (ADR-0233 Phase 2),
        so a plugin whose breaker is open is visibly contained rather than
        silently absent.

        A plugin whose breaker is OPEN is not called at all — that is the point
        of containment; its status reports ``ok=False`` with the breaker detail.
        """
        with self._lock:
            snapshot = list(self._plugins.items())
            layers = dict(self._boot_layers)
            # Snapshot the contexts too. Reading self._contexts later, from
            # outside the lock, meant a plugin unregistered mid-iteration
            # lost its audit context — and the health_check_failed event for
            # it silently went nowhere.
            contexts = dict(self._contexts)

        results: dict[str, HealthStatus] = {}
        for pid, plugin in snapshot:
            breaker = _breakers.get_breaker(pid)
            # ADR-0243: an open breaker is functionally a disable — the plugin
            # stops being called. For the compliance boot layer that is the
            # automatic off switch CLAUDE.md forbids, and it was reachable
            # WITHOUT the healing flag: health_check_all() records a failure on
            # every ok=False, `_health_map()` runs on every GET /api/admin/*,
            # so five ordinary page loads on a plugin whose SIEM was briefly
            # unreachable were enough to contain it. Health is still evaluated
            # and still reported — it simply may not silence a compliance
            # mechanism on its own.
            contained = layers.get(pid, BootLayer.INSTALLED) is not BootLayer.COMPLIANCE
            if contained:
                try:
                    breaker.guard()
                except _breakers.CircuitOpen as exc:
                    results[pid] = HealthStatus(
                        ok=False,
                        message="circuit open — calls contained",
                        details={
                            "breaker": breaker.stats().to_dict(),
                            "retry_in_s": round(exc.retry_in_s, 1),
                        },
                    )
                    continue

            try:
                status = _call_with_deadline(
                    plugin.health_check, HEALTH_CHECK_DEADLINE_S, pid
                )
            except Exception as exc:  # noqa: BLE001
                if contained:
                    breaker.record_failure(exc)
                log.warning("health_check failed for plugin %r: %s", pid, type(exc).__name__)
                if (slog := _structured(pid)) is not None:
                    slog.error(
                        "health_check failed",
                        operation="health_check",
                        error=exc,
                        recovered=False,
                        context={"breaker": breaker.stats().to_dict()},
                    )
                ctx = contexts.get(pid)
                if ctx is not None:
                    ctx.audit_emit("plugin.health_check_failed", {
                        "plugin_id": pid,
                        "error_type": type(exc).__name__,  # class name only — no PII
                    })
                # Exception CLASS only. str(exc) reaches the Console and the logs;
                # a plugin's message routinely carries a path, a host or a record
                # fragment, and this surface must stay PII-free.
                results[pid] = HealthStatus(
                    ok=False,
                    message=f"health_check raised {type(exc).__name__}",
                    details={"breaker": breaker.stats().to_dict()},
                )
                continue

            if status.ok:
                breaker.record_success()
            elif contained:
                # An unhealthy compliance plugin is reported, never contained:
                # containment means "stop calling it", and for this boot layer
                # that is an automatic off switch. This is the path that
                # actually fired in practice — a cooperative ok=False, not an
                # exception — because a backend being briefly unreachable is
                # the normal way a healthy plugin reports trouble.
                breaker.record_failure()
            # Scrub the PLUGIN's contribution, then merge our own trusted breaker
            # stats on top — scrubbing after the merge would run the PII patterns
            # over our own numbers for nothing.
            merged = _scrub_plugin_details(status.details)
            merged["breaker"] = breaker.stats().to_dict()
            results[pid] = HealthStatus(
                ok=status.ok,
                message=_scrub_plugin_text(status.message),
                details=merged,
            )
        return results

    # ── Filtered lookup ───────────────────────────────────────────────────────

    def plugins_by_type(self, plugin_type: str) -> list[CorvinPlugin]:
        """Return all registered plugins of the given type."""
        with self._lock:
            return [p for p in self._plugins.values() if p.plugin_type == plugin_type]

    # ── Discovery ─────────────────────────────────────────────────────────────

    def discover(self) -> list[str]:
        """Return a sorted list of all registered plugin_ids."""
        with self._lock:
            return sorted(self._plugins.keys())

    def __len__(self) -> int:
        with self._lock:
            return len(self._plugins)


# ── Module-level convenience functions ────────────────────────────────────────

_registry: PluginRegistry = PluginRegistry()


def register(
    plugin: CorvinPlugin,
    ctx: PluginContext,
    *,
    boot_layer: BootLayer | str | None = None,
) -> None:
    _registry.register(plugin, ctx, boot_layer=boot_layer)


def unregister(plugin_id: str, *, operator_initiated: bool = False) -> None:
    _registry.unregister(plugin_id, operator_initiated=operator_initiated)


def disable(plugin_id: str) -> None:
    _registry.disable(plugin_id)


def restart(plugin_id: str) -> None:
    _registry.restart(plugin_id)


def can_disable(plugin_id: str) -> bool:
    return _registry.can_disable(plugin_id)


def boot_layer_of(plugin_id: str) -> BootLayer:
    return _registry.boot_layer_of(plugin_id)


def plugins_by_boot_layer(boot_layer: BootLayer | str) -> list[CorvinPlugin]:
    return _registry.plugins_by_boot_layer(boot_layer)


def replace(
    plugin: CorvinPlugin,
    ctx: PluginContext,
    *,
    replaces: str,
    boot_layer: BootLayer | str | None = None,
) -> None:
    _registry.replace(plugin, ctx, replaces=replaces, boot_layer=boot_layer)


def get(plugin_id: str) -> CorvinPlugin:
    return _registry.get(plugin_id)


def health_check_all() -> dict[str, HealthStatus]:
    return _registry.health_check_all()


def discover() -> list[str]:
    return _registry.discover()


def get_registry() -> PluginRegistry:
    """Return the module-level PluginRegistry instance."""
    return _registry


def advance_registration_epoch() -> None:
    """Increment the registration epoch and clear same-epoch tracking (ADR-0233 D5).

    Called from bootstrap.boot_platform() before loading plugins. Prevents threads
    from a previous boot epoch from escalating privilege in the current epoch.
    """
    _registry._advance_epoch()

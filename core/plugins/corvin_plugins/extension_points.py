"""The extension-point bus — hooks into `boot_layer=core` reference code (ADR-0237).

ADR-0237 describes two ways to customise a bundled reference implementation:

* **Full replacement** — a plugin declares ``replaces: <plugin_id>`` in its
  manifest and the registry swaps the whole component
  (:meth:`corvin_plugins.registry.PluginRegistry.replace`, core boot layer only).
* **Hook-based customisation** — the default plugin keeps running and a plugin
  overrides ONE named step of it.  That second mechanism is this module.

Three separate name spaces meet here, and confusing them is the failure mode
this module is shaped to prevent:

``KNOWN_EXTENSION_POINTS``
    The points the bus knows.  Registering anything else is refused, so a typo
    ends as a raised :class:`UnknownExtensionPoint` rather than as a hook that
    is never called and never missed.
``_NEVER_EXTENSIBLE``
    Names that describe a mechanism CLAUDE.md marks as structurally immutable —
    the audit hash chain, A2A signature verification, TDE token accounting, the
    consent gate, the house-rules gate, the path gate.  They are refused with
    their own message.  An extension point on one of them is not a feature
    request, it is a compliance regression (ADR-0237 § Immutable vs. Extensible).
``_PROVIDER_POINTS``
    The eight provider registries under ``corvin_plugins.providers``.  They ARE
    extension points, they simply are not *this* one; a hook is refused with a
    pointer at the registry.  This matters most for ``audit_backend``: routing an
    audit sink through the bus would bypass the additive-only guarantee that a
    backend receives a COPY after the core write has committed.

Behavioural contract
--------------------

* **Flag off = the pre-feature path, quietly.**  With ``plugin_extension_points``
  off (the default, and also whenever the Console package is not importable at
  all) :func:`invoke` returns the caller's ``default`` without consulting any
  hook, without logging per call and without raising.
* **A hook may never take down its call site.**  A raising hook is caught; the
  exception CLASS is logged and audited — never ``str(exc)``, which routinely
  carries a path, a host or a record fragment (CLAUDE.md: no PII in log lines
  or audit details).
* **…except on a fail-closed point,** where the permissive default is the wrong
  answer.  See :data:`_FAIL_CLOSED_POINTS`.
* **Last registration wins, and is audited.**  See :func:`register_hook`.
* **A plugin registers for its own tenant, or not at all.**  ``tenant_id`` is
  checked against the tenant the plugin was LOADED for, so "last wins" cannot be
  turned into a cross-tenant takeover of somebody else's fail-closed gate.  See
  :func:`register_hook` for the exact rule and for what happens to a caller the
  registry does not know.

Nothing in this module calls a hook by itself.  Call sites are wired in a
follow-up phase; today the bus is defined, tested and documented, and every
:func:`invoke` in the platform is still the default path.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

log = logging.getLogger("corvin.plugins.extension_points")

#: The Console feature flag that gates the whole bus.  Ships dark (CLAUDE.md
#: § Feature Flags): off on a fresh install and off after an upgrade.
FLAG_ID = "plugin_extension_points"

#: Free-form text that reaches an audit detail is clipped.  A rejected point
#: name is author-supplied, and the audit chain is append-only — an oversized
#: or leaky value written there cannot be redacted afterwards.
MAX_AUDITED_NAME_CHARS = 64


# ── Exceptions ────────────────────────────────────────────────────────────────


class ExtensionPointError(Exception):
    """Base class for every refusal and denial raised by the bus."""


class UnknownExtensionPoint(ExtensionPointError):
    """The named point is not in :data:`KNOWN_EXTENSION_POINTS`.

    Fail-closed on purpose.  Silently accepting an unknown name would register a
    hook that is never invoked, and the plugin author would have no way to tell
    that from a call site that simply never fired.
    """


class ImmutableExtensionPoint(ExtensionPointError):
    """The named mechanism is structurally not extensible (ADR-0237).

    A distinct class from :class:`UnknownExtensionPoint` so the answer to "why
    was my hook refused" is "this may never have one", not "you misspelled it".
    """


class CrossTenantHookRefused(ExtensionPointError):
    """A loaded plugin named a tenant other than the one it was loaded for.

    Its own class, not :class:`ExtensionPointError`, because the answer to "why
    was my hook refused" is a different sentence from every other refusal here:
    the point is fine, the callable is fine, the plugin simply has no standing
    in that tenant.  See :func:`register_hook` for the full rule.
    """

    def __init__(self, plugin_id: str, requested: str, actual: str):
        super().__init__(
            f"plugin {plugin_id!r} is loaded for tenant {actual!r} and may not "
            f"register an extension hook for tenant {requested!r}; pass "
            f"ctx.tenant_id from its own PluginContext"
        )
        self.plugin_id = plugin_id
        self.requested_tenant_id = requested
        self.actual_tenant_id = actual


class ExtensionPointDenied(ExtensionPointError):
    """A fail-closed point could not produce an answer, so the answer is *no*.

    Deliberately an exception rather than a falsy return value, for the same
    reason :class:`~corvin_plugins.protocol.PluginDisableRefused` is: a caller
    that forgets to branch on a returned sentinel would proceed as if the gate
    had allowed the action, which is the worse of the two failure modes.
    """

    def __init__(self, point: str, reason: str):
        super().__init__(f"extension point {point!r} denied: {reason}")
        self.point = point
        self.reason = reason


# ── The points ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ExtensionPointSpec:
    """What one point is, what a hook must look like, and what happens without one."""

    name: str
    summary: str
    signature: str
    default_behavior: str
    #: True when a hook that fails must produce a DENIAL rather than the default.
    fail_closed: bool = False


#: The four points Phase 3 defines.  ADR-0237 lists a longer backlog and says
#: explicitly that Phase 3 adds 3–5 of them, not all — these four are the ones
#: whose owning subsystems already have a single, shared decision function to
#: hook (ADR-0181 provider/model selection, the shared ``delegation_policy``
#: rule, the workflow gate), so wiring them later is a call-site change and not
#: a redesign.
_POINT_SPECS: Tuple[ExtensionPointSpec, ...] = (
    ExtensionPointSpec(
        name="engine.model_selection",
        summary=(
            "Choose the model for one step, after the provider is fixed "
            "(ADR-0181)."
        ),
        signature="(request: dict) -> str | None",
        default_behavior=(
            "The bundled budget-aware selector decides.  A hook returning None "
            "means 'no opinion' and is treated exactly like no hook at all."
        ),
    ),
    ExtensionPointSpec(
        name="engine.engine_selection",
        summary="Choose the worker engine/provider for one turn (ADR-0181).",
        signature="(request: dict) -> str | None",
        default_behavior=(
            "The operator's ``spec.web_chat.worker_engine`` setting decides, "
            "resolved through the shared delegation_policy module.  A hook may "
            "not widen that: an engine the operator did not select is refused "
            "by the call site, not by the bus (CLAUDE.md § Worker Engine "
            "Selection)."
        ),
    ),
    ExtensionPointSpec(
        name="delegation.route_selection_policy",
        summary="Pick the delegation route for a turn (native / acs / tde).",
        signature="(turn: dict) -> str | None",
        default_behavior=(
            "The bundled classifier decides, and every degrade ladder still "
            "ends at ``native``.  A hook is an input to that rule, never a "
            "replacement for it."
        ),
    ),
    ExtensionPointSpec(
        name="workflow.workflow_gate",
        summary="Allow or refuse a workflow run before any node executes.",
        signature="(workflow: dict) -> bool",
        default_behavior=(
            "Without a hook the core's own gate decides and the run proceeds "
            "as it does today — the bundled path is unchanged."
        ),
        fail_closed=True,
    ),
)

_BY_NAME: Dict[str, ExtensionPointSpec] = {s.name: s for s in _POINT_SPECS}

#: Every point a hook may be registered on.
KNOWN_EXTENSION_POINTS: frozenset[str] = frozenset(_BY_NAME)

#: Points where a hook that raises means DENY, not "fall back to the default".
#:
#: The distinction is about who asked the question.  On a normal point the
#: caller wants an *optimisation* — a cheaper model, a different route — and the
#: bundled default is a perfectly good answer, so a broken hook costs a
#: preference and nothing else.  A gate is the opposite: the call site is asking
#: "may this run at all", and a gate that cannot answer has not said yes.
#: Substituting the permissive default there would mean an operator who
#: installed a workflow gate gets *less* enforcement the moment their gate
#: breaks — the failure mode would silently disable the control it was
#: installed to add.  So a failing hook here raises
#: :class:`ExtensionPointDenied`.
#:
#: Read the scope precisely: this is fail-closed with respect to a REGISTERED
#: hook.  With the flag off, or with no hook installed, the point is not
#: consulted at all and the core's own gate decides exactly as before — that is
#: the ship-dark requirement, not a fail-open hole.  The mechanisms that must
#: never be reachable from a plugin in the first place are in
#: :data:`_NEVER_EXTENSIBLE`, which has no hook path at all.
_FAIL_CLOSED_POINTS: frozenset[str] = frozenset(
    s.name for s in _POINT_SPECS if s.fail_closed
)

#: Names that must FAIL LOUDLY rather than look like a missing entry.
#:
#: Each maps to the mechanism CLAUDE.md § Compliance Baseline marks as
#: non-disableable and non-pluggable.  The denylist is redundant with the
#: unknown-point check — everything here is already absent from
#: ``KNOWN_EXTENSION_POINTS`` — and that redundancy is the point: it turns
#: "your hook was ignored" into "this mechanism has no hook, by construction",
#: and it makes any future attempt to ADD one of these names collide with a
#: named constant instead of quietly slipping into the known set.
_NEVER_EXTENSIBLE: Dict[str, str] = {
    "audit.hash_chain": "the hash-chained audit write (GDPR Art. 30/32, L16)",
    "audit.write_event": "the core audit writer (GDPR Art. 30/32, L16)",
    "a2a.signature_verification": "Ed25519 signature verification (A2A, L38)",
    "a2a.attestation_verify": "A2A instance attestation (L38)",
    "tde.token_accounting": "TDE token accounting",
    "consent.gate": "the per-user consent gate (GDPR Art. 6/7, L16)",
    "house_rules.gate": "the house-rules gate (EU AI Act Art. 5/50, L44)",
    "path_gate.check": "the L10 path gate (GDPR Art. 32)",
    "flow_guard.classify": "the L34 data-classification flow guard",
    "disclosure.bot_card": "the bot-disclosure card (EU AI Act Art. 50)",
    "erasure.execute": "the L36 erasure orchestrator (GDPR Art. 17)",
}

#: The eight provider registries.  These are real extension points, reached
#: through ``ctx.<name>_registry.set_active(self)`` in ``on_load`` — not through
#: this bus.  Routing them here would be actively harmful for ``audit_backend``
#: (it would sidestep the copy-after-commit ordering) and for ``user_backend``
#: (it would sidestep the deny-on-anything-else wrapper).
#: Spelled out rather than derived from the module name: an error message that
#: computes an attribute name by string surgery is one rename away from telling
#: a plugin author to call something that does not exist.
_PROVIDER_POINTS: Dict[str, str] = {
    "router_backend": "router_registry",
    "recall_backend": "recall_registry",
    "summary_provider": "summary_registry",
    "audit_backend": "audit_registry",
    "user_backend": "user_registry",
    "stt_provider": "stt_registry",
    "data_connector": "data_connector_registry",
    "notification_backend": "notification_registry",
}


def spec(point: str) -> ExtensionPointSpec:
    """Return the declared spec for a point.  Raises for anything else."""
    if point in _NEVER_EXTENSIBLE:
        raise ImmutableExtensionPoint(_immutable_message(point))
    try:
        return _BY_NAME[point]
    except KeyError:
        raise UnknownExtensionPoint(_unknown_message(point)) from None


# ── Audit ─────────────────────────────────────────────────────────────────────


def _audit(event_type: str, details: dict, *, tenant_id: str) -> None:
    """Emit through the real hash-chained writer, defensively.

    Same shape and same reasoning as ``bootstrap._default_audit_emit``: the
    audit module is not importable in every layout that imports this package,
    and a missing writer must degrade to a debug line rather than break a
    registration or, worse, a turn.
    """
    try:
        from .bootstrap import _default_audit_emit

        _default_audit_emit(tenant_id)(event_type, details)
    except Exception:  # noqa: BLE001 — visibility must never break the caller
        log.debug("extension-point audit for %s could not be written", event_type)


def _clip(value: object) -> str:
    """Clip an author-supplied name before it reaches an audit detail."""
    return str(value)[:MAX_AUDITED_NAME_CHARS]


# ── Who is asking ─────────────────────────────────────────────────────────────

#: The plugin is registered and its context named a tenant.
_TENANT_VERIFIED = "verified"
#: The registry answered and holds no plugin under this id.
_TENANT_UNREGISTERED = "unregistered"
#: The lookup could not run, or ran and produced no usable tenant.
_TENANT_UNAVAILABLE = "unavailable"


def _loaded_tenant(plugin_id: str) -> Tuple[Optional[str], str]:
    """The tenant ``plugin_id`` was LOADED for, read from the plugin registry.

    ``(tenant_id, _TENANT_VERIFIED)`` for a registered plugin,
    ``(None, _TENANT_UNREGISTERED)`` when the registry answered and holds no
    such plugin, ``(None, _TENANT_UNAVAILABLE)`` when the lookup could not run
    — the registry module is not importable in every layout that imports this
    one (ADR-0241 headless core), and a lookup that raises must not turn a
    registration into a crash.

    Fail-closed in the sense that matters here: it never *invents* agreement.  A
    tenant id comes back only when it was actually read off a live
    :class:`PluginContext`, so no error path and no missing plugin can be
    mistaken for "the tenants match".

    Two sources, in this order:

    1. **The load context** (``loading.current()``). Set by the registry around
       ``on_load``, and it is the only source that also answers *outside* the
       window in which the plugin appears in ``_contexts``.
    2. **The registry**, for a plugin that is loaded but not currently loading.

    Order matters. Consulting only the registry left two natural registration
    windows unguarded — from ``__init__`` (the object is constructed BEFORE
    ``register()`` runs, which is where plugin authors usually put setup) and
    after ``unregister`` — and both took the "unregistered, therefore allowed"
    exception. A plugin could claim another tenant's fail-closed workflow gate
    from its own constructor.

    Fail-closed in the sense that matters here: it never *invents* agreement. A
    tenant id comes back only when it was actually read off a live load context
    or :class:`PluginContext`, so no error path and no missing plugin can be
    mistaken for "the tenants match".
    """
    from . import loading as _loading

    who = _loading.current()
    if who is not None and who.plugin_id == plugin_id:
        return who.tenant_id, _TENANT_VERIFIED

    try:
        from .registry import get_registry

        ctx = get_registry()._contexts.get(plugin_id)
    except Exception:  # noqa: BLE001 — a missing registry must not break a load
        log.debug("plugin registry lookup for %r is unavailable", plugin_id)
        return None, _TENANT_UNAVAILABLE
    if ctx is None:
        return None, _TENANT_UNREGISTERED
    tenant = getattr(ctx, "tenant_id", None)
    if not isinstance(tenant, str) or not tenant:
        return None, _TENANT_UNAVAILABLE
    return tenant, _TENANT_VERIFIED


def _check_tenant(point: str, *, plugin_id: str, tenant_id: str) -> str:
    """Refuse a hook for a tenant the plugin was not loaded for.

    Returns the verification status for the audit record.  Raises
    :class:`CrossTenantHookRefused` on a proven mismatch.

    The rejection is recorded in the plugin's OWN tenant, not in the one it
    asked for.  Emitting it into the target tenant's chain would mean a plugin
    belonging to tenant A appends a record to tenant B's hash-chained trail on
    input A controls — the very cross-tenant write being refused.  Both ids are
    in the details, so the record is complete either way.
    """
    actual, status = _loaded_tenant(plugin_id)
    if status == _TENANT_VERIFIED and actual != tenant_id:
        log.error(
            "extension point %s: plugin %r is loaded for tenant %r and asked to "
            "register for tenant %r — refused",
            point, plugin_id, actual, tenant_id,
        )
        _audit("plugin.extension_hook_rejected", {
            "point": _clip(point),
            "plugin_id": _clip(plugin_id),
            "tenant_id": _clip(actual),
            "requested_tenant_id": _clip(tenant_id),
            "reason": "tenant_mismatch",
        }, tenant_id=str(actual))
        raise CrossTenantHookRefused(plugin_id, tenant_id, str(actual))
    return status


# ── Flag ──────────────────────────────────────────────────────────────────────


#: (tenant, point) pairs already reported as flag-degraded, so a broken config
#: costs one audit record per pair rather than one per turn.  The audit chain is
#: append-only; a per-invocation event on a hot path would be chain spam.
_degraded_reported: set[tuple[str, str]] = set()
_degraded_lock = threading.Lock()

#: Cap on :data:`_degraded_reported`, in the spirit of ``healing.MAX_PLUGIN_LOCKS``.
#: The set is process-wide and keyed by tenant, in a process that runs for
#: months; on a host with many tenants an unbounded "already reported" memo is a
#: slow leak.
MAX_DEGRADED_REPORTED = 1024


def _note_degraded(tenant_id: str, point: str) -> bool:
    """True the first time this ``(tenant, point)`` degrades.  Bounded, atomic.

    The membership test and the insert are one critical section: two concurrent
    turns for the same tenant hitting the same broken ``features.json`` would
    otherwise both read "not reported yet" and both append to an append-only
    chain — the duplicate cannot be removed afterwards.

    At :data:`MAX_DEGRADED_REPORTED` the memo is dropped wholesale rather than
    refusing further keys.  Losing it costs at most one repeated record per
    pair; refusing new keys would silence a NEW degradation for the life of the
    process, which is precisely the blind spot this record exists to close.
    """
    key = (tenant_id, point)
    with _degraded_lock:
        if key in _degraded_reported:
            return False
        if len(_degraded_reported) >= MAX_DEGRADED_REPORTED:
            log.debug("degradation memo full — dropping it and starting over")
            _degraded_reported.clear()
        _degraded_reported.add(key)
        return True


#: The flag registry lives in the Console package.  An ImportError naming one of
#: these is "there is no Console here" (headless core, ADR-0241); an ImportError
#: naming anything else came from INSIDE the Console and is a broken install.
_FLAG_MODULES = frozenset({"corvin_console", "corvin_console.feature_flags"})


def _flag_state(tenant_id: str) -> tuple[bool, bool]:
    """``(enabled, lookup_broken)`` for ``plugin_extension_points``.

    The two failure modes look identical from the outside and are not the same
    thing:

    * **Console absent** — a headless-core layout (ADR-0241) that ships without
      the flag registry. The feature does not exist here, the pre-feature path
      is the correct and complete answer, and there is nothing to report.
      ``(False, False)``.
    * **Console present, lookup raised** — a corrupt ``features.json``, an
      unreadable tenant dir. The operator may well have switched the feature
      ON, and we are about to silently run without it. That is worth a record,
      especially on a fail-closed point where the whole purpose is that
      enforcement does not quietly weaken. ``(False, True)``.

    Both still resolve to "off" for the actual decision: a broken config must
    not break a turn, and refusing every gated workflow because a JSON file lost
    a brace would be a self-inflicted denial of service.

    The distinction covers the IMPORT too, not only the lookup.  A blanket
    ``except Exception`` around the import folded a third case into "absent" —
    Console installed, but ``feature_flags`` unimportable (a half-installed
    dependency, a SyntaxError, a failing module-level init).  That is a broken
    install, indistinguishable at the call site from a deliberate "off", and it
    is the case where the operator most likely DID enable the flag.  The import
    error is therefore classified rather than swallowed: an ``ImportError``
    naming the Console modules themselves is "absent"; anything else — an
    ImportError raised from inside the Console, or a non-import failure — is
    "broken".
    """
    try:
        from corvin_console import feature_flags  # type: ignore[import-not-found]
    except ImportError as exc:
        if getattr(exc, "name", None) in _FLAG_MODULES:
            return False, False
        log.warning(
            "the Console is installed but %s is not importable (%s) — running "
            "the default path", FLAG_ID, type(exc).__name__,
        )
        return False, True
    except Exception as exc:  # noqa: BLE001 — a broken Console must not break a turn
        # Class only, never str(exc): a module-level failure inside the Console
        # can quote a path or a config value, and this line is written per turn.
        log.warning(
            "importing the flag registry for %s failed (%s) — running the "
            "default path", FLAG_ID, type(exc).__name__,
        )
        return False, True
    try:
        return bool(feature_flags.is_enabled(FLAG_ID, tenant_id)), False
    except Exception:  # noqa: BLE001 — a broken config must not break a turn
        log.warning(
            "feature-flag lookup for %s failed — running the default path", FLAG_ID
        )
        return False, True


# ── The bus ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Hook:
    fn: Callable[..., Any]
    plugin_id: str


class _ExtensionPointBus:
    """One hook per (tenant, point).  Thread-safe, re-entrant."""

    def __init__(self) -> None:
        # RLock, not Lock: a hook is free to call invoke() for another point,
        # and a plain lock would deadlock the call site it was meant to protect.
        self._lock = threading.RLock()
        self._hooks: Dict[Tuple[str, str], _Hook] = {}

    def register(
        self,
        point: str,
        fn: Callable[..., Any],
        *,
        plugin_id: str,
        tenant_id: str,
        tenant_check: str = _TENANT_UNAVAILABLE,
    ) -> None:
        if not callable(fn):
            raise ExtensionPointError(
                f"hook for {point!r} from {plugin_id!r} is not callable "
                f"(got {type(fn).__name__})"
            )
        with self._lock:
            previous = self._hooks.get((tenant_id, point))
            self._hooks[(tenant_id, point)] = _Hook(fn=fn, plugin_id=plugin_id)

        if previous is None or previous.plugin_id == plugin_id:
            # A plugin re-registering its OWN hook is idempotent (a hot reload
            # re-runs on_load), not a conflict — auditing it as a takeover would
            # bury the real ones in noise.
            _audit("plugin.extension_hook_registered", {
                "point": point,
                "plugin_id": plugin_id,
                "tenant_id": tenant_id,
                # Whether the claim on this tenant was checked against the
                # registry or merely asserted — see register_hook().
                "tenant_check": tenant_check,
            }, tenant_id=tenant_id)
            return

        # Last registration wins.  Load order already runs global/bundled code
        # before operator-installed plugins, so "last" is the direction an
        # override needs (ADR-0237 Approach 1: the user's hook must be able to
        # beat the bundled default).  Refusing the second registration instead
        # would make the documented override impossible.  What is NOT acceptable
        # is doing it silently: which plugin owns a point would then be an
        # emergent property of load order with no record anywhere, so the
        # takeover is audited with both ids.
        log.warning(
            "extension point %s: hook from %r replaced the hook from %r (tenant %r)",
            point, plugin_id, previous.plugin_id, tenant_id,
        )
        _audit("plugin.extension_hook_replaced", {
            "point": point,
            "plugin_id": plugin_id,
            "replaced_plugin_id": previous.plugin_id,
            "tenant_id": tenant_id,
            "tenant_check": tenant_check,
        }, tenant_id=tenant_id)

    def get(self, point: str, tenant_id: str) -> Optional[_Hook]:
        with self._lock:
            return self._hooks.get((tenant_id, point))

    def verify_owner(self, plugin_id: str, tenant_id: str) -> int:
        """Drop hooks this plugin registered for a tenant that is not its own.

        Closes the window the up-front check structurally cannot: a plugin's
        ``__init__`` runs BEFORE ``register()``, so a hook registered there has
        no load context and no registry entry to check against, and takes the
        "unregistered, therefore allowed" path. That is where a plugin author
        naturally puts setup — and where a hostile one would put a claim on
        another tenant's fail-closed workflow gate.

        The claim cannot survive the plugin actually being loaded: at that
        moment its real tenant becomes known, and every hook it holds for a
        different tenant is removed and audited. Registration is no longer the
        last word; loading is.

        Returns the number of hooks revoked.
        """
        with self._lock:
            doomed = [
                key for key, hook in self._hooks.items()
                if hook.plugin_id == plugin_id and key[0] != tenant_id
            ]
            for key in doomed:
                del self._hooks[key]
        for foreign_tenant, point in doomed:
            log.error(
                "extension point %s: revoked a hook %r had registered for tenant "
                "%r before it was loaded for tenant %r",
                point, plugin_id, foreign_tenant, tenant_id,
            )
            _audit("plugin.extension_hook_revoked", {
                "point": _clip(point),
                "plugin_id": _clip(plugin_id),
                "tenant_id": _clip(tenant_id),
                "revoked_tenant_id": _clip(foreign_tenant),
                "reason": "claimed_before_load",
            }, tenant_id=tenant_id)
        return len(doomed)

    def unregister_all(self, plugin_id: str) -> int:
        with self._lock:
            doomed = [k for k, h in self._hooks.items() if h.plugin_id == plugin_id]
            for key in doomed:
                del self._hooks[key]
        return len(doomed)

    def describe(self, tenant_id: str) -> Dict[str, str]:
        with self._lock:
            return {
                point: hook.plugin_id
                for (tid, point), hook in self._hooks.items()
                if tid == tenant_id
            }

    def clear(self) -> None:
        """Drop every hook.  Process shutdown and test isolation only."""
        with self._lock:
            self._hooks.clear()


_bus = _ExtensionPointBus()


# ── Public API ────────────────────────────────────────────────────────────────


def _immutable_message(point: str) -> str:
    return (
        f"{point!r} names {_NEVER_EXTENSIBLE[point]}, which is not extensible "
        f"— a hook there would be a compliance regression, not a feature "
        f"(ADR-0237 § Immutable vs. Extensible)"
    )


def _unknown_message(point: object) -> str:
    name = _clip(point)
    handle = _PROVIDER_POINTS.get(name)
    if handle is not None:
        return (
            f"{name!r} is a provider registry, not an extension-point hook — "
            f"register it from on_load with ctx.{handle}.set_active(self); see "
            f"corvin_plugins.providers.{name}.  Routing it through the bus "
            f"would bypass that registry's own guarantees."
        )
    return (
        f"{name!r} is not a known extension point; expected one of "
        f"{sorted(KNOWN_EXTENSION_POINTS)}"
    )


def _check_name(point: object, *, plugin_id: str, tenant_id: str) -> str:
    """Validate a point name.  Raises; never returns an unknown name."""
    if not isinstance(point, str) or not point:
        raise UnknownExtensionPoint(
            f"extension point must be a non-empty str, got {type(point).__name__}"
        )
    if point in _NEVER_EXTENSIBLE:
        _audit("plugin.extension_hook_rejected", {
            "point": _clip(point),
            "plugin_id": plugin_id,
            "tenant_id": tenant_id,
            "reason": "never_extensible",
        }, tenant_id=tenant_id)
        raise ImmutableExtensionPoint(_immutable_message(point))
    if point not in KNOWN_EXTENSION_POINTS:
        _audit("plugin.extension_hook_rejected", {
            "point": _clip(point),
            "plugin_id": plugin_id,
            "tenant_id": tenant_id,
            "reason": "unknown_point",
        }, tenant_id=tenant_id)
        raise UnknownExtensionPoint(_unknown_message(point))
    return point


def register_hook(
    point: str,
    fn: Callable[..., Any],
    *,
    plugin_id: str,
    tenant_id: str,
) -> None:
    """Register ``fn`` as the hook for ``point`` in ``tenant_id``.

    ``tenant_id`` is keyword-REQUIRED and has no default. It used to default to
    ``"_default"``, which is a trap in both directions: a call site that forgets
    it registers into the default tenant (and, at :func:`invoke` time, runs the
    default tenant's hooks inside somebody else's turn), and a plugin loaded for
    tenant A can pass tenant B's id and take over a point there — including the
    fail-closed ``workflow.workflow_gate``, since last-registration-wins makes
    that a takeover rather than a collision. The value a plugin should pass is
    ``ctx.tenant_id`` from its own :class:`PluginContext`, which the bootstrap
    sets to the tenant it was loaded for.

    **Requiring the argument does not validate it, so it is validated.** The
    tenant a plugin was loaded for is knowable: ``PluginRegistry.register()``
    stores the :class:`PluginContext` — and reserves the slot — BEFORE it calls
    ``on_load()``, so a plugin registering a hook from its own ``on_load`` is
    already findable in the registry. Three outcomes:

    * **Registered, tenants agree** — allowed, audited as ``verified``.
    * **Registered, tenants differ** — refused with
      :class:`CrossTenantHookRefused` and a ``plugin.extension_hook_rejected``
      record carrying both the requested and the actual tenant. This is the
      takeover case, and no legitimate caller reaches it: a plugin that wants
      its own tenant has ``ctx.tenant_id`` in hand.
    * **Not registered** — ALLOWED, and audited as ``unregistered`` rather than
      ``verified``.

    The third decision is the load-bearing one, so here is the reasoning.
    Rejecting an unregistered id would refuse every caller that legitimately has
    no registry identity — bundled reference code registering a default before
    any lifecycle runs, an embedding host, and the bus's own test suite — while
    buying no protection worth the name: the same single line that names a
    foreign tenant can name an id that is not in the registry, and any code
    already running in this process can reach ``_bus._hooks`` directly. An
    in-process check is not a sandbox and pretending otherwise would be worse
    than being honest about it.

    So the rule is scoped to what it can actually prove: a plugin the registry
    knows is held to the tenant the registry loaded it for, and a registration
    that cannot be verified is not silently treated as if it had been — it goes
    into the trail marked as unverified, so "which hooks were provably
    attributable" is a question the audit log can answer.

    Refused — with a raised exception, never a silent no-op — when the point is
    unknown (:class:`UnknownExtensionPoint`), names an immutable mechanism
    (:class:`ImmutableExtensionPoint`), when the plugin is loaded for a
    different tenant (:class:`CrossTenantHookRefused`) or when ``fn`` is not
    callable.

    **Conflict rule: the last registration wins, and it is audited.**  A second
    plugin registering on a point already claimed by another one replaces it and
    emits ``plugin.extension_hook_replaced`` carrying both plugin_ids.  The
    direction follows load order — bundled code registers before an
    operator-installed plugin, so "last wins" is what makes the documented
    override work — and the audit event is what keeps the takeover attributable.
    A plugin re-registering its own hook is idempotent and audited as a normal
    registration.

    Registration is deliberately NOT gated on the feature flag: a plugin's
    ``on_load`` may run before an operator flips the flag, and a hook that had to
    be registered in the right order relative to a Console toggle would be a
    restart-shaped trap.  The flag is checked at :func:`invoke` time instead, so
    turning it on takes effect immediately and turning it off leaves the
    registered hooks inert.
    """
    _check_name(point, plugin_id=plugin_id, tenant_id=tenant_id)
    status = _check_tenant(point, plugin_id=plugin_id, tenant_id=tenant_id)
    _bus.register(
        point, fn, plugin_id=plugin_id, tenant_id=tenant_id, tenant_check=status
    )


def verify_owner(plugin_id: str, tenant_id: str) -> int:
    """Revoke hooks ``plugin_id`` claimed for a tenant other than ``tenant_id``.

    Called by the registry once a plugin is actually loaded, so a claim staked
    from ``__init__`` — before any check could resolve the plugin — cannot
    outlive the load.
    """
    return _bus.verify_owner(plugin_id, tenant_id)


def unregister_all(plugin_id: str) -> int:
    """Drop every hook registered by ``plugin_id``, across all tenants.

    Call from ``on_unload``.  Returns the number of hooks removed; removing
    nothing is normal (a plugin need not have registered any) and is not an
    error.  Cross-tenant by design: a plugin object is unloaded once, and
    leaving a hook behind for a tenant it no longer serves would keep calling
    code that the registry has already torn down.
    """
    removed = _bus.unregister_all(plugin_id)
    if removed:
        log.info("removed %d extension hook(s) for plugin %r", removed, plugin_id)
    return removed


def invoke(
    point: str,
    *args: Any,
    default: Any,
    tenant_id: str = "_default",
    **kwargs: Any,
) -> Any:
    """Run the hook for ``point``, or produce ``default``.

    ``default`` is keyword-only and REQUIRED: every call site has to spell out
    its pre-feature behaviour, because that behaviour is what runs on a default
    install.  When ``default`` is callable it is invoked with the same
    ``*args``/``**kwargs`` as the hook would have been; otherwise it is returned
    as-is.  (A default that IS a function object therefore needs wrapping in
    ``lambda: fn``.)

    The default path — and nothing else — runs when:

    * ``plugin_extension_points`` is off for this tenant, or the Console package
      is not importable at all;
    * no hook is registered for this ``(tenant_id, point)``;
    * a hook on a NON-fail-closed point raised.

    A hook that raises on a point in :data:`_FAIL_CLOSED_POINTS` raises
    :class:`ExtensionPointDenied` instead.  Either way the exception CLASS is
    logged and audited and the original exception is never re-raised into the
    call site, so a broken plugin cannot crash the turn it hooked.

    Raises :class:`UnknownExtensionPoint` / :class:`ImmutableExtensionPoint` for
    a bad point name — that is a bug in the CALL SITE, not plugin input, so it
    surfaces rather than degrading.
    """
    if not isinstance(point, str) or point not in KNOWN_EXTENSION_POINTS:
        # Same refusal as registration, so a mistyped call site cannot sit there
        # silently taking the default forever.
        _check_name(point, plugin_id="<call-site>", tenant_id=tenant_id)

    enabled, lookup_broken = _flag_state(tenant_id)
    if not enabled:
        # Quiet: no log line, no audit event, no hook lookup.  This runs on
        # every default install for every call site, so anything here would be
        # a per-turn cost and a log flood (CLAUDE.md: off must be a quiet path).
        #
        # The one exception is a BROKEN lookup on a fail-closed point: there the
        # operator may have switched enforcement on and we are about to run
        # without it. Silence would make a corrupt config indistinguishable from
        # a deliberate "off" — recorded once per (tenant, point) so a hot path
        # cannot spam an append-only chain.
        if lookup_broken and point in _FAIL_CLOSED_POINTS:
            if _note_degraded(tenant_id, point):
                _audit("plugin.extension_flag_degraded", {
                    "point": point,
                    "tenant_id": tenant_id,
                    "outcome": "default",
                }, tenant_id=tenant_id)
        return _resolve_default(default, args, kwargs)

    hook = _bus.get(point, tenant_id)
    if hook is None:
        return _resolve_default(default, args, kwargs)

    try:
        return hook.fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 — a plugin must never break its host
        fail_closed = point in _FAIL_CLOSED_POINTS
        # Exception CLASS only.  str(exc) from a plugin routinely carries a
        # path, a prompt fragment or a host, and an audit record is append-only.
        log.error(
            "extension hook %s from plugin %r raised %s — %s",
            point, hook.plugin_id, type(exc).__name__,
            "denying (fail-closed point)" if fail_closed else "using the default",
        )
        _audit("plugin.extension_hook_failed", {
            "point": point,
            "plugin_id": hook.plugin_id,
            "tenant_id": tenant_id,
            "error_type": type(exc).__name__,
            "outcome": "deny" if fail_closed else "default",
        }, tenant_id=tenant_id)
        if fail_closed:
            raise ExtensionPointDenied(
                point, f"hook raised {type(exc).__name__}"
            ) from None
        return _resolve_default(default, args, kwargs)


def _resolve_default(default: Any, args: tuple, kwargs: dict) -> Any:
    if callable(default):
        return default(*args, **kwargs)
    return default


def describe(tenant_id: str = "_default") -> Dict[str, str]:
    """``{point: plugin_id}`` for the hooks registered in this tenant.

    Read-only introspection for the Console and for tests.  It reports what is
    REGISTERED, which is not the same as what is live — with the flag off every
    entry here is inert.
    """
    return _bus.describe(tenant_id)


def clear_all() -> None:
    """Drop every hook in every tenant.  Shutdown and test isolation only."""
    _bus.clear()


__all__ = [
    "FLAG_ID",
    "KNOWN_EXTENSION_POINTS",
    "CrossTenantHookRefused",
    "ExtensionPointDenied",
    "ExtensionPointError",
    "ExtensionPointSpec",
    "ImmutableExtensionPoint",
    "UnknownExtensionPoint",
    "clear_all",
    "describe",
    "invoke",
    "register_hook",
    "spec",
    "unregister_all",
    "verify_owner",
]

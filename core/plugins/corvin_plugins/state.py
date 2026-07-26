"""Per-tenant plugin registry on disk + runtime lifecycle (ADR-0233 Phase 3).

Layout, per ADR-0007's five-scope model:

    <corvin_home>/tenants/<tid>/plugins/
    ├── registry.yaml                  # the records (PluginRecord.to_dict())
    └── instances/<plugin_id>/         # per-plugin state a plugin may write to

Properties that are load-bearing:

* **Tenant comes from the resolver, never from an env var at the call site.**
  Every public function takes a keyword-only ``tenant_id`` which is passed through
  ``current_tenant()`` → ``validate_tenant_id()``.  A console route must pass
  ``rec.tenant_id`` from the authenticated session (CLAUDE.md § Multi-tenant).
* **Atomic writes, mode 0600.**  A crash mid-save must leave the previous registry
  intact, and a registry is operator configuration — not world-readable.
* **Corruption fails closed.**  A registry that does not parse raises; it is never
  silently treated as "no plugins" and then overwritten with an empty file, which
  would turn one bad write into permanent data loss.
* **Every transition emits a REAL hash-chained audit event** through the core
  writer (``bridges/shared/audit.py``), not a dataclass — the retired prototype's
  ``AuditEvent.to_dict()`` never reached the chain.
* **Consent is checked before enable, not after.**  ``enable()`` refuses a record
  whose ``consent_required()`` is true unless the caller passes an explicit
  ``consent_granted_by``, and the grant is recorded in the audit event.
"""
from __future__ import annotations

import contextlib
import logging
import os
import tempfile
import threading
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterator, Optional

import yaml

from .manifest import (
    BootLayer,
    DependencyResolver,
    Locality,
    NetworkEgress,
    PIIRisk,
    PluginError,
    PluginNotFound,
    PluginOrigin,
    PluginRecord,
    SettingsValidator,
    validate_plugin_id,
)
from .protocol import PluginDisableRefused

log = logging.getLogger("corvin.plugins.state")

#: In-process guard. Two threads in ONE process (two Console requests) would
#: otherwise interleave load → modify → save and lose one of the two writes.
_MUTATION_LOCK = threading.RLock()


def _fcntl_shim():
    """The repo's portable flock shim: real on POSIX, no-op on Windows.

    Windows has no POSIX advisory locks and a bare ``import fcntl`` crashed
    mandatory layers at import time once (security audit 2026-06-25); the shared
    shim is the established answer, so this reuses it rather than inventing a
    second story.
    """
    import sys

    try:
        from _compat_fcntl import fcntl  # type: ignore[import-not-found]

        return fcntl
    except ImportError:
        pass
    shared = Path(__file__).resolve().parents[3] / "operator" / "bridges" / "shared"
    if shared.is_dir() and str(shared) not in sys.path:
        sys.path.append(str(shared))
    try:
        from _compat_fcntl import fcntl  # type: ignore[import-not-found]

        return fcntl
    except ImportError:
        return None


@contextlib.contextmanager
def registry_mutation(
    *, tenant_id: Optional[str] = None, corvin_home_path: Optional[Path] = None
) -> Iterator["TenantRegistry"]:
    """Hold the registry lock across a load → modify → save cycle.

    Without this, concurrent mutations silently LOSE writes: each caller read the
    file, changed its own copy and wrote the whole thing back, so the last writer
    won. Measured before the fix: 12 concurrent installs left 2 records on disk.

    Locking is two-layer because both kinds of concurrency are real here — several
    threads inside the gateway process, and separate processes (gateway, adapter,
    CLI) against the same tenant directory.
    """
    path = registry_path(tenant_id=tenant_id, corvin_home_path=corvin_home_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(".registry.lock")
    fcntl = _fcntl_shim()
    with _MUTATION_LOCK:
        lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
            registry = TenantRegistry.load(
                tenant_id=tenant_id, corvin_home_path=corvin_home_path
            )
            yield registry
            registry.save()
        finally:
            try:
                if fcntl is not None:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)

#: Schema version of registry.yaml itself.  Bumped only for a breaking layout
#: change; from_dict() already fails closed on unknown record fields.
REGISTRY_SCHEMA_VERSION = "1.0"


class RegistryCorrupt(PluginError):
    """registry.yaml exists but cannot be parsed — refuse rather than reset."""


class ConsentRequired(PluginError):
    """The record needs explicit consent before it may be enabled."""


class LifecycleDisabled(PluginError):
    """Runtime mutation is off (feature flag plugin_runtime_lifecycle)."""


class EgressNotDeclared(PluginError):
    """The record wants unrestricted internet without declaring hosts (L35)."""


# ── Paths ─────────────────────────────────────────────────────────────────────


def _tenants_module():
    """Import ``forge.tenants`` — the ONE canonical tenant resolver (ADR-0007).

    There is deliberately no local fallback implementation: a second resolver
    would be a second answer to "which tenant am I", and the whole point of
    ``current_tenant`` → ``validate_tenant_id`` → ``tenant_home`` is that there is
    exactly one.  When forge is genuinely absent the import error surfaces.
    """
    import sys

    try:
        from forge import tenants  # type: ignore[import-not-found]

        return tenants
    except ImportError:
        pass

    forge_root = Path(__file__).resolve().parents[3] / "operator" / "forge"
    if forge_root.is_dir() and str(forge_root) not in sys.path:
        # append, NOT insert(0): this directory also contains generic top-level
        # names (tests/, templates/) with no __init__.py, so putting it FIRST on
        # sys.path lets them shadow another package's `tests` — the same class as
        # the operator/ stdlib-shadow trap. Appending means existing paths win.
        sys.path.append(str(forge_root))
    from forge import tenants  # type: ignore[import-not-found]

    return tenants


def _tenant_root(tenant_id: Optional[str], corvin_home_path: Optional[Path]) -> Path:
    """Resolve ``<corvin_home>/tenants/<tid>/plugins`` via the shared resolver."""
    return (
        _tenants_module().tenant_home(tenant_id, corvin_home_path=corvin_home_path)
        / "plugins"
    )


def registry_path(
    *, tenant_id: Optional[str] = None, corvin_home_path: Optional[Path] = None
) -> Path:
    return _tenant_root(tenant_id, corvin_home_path) / "registry.yaml"


def instance_dir(
    plugin_id: str,
    *,
    tenant_id: Optional[str] = None,
    corvin_home_path: Optional[Path] = None,
) -> Path:
    """Where a plugin may keep its own state.  Created on install.

    Two independent guards, because this path is handed to ``shutil.rmtree`` on
    uninstall and one guard is one bug away from a delete outside the tenant:

    1. ``validate_plugin_id`` — an allowlist charset. The previous denylist only
       rejected ``/``, so ``..\\..\\etc`` passed and is a real traversal on
       Windows (a supported platform, ADR-0159).
    2. a resolved-containment check — even a future id that slips the charset
       cannot resolve outside ``<tenant>/plugins/instances``.
    """
    validate_plugin_id(plugin_id)
    root = _tenant_root(tenant_id, corvin_home_path) / "instances"
    candidate = root / plugin_id
    resolved_root = root.resolve(strict=False)
    resolved = candidate.resolve(strict=False)
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise PluginError(
            f"plugin_id {plugin_id!r} resolves outside the tenant instance dir"
        )
    return candidate


# ── Persistence ───────────────────────────────────────────────────────────────


#: Boot layers a per-tenant ``registry.yaml`` may express.  Mirrors
#: ``bootstrap._TENANT_DECLARABLE_BOOT_LAYERS`` — same trust boundary, other file.
_TENANT_RECORD_BOOT_LAYERS = frozenset({BootLayer.BUNDLED, BootLayer.INSTALLED})


def _downgrade_privileged_boot_layer(
    record: PluginRecord, *, path: Path
) -> PluginRecord:
    """Force a tenant-written record down to ``installed`` if it claims more.

    ``registry.yaml`` is per-tenant, operator-writable state, so it sits on the
    same side of the trust boundary as ``tenant.corvin.yaml``. ``bootstrap``
    already downgrades a privileged claim from there — but it does so when
    REGISTERING, which only fixes the runtime boot layer. The record itself kept
    ``boot_layer: compliance``, and ``PluginRecord.can_disable()`` reads it:
    the admin API then answered 403 forever for a plugin that was running as
    ``installed``. A tenant could mint an un-disableable entry with one line of
    YAML, which is the exact escalation the boot guard exists to prevent.

    Downgrading rather than raising is deliberate: a corrupt or over-reaching
    line should cost that entry its privilege, not make the whole registry
    unreadable (which would take every other plugin down with it).
    """
    if record.boot_layer in _TENANT_RECORD_BOOT_LAYERS:
        return record
    log.error(
        "registry record %r in %s claims privileged boot_layer %s — "
        "downgrading to installed (privileged boot layers come from the wheel)",
        record.plugin_id, path.name, record.boot_layer.value,
    )
    _audit(
        "plugin.boot_layer_rejected",
        {
            "plugin_id": record.plugin_id,
            "declared_boot_layer": record.boot_layer.value,
            "reason": "privileged_boot_layer_from_tenant_registry",
        },
        tenant_id="_default",
    )
    return replace(record, boot_layer=BootLayer.INSTALLED)


class TenantRegistry:
    """The set of plugin records for one tenant, backed by registry.yaml."""

    def __init__(self, path: Path, records: Dict[str, PluginRecord] | None = None):
        self.path = path
        self.records: Dict[str, PluginRecord] = records or {}

    # ── load / save ──────────────────────────────────────────────────────────

    @classmethod
    def load(
        cls,
        *,
        tenant_id: Optional[str] = None,
        corvin_home_path: Optional[Path] = None,
    ) -> TenantRegistry:
        path = registry_path(tenant_id=tenant_id, corvin_home_path=corvin_home_path)
        if not path.exists():
            return cls(path)

        try:
            raw = yaml.safe_load(path.read_text()) or {}
        except (yaml.YAMLError, OSError) as exc:
            # Fail closed: do NOT return an empty registry. Returning empty would
            # let the next save() overwrite a merely unreadable file with {}.
            raise RegistryCorrupt(
                f"{path} is unreadable ({type(exc).__name__}); refusing to continue"
            ) from exc

        if not isinstance(raw, dict):
            raise RegistryCorrupt(f"{path} is not a mapping")

        records: Dict[str, PluginRecord] = {}
        for pid, data in (raw.get("plugins") or {}).items():
            if not isinstance(data, dict):
                raise RegistryCorrupt(f"{path}: record {pid!r} is not a mapping")
            records[pid] = _downgrade_privileged_boot_layer(
                PluginRecord.from_dict(data), path=path
            )
        return cls(path, records)

    def save(self) -> None:
        """Persist atomically with mode 0600."""
        payload = {
            "spec": {"schema_version": REGISTRY_SCHEMA_VERSION},
            "plugins": {pid: rec.to_dict() for pid, rec in sorted(self.records.items())},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=".registry-", suffix=".tmp"
        )
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w") as fh:
                yaml.safe_dump(payload, fh, sort_keys=False, default_flow_style=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    # ── accessors ────────────────────────────────────────────────────────────

    def get(self, plugin_id: str) -> PluginRecord:
        try:
            return self.records[plugin_id]
        except KeyError as exc:
            raise PluginNotFound(plugin_id) from exc

    def has(self, plugin_id: str) -> bool:
        return plugin_id in self.records

    def enabled_records(self) -> list[PluginRecord]:
        return [r for r in self.records.values() if r.enabled]

    def load_order(self) -> list[str]:
        """Dependency-ordered plugin_ids of the ENABLED records."""
        enabled = {r.plugin_id: r for r in self.enabled_records()}
        return DependencyResolver(enabled).load_order()


# ── Lifecycle ─────────────────────────────────────────────────────────────────


def _default_corvin_home() -> Path:
    """The corvin_home the tenant resolver would use when none was injected."""
    from forge.paths import corvin_home  # type: ignore[import-not-found]

    return corvin_home()


def _audit(event_type: str, details: dict, *, tenant_id: str) -> None:
    """Emit a real hash-chained audit event; never raise into the caller."""
    try:
        from audit import audit_event  # type: ignore[import-not-found]
    except ImportError:
        log.warning("audit module unavailable — %s not recorded", event_type)
        return
    try:
        audit_event(event_type, details=details, tenant_id=tenant_id)
    except Exception as exc:  # noqa: BLE001
        log.error("audit emit failed for %s (%s)", event_type, type(exc).__name__)


class PluginLifecycle:
    """install / enable / configure / disable / uninstall against one tenant.

    Every method is gated on ``lifecycle_enabled``: with the feature flag off the
    registry is read-only at runtime and plugins load exactly as they do today
    (from ``spec.plugins.installed`` at boot).
    """

    def __init__(
        self,
        *,
        tenant_id: Optional[str] = None,
        corvin_home_path: Optional[Path] = None,
        lifecycle_enabled: Callable[[], bool] | bool = False,
    ):
        # Resolved once, through the canonical resolver, and never re-read from an
        # env var afterwards: a console route passes rec.tenant_id from the
        # authenticated session (CLAUDE.md § Multi-tenant axis).
        self.tenant_id = _tenants_module().current_tenant(tenant_id)
        self.corvin_home_path = corvin_home_path
        self._enabled_check = lifecycle_enabled

    # ── gate ─────────────────────────────────────────────────────────────────

    def _require_enabled(self, action: str) -> None:
        allowed = (
            self._enabled_check() if callable(self._enabled_check) else bool(self._enabled_check)
        )
        if not allowed:
            raise LifecycleDisabled(
                f"runtime plugin {action} is off "
                f"(enable the plugin_runtime_lifecycle feature flag)"
            )

    def _registry(self) -> TenantRegistry:
        """Read-only snapshot.  Mutations MUST go through :meth:`_mutation`."""
        return TenantRegistry.load(
            tenant_id=self.tenant_id, corvin_home_path=self.corvin_home_path
        )

    def _mutation(self):
        """Locked load → modify → save cycle (see :func:`registry_mutation`)."""
        return registry_mutation(
            tenant_id=self.tenant_id, corvin_home_path=self.corvin_home_path
        )

    # ── transitions ──────────────────────────────────────────────────────────

    def install(self, record: PluginRecord, *, installed_by: str) -> PluginRecord:
        """Add a record to the registry, disabled.

        Install never enables: a freshly installed plugin is inert until an
        operator turns it on (and passes the consent gate if it applies).
        """
        self._require_enabled("install")
        with self._mutation() as reg:
            return self._install_locked(reg, record, installed_by=installed_by)

    def _install_locked(
        self, reg: TenantRegistry, record: PluginRecord, *, installed_by: str
    ) -> PluginRecord:
        if reg.has(record.plugin_id):
            raise PluginError(f"{record.plugin_id} is already installed")

        # Validate declared defaults against the plugin's own schema now, so a
        # broken schema surfaces at install time rather than at first enable.
        validator = SettingsValidator(record.settings_schema)
        settings = record.settings or validator.defaults()
        validator.validate(settings)

        stored = PluginRecord.from_dict(
            {
                **record.to_dict(),
                "installed_at": datetime.now(timezone.utc).isoformat(),
                "installed_by": installed_by,
                "enabled": False,
                "enabled_at": None,
                "settings": settings,
            }
        )
        reg.records[stored.plugin_id] = stored
        instance_dir(
            stored.plugin_id,
            tenant_id=self.tenant_id,
            corvin_home_path=self.corvin_home_path,
        ).mkdir(parents=True, exist_ok=True)

        _audit(
            "plugin.installed",
            {
                "plugin_id": stored.full_id,
                "plugin_type": stored.plugin_type,
                "origin": stored.origin.value,
                "pii_risk": stored.pii_risk.value,
                "requires_consent": stored.consent_required(),
                # installed_by is an operator identifier: a role, not a name.
                "installed_by": installed_by,
            },
            tenant_id=self.tenant_id,
        )
        return stored

    def enable(
        self, plugin_id: str, *, consent_granted_by: str | None = None
    ) -> PluginRecord:
        """Enable a record, enforcing the consent gate and dependency health."""
        self._require_enabled("enable")
        with self._mutation() as reg:
            return self._enable_locked(
                reg, plugin_id, consent_granted_by=consent_granted_by
            )

    def _enable_locked(
        self, reg: TenantRegistry, plugin_id: str, *, consent_granted_by: str | None
    ) -> PluginRecord:
        record = reg.get(plugin_id)

        if record.consent_required() and not consent_granted_by:
            _audit(
                "plugin.enable_denied",
                {
                    "plugin_id": record.full_id,
                    "reason": "consent_required",
                    "pii_risk": record.pii_risk.value,
                    "origin": record.origin.value,
                },
                tenant_id=self.tenant_id,
            )
            raise ConsentRequired(
                f"{plugin_id} needs explicit consent "
                f"(origin={record.origin.value}, pii_risk={record.pii_risk.value})"
            )

        self._assert_flow_declarations(record)

        enabled = record.with_enabled(True)
        reg.records[plugin_id] = enabled
        # Dependency check AFTER staging the change: enabling must not produce a
        # registry whose enabled set cannot be ordered.
        try:
            reg.load_order()
        except PluginError:
            reg.records[plugin_id] = record  # roll back the staged change
            raise

        # ── Hot-reload (ADR-0124 Inv. 6) ─────────────────────────────────────
        # Load the plugin NOW, before persisting. Without this, enable() only
        # wrote a flag: the Console showed the toggle on while the plugin stayed
        # inert until the next process boot — a silent lie in the UI.
        #
        # Order matters. A failed on_load() must leave BOTH the runtime and the
        # file in the pre-enable state, so the registry never claims an active
        # plugin that isn't running.
        loaded = self._activate(enabled)
        if loaded is False:
            reg.records[plugin_id] = record
            _audit(
                "plugin.enable_failed",
                {"plugin_id": enabled.full_id, "reason": "load_failed"},
                tenant_id=self.tenant_id,
            )
            raise PluginError(
                f"{plugin_id} could not be loaded; it stays disabled "
                f"(see the log for the failing class_path)"
            )

        _audit(
            "plugin.enabled",
            {
                "plugin_id": enabled.full_id,
                "consent_granted_by": consent_granted_by or "",
                # None means "no class_path, nothing to load" — a record-only
                # entry, which is legitimate for a plugin registered by other
                # means (entry point already loaded at boot).
                "activated": loaded is not None,
            },
            tenant_id=self.tenant_id,
        )
        return enabled

    def _assert_flow_declarations(self, record: PluginRecord) -> None:
        """L34/L35 gate on enable (ADR-0124 Inv. 3).

        Two refusals, both aimed at the case the operator cannot see:

        * a COMMUNITY plugin that wants the open internet without naming a single
          host — "external egress, hosts unknown" is exactly the shape an
          exfiltration path takes, and ADR-0124 requires the declaration;
        * an unclassified locality combined with a high PII risk — the plugin would
          be handling personal data with no answer to "in which jurisdiction".

        A vetted/builtin plugin may leave egress_hosts empty: the maintainer
        reviewed it. That asymmetry is the point of the origin field.
        """
        if (
            record.network_egress is NetworkEgress.EXTERNAL
            and not record.egress_hosts
            and record.origin is PluginOrigin.COMMUNITY
        ):
            _audit(
                "plugin.enable_denied",
                {
                    "plugin_id": record.full_id,
                    "reason": "egress_not_declared",
                    "network_egress": record.network_egress.value,
                    "origin": record.origin.value,
                },
                tenant_id=self.tenant_id,
            )
            raise EgressNotDeclared(
                f"{record.plugin_id} declares external egress with no egress_hosts; "
                f"a community plugin must name the hosts it needs (L35)"
            )

        if record.locality is Locality.UNKNOWN and record.pii_risk is PIIRisk.HIGH:
            _audit(
                "plugin.enable_denied",
                {
                    "plugin_id": record.full_id,
                    "reason": "locality_unknown_with_high_pii",
                    "pii_risk": record.pii_risk.value,
                },
                tenant_id=self.tenant_id,
            )
            raise PluginError(
                f"{record.plugin_id} handles high-PII data but declares "
                f"locality=unknown; classify it before enabling (L34)"
            )

    def _activate(self, record: PluginRecord) -> bool | None:
        """Instantiate + register the plugin for this record, right now.

        Returns True on success, False on failure, and None when there is nothing
        to load (no ``class_path``).  Never raises — the caller decides what a
        failure means.
        """
        if not record.class_path:
            return None
        try:
            from .bootstrap import _load_one
            from .registry import get_registry

            # The process registry holds ONE instance per plugin_id. If another
            # tenant already loaded this plugin, `_load_one` treats
            # "already registered" as loaded and reports success — so this
            # tenant would be told `activated: true`, and would write that into
            # its own hash-chained audit trail, for an object running with the
            # OTHER tenant's context, corvin_home and settings. Reporting "not
            # loaded" is the honest answer: the record is enabled, the instance
            # is not ours.
            owner = get_registry().tenant_of(record.plugin_id)
            if owner is not None and owner != self.tenant_id:
                log.info(
                    "not hot-loading %r: an instance is already loaded for "
                    "another tenant; this tenant's record is enabled only",
                    record.plugin_id,
                )
                return False

            ok = _load_one(
                record,
                tenant_id=self.tenant_id,
                corvin_home=self.corvin_home_path or _default_corvin_home(),
            )
            return bool(ok)
        except Exception as exc:  # noqa: BLE001 - report, never propagate
            log.error(
                "hot-load of %r failed (%s)", record.plugin_id, type(exc).__name__
            )
            return False

    def _deactivate(self, record: PluginRecord) -> None:
        """Unregister the plugin for this record, right now.

        Raises :class:`PluginDisableRefused` when the id currently occupies the
        compliance boot layer in the process registry; every other failure is logged
        and swallowed as before.

        The refusal has to live here rather than only in the caller. This is
        reached from ``PluginLifecycle.disable()``, which the older Console
        route ``POST /plugins/{id}/disable`` calls directly — that route checks
        no boot layer at all. Without this guard a tenant record sharing an id with a
        global compliance plugin would unregister it from the process, taking
        its provider slot with it, while the ADR-0239 admin API correctly
        answers 403 for the same request. One mechanism, two doors, and the
        older door was unlocked.
        """
        try:
            from .registry import get_registry, unregister

            registry = get_registry()
            if record.plugin_id not in registry.discover():
                return
            # The process registry is keyed by plugin_id alone, so the loaded
            # object may belong to a DIFFERENT tenant that installed the same
            # plugin. Disabling our own record must not stop their instance:
            # that was a cross-tenant control path reachable with an ordinary
            # 200, and it wrote nothing into the victim's audit chain. Our
            # record still flips to disabled — we simply do not touch an object
            # that is not ours.
            owner = registry.tenant_of(record.plugin_id)
            if owner is not None and owner != self.tenant_id:
                log.info(
                    "not unloading %r: the loaded instance belongs to another "
                    "tenant; this tenant's record is disabled only",
                    record.plugin_id,
                )
                return
            unregister(record.plugin_id, operator_initiated=True)
        except PluginDisableRefused:
            raise
        except Exception as exc:  # noqa: BLE001
            log.error(
                "hot-unload of %r failed (%s)", record.plugin_id, type(exc).__name__
            )

    def set_settings(self, plugin_id: str, settings: dict) -> PluginRecord:
        """Validate and persist new settings.

        A rejected write leaves the previous settings intact — validation happens
        before any mutation reaches disk.
        """
        self._require_enabled("configuration")
        with self._mutation() as reg:
            return self._set_settings_locked(reg, plugin_id, settings)

    def _set_settings_locked(
        self, reg: TenantRegistry, plugin_id: str, settings: dict
    ) -> PluginRecord:
        record = reg.get(plugin_id)

        SettingsValidator(record.settings_schema).validate(settings)

        old_keys = sorted(record.settings)
        updated = PluginRecord.from_dict({**record.to_dict(), "settings": settings})
        reg.records[plugin_id] = updated

        # KEY NAMES ONLY. Setting VALUES are operator config that can contain a
        # webhook URL, a project path or an account id — never put them in the
        # audit chain (CLAUDE.md: don't leak PII into audit details).
        _audit(
            "plugin.config_changed",
            {
                "plugin_id": updated.full_id,
                "keys_before": old_keys,
                "keys_after": sorted(settings),
            },
            tenant_id=self.tenant_id,
        )
        return updated

    def disable(self, plugin_id: str) -> PluginRecord:
        """Disable a record.  Dependents that are still enabled block the change."""
        self._require_enabled("disable")
        with self._mutation() as reg:
            return self._disable_locked(reg, plugin_id)

    def _disable_locked(self, reg: TenantRegistry, plugin_id: str) -> PluginRecord:
        record = reg.get(plugin_id)

        dependents = [
            r.plugin_id
            for r in reg.enabled_records()
            if r.plugin_id != plugin_id
            and any(d.plugin_id == plugin_id for d in r.parsed_dependencies())
        ]
        if dependents:
            _audit(
                "plugin.disable_denied",
                {
                    "plugin_id": record.full_id,
                    "reason": "enabled_dependents",
                    "dependents": sorted(dependents),
                },
                tenant_id=self.tenant_id,
            )
            raise PluginError(
                f"{plugin_id} is required by enabled plugin(s): {sorted(dependents)}"
            )

        disabled = record.with_enabled(False)
        reg.records[plugin_id] = disabled

        # Hot-unload (ADR-0124 Inv. 6): stop the plugin NOW rather than at the
        # next boot. `_deactivate` -> `registry.unregister()` already releases
        # the provider slot INSTANCE-CHECKED.
        #
        # This used to be followed by an unconditional `_detach_providers(
        # record.plugin_type)`, and that second call was a compliance hole with
        # a 200 on it: disabling any ordinary `audit_backend` plugin cleared the
        # slot held by a DIFFERENT, compliance-boot-layer plugin — the one whose
        # own disable correctly answers 403. Same effect, neighbouring request,
        # no audit record naming it. Clearing by TYPE cannot distinguish "the
        # plugin I am disabling" from "whoever happens to hold this slot", so
        # the type-wide call is gone and the instance-checked one in
        # `unregister()` is the only path.
        self._deactivate(record)

        _audit(
            "plugin.disabled", {"plugin_id": disabled.full_id}, tenant_id=self.tenant_id
        )
        return disabled

    def uninstall(self, plugin_id: str, *, purge_state: bool = True) -> None:
        """Remove a record (and optionally its state dir).

        The audit trail is NEVER removed — it is immutable per GDPR Art. 30 and
        outlives the plugin (Marketplace-ADR open question #4, answered: no).
        """
        self._require_enabled("uninstall")
        with self._mutation() as reg:
            self._uninstall_locked(reg, plugin_id, purge_state=purge_state)

    def _uninstall_locked(
        self, reg: TenantRegistry, plugin_id: str, *, purge_state: bool
    ) -> None:
        record = reg.get(plugin_id)
        if record.enabled:
            raise PluginError(f"{plugin_id} is enabled; disable it before uninstalling")

        # A record can only be uninstalled while disabled, but a stale runtime
        # registration (enabled at boot, disabled in a previous process) would
        # otherwise outlive the record.
        self._deactivate(record)
        del reg.records[plugin_id]

        purged = False
        if purge_state:
            state = instance_dir(
                plugin_id,
                tenant_id=self.tenant_id,
                corvin_home_path=self.corvin_home_path,
            )
            if state.is_dir():
                import shutil

                shutil.rmtree(state, ignore_errors=True)
                purged = not state.exists()

        _audit(
            "plugin.uninstalled",
            {
                "plugin_id": record.full_id,
                "state_purged": purged,
                "audit_retained": True,
            },
            tenant_id=self.tenant_id,
        )


#: Every plugin_type that owns a provider slot, mapped to its module name.
#: All EIGHT of them — an earlier version listed four, so unloading a
#: recall/router/summary/notification plugin left the registry pointing at the
#: torn-down object and the next call went into a closed handle.
_PROVIDER_MODULES: Dict[str, str] = {
    "audit_backend": "audit_backend",
    "user_backend": "user_backend",
    "stt_provider": "stt_provider",
    "data_connector": "data_connector",
    "recall_backend": "recall_backend",
    "router_backend": "router_backend",
    "summary_provider": "summary_provider",
    "notification_backend": "notification_backend",
}


def _detach_providers(plugin_type: str, instance: object = None) -> None:
    """Release the provider slot a plugin of this type may hold.

    With ``instance`` given the detach is **instance-checked**: the slot is only
    released when it still points at that very object. Clearing by type alone
    evicts whatever is installed, which is wrong as soon as a second plugin of
    the same type took the slot over — the survivor would keep believing it is
    active while calls went to the bundled default.

    Without ``instance`` (the operator-disable path, which knows the type but not
    the object) the slot is cleared unconditionally, as before.
    """
    module_name = _PROVIDER_MODULES.get(plugin_type)
    if module_name is None:
        return
    try:
        from importlib import import_module

        module = import_module(f".providers.{module_name}", package=__package__)
        if instance is not None:
            module.clear_if_active(instance)
        else:
            module.clear()
    except Exception as exc:  # noqa: BLE001
        log.error("failed to detach %s provider (%s)", plugin_type, type(exc).__name__)


__all__ = [
    "ConsentRequired",
    "LifecycleDisabled",
    "PluginLifecycle",
    "REGISTRY_SCHEMA_VERSION",
    "RegistryCorrupt",
    "TenantRegistry",
    "instance_dir",
    "registry_path",
]

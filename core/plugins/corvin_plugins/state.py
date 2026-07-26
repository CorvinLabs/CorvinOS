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

import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Optional

import yaml

from .manifest import (
    DependencyResolver,
    PluginError,
    PluginNotFound,
    PluginRecord,
    SettingsValidator,
)

log = logging.getLogger("corvin.plugins.state")

#: Schema version of registry.yaml itself.  Bumped only for a breaking layout
#: change; from_dict() already fails closed on unknown record fields.
REGISTRY_SCHEMA_VERSION = "1.0"


class RegistryCorrupt(PluginError):
    """registry.yaml exists but cannot be parsed — refuse rather than reset."""


class ConsentRequired(PluginError):
    """The record needs explicit consent before it may be enabled."""


class LifecycleDisabled(PluginError):
    """Runtime mutation is off (feature flag plugin_runtime_lifecycle)."""


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
    """Where a plugin may keep its own state.  Created on install."""
    safe = plugin_id.replace("/", "_")
    if safe != plugin_id or safe in ("", ".", ".."):
        raise PluginError(f"plugin_id {plugin_id!r} is not usable as a directory name")
    return _tenant_root(tenant_id, corvin_home_path) / "instances" / safe


# ── Persistence ───────────────────────────────────────────────────────────────


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
            records[pid] = PluginRecord.from_dict(data)
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
        return TenantRegistry.load(
            tenant_id=self.tenant_id, corvin_home_path=self.corvin_home_path
        )

    # ── transitions ──────────────────────────────────────────────────────────

    def install(self, record: PluginRecord, *, installed_by: str) -> PluginRecord:
        """Add a record to the registry, disabled.

        Install never enables: a freshly installed plugin is inert until an
        operator turns it on (and passes the consent gate if it applies).
        """
        self._require_enabled("install")
        reg = self._registry()
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
        reg.save()
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
        reg = self._registry()
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

        enabled = record.with_enabled(True)
        reg.records[plugin_id] = enabled
        # Dependency check AFTER staging the change: enabling must not produce a
        # registry whose enabled set cannot be ordered.
        try:
            reg.load_order()
        except PluginError:
            reg.records[plugin_id] = record  # roll back the staged change
            raise
        reg.save()

        _audit(
            "plugin.enabled",
            {
                "plugin_id": enabled.full_id,
                "consent_granted_by": consent_granted_by or "",
            },
            tenant_id=self.tenant_id,
        )
        return enabled

    def set_settings(self, plugin_id: str, settings: dict) -> PluginRecord:
        """Validate and persist new settings.

        A rejected write leaves the previous settings intact — validation happens
        before any mutation reaches disk.
        """
        self._require_enabled("configuration")
        reg = self._registry()
        record = reg.get(plugin_id)

        SettingsValidator(record.settings_schema).validate(settings)

        old_keys = sorted(record.settings)
        updated = PluginRecord.from_dict({**record.to_dict(), "settings": settings})
        reg.records[plugin_id] = updated
        reg.save()

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
        reg = self._registry()
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
        reg.save()

        # Detach any provider slot this plugin may hold, so a disabled plugin
        # stops receiving traffic even if its object is still referenced.
        _detach_providers(record.plugin_type)

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
        reg = self._registry()
        record = reg.get(plugin_id)
        if record.enabled:
            raise PluginError(f"{plugin_id} is enabled; disable it before uninstalling")

        del reg.records[plugin_id]
        reg.save()

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


def _detach_providers(plugin_type: str) -> None:
    """Clear the provider slot for a plugin type, if that type has one."""
    try:
        if plugin_type == "audit_backend":
            from .providers import audit_backend

            audit_backend.clear()
        elif plugin_type == "user_backend":
            from .providers import user_backend

            user_backend.clear()
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

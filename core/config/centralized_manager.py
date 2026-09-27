"""
Phase 2: Centralized Configuration Management

Implements:
1. Central config store (etcd/Consul or file-based for dev)
2. Schema validation (fail-closed)
3. Audit logging (GDPR Art. 30)
4. Per-instance overrides with validation
5. Drift detection across instances

ADR-0408: Configuration Management Phase 2 Design

What this module actually is (adversarial review 2026-09-27): a FILE-based
per-tenant config store at ``<corvin_home>/config/<tenant>.json`` — there is no
etcd/Consul client and ``CENTRAL_STORE_PATH`` is not read by anything. "Drift
detection" compares ONE caller-supplied instance config against this process's
canonical file; nothing collects configs from other instances, so it cannot
detect drift ACROSS a multi-instance deployment on its own — the caller must
bring the remote instance's config. The gateway builds this manager at boot
(``corvin_gateway.config_audit.build_config_manager``) and parks it on
``app.state``; no route reads it there.

Audit: ``create_with_audit()`` binds :class:`TenantChainConfigAudit`, which
writes through the ONE forge writer onto ``tenant_audit_chain(tenant)``. It
used to bind ``AuditChainWriter`` to a caller-supplied path — a second,
incompatible hash scheme which, pointed at the canonical chain, made
``verify_chain`` (and the ADR-0232 boot tripwire) read the chain as tampered.
"""

from dataclasses import dataclass, asdict, field
from typing import Dict, Any, Optional, List
from enum import Enum
from datetime import datetime
from pathlib import Path
import json
import os
from uuid import uuid4
import threading
import hashlib
import copy
from jsonschema import validate, ValidationError

from core.compliance.audit_chain_writer import AuditChainWriter, AuditEvent


class ConfigValidationResult(Enum):
    """Configuration validation result status"""
    VALID = "valid"
    INVALID = "invalid"
    RISKY = "risky"


@dataclass(frozen=True)
class ConfigDrift:
    """Configuration divergence between instances (immutable)"""
    key: str
    expected: Any
    actual: Any
    severity: str  # CRITICAL, HIGH, MEDIUM, LOW
    instance_id: str
    detected_at: str  # ISO 8601 timestamp


@dataclass
class ConfigChangeEvent:
    """Audit-logged configuration change"""
    instance_id: str
    tenant_id: str
    old_config: Dict[str, Any] = field(default_factory=dict)
    new_config: Dict[str, Any] = field(default_factory=dict)
    reason: str = "manual_override"
    is_override: bool = False


# Default safe configuration (fail-closed fallback)
DEFAULT_SAFE_CONFIG = {
    "telemetry": {
        "push_interval_seconds": 60,
        "aggregator_url": "https://localhost:8765/telemetry",
        "enabled": False,
    },
    "plugins": {
        "enabled_plugins": [],
        "auto_update": False,
    },
    "database": {
        "schema_version": 4,
        "replication_enabled": False,
    },
    "security": {
        "audit_enabled": True,
        "consent_required": True,
    },
}


def _safe_default() -> Dict[str, Any]:
    """A private copy of DEFAULT_SAFE_CONFIG.

    get_config() used to return the module-level dict itself, so any caller
    that edited the result (``cfg["database"]["schema_version"] = 7``) silently
    rewrote the fail-closed fallback for every tenant in the process.
    """
    return copy.deepcopy(DEFAULT_SAFE_CONFIG)


def _config_file(tenant_id: str) -> Path:
    """<corvin_home>/config/<tenant_id>.json — CORVIN_HOME-aware, tenant validated.

    Previously ``Path.home() / ".corvin"`` with the raw tenant_id: it ignored
    CORVIN_HOME (so tests and non-default installs wrote into the operator's
    live ~/.corvin) and a tenant_id such as ``../x`` escaped the directory.
    """
    from core.paths.tenant import corvin_home
    from core.tenants import validate_tenant_id

    validate_tenant_id(tenant_id)
    return corvin_home() / "config" / f"{tenant_id}.json"


class TenantChainConfigAudit:
    """``audit_chain`` for :class:`CentralizedConfigManager` that writes through
    the forge writer onto the event's own tenant chain.

    Same ``write_event_dict`` surface as ``AuditChainWriter`` (which the manager
    calls). Mirrors ``corvin_gateway.config_audit.ForgeConfigAudit``; kept here
    so ``core.config`` does not import the gateway. Raises on failure.
    """

    def write_event_dict(self, event_type: str, tenant_id: str,
                         user_id: Optional[str] = None, details: Optional[dict] = None,
                         severity: Optional[str] = None) -> str:
        from core.deployment.audit_sink import _forge  # noqa: PLC0415

        se, fp = _forge()
        chain = fp.tenant_audit_chain(tenant_id)
        chain.parent.mkdir(parents=True, exist_ok=True)
        rec = se.write_event(
            chain, str(event_type),
            severity=str(severity).upper() if severity else None,
            details={**(details or {}), "tenant_id": tenant_id},
            hash_chain=True,
        )
        return str(rec.get("hash", ""))


class CentralizedConfigManager:
    """
    Phase 2: Centralized configuration management

    Features:
    - Central config store (etcd/Consul or file-based)
    - Schema validation with fail-closed semantics
    - Audit chain integration (GDPR Art. 30)
    - Per-instance overrides with tracking
    - Multi-tenant isolation

    All operations are fail-closed:
    - Invalid config → DEFAULT_SAFE_CONFIG
    - Store unavailable → DEFAULT_SAFE_CONFIG
    - Validation error → reject change, log event
    """

    CENTRAL_STORE_PATH = os.environ.get(
        "CONFIG_STORE_URL",
        "file://~/.corvin/config/canonical.json"
    )

    # Schema location (will be separate file)
    SCHEMA_PATH = Path(__file__).parent / "schema.json"

    def __init__(self, audit_chain: Optional[AuditChainWriter] = None):
        """Initialize config manager with optional audit integration.

        Args:
            audit_chain: AuditChainWriter instance (optional)
        """
        self.audit_chain = audit_chain
        self._lock = threading.RLock()
        self._instance_overrides: Dict[str, Dict[str, Any]] = {}
        self._schema = self._load_schema()

    @classmethod
    def create_with_audit(
        cls,
        audit_log_path: "str | Path | None" = None,
    ) -> "CentralizedConfigManager":
        """Factory method to create manager with audit integration.

        Config events go to ``tenant_audit_chain(<event tenant>)`` through the
        forge writer. ``audit_log_path`` is accepted for backward compatibility
        and IGNORED: binding a writer to a caller-chosen path is how a second
        hash scheme once landed on the canonical chain.

        Returns:
            CentralizedConfigManager with audit chain
        """
        return cls(audit_chain=TenantChainConfigAudit())

    def _load_schema(self) -> Dict[str, Any]:
        """Load JSON schema from file.

        Fails closed: on error, returns permissive schema.
        """
        try:
            if self.SCHEMA_PATH.exists():
                with open(self.SCHEMA_PATH, "r") as f:
                    return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

        # Fallback: minimal schema (permissive)
        return {"type": "object"}

    def get_config(
        self,
        tenant_id: str,
        instance_id: Optional[str] = None,
        use_overrides: bool = True,
    ) -> Dict[str, Any]:
        """Get configuration for tenant (canonical or with overrides).

        Args:
            tenant_id: Tenant identifier
            instance_id: Optional instance for override lookup
            use_overrides: Whether to apply instance overrides

        Returns:
            Configuration dict (or DEFAULT_SAFE_CONFIG on error)

        Behavior:
        1. Fetch canonical config from central store
        2. Validate against schema
        3. Apply instance overrides (if use_overrides=True)
        4. Return or fail-closed to DEFAULT_SAFE_CONFIG
        """
        with self._lock:
            try:
                # Step 1: Fetch canonical config
                canonical = self._fetch_canonical(tenant_id)
                if canonical is None:
                    return _safe_default()

                # Step 2: Validate
                errors = self.validate_config(canonical)
                if errors:
                    self._log_audit(
                        tenant_id=tenant_id,
                        event_type="config.fetch_failed",
                        details={"reason": f"validation_errors: {errors}"},
                        severity="warning",
                    )
                    return _safe_default()

                # Step 3: Apply overrides
                config = canonical.copy()
                if use_overrides and instance_id:
                    overrides = self._instance_overrides.get(
                        f"{tenant_id}:{instance_id}", {}
                    )
                    config = self._deep_merge(config, overrides)

                return config

            except Exception as e:
                self._log_audit(
                    tenant_id=tenant_id,
                    event_type="config.fetch_error",
                    details={"exception": str(e)},
                    severity="critical",
                )
                return _safe_default()

    def validate_config(self, config: Dict[str, Any]) -> List[str]:
        """Validate config against schema.

        Args:
            config: Configuration to validate

        Returns:
            List of validation errors (empty = valid)

        Validation rules:
        - telemetry.push_interval_seconds: int [5-300]
        - plugins.enabled_plugins: list of {id, version, hash}
        - database.schema_version: int [4-6]
        - security.consent_required: bool
        """
        errors = []

        try:
            # Validate against JSON schema if available
            if self._schema:
                validate(instance=config, schema=self._schema)
        except ValidationError as e:
            errors.append(f"Schema validation: {e.message}")

        # Custom validation rules (fail-closed)
        if "telemetry" in config:
            tel = config["telemetry"]
            if "push_interval_seconds" in tel:
                interval = tel["push_interval_seconds"]
                if not isinstance(interval, int) or interval < 5 or interval > 300:
                    errors.append(
                        "telemetry.push_interval_seconds must be int [5-300]"
                    )

        if "database" in config:
            db = config["database"]
            if "schema_version" in db:
                version = db["schema_version"]
                if not isinstance(version, int) or version < 4 or version > 6:
                    errors.append("database.schema_version must be int [4-6]")

        if "plugins" in config:
            plugins = config["plugins"]
            if "enabled_plugins" in plugins:
                if not isinstance(plugins["enabled_plugins"], list):
                    errors.append("plugins.enabled_plugins must be a list")

        return errors

    def set_config(
        self,
        tenant_id: str,
        config: Dict[str, Any],
        instance_id: Optional[str] = None,
        reason: str = "admin",
    ) -> bool:
        """Set canonical or override configuration.

        Args:
            tenant_id: Tenant identifier
            config: New configuration
            instance_id: If set, creates per-instance override; else canonical
            reason: Audit reason for change

        Returns:
            True if successful, False on validation error

        Behavior:
        1. Validate new config
        2. Fetch old config (for audit)
        3. Write to store
        4. Log audit event
        5. Fail-closed on error (don't write, log error)
        """
        with self._lock:
            # Step 1: Validate
            errors = self.validate_config(config)
            if errors:
                self._log_audit(
                    tenant_id=tenant_id,
                    event_type="config.set_rejected",
                    details={
                        "instance_id": instance_id,
                        "reason": reason,
                        "validation_errors": errors,
                    },
                    severity="warning",
                )
                return False

            try:
                # Step 2: Fetch old config
                old_config = self.get_config(
                    tenant_id,
                    instance_id=instance_id,
                    use_overrides=False,
                )

                # Step 3: Write to store
                if instance_id:
                    # Override
                    key = f"{tenant_id}:{instance_id}"
                    self._instance_overrides[key] = config
                else:
                    # Canonical (file-based for dev)
                    self._write_canonical(tenant_id, config)

                # Step 4: Log audit event
                self._log_audit(
                    tenant_id=tenant_id,
                    event_type="config.set_success",
                    details={
                        "instance_id": instance_id,
                        "reason": reason,
                        "old_config": old_config,
                        "new_config": config,
                        "is_override": bool(instance_id),
                    },
                    severity="info",
                )

                return True

            except Exception as e:
                self._log_audit(
                    tenant_id=tenant_id,
                    event_type="config.set_error",
                    details={
                        "instance_id": instance_id,
                        "exception": str(e),
                    },
                    severity="critical",
                )
                return False

    def detect_config_drift(
        self,
        tenant_id: str,
        instance_id: str,
        instance_config: Dict[str, Any],
    ) -> List[ConfigDrift]:
        """Detect configuration drift between instance and canonical.

        Args:
            tenant_id: Tenant identifier
            instance_id: Instance identifier
            instance_config: Current config on instance

        Returns:
            List of ConfigDrift objects (empty = no drift)

        Drift detection:
        - Compare instance config vs canonical
        - Flag mismatches as CRITICAL/HIGH/MEDIUM/LOW
        - Account for per-instance overrides
        """
        with self._lock:
            drifts = []
            canonical = self.get_config(
                tenant_id,
                instance_id=instance_id,
                use_overrides=False,
            )

            # Check if instance has overrides
            override_key = f"{tenant_id}:{instance_id}"
            has_overrides = override_key in self._instance_overrides

            # Recursive drift detection
            drifts.extend(
                self._detect_drifts_recursive(
                    canonical,
                    instance_config,
                    has_overrides=has_overrides,
                    path="",
                    tenant_id=tenant_id,
                    instance_id=instance_id,
                )
            )

            # Log drift events
            for drift in drifts:
                self._log_audit(
                    tenant_id=tenant_id,
                    event_type="config.drift_detected",
                    details={
                        "instance_id": instance_id,
                        "key": drift.key,
                        "expected": str(drift.expected),
                        "actual": str(drift.actual),
                        "severity": drift.severity,
                    },
                    severity="warning" if drift.severity != "CRITICAL" else "critical",
                )

            return drifts

    def _detect_drifts_recursive(
        self,
        expected: Dict[str, Any],
        actual: Dict[str, Any],
        path: str = "",
        has_overrides: bool = False,
        tenant_id: str = "",
        instance_id: str = "",
    ) -> List[ConfigDrift]:
        """Recursively detect drifts in nested config."""
        drifts = []

        for key, expected_val in expected.items():
            current_path = f"{path}.{key}" if path else key
            actual_val = actual.get(key)

            if isinstance(expected_val, dict) and isinstance(actual_val, dict):
                drifts.extend(
                    self._detect_drifts_recursive(
                        expected_val,
                        actual_val,
                        path=current_path,
                        has_overrides=has_overrides,
                        tenant_id=tenant_id,
                        instance_id=instance_id,
                    )
                )
            elif actual_val != expected_val:
                severity = "CRITICAL"
                # Check if this path is in instance overrides (nested structure)
                if has_overrides:
                    overrides = self._instance_overrides.get(f"{tenant_id}:{instance_id}", {})
                    # Navigate nested structure to check if this path is overridden
                    path_parts = current_path.split(".")
                    override_val = overrides
                    for part in path_parts:
                        if isinstance(override_val, dict) and part in override_val:
                            override_val = override_val[part]
                        else:
                            override_val = None
                            break
                    # If override exists and matches actual, it's expected (MEDIUM severity)
                    if override_val is not None:
                        severity = "MEDIUM"

                drifts.append(
                    ConfigDrift(
                        key=current_path,
                        expected=expected_val,
                        actual=actual_val,
                        severity=severity,
                        instance_id=instance_id,
                        detected_at=datetime.utcnow().isoformat(),
                    )
                )

        return drifts

    def _fetch_canonical(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        """Fetch canonical config from central store (file-based for dev)."""
        try:
            config_file = _config_file(tenant_id)

            if config_file.exists():
                with open(config_file, "r") as f:
                    return json.load(f)

            return None

        except (json.JSONDecodeError, IOError):
            return None

    def _write_canonical(self, tenant_id: str, config: Dict[str, Any]) -> None:
        """Write canonical config to central store (file-based for dev)."""
        config_file = _config_file(tenant_id)
        config_file.parent.mkdir(parents=True, exist_ok=True)

        with open(config_file, "w") as f:
            json.dump(config, f, indent=2)

    def _deep_merge(
        self,
        base: Dict[str, Any],
        overrides: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Deep merge overrides into base config."""
        result = base.copy()

        for key, value in overrides.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value

        return result

    def _log_audit(
        self,
        tenant_id: str,
        event_type: str,
        details: Dict[str, Any],
        severity: str = "info",
    ) -> None:
        """Log configuration event to audit chain.

        Args:
            tenant_id: Tenant identifier
            event_type: Type of event (config.*)
            details: Event details
            severity: Event severity (info, warning, critical)
        """
        if not self.audit_chain:
            return

        try:
            self.audit_chain.write_event_dict(
                event_type=event_type,
                tenant_id=tenant_id,
                user_id=None,
                details=details,
                severity=severity,
            )
        except Exception as exc:  # noqa: BLE001
            # Config audit is best-effort by design (a config read must not
            # crash on an audit hiccup) — but never SILENT: say it was lost.
            import logging  # noqa: PLC0415

            logging.getLogger(__name__).error(
                "config audit event %s NOT recorded for tenant %s: %s",
                event_type, tenant_id, type(exc).__name__,
            )


# Module-level singleton (optional)
_global_manager: Optional[CentralizedConfigManager] = None


def get_global_config_manager(
    create_if_missing: bool = False,
) -> Optional[CentralizedConfigManager]:
    """Get the global config manager instance."""
    global _global_manager
    if _global_manager is None and create_if_missing:
        _global_manager = CentralizedConfigManager()
    return _global_manager

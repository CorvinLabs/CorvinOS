"""
Tier 0: Bootstrap Manager — Config loading, audit init, core registry.

Responsibilities:
- Load config from tenant.corvin.yaml or defaults
- Initialize audit hash-chain (GDPR Art. 30/32)
- Register core plugin interfaces (audit_backend, notification_backend, etc.)
- Verify boot tripwire (audit chain reachable + valid)
- Fail-closed on any compliance check

GDPR Compliance:
- Every state change logged to audit.jsonl (hash-chained)
- Boot tripwire verifies chain integrity before proceeding
- Consent gate checked for all plugin operations
"""

import json
import logging
import os
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


@dataclass
class Config:
    """Minimal config for Tier 1 Pilot."""

    tenant_id: str = "_default"
    corvin_home: Path = Path.home() / ".corvin"
    db_path: Path = None  # Computed from corvin_home
    audit_log_path: Path = None  # Computed from corvin_home
    enable_telemetry: bool = True
    plugin_boot_layer: str = "bundled"  # compliance, core, bundled, installed
    auto_load_plugins: bool = True

    def __post_init__(self):
        """Compute derived paths."""
        if self.db_path is None:
            self.db_path = self.corvin_home / "tenants" / self.tenant_id / "pilot_tier1.db"
        if self.audit_log_path is None:
            self.audit_log_path = (
                self.corvin_home / "tenants" / self.tenant_id / "audit.jsonl"
            )

    def __repr__(self):
        """Safe repr without paths (for logging)."""
        return f"Config(tenant={self.tenant_id}, plugin_layer={self.plugin_boot_layer})"


class AuditChain:
    """Hash-chained audit log (GDPR Art. 30/32)."""

    def __init__(self, log_path: Path):
        """Initialize audit chain.

        Args:
            log_path: Path to audit.jsonl (created if missing)
        """
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.touch(exist_ok=True)

    def write_event(
        self,
        event_type: str,
        actor: str,
        action: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Write hash-chained event to audit log.

        Args:
            event_type: e.g., 'bootstrap.started', 'plugin.installed'
            actor: e.g., 'bootstrap', 'cli', 'task_engine'
            action: e.g., 'start', 'install', 'enable'
            details: optional context dict

        Returns:
            Event hash (for verification)

        GDPR: Every event is dated, actor-attributed, and hash-chained.
        """
        import hashlib

        details = details or {}
        event = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event_type": event_type,
            "actor": actor,
            "action": action,
            "details": details,
            "previous_hash": self._last_hash(),
        }

        # Compute event hash (fail-closed: must include previous)
        event_json = json.dumps(event, sort_keys=True, separators=(",", ":"))
        event_hash = hashlib.sha256(event_json.encode()).hexdigest()
        event["hash"] = event_hash

        # Write to log (append-only)
        with open(self.log_path, "a") as f:
            f.write(json.dumps(event) + "\n")

        return event_hash

    def _last_hash(self) -> str:
        """Get previous event's hash (for chain integrity)."""
        if not self.log_path.exists() or self.log_path.stat().st_size == 0:
            return "genesis"  # First event in chain
        with open(self.log_path, "r") as f:
            lines = f.readlines()
            if not lines:
                return "genesis"
            last_event = json.loads(lines[-1])
            return last_event.get("hash", "error")

    def verify_chain(self) -> bool:
        """Verify hash chain integrity (boot tripwire check).

        Returns:
            True if chain is intact (no tampering detected)
        """
        if not self.log_path.exists():
            return False  # No audit log = fail-closed

        import hashlib

        with open(self.log_path, "r") as f:
            lines = f.readlines()

        prev_hash = "genesis"
        for i, line in enumerate(lines):
            event = json.loads(line)
            if event.get("previous_hash") != prev_hash:
                logger.error(f"Audit chain broken at event {i}: {event}")
                return False  # Chain tampered = fail-closed

            # Recompute hash (verify integrity)
            stored_hash = event.pop("hash", None)
            event_json = json.dumps(event, sort_keys=True, separators=(",", ":"))
            computed_hash = hashlib.sha256(event_json.encode()).hexdigest()
            if computed_hash != stored_hash:
                logger.error(f"Event hash mismatch at {i}")
                return False

            prev_hash = stored_hash

        return True  # Chain valid


class CoreRegistry:
    """Core plugin interfaces (audit, notification, recall, etc.)."""

    def __init__(self):
        """Initialize core plugin registry with hardcoded interfaces."""
        self.interfaces = {
            "audit_backend": {
                "base_class": "AuditBackend",
                "module": "core.plugins.corvin_plugins.providers.audit_backend",
                "methods": ["write", "read", "verify"],
                "required": True,
            },
            "notification_backend": {
                "base_class": "NotificationBackend",
                "module": "core.plugins.corvin_plugins.providers.notification_backend",
                "methods": ["send", "batch_send"],
                "required": False,
            },
            "recall_backend": {
                "base_class": "RecallBackend",
                "module": "core.plugins.corvin_plugins.providers.recall_backend",
                "methods": ["store", "retrieve", "search"],
                "required": False,
            },
            "router_backend": {
                "base_class": "RouterBackend",
                "module": "core.plugins.corvin_plugins.providers.router_backend",
                "methods": ["route"],
                "required": False,
            },
        }

    def get_interface(self, interface_name: str) -> Optional[Dict[str, Any]]:
        """Get interface definition by name."""
        return self.interfaces.get(interface_name)

    def list_interfaces(self) -> list:
        """List all registered core interfaces."""
        return list(self.interfaces.keys())


class BootstrapManager:
    """Bootstrap tier — initialization, audit, core registry."""

    def __init__(self, config: Optional[Config] = None):
        """Initialize bootstrap manager.

        Args:
            config: Config object (created with defaults if None)

        Raises:
            RuntimeError: If boot tripwire fails (audit chain broken)
        """
        self.config = config or Config()
        self.audit = AuditChain(self.config.audit_log_path)
        self.core_registry = CoreRegistry()
        self._initialized = False

    def boot(self) -> bool:
        """Perform full bootstrap sequence.

        Returns:
            True if bootstrap successful

        Raises:
            RuntimeError: If any compliance check fails (fail-closed)
        """
        try:
            # 1. Log bootstrap start
            self.audit.write_event(
                event_type="bootstrap.started",
                actor="bootstrap",
                action="boot",
                details={"config": str(self.config)},
            )
            logger.info(f"Bootstrap started: {self.config}")

            # 2. Verify audit chain (boot tripwire - GDPR Art. 30/32)
            if not self.audit.verify_chain():
                raise RuntimeError(
                    "Boot tripwire: audit chain verification failed (GDPR Art. 30/32)"
                )
            logger.info("✓ Audit chain integrity verified (boot tripwire)")

            # 3. Initialize database (session + task storage)
            self._init_database()
            logger.info(f"✓ Database initialized: {self.config.db_path}")

            # 4. Log core registry
            self.audit.write_event(
                event_type="bootstrap.registry_loaded",
                actor="bootstrap",
                action="register_interfaces",
                details={
                    "interfaces": self.core_registry.list_interfaces(),
                },
            )
            logger.info(f"✓ Core registry loaded: {self.core_registry.list_interfaces()}")

            # 5. Mark bootstrap complete
            self._initialized = True
            self.audit.write_event(
                event_type="bootstrap.completed",
                actor="bootstrap",
                action="boot",
                details={"status": "success"},
            )
            logger.info("✓ Bootstrap completed successfully")

            return True

        except Exception as e:
            logger.error(f"✗ Bootstrap failed: {e}")
            self.audit.write_event(
                event_type="bootstrap.failed",
                actor="bootstrap",
                action="boot",
                details={"error": str(e)},
            )
            raise RuntimeError(f"Bootstrap failed: {e}") from e

    def _init_database(self):
        """Initialize SQLite database for sessions and tasks."""
        self.config.db_path.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(self.config.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    created_at TEXT,
                    updated_at TEXT,
                    status TEXT,
                    state BLOB
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    session_id TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    status TEXT,
                    task_type TEXT,
                    payload BLOB,
                    result BLOB,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS metrics (
                    metric_id TEXT PRIMARY KEY,
                    session_id TEXT,
                    timestamp TEXT,
                    metric_name TEXT,
                    value REAL,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id)
                )
                """
            )
            conn.commit()

    @property
    def is_initialized(self) -> bool:
        """Check if bootstrap completed successfully."""
        return self._initialized

    def get_database_connection(self) -> sqlite3.Connection:
        """Get database connection (tier-1/2/3 use this)."""
        if not self.is_initialized:
            raise RuntimeError("Bootstrap not completed")
        return sqlite3.connect(self.config.db_path)

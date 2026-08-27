"""Plugin Registry — tracks installed plugins with audit trail.

ADR-0444: Storage & Registry
- Maintains registry.json (source of truth)
- Finding #2 Fix: Secrets masking in audit logs (never log values)
- Finding #4 Fix: Config change tracking
"""

import os
import json
import time
import hashlib
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


@dataclass
class PluginEntry:
    """Registry entry for installed plugin."""
    id: str
    name: str
    version: str
    repo: str
    commit_hash: str
    installed_at: int
    status: str = "active"
    config_hash: Optional[str] = None
    config_updated_at: Optional[int] = None


class PluginRegistry:
    """Finding #2 & #4 Fix: Registry with secret masking + config tracking."""

    def __init__(self, registry_path: str = "~/.corvin/plugins/registry.json"):
        self.path = Path(registry_path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data = self._load()

    def add(
        self,
        plugin_id: str,
        name: str,
        version: str,
        repo: str,
        commit_hash: str
    ):
        """Add plugin to registry."""
        entry = PluginEntry(
            id=plugin_id,
            name=name,
            version=version,
            repo=repo,
            commit_hash=commit_hash,
            installed_at=int(time.time())
        )

        self.data["installed"].append(asdict(entry))
        self._save()

        # Finding #2 Fix: Audit log with secret masking
        self._audit_log("plugin_installed", {
            "plugin_id": plugin_id,
            "version": version,
            "repo": repo,
            # Never log commit_hash (could be sensitive)
            "commit_hash_prefix": commit_hash[:8]  # Only first 8 chars
        })

    def remove(self, plugin_id: str):
        """Remove plugin from registry."""
        self.data["installed"] = [
            p for p in self.data["installed"] if p["id"] != plugin_id
        ]
        self._save()

        self._audit_log("plugin_uninstalled", {
            "plugin_id": plugin_id
        })

    def update_config(self, plugin_id: str, config: Dict[str, Any]):
        """
        Update plugin config with secret masking.

        Finding #2 Fix: Never log secret values, only hashes
        """
        config_hash = self._hash_config(config)

        for entry in self.data["installed"]:
            if entry["id"] == plugin_id:
                old_hash = entry.get("config_hash")
                entry["config_hash"] = config_hash
                entry["config_updated_at"] = int(time.time())

                self._save()

                # Finding #2 Fix: Mask secrets in audit trail
                self._audit_log("plugin_config_changed", {
                    "plugin_id": plugin_id,
                    "old_config_hash": old_hash,
                    "new_config_hash": config_hash,
                    # NEVER log actual config values
                    "changed_keys": list(config.keys())
                })
                return

        raise ValueError(f"Plugin not found: {plugin_id}")

    def get_all(self) -> list:
        """Get all installed plugins."""
        return self.data.get("installed", [])

    def get(self, plugin_id: str) -> Optional[Dict[str, Any]]:
        """Get single plugin entry."""
        for entry in self.data["installed"]:
            if entry["id"] == plugin_id:
                return entry
        return None

    def _hash_config(self, config: Dict[str, Any]) -> str:
        """Hash config for audit trail (not stored in registry)."""
        config_json = json.dumps(config, sort_keys=True)
        return hashlib.sha256(config_json.encode()).hexdigest()[:16]

    def _audit_log(self, event_type: str, data: Dict[str, Any]):
        """
        Finding #2 Fix: Log to audit.jsonl with secret masking.

        Secrets (api_key, password, token) are never logged.
        Only hashes and metadata logged.
        """
        # Import audit trail (would be from core)
        try:
            from core.audit.audit_chain import AuditChain
            audit = AuditChain()

            # Mask secrets before logging
            safe_data = self._mask_secrets(data)

            audit.log_event(f"plugin.registry.{event_type}", safe_data)
        except ImportError:
            logger.warning("Audit chain not available, skipping log")

    def _mask_secrets(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Remove/mask secrets from audit data."""
        secret_keys = {"api_key", "password", "token", "secret", "key"}
        masked = {}

        for k, v in data.items():
            if any(secret in k.lower() for secret in secret_keys):
                # Replace secret with presence indicator + hash
                masked[k] = f"<MASKED:{hashlib.md5(str(v).encode()).hexdigest()[:8]}>"
            else:
                masked[k] = v

        return masked

    def _load(self) -> Dict[str, Any]:
        """Load registry from disk."""
        if self.path.exists():
            try:
                return json.load(open(self.path))
            except Exception as e:
                logger.error(f"Registry load error: {e}, using empty")
                return {"version": "1.0", "installed": []}
        return {"version": "1.0", "installed": []}

    def _save(self):
        """Save registry to disk."""
        try:
            json.dump(self.data, open(self.path, "w"), indent=2)
            logger.info(f"Registry saved: {len(self.data['installed'])} plugins")
        except Exception as e:
            logger.error(f"Registry save error: {e}")
            raise

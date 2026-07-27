"""Voice Config Manager — centralized resolver for tenant-based voice config.

Phase 1a: Voice Directory Consolidation

Consolidates voice configuration paths (profile.json, vault, memory, piper models)
from the legacy ~/.config/corvin-voice/ location into tenant-scoped directories
at <corvin_home>/tenants/<tenant_id>/voice/.

Automatic migration on first access (idempotent).
"""
from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)


@dataclass
class MigrationResult:
    """Result of a migration attempt."""
    success: bool
    migrated_items: int
    errors: list[str]
    warnings: list[str]


class VoiceConfigManager:
    """Centralized voice config path resolver with tenant consolidation.

    Handles:
    - Resolving voice config paths (profile.json, vault, memory, piper models)
    - Detecting legacy ~/.config/corvin-voice/ vs. new tenant/voice/
    - Transparent migration from legacy to tenant-scoped location
    - Environment variable overrides (VOICE_CONFIG_DIR for testing/deployment)
    """

    def __init__(self, tenant_id: Optional[str] = None):
        """Initialize manager for a specific tenant.

        Args:
            tenant_id: Tenant identifier. If None, uses current_tenant().
        """
        self.tenant_id = tenant_id
        if self.tenant_id is None:
            try:
                from forge.tenants import current_tenant
                self.tenant_id = current_tenant()
            except (ImportError, RuntimeError):
                self.tenant_id = "_default"

    def _tenant_home(self) -> Path:
        """Get the tenant's home directory."""
        try:
            from forge.paths import tenant_home
            return tenant_home(self.tenant_id)
        except ImportError:
            # Fallback when forge is not available (test environment)
            corvin_home = os.environ.get("CORVIN_HOME")
            if corvin_home:
                return Path(corvin_home) / "tenants" / self.tenant_id
            return Path.home() / ".corvin" / "tenants" / self.tenant_id

    def voice_home(self) -> Path:
        """Return tenant's voice config directory.

        This is the canonical location for tenant-scoped voice configuration.
        """
        return self._tenant_home() / "voice"

    def legacy_voice_config_dir(self) -> Path:
        """Return legacy ~/.config/corvin-voice location (DEPRECATED).

        This is the old location from which we migrate. Kept for backward
        compatibility and migration purposes.
        """
        override = os.environ.get("VOICE_CONFIG_DIR", "").strip()
        if override:
            return Path(os.path.expanduser(os.path.expandvars(override)))

        xdg_config = os.environ.get("XDG_CONFIG_HOME", "").strip()
        if xdg_config:
            return Path(xdg_config) / "corvin-voice"
        return Path.home() / ".config" / "corvin-voice"

    def has_legacy_config(self) -> bool:
        """Check if legacy ~/.config/corvin-voice exists."""
        return self.legacy_voice_config_dir().exists()

    def has_new_config(self) -> bool:
        """Check if new tenant/voice/ exists."""
        return self.voice_home().exists()

    def needs_migration(self) -> bool:
        """Check if migration is needed.

        Returns True if legacy config exists and new location does not.
        """
        return self.has_legacy_config() and not self.has_new_config()

    def profile_path(self) -> Path:
        """Path to voice profile.json (Tier 1 memory).

        Prefers new tenant location if it exists; falls back to legacy.
        """
        new_path = self.voice_home() / "profile.json"
        if new_path.exists():
            return new_path

        legacy_path = self.legacy_voice_config_dir() / "profile.json"
        if legacy_path.exists():
            return legacy_path

        # Default to new location (will be created if needed)
        return new_path

    def vault_dir(self) -> Path:
        """Path to voice vault directory (Tier 3 memory).

        Stores encrypted credentials and API keys.
        """
        new_path = self.voice_home() / "vault"
        if new_path.exists():
            return new_path

        legacy_path = self.legacy_voice_config_dir() / "vault"
        if legacy_path.exists():
            return legacy_path

        return new_path

    def memory_dir(self) -> Path:
        """Path to voice memory directory (Tier 2 memory).

        Stores longer-form conversation history and notes.
        """
        new_path = self.voice_home() / "memory"
        if new_path.exists():
            return new_path

        legacy_path = self.legacy_voice_config_dir() / "memory"
        if legacy_path.exists():
            return legacy_path

        return new_path

    def piper_models_dir(self) -> Path:
        """Path to piper TTS models.

        Large binary models for speech synthesis.
        """
        new_path = self.voice_home() / "piper-models"
        if new_path.exists():
            return new_path

        legacy_path = self.legacy_voice_config_dir() / "piper-models"
        if legacy_path.exists():
            return legacy_path

        return new_path

    def migrate_from_legacy(self) -> MigrationResult:
        """Migrate voice config from legacy to tenant location.

        Copies all voice configuration from ~/.config/corvin-voice/
        to the new tenant-scoped directory.

        This is idempotent: safe to call multiple times. A `.migrated`
        marker file prevents re-running the copy on subsequent calls.

        Returns:
            MigrationResult with success status, item count, and any errors.
        """
        errors = []
        warnings = []
        migrated_items = 0

        # Check if already migrated
        migration_marker = self.voice_home() / ".migrated"
        if migration_marker.exists():
            _log.debug(f"Voice migration already completed for tenant {self.tenant_id}")
            return MigrationResult(
                success=True,
                migrated_items=0,
                errors=[],
                warnings=[]
            )

        # Nothing to migrate if legacy config doesn't exist
        if not self.has_legacy_config():
            _log.debug(f"No legacy voice config found for tenant {self.tenant_id}")
            return MigrationResult(
                success=True,
                migrated_items=0,
                errors=[],
                warnings=[]
            )

        try:
            # Create new directory
            self.voice_home().mkdir(parents=True, exist_ok=True)

            # Copy files and directories
            legacy_dir = self.legacy_voice_config_dir()
            for item in legacy_dir.iterdir():
                try:
                    src = item
                    dst = self.voice_home() / item.name

                    if src.is_dir():
                        # Copy directory recursively
                        if dst.exists():
                            _log.debug(f"  Destination {item.name}/ exists, skipping")
                        else:
                            shutil.copytree(src, dst, dirs_exist_ok=True)
                            _log.debug(f"  Migrated {item.name}/ (directory)")
                            migrated_items += 1
                    else:
                        # Copy file
                        if dst.exists():
                            _log.debug(f"  Destination {item.name} exists, skipping")
                        else:
                            shutil.copy2(src, dst)
                            _log.debug(f"  Migrated {item.name} (file)")
                            migrated_items += 1

                except Exception as e:
                    error_msg = f"Failed to migrate {item.name}: {str(e)}"
                    errors.append(error_msg)
                    _log.warning(f"  {error_msg}")

            # Mark as migrated (even if there were errors, so we don't retry forever)
            migration_marker.touch()

            _log.info(
                f"Voice migration complete for tenant {self.tenant_id}: "
                f"{migrated_items} items, {len(errors)} errors"
            )

            return MigrationResult(
                success=len(errors) == 0,
                migrated_items=migrated_items,
                errors=errors,
                warnings=warnings
            )

        except Exception as e:
            error_msg = f"Voice migration failed: {str(e)}"
            _log.error(error_msg, exc_info=True)
            return MigrationResult(
                success=False,
                migrated_items=migrated_items,
                errors=[error_msg],
                warnings=[]
            )


# Singleton instance cache (per tenant)
_instances: dict[str, VoiceConfigManager] = {}
_instance_lock = __import__("threading").Lock()


def get_voice_config_manager(tenant_id: Optional[str] = None) -> VoiceConfigManager:
    """Get or create a VoiceConfigManager instance for a tenant.

    Caches instances per tenant to avoid redundant initialization.
    """
    if tenant_id is None:
        try:
            from forge.tenants import current_tenant
            tenant_id = current_tenant()
        except (ImportError, RuntimeError):
            tenant_id = "_default"

    with _instance_lock:
        if tenant_id not in _instances:
            _instances[tenant_id] = VoiceConfigManager(tenant_id)
        return _instances[tenant_id]

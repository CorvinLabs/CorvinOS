"""Voice Config Manager — centralized resolver for tenant-based voice config.

Phase 1a: Voice Directory Consolidation

Consolidates voice configuration paths (profile.json, vault, memory, piper models)
from the legacy ~/.config/corvin-voice/ location into tenant-scoped directories
at <corvin_home>/tenants/<tenant_id>/voice/.

Automatic migration on first access (idempotent).

Two invariants were added on 2026-09-07 after the migration was measured
copying 1.4 GB / 275,414 files on every fresh voice home (adversarial review):

1. **Allow-list, not "copy everything".** ``migrate_from_legacy()`` copies only
   the named configuration artefacts in ``MIGRATABLE_FILES`` /
   ``MIGRATABLE_DIRS``. The legacy directory is not only configuration — it is
   also the anchor-key directory for the Layer-16 audit chain
   (``audit.jsonl``, ``audit_anchor.key``, ``chain_ids/``, ``chain_tails/``,
   ``mac_active_chains/`` — 268k marker files on the maintainer's host), a
   virtualenv (``google/venv``, 147 MB), pid/lock/log files, and setup markers.
   Copying an audit chain or an anchor key into a second location is a
   compliance hazard, and copying runtime state is unbounded. An ALLOW-list is
   used rather than a deny-list because the failure mode of a deny-list is
   "the next runtime directory someone drops in here gets copied again",
   which is exactly the bug; the failure mode of an allow-list is "a new
   config file is not carried over", which is visible, recoverable, and
   already covered by the legacy read-fallback below.

2. **The ambient user config never migrates into a non-ambient home.**
   ``~/.config/corvin-voice`` is user-global. The install home is per-install.
   Pairing them implicitly meant every test that set ``CORVIN_HOME`` to a
   throwaway temp dir "needed migration" and copied the maintainer's real
   1.4 GB config into ``/tmp``. Now the ambient legacy dir is only a migration
   source for the AMBIENT corvin home (the one ``corvin_home()`` resolves to
   with ``CORVIN_HOME`` unset). A redirected home must name its legacy source
   explicitly via ``VOICE_CONFIG_DIR``. See ``legacy_source_allowed()``.
"""
from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)

#: Top-level FILES carried over by a legacy→tenant migration. Everything not
#: listed here stays in the legacy directory (see module docstring). Notable
#: deliberate exclusions: ``audit.jsonl`` + ``audit_anchor.key`` +
#: ``audit_mac_active`` + ``audit_manifest_mac_active`` (Layer-16 audit chain
#: and its anchor — an audit chain is evidence and is never duplicated),
#: ``maintainer.key`` / ``maintainer.env`` (host-level maintainer credentials,
#: not tenant config), ``current.pgid`` / ``tts.lock`` (runtime), ``*.log``,
#: ``*.bak-*`` and the ``.*_setup_complete`` markers.
MIGRATABLE_FILES: frozenset[str] = frozenset({
    "profile.json",     # Tier-1 voice profile (profile_path())
    "config.json",      # voice runtime configuration
    "secrets.json",     # BYOK secret index
    "service.env",      # bridge/service configuration
    ".env",             # provider keys / environment configuration
    "license.jwt",      # Tier licence token (ADR-0156)
})

#: Top-level DIRECTORIES carried over by a legacy→tenant migration. Deliberate
#: exclusions: ``mac_active_chains`` / ``chain_ids`` / ``chain_tails`` /
#: ``manifest_mac_active_dirs`` (out-of-tree audit anchors, keyed to the anchor
#: key's own directory — meaningless anywhere else, and 268k files on the
#: maintainer's host), ``forge`` (holds a second ``audit.jsonl`` hash chain),
#: ``google`` and ``venv`` (virtualenvs — build artefacts, 147 MB), and
#: ``whatsapp`` (pid files).
MIGRATABLE_DIRS: frozenset[str] = frozenset({
    "vault",            # Tier-3 encrypted credential vault (vault_dir())
    "memory",           # Tier-2 topic memory (memory_dir())
    "piper-models",     # piper TTS voices (piper_models_dir())
    "whisper-models",   # local STT models
})

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
            return self._corvin_home() / "tenants" / self.tenant_id

    def _corvin_home(self) -> Path:
        """The EFFECTIVE runtime root — ``$CORVIN_HOME`` when set, else ambient.

        Delegates to ``forge.paths.corvin_home()`` when forge is importable so
        this cannot drift from the resolver ``_tenant_home()`` actually uses.
        """
        try:
            from forge.paths import corvin_home
            return corvin_home()
        except ImportError:
            env = os.environ.get("CORVIN_HOME", "").strip()
            if env:
                return Path(os.path.expanduser(os.path.expandvars(env)))
            return self._ambient_corvin_home()

    def _ambient_corvin_home(self) -> Path:
        """The runtime root as it resolves WITHOUT the ``CORVIN_HOME`` override.

        Mirrors ``forge.paths.corvin_home()`` minus its env branch: the
        repo-local ``.corvin`` in a source checkout, else ``~/.corvin``. This is
        "the home this user's install would use by default", and it is the only
        home the AMBIENT ``~/.config/corvin-voice`` may be migrated into.
        """
        try:
            from forge.paths import _repo_root  # type: ignore[attr-defined]
            repo = _repo_root()
            if repo is not None:
                return Path(repo) / ".corvin"
        except Exception:  # noqa: BLE001 - forge absent / private API moved
            pass
        # core/console/corvin_console/voice_config.py -> repo root
        repo_local = Path(__file__).resolve().parents[3] / ".corvin"
        if repo_local.is_dir():
            return repo_local
        return Path.home() / ".corvin"

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

    def legacy_source_explicit(self) -> bool:
        """True when the legacy location was named explicitly (VOICE_CONFIG_DIR).

        An explicit source is a deliberate act by the operator (or a test) and
        is always an admissible migration source, whatever the target home is.
        """
        return bool(os.environ.get("VOICE_CONFIG_DIR", "").strip())

    def legacy_source_allowed(self) -> bool:
        """May this manager read/migrate FROM the legacy directory at all?

        ``~/.config/corvin-voice`` is USER-global; the corvin home is
        PER-INSTALL. Before 2026-09-07 the two were paired implicitly, so a
        process that pointed ``CORVIN_HOME`` at a throwaway directory — every
        test that builds the console app does exactly that — still resolved its
        legacy source to the operator's real ``~/.config/corvin-voice`` and
        "needed migration" from it (1.4 GB / 275k files, copied per test run).

        The honest rule: an ambient user-global config is a migration source
        only for the AMBIENT install home. A redirected home must name its
        source explicitly via ``VOICE_CONFIG_DIR``.
        """
        if self.legacy_source_explicit():
            return True
        try:
            return self._corvin_home().resolve() == self._ambient_corvin_home().resolve()
        except OSError:  # unresolvable path -> deny (fail closed)
            return False

    def has_legacy_config(self) -> bool:
        """Check if a *usable* legacy config directory exists.

        False when the legacy source is not admissible for this home
        (``legacy_source_allowed()``), even if the directory is on disk.
        """
        return self.legacy_source_allowed() and self.legacy_voice_config_dir().exists()

    def has_new_config(self) -> bool:
        """Check if new tenant/voice/ exists."""
        return self.voice_home().exists()

    def needs_migration(self) -> bool:
        """Check if migration is needed.

        Returns True if legacy config exists and new location does not.
        """
        return self.has_legacy_config() and not self.has_new_config()

    def _legacy_child(self, name: str) -> Optional[Path]:
        """An EXISTING entry inside the legacy dir, or None.

        Returns None whenever the legacy source is not admissible for this home
        (``legacy_source_allowed()``), so an isolated/redirected home never
        reads the operator's ambient ``~/.config/corvin-voice``.
        """
        if not self.legacy_source_allowed():
            return None
        candidate = self.legacy_voice_config_dir() / name
        return candidate if candidate.exists() else None

    def profile_path(self) -> Path:
        """Path to voice profile.json (Tier 1 memory).

        Prefers new tenant location if it exists; falls back to legacy.
        """
        new_path = self.voice_home() / "profile.json"
        if new_path.exists():
            return new_path

        legacy_path = self._legacy_child("profile.json")
        if legacy_path is not None:
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

        legacy_path = self._legacy_child("vault")
        if legacy_path is not None:
            return legacy_path

        return new_path

    def memory_dir(self) -> Path:
        """Path to voice memory directory (Tier 2 memory).

        Stores longer-form conversation history and notes.
        """
        new_path = self.voice_home() / "memory"
        if new_path.exists():
            return new_path

        legacy_path = self._legacy_child("memory")
        if legacy_path is not None:
            return legacy_path

        return new_path

    def piper_models_dir(self) -> Path:
        """Path to piper TTS models.

        Large binary models for speech synthesis.
        """
        new_path = self.voice_home() / "piper-models"
        if new_path.exists():
            return new_path

        legacy_path = self._legacy_child("piper-models")
        if legacy_path is not None:
            return legacy_path

        return new_path

    def migrate_from_legacy(self) -> MigrationResult:
        """Migrate voice CONFIGURATION from legacy to tenant location.

        Copies only the artefacts named in :data:`MIGRATABLE_FILES` and
        :data:`MIGRATABLE_DIRS`. The legacy directory is not a pure config
        directory — it is also the Layer-16 audit anchor-key directory, a
        virtualenv, a pid/lock/log directory and a marker store — and copying
        all of it moved 1.4 GB / 275,414 files on the maintainer's host every
        time a fresh voice home was initialised. Anything outside the
        allow-list is left where it is and reported in ``warnings``; the
        legacy read-fallbacks above keep it reachable.

        Runs only when the legacy source is admissible for this home
        (``legacy_source_allowed()``).

        This is idempotent: safe to call multiple times. A `.migrated`
        marker file prevents re-running the copy on subsequent calls.

        Returns:
            MigrationResult with success status, item count, and any errors.
        """
        errors: list[str] = []
        warnings: list[str] = []
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

        # Nothing to migrate if the legacy config does not exist -- or is not an
        # admissible source for this home (an isolated CORVIN_HOME must never
        # inherit the user-global ~/.config/corvin-voice; see
        # legacy_source_allowed()).
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

            legacy_dir = self.legacy_voice_config_dir()
            for item in sorted(legacy_dir.iterdir(), key=lambda q: q.name):
                is_dir = item.is_dir()
                allowed = (MIGRATABLE_DIRS if is_dir else MIGRATABLE_FILES)
                if item.name not in allowed:
                    warnings.append(f"not migrated (not voice configuration): {item.name}")
                    _log.debug(f"  Skipping {item.name} (outside migration allow-list)")
                    continue
                try:
                    src = item
                    dst = self.voice_home() / item.name

                    if is_dir:
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
                f"{migrated_items} items, {len(errors)} errors, "
                f"{len(warnings)} left in place"
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
                warnings=warnings
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

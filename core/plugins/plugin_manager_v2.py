"""Plugin Manager v2: Discovery, Installation, and Lifecycle Management.

Phase 2 of plugin ecosystem: adds marketplace discovery, installation workflows,
and runtime enable/disable without restart.

Key features:
1. Plugin discovery (local + marketplace)
2. Installation workflow (download, validate, extract)
3. Runtime lifecycle (enable/disable/uninstall)
4. Licensing gate integration (ADR-0769)
5. Audit trail for all operations
"""

import asyncio
import hashlib
import json
import logging
import shutil
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from urllib.parse import urljoin

logger = logging.getLogger(__name__)


class PluginStatus(Enum):
    """Plugin lifecycle status."""

    DISCOVERED = "discovered"  # Found but not installed
    INSTALLED = "installed"  # Installed, may be disabled
    ENABLED = "enabled"  # Loaded and active
    DISABLED = "disabled"  # Installed but disabled
    FAILED = "failed"  # Failed to load
    UNINSTALLED = "uninstalled"  # Removed


class PluginSource(Enum):
    """Plugin origin."""

    BUILTIN = "builtin"  # Shipped with CorvinOS
    MARKETPLACE = "marketplace"  # From Corvin-Marketplace
    COMMUNITY = "community"  # Community-contributed


@dataclass
class PluginInfo:
    """Plugin metadata from discovery."""

    id: str
    name: str
    version: str
    author: str
    description: str
    license: str
    homepage: Optional[str] = None
    repository: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    dependencies: List[Dict[str, str]] = field(default_factory=list)
    min_corvinOS_version: str = "3.0.0"
    required_tier: str = "free"  # free | pro | enterprise


@dataclass
class InstalledPlugin:
    """Installed plugin state."""

    id: str
    version: str
    status: PluginStatus
    source: PluginSource
    install_path: Path
    install_date: str  # ISO 8601
    checksum: str  # SHA256 of plugin archive
    enabled: bool
    config: Dict[str, Any] = field(default_factory=dict)
    last_error: Optional[str] = None


class PluginDiscoveryError(Exception):
    """Plugin discovery failed."""

    pass


class PluginInstallationError(Exception):
    """Plugin installation failed."""

    pass


class PluginManager:
    """Plugin Manager v2: Full lifecycle from discovery to execution.

    Responsibilities:
    1. Discover plugins (builtin + marketplace + community)
    2. Install/uninstall workflows
    3. Enable/disable lifecycle (runtime)
    4. Validate dependencies and licensing
    5. Audit all operations
    """

    def __init__(
        self,
        plugins_dir: Path = None,
        marketplace_url: str = "https://marketplace.corvinlabs.com",
        audit_logger: Optional[logging.Logger] = None,
        licensing_gate: Optional[callable] = None,
    ):
        """Initialize Plugin Manager v2.

        Args:
            plugins_dir: Root directory for installed plugins
            marketplace_url: Marketplace API endpoint
            audit_logger: Audit trail logger
            licensing_gate: Function(plugin_info) -> bool for tier validation
        """
        self.plugins_dir = plugins_dir or (
            Path.home() / ".corvin" / "plugins"
        )
        self.marketplace_url = marketplace_url
        self.audit_logger = audit_logger or logging.getLogger("audit")
        self.licensing_gate = licensing_gate

        # Create plugins directory
        self.plugins_dir.mkdir(parents=True, exist_ok=True)

        # Plugin state: id → InstalledPlugin
        self.installed_plugins: Dict[str, InstalledPlugin] = {}
        self.discovered_plugins: Dict[str, PluginInfo] = {}

        # Load existing plugins from disk
        self._load_installed_plugins()

    def _load_installed_plugins(self):
        """Load installed plugins from disk state."""
        state_file = self.plugins_dir / "installed.json"
        if not state_file.exists():
            return

        try:
            with open(state_file) as f:
                state = json.load(f)
                for plugin_id, plugin_data in state.items():
                    self.installed_plugins[plugin_id] = InstalledPlugin(
                        id=plugin_data["id"],
                        version=plugin_data["version"],
                        status=PluginStatus(plugin_data["status"]),
                        source=PluginSource(plugin_data["source"]),
                        install_path=Path(plugin_data["install_path"]),
                        install_date=plugin_data["install_date"],
                        checksum=plugin_data["checksum"],
                        enabled=plugin_data["enabled"],
                        config=plugin_data.get("config", {}),
                        last_error=plugin_data.get("last_error"),
                    )
        except Exception as e:
            logger.warning(f"Failed to load installed plugins: {e}")

    def _save_installed_plugins(self):
        """Save installed plugins state to disk."""
        state_file = self.plugins_dir / "installed.json"
        try:
            state = {
                pid: {
                    "id": p.id,
                    "version": p.version,
                    "status": p.status.value,
                    "source": p.source.value,
                    "install_path": str(p.install_path),
                    "install_date": p.install_date,
                    "checksum": p.checksum,
                    "enabled": p.enabled,
                    "config": p.config,
                    "last_error": p.last_error,
                }
                for pid, p in self.installed_plugins.items()
            }
            with open(state_file, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save plugin state: {e}")

    async def discover_plugins(
        self, source: Optional[PluginSource] = None
    ) -> Tuple[List[PluginInfo], List[str]]:
        """Discover available plugins.

        Args:
            source: Filter by source (all if None)

        Returns:
            (discovered_plugins, errors)
        """
        errors = []
        discovered = []

        try:
            # 1. Discover builtin plugins
            if source is None or source == PluginSource.BUILTIN:
                try:
                    builtin = await self._discover_builtin()
                    discovered.extend(builtin)
                except Exception as e:
                    errors.append(f"Builtin discovery failed: {e}")

            # 2. Discover marketplace plugins
            if source is None or source == PluginSource.MARKETPLACE:
                try:
                    marketplace = await self._discover_marketplace()
                    discovered.extend(marketplace)
                except Exception as e:
                    errors.append(f"Marketplace discovery failed: {e}")

            # 3. Discover community plugins
            if source is None or source == PluginSource.COMMUNITY:
                try:
                    community = await self._discover_community()
                    discovered.extend(community)
                except Exception as e:
                    errors.append(f"Community discovery failed: {e}")

            # Cache discovered plugins
            for plugin in discovered:
                self.discovered_plugins[plugin.id] = plugin

            self.audit_logger.info(
                f"plugin_discovery_complete: discovered={len(discovered)}, "
                f"errors={len(errors)}"
            )
            return discovered, errors

        except Exception as e:
            errors.append(f"Discovery failed: {e}")
            return discovered, errors

    async def _discover_builtin(self) -> List[PluginInfo]:
        """Discover builtin plugins shipped with CorvinOS."""
        plugins = []
        builtin_dir = Path(__file__).parent / "buildin"

        if not builtin_dir.exists():
            return plugins

        for category_dir in builtin_dir.iterdir():
            if not category_dir.is_dir():
                continue

            for plugin_dir in category_dir.iterdir():
                if not plugin_dir.is_dir():
                    continue

                try:
                    manifest = await self._load_plugin_manifest(
                        plugin_dir, PluginSource.BUILTIN
                    )
                    if manifest:
                        plugins.append(manifest)
                except Exception as e:
                    logger.warning(
                        f"Failed to load builtin {plugin_dir}: {e}"
                    )

        return plugins

    async def _discover_marketplace(self) -> List[PluginInfo]:
        """Discover plugins from Corvin-Marketplace."""
        # In production, would fetch from marketplace API
        # For now, return empty (mock integration)
        return []

    async def _discover_community(self) -> List[PluginInfo]:
        """Discover community-contributed plugins."""
        # In production, would scan configured community repos
        # For now, return empty
        return []

    async def _load_plugin_manifest(
        self, plugin_dir: Path, source: PluginSource
    ) -> Optional[PluginInfo]:
        """Load plugin manifest from directory.

        Args:
            plugin_dir: Directory containing plugin.json
            source: Plugin source

        Returns:
            PluginInfo if valid, None otherwise
        """
        manifest_file = plugin_dir / "plugin.json"
        if not manifest_file.exists():
            return None

        try:
            with open(manifest_file) as f:
                data = json.load(f)["plugin"]
                return PluginInfo(
                    id=data["id"],
                    name=data.get("name", data["id"]),
                    version=data["version"],
                    author=data.get("author", "unknown"),
                    description=data.get("description", ""),
                    license=data.get("license", "MIT"),
                    homepage=data.get("homepage"),
                    repository=data.get("repository"),
                    tags=data.get("tags", []),
                    dependencies=data.get("dependencies", []),
                    min_corvinOS_version=data.get("min_corvinOS_version", "3.0.0"),
                    required_tier=data.get("required_tier", "free"),
                )
        except Exception as e:
            logger.warning(f"Failed to parse manifest from {plugin_dir}: {e}")
            return None

    async def install_plugin(
        self, plugin_id: str, version: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Install a plugin (from discovered list).

        Args:
            plugin_id: Plugin identifier
            version: Specific version (latest if None)

        Returns:
            (success, message)
        """
        try:
            # 1. Find plugin in discovered
            if plugin_id not in self.discovered_plugins:
                return False, f"Plugin not found: {plugin_id}"

            plugin_info = self.discovered_plugins[plugin_id]

            # 2. Check licensing gate
            if self.licensing_gate:
                if not self.licensing_gate(plugin_info):
                    return (
                        False,
                        f"Plugin tier not available: {plugin_info.required_tier}",
                    )

            # 3. Check dependencies
            for dep in plugin_info.dependencies:
                dep_id = dep["id"]
                if dep_id not in self.installed_plugins:
                    return (
                        False,
                        f"Missing dependency: {dep_id}",
                    )

            # 4. Download/extract plugin
            install_path = self.plugins_dir / "installed" / plugin_id
            checksum = await self._download_plugin(
                plugin_info, install_path
            )

            # 5. Register installed plugin
            now = datetime.utcnow().isoformat() + "Z"
            self.installed_plugins[plugin_id] = InstalledPlugin(
                id=plugin_id,
                version=plugin_info.version,
                status=PluginStatus.INSTALLED,
                source=plugin_info.source if hasattr(
                    plugin_info, "source"
                ) else PluginSource.MARKETPLACE,
                install_path=install_path,
                install_date=now,
                checksum=checksum,
                enabled=False,  # Start disabled
                config={},
            )

            self._save_installed_plugins()

            self.audit_logger.info(
                f"plugin_installed: id={plugin_id}, version={plugin_info.version}, "
                f"path={install_path}"
            )
            return True, f"Plugin installed: {plugin_id} v{plugin_info.version}"

        except Exception as e:
            self.audit_logger.error(
                f"plugin_install_failed: id={plugin_id}, error={str(e)}"
            )
            return False, f"Installation failed: {str(e)}"

    async def _download_plugin(
        self, plugin_info: PluginInfo, install_path: Path
    ) -> str:
        """Download and extract plugin archive.

        Args:
            plugin_info: Plugin metadata
            install_path: Where to extract

        Returns:
            SHA256 checksum of archive
        """
        # Mock: create a minimal plugin structure
        install_path.mkdir(parents=True, exist_ok=True)

        # Create minimal plugin.json
        plugin_json = {
            "plugin": asdict(plugin_info)
        }
        with open(install_path / "plugin.json", "w") as f:
            json.dump(plugin_json, f, indent=2)

        # Return dummy checksum
        return hashlib.sha256(
            json.dumps(plugin_json).encode()
        ).hexdigest()

    async def enable_plugin(self, plugin_id: str) -> Tuple[bool, str]:
        """Enable an installed plugin (runtime).

        Args:
            plugin_id: Plugin identifier

        Returns:
            (success, message)
        """
        try:
            if plugin_id not in self.installed_plugins:
                return False, f"Plugin not installed: {plugin_id}"

            plugin = self.installed_plugins[plugin_id]
            if plugin.enabled:
                return True, f"Plugin already enabled: {plugin_id}"

            # Attempt to load plugin (mock)
            plugin.enabled = True
            plugin.status = PluginStatus.ENABLED
            self._save_installed_plugins()

            self.audit_logger.info(
                f"plugin_enabled: id={plugin_id}, version={plugin.version}"
            )
            return True, f"Plugin enabled: {plugin_id}"

        except Exception as e:
            self.audit_logger.error(
                f"plugin_enable_failed: id={plugin_id}, error={str(e)}"
            )
            return False, f"Enable failed: {str(e)}"

    async def disable_plugin(self, plugin_id: str) -> Tuple[bool, str]:
        """Disable an installed plugin (runtime).

        Args:
            plugin_id: Plugin identifier

        Returns:
            (success, message)
        """
        try:
            if plugin_id not in self.installed_plugins:
                return False, f"Plugin not installed: {plugin_id}"

            plugin = self.installed_plugins[plugin_id]
            if not plugin.enabled:
                return True, f"Plugin already disabled: {plugin_id}"

            plugin.enabled = False
            plugin.status = PluginStatus.DISABLED
            self._save_installed_plugins()

            self.audit_logger.info(
                f"plugin_disabled: id={plugin_id}, version={plugin.version}"
            )
            return True, f"Plugin disabled: {plugin_id}"

        except Exception as e:
            self.audit_logger.error(
                f"plugin_disable_failed: id={plugin_id}, error={str(e)}"
            )
            return False, f"Disable failed: {str(e)}"

    async def uninstall_plugin(self, plugin_id: str) -> Tuple[bool, str]:
        """Uninstall a plugin.

        Args:
            plugin_id: Plugin identifier

        Returns:
            (success, message)
        """
        try:
            if plugin_id not in self.installed_plugins:
                return False, f"Plugin not installed: {plugin_id}"

            plugin = self.installed_plugins[plugin_id]

            # Remove installation directory
            if plugin.install_path.exists():
                shutil.rmtree(plugin.install_path)

            # Remove from registry
            del self.installed_plugins[plugin_id]
            self._save_installed_plugins()

            self.audit_logger.info(
                f"plugin_uninstalled: id={plugin_id}, version={plugin.version}"
            )
            return True, f"Plugin uninstalled: {plugin_id}"

        except Exception as e:
            self.audit_logger.error(
                f"plugin_uninstall_failed: id={plugin_id}, error={str(e)}"
            )
            return False, f"Uninstall failed: {str(e)}"

    def list_installed_plugins(
        self, status_filter: Optional[PluginStatus] = None
    ) -> List[InstalledPlugin]:
        """List installed plugins with optional status filter.

        Args:
            status_filter: Filter by status (all if None)

        Returns:
            List of InstalledPlugin
        """
        plugins = list(self.installed_plugins.values())
        if status_filter:
            plugins = [p for p in plugins if p.status == status_filter]
        return plugins

    def get_plugin_config(self, plugin_id: str) -> Dict[str, Any]:
        """Get plugin runtime configuration.

        Args:
            plugin_id: Plugin identifier

        Returns:
            Configuration dict
        """
        if plugin_id not in self.installed_plugins:
            return {}
        return self.installed_plugins[plugin_id].config

    async def update_plugin_config(
        self, plugin_id: str, config: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """Update plugin configuration.

        Args:
            plugin_id: Plugin identifier
            config: New configuration dict

        Returns:
            (success, message)
        """
        try:
            if plugin_id not in self.installed_plugins:
                return False, f"Plugin not installed: {plugin_id}"

            self.installed_plugins[plugin_id].config = config
            self._save_installed_plugins()

            self.audit_logger.info(
                f"plugin_config_updated: id={plugin_id}, "
                f"keys={list(config.keys())}"
            )
            return True, f"Config updated: {plugin_id}"

        except Exception as e:
            self.audit_logger.error(
                f"plugin_config_update_failed: id={plugin_id}, error={str(e)}"
            )
            return False, f"Config update failed: {str(e)}"

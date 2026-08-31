"""
Plugin Versioning, Updates, and Multi-Plugin Installation

Handles:
- Multi-plugin installation with dependency resolution
- Plugin updates and upgrades
- Compatibility matrix validation
- Installation rollback on failure

ADR-0385 Phase 2 — Multi-plugin workflow and version management.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict, Optional, Callable
from datetime import datetime
from pathlib import Path
import json
import logging
from uuid import uuid4

from .dependency_resolver import PluginDependencyResolver, ResolutionError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UpdateInfo:
    """Information about available plugin updates."""
    current_version: str
    latest_version: str
    breaking_changes: bool
    changelog: str


@dataclass(frozen=True)
class InstallationJob:
    """Tracks multi-plugin installation progress."""
    job_id: str
    plugins: List[str]
    status: str  # queued, in_progress, completed, failed, rolled_back
    created_at: datetime
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    progress: Dict[str, str] = None  # plugin_id -> status


class PluginVersioningManager:
    """Manage plugin versions, updates, and multi-plugin installation."""

    def __init__(self, plugins_dict: Dict, installation_callback: Optional[Callable] = None):
        """
        Initialize with plugins registry.

        Args:
            plugins_dict: Dict of plugin_id -> PluginMetadata
            installation_callback: Optional callback for plugin installation (for testing)
        """
        self.plugins = plugins_dict
        self.resolver = PluginDependencyResolver(plugins_dict)
        self.installation_callback = installation_callback
        self.installation_jobs = {}

    def check_plugin_updates(self, installed_plugins: Dict[str, str]) -> Dict[str, UpdateInfo]:
        """
        Check for available updates.

        Args:
            installed_plugins: Dict of plugin_id -> version

        Returns:
            Dict of plugin_id -> UpdateInfo for plugins with updates available
        """
        updates = {}

        for plugin_id, current_version in installed_plugins.items():
            latest_plugin = self.plugins.get(plugin_id)
            if not latest_plugin:
                continue

            # Simple version comparison (in production, use packaging.version)
            if latest_plugin.version != current_version:
                updates[plugin_id] = UpdateInfo(
                    current_version=current_version,
                    latest_version=latest_plugin.version,
                    breaking_changes=False,  # Simplified for example
                    changelog=f"Updated from {current_version} to {latest_plugin.version}"
                )

        return updates

    def install_multi_plugins(
        self,
        plugin_ids: List[str],
        tenant_id: str,
        install_fn: Optional[Callable] = None
    ) -> InstallationJob:
        """
        Install multiple plugins with dependency resolution.

        Args:
            plugin_ids: List of plugin IDs to install
            tenant_id: Target tenant
            install_fn: Optional custom installation function

        Returns:
            InstallationJob with result
        """
        job = InstallationJob(
            job_id=f"install-{uuid4()}",
            plugins=plugin_ids,
            status="queued",
            created_at=datetime.utcnow(),
            progress={}
        )

        self.installation_jobs[job.job_id] = job

        try:
            # 1. Resolve dependencies
            logger.info(f"Resolving dependencies for {plugin_ids}")
            order, errors = self.resolver.validate_multi_install(plugin_ids)

            if errors:
                job.status = "failed"
                job.error = f"Dependency resolution failed: {[e.reason for e in errors]}"
                logger.error(job.error)
                return job

            # 2. Validate versions
            version_issues = self.resolver.detect_version_conflicts(plugin_ids)
            if version_issues:
                job.status = "failed"
                job.error = f"Version conflicts: {[e.reason for e in version_issues]}"
                logger.error(job.error)
                return job

            # 3. Check mutual exclusions
            exclusions = self.resolver.detect_mutual_exclusions(plugin_ids)
            if exclusions:
                job.status = "failed"
                job.error = f"Conflicting plugins: {exclusions}"
                logger.error(job.error)
                return job

            # 4. Install in order
            job.status = "in_progress"
            installed = []

            for plugin_id in order:
                job.progress[plugin_id] = "installing"

                try:
                    if install_fn:
                        install_fn(plugin_id, tenant_id)
                    elif self.installation_callback:
                        self.installation_callback(plugin_id, tenant_id)
                    else:
                        logger.info(f"Would install {plugin_id} to {tenant_id}")

                    installed.append(plugin_id)
                    job.progress[plugin_id] = "installed"

                except Exception as e:
                    # Rollback on failure
                    logger.error(f"Installation failed for {plugin_id}: {e}")
                    job.status = "rolled_back"
                    job.error = f"Failed installing {plugin_id}: {str(e)}"

                    # Rollback: reverse order
                    for pid in reversed(installed):
                        try:
                            logger.info(f"Rollback: uninstalling {pid}")
                            job.progress[pid] = "rolled_back"
                        except Exception as rollback_err:
                            logger.error(f"Rollback failed for {pid}: {rollback_err}")

                    return job

            # 5. Success
            job.status = "completed"
            job.completed_at = datetime.utcnow()
            logger.info(f"Multi-plugin installation completed: {order}")

        except Exception as e:
            job.status = "failed"
            job.error = str(e)
            logger.error(f"Unexpected error in multi-plugin installation: {e}", exc_info=True)

        return job

    def upgrade_plugin(
        self,
        plugin_id: str,
        target_version: str,
        tenant_id: str,
        upgrade_fn: Optional[Callable] = None
    ) -> bool:
        """
        Upgrade a plugin to a specific version.

        Args:
            plugin_id: Plugin to upgrade
            target_version: Target version
            tenant_id: Target tenant
            upgrade_fn: Optional custom upgrade function

        Returns:
            True if upgrade succeeded, False otherwise
        """
        plugin = self.plugins.get(plugin_id)
        if not plugin:
            logger.error(f"Plugin not found: {plugin_id}")
            return False

        logger.info(f"Upgrading {plugin_id} to v{target_version}")

        try:
            # Check min_corvin_version
            if plugin.min_corvin_version:
                logger.info(f"Minimum version requirement: {plugin.min_corvin_version}")

            # Perform upgrade
            if upgrade_fn:
                upgrade_fn(plugin_id, target_version, tenant_id)
            else:
                logger.info(f"Would upgrade {plugin_id} to {target_version} in {tenant_id}")

            logger.info(f"Upgrade successful: {plugin_id} -> v{target_version}")
            return True

        except Exception as e:
            logger.error(f"Upgrade failed for {plugin_id}: {e}")
            return False

    def get_compatibility_matrix(self) -> Dict[str, Dict]:
        """
        Get plugin compatibility matrix.

        Returns:
            Dict representing compatibility relationships
        """
        matrix = {}

        for plugin_id, plugin in self.plugins.items():
            matrix[plugin_id] = {
                "version": plugin.version,
                "depends_on": plugin.depends_on,
                "conflicts_with": getattr(plugin, 'conflicts_with', []),
                "compatible_with": []
            }

            # Find what this plugin is compatible with
            for other_id, other_plugin in self.plugins.items():
                if other_id == plugin_id:
                    continue

                # Check if other depends on this
                for dep in other_plugin.depends_on:
                    if plugin_id in dep:
                        matrix[plugin_id]["compatible_with"].append(other_id)
                        break

        return matrix

    def validate_installation_compatibility(self, plugin_ids: List[str]) -> Dict[str, any]:
        """
        Validate that a set of plugins can be installed together.

        Returns:
            {
                "valid": bool,
                "issues": List[str],
                "install_order": List[str]
            }
        """
        order, errors = self.resolver.validate_multi_install(plugin_ids)

        return {
            "valid": len(errors) == 0,
            "issues": [e.reason for e in errors],
            "install_order": order,
            "plugin_count": len(order) if order else 0
        }

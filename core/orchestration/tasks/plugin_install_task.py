"""Plugin Installation Task — brain engine task for installing plugins.

ADR-0443: Installation Engine
- Finding #3 Fix: Async event queue (non-blocking audit)
- Finding #4 Fix: Disk space pre-check
- Finding #5 Fix: Directory collision detection
"""

import os
import shutil
import asyncio
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from asyncio import Queue

logger = logging.getLogger(__name__)


class PluginInstallTask:
    """
    Installation task executed by Brain Engine.

    Findings fixes:
    - #3: Async queue for audit events (non-blocking)
    - #4: Pre-check disk space before clone
    - #5: Fail-closed on directory collision
    """

    def __init__(
        self,
        repo: str,
        plugin_id: str,
        version: str,
        min_disk_mb: int = 100
    ):
        self.repo = repo
        self.plugin_id = plugin_id
        self.version = version
        self.min_disk_mb = min_disk_mb
        self.status = "pending"
        # Finding #3, #10 Fix: Async event queue (robustified)
        # Increased from 1000 to 5000 for better throughput
        self.event_queue: Queue = Queue(maxsize=5000)
        # Critical events that MUST NOT be dropped
        self.critical_events = {"manifest_validated", "plugin_installed", "registry_updated"}

    async def execute(self) -> Dict[str, Any]:
        """Execute installation with full error handling."""
        try:
            logger.info(f"Installing plugin: {self.plugin_id} from {self.repo}")

            # Finding #5 Fix: Check directory collision
            await self._check_collision()

            # Finding #4 Fix: Check disk space
            await self._check_disk_space()

            # Import dependencies
            from core.console.github_client import GitHubClient
            from core.plugins.plugin_registry import PluginRegistry

            github = GitHubClient()
            registry = PluginRegistry()

            # 1. Fetch manifest from GitHub
            logger.info(f"Fetching manifest from {self.repo}")
            manifest = await github.get_manifest(self.repo)
            await self._emit_event("manifest_fetched", {"repo": self.repo})

            # 2. Validate manifest
            logger.info(f"Validating manifest")
            self._validate_manifest(manifest)
            await self._emit_event("manifest_validated", {"plugin_id": self.plugin_id})

            # 3. Git clone
            logger.info(f"Cloning repo")
            plugin_path = await self._git_clone(self.repo)
            await self._emit_event("repo_cloned", {"path": str(plugin_path)})

            # 4. Update registry
            logger.info(f"Registering plugin")
            registry.add(
                plugin_id=self.plugin_id,
                name=manifest.get("plugin", {}).get("name", self.plugin_id),
                version=self.version,
                repo=self.repo,
                commit_hash="latest"
            )
            await self._emit_event("registry_updated", {"plugin_id": self.plugin_id})

            # 5. Register Settings Panel
            logger.info(f"Registering Settings Panel")
            await self._register_panel(manifest)
            await self._emit_event("panel_registered", {"plugin_id": self.plugin_id})

            self.status = "success"
            logger.info(f"✓ Plugin installed: {self.plugin_id}")

            return {
                "success": True,
                "plugin_id": self.plugin_id,
                "version": self.version,
                "path": str(plugin_path)
            }

        except Exception as e:
            logger.error(f"Installation failed: {e}")
            await self._rollback()
            self.status = "failed"

            return {
                "success": False,
                "plugin_id": self.plugin_id,
                "error": str(e)
            }

    async def _check_collision(self):
        """Finding #5 Fix: Fail-closed on directory collision."""
        plugin_dir = Path.home() / ".corvin" / "plugins" / self.plugin_id

        if plugin_dir.exists():
            logger.error(f"Plugin directory already exists: {plugin_dir}")
            raise RuntimeError(
                f"Plugin '{self.plugin_id}' is already installed. "
                f"Uninstall first or use a different ID."
            )

        logger.info(f"Directory collision check PASSED: {plugin_dir}")

    async def _check_disk_space(self):
        """Finding #4 Fix: Pre-check disk space before clone."""
        plugin_dir = Path.home() / ".corvin" / "plugins"

        try:
            stat = os.statvfs(plugin_dir)
            available_mb = (stat.f_bavail * stat.f_frsize) / (1024 * 1024)

            if available_mb < self.min_disk_mb:
                logger.error(
                    f"Insufficient disk space: {available_mb:.0f}MB available, "
                    f"{self.min_disk_mb}MB required"
                )
                raise RuntimeError(
                    f"Insufficient disk space. Need {self.min_disk_mb}MB, "
                    f"have {available_mb:.0f}MB"
                )

            logger.info(f"Disk space check PASSED: {available_mb:.0f}MB available")

        except Exception as e:
            logger.error(f"Disk space check failed: {e}")
            raise

    async def _git_clone(self, repo: str) -> Path:
        """Clone repo to ~/.corvin/plugins/{plugin_id}/."""
        plugin_dir = Path.home() / ".corvin" / "plugins" / self.plugin_id
        plugin_dir.mkdir(parents=True, exist_ok=False)  # Fail if exists

        try:
            # Simulate async git clone (would use subprocess.run in production)
            import subprocess

            result = subprocess.run(
                ["git", "clone", "--depth=1", f"https://github.com/{repo}.git", str(plugin_dir)],
                capture_output=True,
                timeout=30
            )

            if result.returncode != 0:
                raise RuntimeError(f"Git clone failed: {result.stderr.decode()}")

            return plugin_dir

        except Exception as e:
            # Clean up on failure
            if plugin_dir.exists():
                shutil.rmtree(plugin_dir)
            raise

    def _validate_manifest(self, manifest: Dict[str, Any]):
        """
        Finding #6 Fix: Strengthen manifest validation.
        - Reject paths with ../
        - Validate required schema
        - Prevent path traversal attacks
        """
        plugin = manifest.get("plugin", {})

        if not plugin.get("id"):
            raise ValueError("Manifest missing plugin.id")

        # Reject path traversal attempts
        for key, value in self._deep_items(manifest):
            if isinstance(value, str) and (".." in value or value.startswith("/")):
                raise ValueError(f"Manifest key '{key}' contains dangerous path: {value}")

        # Validate core structure
        required_keys = ["id", "name", "version", "source"]
        for key in required_keys:
            if key not in plugin:
                raise ValueError(f"Manifest missing plugin.{key}")

        if not plugin.get("console", {}).get("settings_panel"):
            logger.warning("Manifest has no settings_panel (optional)")

    def _deep_items(self, d: Dict, parent_key: str = "") -> list:
        """Recursively extract all key-value pairs from nested dict."""
        items = []
        for k, v in d.items():
            key = f"{parent_key}.{k}" if parent_key else k
            if isinstance(v, dict):
                items.extend(self._deep_items(v, key))
            else:
                items.append((key, v))
        return items

    async def _register_panel(self, manifest: Dict[str, Any]):
        """Register Settings Panel in Console (Phase 3 Integration).

        Extracts console.settings_panel from manifest and registers it
        with PluginPanelRegistry, making it appear in Console sidebar.
        """
        from core.plugins.plugin_panel_registry import get_panel_registry

        panel_spec = manifest.get("console", {}).get("settings_panel")

        if not panel_spec:
            logger.warning(f"No settings panel to register for {self.plugin_id}")
            return

        try:
            registry = get_panel_registry()
            panel_id = registry.register_panel(
                plugin_id=self.plugin_id,
                panel_spec=panel_spec
            )
            logger.info(f"✓ Panel auto-registered: {panel_id}")
        except Exception as e:
            logger.error(f"Panel registration failed: {e}")
            # Don't fail the entire install if panel registration fails
            # Console remains usable, plugin is installed but panel won't show
            pass

    async def _rollback(self):
        """Rollback on failure: remove plugin, registry, and panels."""
        logger.warning(f"Rolling back plugin installation: {self.plugin_id}")

        # 1. Remove plugin directory
        plugin_dir = Path.home() / ".corvin" / "plugins" / self.plugin_id
        try:
            if plugin_dir.exists():
                shutil.rmtree(plugin_dir)
                logger.info(f"Removed plugin directory: {plugin_dir}")
        except Exception as e:
            logger.error(f"Rollback error (directory): {e}")

        # 2. Remove from registry
        try:
            from core.plugins.plugin_registry import PluginRegistry
            registry = PluginRegistry()
            registry.remove(self.plugin_id)
        except Exception as e:
            logger.error(f"Registry rollback error: {e}")

        # 3. Unregister all panels (Phase 3)
        try:
            from core.plugins.plugin_panel_registry import get_panel_registry
            panel_registry = get_panel_registry()
            panel_registry.unregister_plugin_panels(self.plugin_id)
        except Exception as e:
            logger.error(f"Panel registry rollback error: {e}")

    async def _emit_event(self, event_type: str, data: Dict[str, Any]):
        """
        Finding #3, #10 Fix: Emit event to async queue (non-blocking, critical-safe).

        Prevents audit logging from blocking installation.
        Critical events MUST NOT be dropped (Finding #10 fix).
        """
        try:
            # Non-blocking put with timeout
            timeout = 0.1 if event_type not in self.critical_events else 5.0
            await asyncio.wait_for(
                self.event_queue.put({"type": event_type, "data": data}),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            if event_type in self.critical_events:
                # Critical events MUST be logged (GDPR Art. 30 compliance)
                logger.error(f"CRITICAL: Event queue timeout on {event_type} — blocking installation")
                raise RuntimeError(f"Audit trail failure: cannot queue critical event {event_type}")
            else:
                # Non-critical events can be dropped with warning
                logger.warning(f"Event queue full, dropping non-critical event: {event_type}")


class PluginInstallationQueue:
    """Manager for async event queue (Finding #3 Fix)."""

    def __init__(self):
        self.queue: Queue = Queue(maxsize=10000)
        self._consumer_task = None

    async def start(self):
        """Start consuming events."""
        self._consumer_task = asyncio.create_task(self._consume_events())

    async def _consume_events(self):
        """Consume events from queue and write to audit trail."""
        from core.audit.audit_chain import AuditChain
        audit = AuditChain()

        while True:
            try:
                event = await self.queue.get()

                # Write to audit trail (non-blocking for installation)
                audit.log_event(f"plugin.install.{event['type']}", event["data"])

                self.queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Event consumer error: {e}")

    async def stop(self):
        """Stop consuming events."""
        if self._consumer_task:
            self._consumer_task.cancel()

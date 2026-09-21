"""Manifest Refresh Handler — Auto-update learning loop index from manifest.

This module handles polling `/v1/console/capabilities/manifest` and updating
the learning loop index when the manifest changes.

Responsibilities:
- Poll manifest every 5 minutes
- Detect new loops (add to index)
- Detect removed loops (mark archived)
- Emit audit events for changes

ADR-0907: KG MCP Learning-Loop Index Service (Stream 2.4)
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional, List, Dict
from uuid import uuid4

logger = logging.getLogger(__name__)


class ManifestRefreshService:
    """Service for refreshing learning loop index from manifest."""

    def __init__(
        self,
        learning_loop_service,
        manifest_url: str = "http://localhost:8765/v1/console/capabilities/manifest",
        poll_interval_seconds: int = 300,  # 5 minutes
    ):
        """Initialize refresh service.

        Args:
            learning_loop_service: LearningLoopService instance
            manifest_url: URL to fetch manifest from
            poll_interval_seconds: Polling interval (default 5 min)
        """
        self.service = learning_loop_service
        self.manifest_url = manifest_url
        self.poll_interval_seconds = poll_interval_seconds
        self._last_manifest: Optional[dict] = None
        self._running = False

    async def refresh_manifest(self) -> dict:
        """Fetch and process manifest from endpoint.

        Returns:
            Dict with refresh result:
            {
                "added": [...],
                "removed": [...],
                "modified": [...],
                "error": Optional[str],
            }
        """
        try:
            # Fetch manifest
            manifest = await self._fetch_manifest()
            if manifest is None:
                return {"error": "Failed to fetch manifest"}

            # Detect changes
            changes = self._diff_manifest(self._last_manifest or {}, manifest)

            # Apply changes
            await self._apply_changes(changes)

            # Update cached manifest
            self._last_manifest = manifest

            # Audit the refresh
            self._audit_manifest_refresh(changes)

            return {
                "added": changes["added"],
                "removed": changes["removed"],
                "modified": changes["modified"],
            }
        except Exception as exc:
            logger.error(f"Manifest refresh failed: {exc}", exc_info=True)
            return {"error": str(exc)}

    async def _fetch_manifest(self) -> Optional[dict]:
        """Fetch manifest from endpoint.

        Returns:
            Manifest dict or None on error
        """
        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.manifest_url,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    logger.error(f"Manifest fetch failed: {resp.status}")
                    return None
        except ImportError:
            logger.warning("aiohttp not available; skipping manifest fetch")
            return None
        except Exception as exc:
            logger.error(f"Manifest fetch error: {exc}")
            return None

    def _diff_manifest(self, old: dict, new: dict) -> dict:
        """Diff old and new manifests.

        Args:
            old: Old manifest dict
            new: New manifest dict

        Returns:
            Dict with added, removed, modified lists
        """
        old_loops = {
            f"{l['plugin_id']}:{l['loop_id']}": l
            for l in old.get("learning_loops", [])
        }
        new_loops = {
            f"{l['plugin_id']}:{l['loop_id']}": l
            for l in new.get("learning_loops", [])
        }

        added = [
            new_loops[k]
            for k in new_loops
            if k not in old_loops
        ]
        removed = [
            old_loops[k]
            for k in old_loops
            if k not in new_loops
        ]
        modified = [
            new_loops[k]
            for k in new_loops
            if k in old_loops and new_loops[k] != old_loops[k]
        ]

        return {
            "added": added,
            "removed": removed,
            "modified": modified,
        }

    async def _apply_changes(self, changes: dict) -> None:
        """Apply manifest changes to index.

        Args:
            changes: Dict with added, removed, modified lists
        """
        # Add new loops
        for loop_def in changes["added"]:
            try:
                self.service.insert_from_manifest(
                    plugin_id=loop_def["plugin_id"],
                    loop_id=loop_def["loop_id"],
                    description=loop_def.get("description", ""),
                    event_source=loop_def.get("event_source", ""),
                    feedback_types=loop_def.get("feedback_types", []),
                    aggregation=loop_def.get("aggregation", "rolling_mean_7d"),
                    health_threshold=loop_def.get("health_threshold"),
                    dormancy_alert_hours=loop_def.get("dormancy_alert_hours", 24),
                    owner_skill=loop_def.get("owner_skill"),
                )
                logger.info(
                    f"Added loop: {loop_def['plugin_id']}:{loop_def['loop_id']}"
                )
            except Exception as exc:
                logger.error(f"Failed to add loop: {exc}")

        # Archive removed loops
        for loop_def in changes["removed"]:
            try:
                self.service.archive_loop(
                    loop_def["plugin_id"],
                    loop_def["loop_id"],
                )
                logger.info(
                    f"Archived loop: {loop_def['plugin_id']}:{loop_def['loop_id']}"
                )
            except Exception as exc:
                logger.error(f"Failed to archive loop: {exc}")

        # Update modified loops
        for loop_def in changes["modified"]:
            try:
                # Re-insert with new definition
                self.service.insert_from_manifest(
                    plugin_id=loop_def["plugin_id"],
                    loop_id=loop_def["loop_id"],
                    description=loop_def.get("description", ""),
                    event_source=loop_def.get("event_source", ""),
                    feedback_types=loop_def.get("feedback_types", []),
                    aggregation=loop_def.get("aggregation", "rolling_mean_7d"),
                    health_threshold=loop_def.get("health_threshold"),
                    dormancy_alert_hours=loop_def.get("dormancy_alert_hours", 24),
                    owner_skill=loop_def.get("owner_skill"),
                )
                logger.info(
                    f"Updated loop: {loop_def['plugin_id']}:{loop_def['loop_id']}"
                )
            except Exception as exc:
                logger.error(f"Failed to update loop: {exc}")

    def _audit_manifest_refresh(self, changes: dict) -> None:
        """Emit audit event for manifest refresh.

        Args:
            changes: Dict with changes
        """
        try:
            from core.learning.event_persistence import core_audit_event

            core_audit_event(
                "learning.manifest_refreshed",
                tenant_id=self.service.tenant_id,
                details={
                    "added_count": len(changes["added"]),
                    "removed_count": len(changes["removed"]),
                    "modified_count": len(changes["modified"]),
                    "refresh_id": str(uuid4()),
                },
            )
        except Exception as exc:
            logger.error(f"Failed to audit manifest refresh: {exc}")

    async def start_polling(self) -> None:
        """Start polling manifest (background task)."""
        self._running = True
        while self._running:
            try:
                await self.refresh_manifest()
                await asyncio.sleep(self.poll_interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Polling error: {exc}")
                await asyncio.sleep(self.poll_interval_seconds)

    def stop_polling(self) -> None:
        """Stop polling manifest."""
        self._running = False

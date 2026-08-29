"""Workflow completion queue with webhook notification (ADR-0423 Phase 2, Gap 2).

Persistent, tenant-scoped queue for tracking workflow run completions.
Used to track background task outcomes across bridge lifecycle (bridge may die,
but completions are durably recorded and can notify Discord webhooks on restart).

Stores:
- CORVIN_HOME/tenants/<tenant_id>/workflow_runs/completions.jsonl

Provides:
- push(run_id, status, output) — add to queue
- pop(run_id) — retrieve and remove
- background_tracker_cron() — async notifier for Discord/Slack webhooks
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from .path_resolver import completion_queue_path, resolve_tenant_id, PathResolutionError

logger = logging.getLogger(__name__)


@dataclass
class CompletionRecord:
    """Single workflow completion event."""

    run_id: str
    status: str  # "complete", "failed", "timeout", "cancelled"
    output: Dict[str, Any]  # workflow output or error details
    timestamp_s: float = field(default_factory=time.time)
    tenant_id: str = "_default"
    webhook_notified: bool = False

    def to_dict(self) -> dict:
        """Serialize to dict for JSONL storage."""
        return {
            "run_id": self.run_id,
            "status": self.status,
            "output": self.output,
            "timestamp_s": self.timestamp_s,
            "tenant_id": self.tenant_id,
            "webhook_notified": self.webhook_notified,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CompletionRecord:
        """Deserialize from dict (from JSONL)."""
        return cls(
            run_id=data["run_id"],
            status=data["status"],
            output=data["output"],
            timestamp_s=data.get("timestamp_s", time.time()),
            tenant_id=data.get("tenant_id", "_default"),
            webhook_notified=data.get("webhook_notified", False),
        )


class WorkflowCompletionQueue:
    """Persistent completion queue for workflow outcomes.

    Persists completions to JSONL so they survive bridge restart.
    Tracks webhook notification state to avoid duplicate notifications.
    Supports multi-tenant isolation via tenant_id.
    """

    def __init__(self):
        """Initialize queue."""
        self._in_memory: Dict[str, CompletionRecord] = {}
        self._lock = asyncio.Lock()

    async def push(
        self,
        run_id: str,
        status: str,
        output: Dict[str, Any],
        tenant_id: Optional[str] = None,
    ) -> None:
        """Add completion to queue (in-memory + persistent).

        Args:
            run_id: Unique run identifier
            status: Completion status ("complete", "failed", "timeout", "cancelled")
            output: Workflow output or error details
            tenant_id: Tenant ID (or resolved from env/default)
        """
        tenant = resolve_tenant_id(tenant_id)

        async with self._lock:
            # Add to in-memory queue
            record = CompletionRecord(
                run_id=run_id,
                status=status,
                output=output,
                tenant_id=tenant,
            )
            self._in_memory[run_id] = record

            # Persist to JSONL
            try:
                queue_path = completion_queue_path(tenant)
                line = json.dumps(record.to_dict(), ensure_ascii=False)
                with open(queue_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
                logger.info(
                    f"Pushed completion: run_id={run_id}, status={status}, "
                    f"tenant={tenant}"
                )
            except OSError as e:
                logger.error(
                    f"Error persisting completion to queue: {e}", exc_info=True
                )

    async def pop(
        self, run_id: str, tenant_id: Optional[str] = None
    ) -> Optional[CompletionRecord]:
        """Retrieve and remove a completion from queue.

        Args:
            run_id: Unique run identifier
            tenant_id: Tenant ID (or resolved from env/default)

        Returns:
            CompletionRecord if found, None otherwise
        """
        async with self._lock:
            if run_id not in self._in_memory:
                return None
            return self._in_memory.pop(run_id)

    async def get_all(self, tenant_id: Optional[str] = None) -> list[CompletionRecord]:
        """Get all completions for a tenant (for monitoring).

        Args:
            tenant_id: Tenant ID (or resolved from env/default)

        Returns:
            List of CompletionRecords
        """
        tenant = resolve_tenant_id(tenant_id)
        async with self._lock:
            return [
                r for r in self._in_memory.values()
                if r.tenant_id == tenant
            ]

    async def load_from_disk(self, tenant_id: Optional[str] = None) -> None:
        """Load persisted completions from JSONL file.

        Called on startup to recover completions from last session.

        Args:
            tenant_id: Tenant ID (or resolved from env/default)
        """
        tenant = resolve_tenant_id(tenant_id)

        try:
            queue_path = completion_queue_path(tenant)
            if not queue_path.exists():
                logger.debug(f"No completion queue file found: {queue_path}")
                return

            async with self._lock:
                with open(queue_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            record = CompletionRecord.from_dict(data)
                            self._in_memory[record.run_id] = record
                        except (json.JSONDecodeError, KeyError) as e:
                            logger.warning(f"Skipped malformed completion line: {e}")

            count = len(self._in_memory)
            logger.info(
                f"Loaded {count} completion(s) from disk for tenant={tenant}"
            )

        except OSError as e:
            logger.error(
                f"Error loading completion queue from disk: {e}", exc_info=True
            )

    async def mark_webhook_notified(
        self, run_id: str, tenant_id: Optional[str] = None
    ) -> None:
        """Mark a completion as webhook-notified (prevents duplicate notifications).

        Args:
            run_id: Unique run identifier
            tenant_id: Tenant ID (or resolved from env/default)
        """
        async with self._lock:
            if run_id in self._in_memory:
                self._in_memory[run_id].webhook_notified = True

    async def get_unnotified(self, tenant_id: Optional[str] = None) -> list[CompletionRecord]:
        """Get all completions that haven't been webhook-notified yet.

        Args:
            tenant_id: Tenant ID (or resolved from env/default)

        Returns:
            List of unnotified CompletionRecords
        """
        tenant = resolve_tenant_id(tenant_id)
        async with self._lock:
            return [
                r for r in self._in_memory.values()
                if r.tenant_id == tenant and not r.webhook_notified
            ]

    async def background_tracker_cron(
        self,
        webhook_url: Optional[str] = None,
        interval_s: float = 30.0,
    ) -> None:
        """Background coroutine that notifies Discord webhooks of completions.

        Runs indefinitely; intended to be scheduled as asyncio.create_task().
        Checks for unnotified completions every interval_s seconds and POSTs them.

        Args:
            webhook_url: Discord webhook URL (or read from env if None)
            interval_s: Check interval (seconds). Default: 30s.
        """
        while True:
            try:
                await asyncio.sleep(interval_s)

                # Determine webhook URL
                url = webhook_url or __import__("os").environ.get(
                    "DISCORD_WORKFLOW_WEBHOOK_URL"
                )
                if not url:
                    continue

                # Notify all unnotified completions
                unnotified = await self.get_unnotified()
                for record in unnotified:
                    try:
                        await self._post_webhook(url, record)
                        await self.mark_webhook_notified(record.run_id)
                        logger.info(
                            f"Notified webhook for completion: run_id={record.run_id}"
                        )
                    except Exception as e:
                        logger.error(
                            f"Error posting webhook notification: {e}", exc_info=True
                        )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    f"Error in background_tracker_cron: {e}", exc_info=True
                )

    async def _post_webhook(self, webhook_url: str, record: CompletionRecord) -> None:
        """POST a completion notification to a Discord webhook.

        Args:
            webhook_url: Discord webhook URL
            record: CompletionRecord to notify

        Raises:
            Exception: On HTTP error (caller should retry)
        """
        import aiohttp

        payload = {
            "content": (
                f"Workflow {record.run_id} completed with status: {record.status}"
            ),
            "embeds": [
                {
                    "title": f"Workflow: {record.run_id}",
                    "description": f"Status: {record.status}",
                    "fields": [
                        {
                            "name": "Timestamp",
                            "value": str(record.timestamp_s),
                            "inline": True,
                        },
                        {
                            "name": "Tenant",
                            "value": record.tenant_id,
                            "inline": True,
                        },
                    ],
                    "color": 0x00FF00 if record.status == "complete" else 0xFF0000,
                }
            ],
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=payload, timeout=10) as resp:
                resp.raise_for_status()

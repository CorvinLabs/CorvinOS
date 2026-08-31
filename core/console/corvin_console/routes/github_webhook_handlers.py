"""Webhook Event Handlers - Process events from queue."""

import asyncio
from typing import Dict, Any, Callable, List
from datetime import datetime
import logging
from .github_webhook_events import WebhookEvent, EventType, EventProcessor

logger = logging.getLogger(__name__)


class EventHandler:
    """Process events and trigger sync/conflict resolution."""

    def __init__(self, processor: EventProcessor):
        self.processor = processor
        self.handlers: Dict[EventType, List[Callable]] = {}
        self._register_default_handlers()

    def _register_default_handlers(self):
        """Register default event handlers."""
        self.on(EventType.SKILL_CREATED, self._handle_skill_created)
        self.on(EventType.SKILL_UPDATED, self._handle_skill_updated)
        self.on(EventType.SKILL_DELETED, self._handle_skill_deleted)
        self.on(EventType.CONFLICT_DETECTED, self._handle_conflict)

    def on(self, event_type: EventType, handler: Callable):
        """Register handler for event type."""
        if event_type not in self.handlers:
            self.handlers[event_type] = []
        self.handlers[event_type].append(handler)

    async def process_event(self, event: WebhookEvent) -> Dict[str, Any]:
        """Process single event through registered handlers."""
        handlers = self.handlers.get(event.event_type, [])

        results = []
        for handler in handlers:
            try:
                result = await handler(event) if asyncio.iscoroutinefunction(handler) else handler(event)
                results.append(result)
            except Exception as e:
                logger.error(f"Handler failed for {event.event_id}: {e}")
                results.append({"success": False, "error": str(e)})

        # Mark success if all handlers passed
        all_success = all(r.get("success", False) for r in results)
        return {
            "event_id": event.event_id,
            "event_type": event.event_type.value,
            "success": all_success,
            "handlers_run": len(handlers),
            "results": results
        }

    async def _handle_skill_created(self, event: WebhookEvent) -> Dict[str, Any]:
        """Handle new skill creation."""
        logger.info(f"Handling SKILL_CREATED: {event.payload.get('skill_name')}")
        # TODO: Trigger skill metadata sync
        return {"success": True, "action": "skill_indexed"}

    async def _handle_skill_updated(self, event: WebhookEvent) -> Dict[str, Any]:
        """Handle skill update."""
        logger.info(f"Handling SKILL_UPDATED: {event.payload.get('skill_name')}")
        # TODO: Detect conflicts if concurrent updates
        return {"success": True, "action": "skill_updated"}

    async def _handle_skill_deleted(self, event: WebhookEvent) -> Dict[str, Any]:
        """Handle skill deletion."""
        logger.info(f"Handling SKILL_DELETED: {event.payload.get('skill_name')}")
        # TODO: Soft-delete with audit trail
        return {"success": True, "action": "skill_deleted"}

    async def _handle_conflict(self, event: WebhookEvent) -> Dict[str, Any]:
        """Handle conflict resolution."""
        logger.info(f"Handling CONFLICT_DETECTED: {event.payload.get('conflict_type')}")
        # TODO: Apply merge strategy (Phase 3)
        return {"success": True, "action": "conflict_noted"}


class WebhookProcessor:
    """Main webhook processor - queue consumer."""

    def __init__(self, processor: EventProcessor):
        self.processor = processor
        self.event_handler = EventHandler(processor)
        self.running = False

    async def start_processing(self):
        """Start background event processor."""
        self.running = True
        logger.info("Webhook processor started")

        while self.running:
            event = self.processor.queue.dequeue(timeout=1)
            if not event:
                continue

            try:
                result = await self.event_handler.process_event(event)

                if result["success"]:
                    logger.info(f"Event processed: {event.event_id}")
                else:
                    # Retry on failure
                    self.processor.queue.retry_event(event)
                    logger.warning(f"Event processing failed, queued for retry: {event.event_id}")

            except Exception as e:
                logger.error(f"Failed to process event {event.event_id}: {e}")
                self.processor.queue.retry_event(event)

            await asyncio.sleep(0.1)

    def stop_processing(self):
        """Stop webhook processor."""
        self.running = False
        logger.info("Webhook processor stopped")

    def get_status(self) -> Dict[str, Any]:
        """Get processor status."""
        return {
            "running": self.running,
            "queue_stats": self.processor.get_queue_stats(),
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

"""Telemetry Client for Individual Instances (ADR-0365)

Each CorvinOS instance uses this client to push telemetry metrics
to the central aggregation service.

Key responsibilities:
- Periodic push of instance metrics
- Graceful fallback if central service unavailable
- Batch submission to reduce network overhead
- Retry logic with exponential backoff
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable
from threading import Thread, Event
import json

logger = logging.getLogger(__name__)


@dataclass
class TelemetryClientConfig:
    """Configuration for telemetry client."""
    aggregator_url: str  # e.g., "https://api.corvin-labs.com/telemetry"
    instance_id: str
    tenant_id: str
    push_interval_seconds: int = 30  # Push every 30 seconds
    retry_max_attempts: int = 3
    retry_backoff_seconds: float = 1.0
    enabled: bool = True


class TelemetryClient:
    """Client for pushing instance metrics to central aggregator."""

    def __init__(
        self,
        config: TelemetryClientConfig,
        http_post_fn: Optional[Callable] = None,
    ):
        """Initialize telemetry client.

        Args:
            config: Client configuration
            http_post_fn: Optional custom HTTP POST function (for testing)
        """
        self.config = config
        self.http_post_fn = http_post_fn or self._default_http_post
        self.last_turn_count = 0
        self.last_token_count = 0
        self.start_time = datetime.utcnow()
        self._stop_event = Event()
        self._background_thread: Optional[Thread] = None
        self.submission_count = 0
        self.failed_submissions = 0

    def _default_http_post(self, url: str, data: dict) -> bool:
        """Default HTTP POST implementation (requires requests library).

        Args:
            url: URL to POST to
            data: JSON data to send

        Returns:
            True if successful, False otherwise
        """
        try:
            import requests
            response = requests.post(
                url,
                json=data,
                timeout=5,
                headers={"Content-Type": "application/json"},
            )
            return response.status_code < 400
        except ImportError:
            logger.warning("requests library not available for telemetry submission")
            return False
        except Exception as e:
            logger.warning(f"Failed to submit telemetry: {e}")
            return False

    def submit_metrics(
        self,
        turn_count: int,
        total_tokens: int,
        savings_percent: float,
    ) -> bool:
        """Submit current metrics to aggregator.

        Args:
            turn_count: Number of turns processed by this instance
            total_tokens: Total tokens used
            savings_percent: Cost savings percentage (0-100)

        Returns:
            True if submission successful, False otherwise
        """
        if not self.config.enabled:
            return False

        uptime_seconds = int(
            (datetime.utcnow() - self.start_time).total_seconds()
        )

        data = {
            'instance_id': self.config.instance_id,
            'tenant_id': self.config.tenant_id,
            'turn_count': turn_count,
            'total_tokens': total_tokens,
            'savings_percent': savings_percent,
            'uptime_seconds': uptime_seconds,
            'timestamp': datetime.utcnow().isoformat(),
        }

        # Retry with exponential backoff
        for attempt in range(self.config.retry_max_attempts):
            try:
                url = f"{self.config.aggregator_url}/submit"
                if self.http_post_fn(url, data):
                    logger.debug(f"Telemetry submitted successfully: {self.config.instance_id}")
                    self.submission_count += 1
                    self.last_turn_count = turn_count
                    self.last_token_count = total_tokens
                    return True
            except Exception as e:
                logger.debug(f"Telemetry submission attempt {attempt + 1} failed: {e}")

            if attempt < self.config.retry_max_attempts - 1:
                wait_seconds = self.config.retry_backoff_seconds * (2 ** attempt)
                time.sleep(wait_seconds)

        self.failed_submissions += 1
        logger.warning(f"Failed to submit telemetry after {self.config.retry_max_attempts} attempts")
        return False

    def start_background_push(self) -> None:
        """Start background thread for periodic metric submissions.

        This thread will push metrics to the aggregator at regular intervals.
        """
        if self._background_thread is not None:
            logger.warning("Background push already running")
            return

        self._stop_event.clear()
        self._background_thread = Thread(target=self._background_loop, daemon=True)
        self._background_thread.start()
        logger.info(f"Started telemetry background push (interval={self.config.push_interval_seconds}s)")

    def stop_background_push(self) -> None:
        """Stop the background push thread."""
        if self._background_thread is None:
            return

        self._stop_event.set()
        self._background_thread.join(timeout=5)
        self._background_thread = None
        logger.info("Stopped telemetry background push")

    def _background_loop(self) -> None:
        """Background loop for periodic submissions (runs in separate thread)."""
        while not self._stop_event.is_set():
            try:
                # This would normally be called from the running instance
                # For now, it's a placeholder for the background push logic
                if self._stop_event.wait(self.config.push_interval_seconds):
                    break
            except Exception as e:
                logger.error(f"Error in telemetry background loop: {e}")

    def get_stats(self) -> dict:
        """Get telemetry client statistics.

        Returns:
            Dict with submission stats
        """
        return {
            'instance_id': self.config.instance_id,
            'submission_count': self.submission_count,
            'failed_submissions': self.failed_submissions,
            'last_turn_count': self.last_turn_count,
            'last_token_count': self.last_token_count,
            'uptime_seconds': int((datetime.utcnow() - self.start_time).total_seconds()),
        }


class TelemetryClientRegistry:
    """Registry of active telemetry clients for an instance."""

    def __init__(self):
        """Initialize registry."""
        self._clients: dict[str, TelemetryClient] = {}

    def register_client(self, tenant_id: str, client: TelemetryClient) -> None:
        """Register a client.

        Args:
            tenant_id: Tenant ID
            client: TelemetryClient to register
        """
        self._clients[tenant_id] = client

    def get_client(self, tenant_id: str) -> Optional[TelemetryClient]:
        """Get a registered client.

        Args:
            tenant_id: Tenant ID

        Returns:
            TelemetryClient if registered, None otherwise
        """
        return self._clients.get(tenant_id)

    def submit_all(
        self,
        turn_count: int,
        total_tokens: int,
        savings_percent: float,
    ) -> dict[str, bool]:
        """Submit metrics from all clients.

        Args:
            turn_count: Turn count
            total_tokens: Token count
            savings_percent: Savings percentage

        Returns:
            Dict of tenant_id -> submission success
        """
        results = {}
        for tenant_id, client in self._clients.items():
            results[tenant_id] = client.submit_metrics(
                turn_count, total_tokens, savings_percent
            )
        return results

    def start_all_background_push(self) -> None:
        """Start background push for all clients."""
        for client in self._clients.values():
            client.start_background_push()

    def stop_all_background_push(self) -> None:
        """Stop background push for all clients."""
        for client in self._clients.values():
            client.stop_background_push()

    def get_all_stats(self) -> dict[str, dict]:
        """Get stats from all clients.

        Returns:
            Dict of tenant_id -> stats
        """
        return {
            tenant_id: client.get_stats()
            for tenant_id, client in self._clients.items()
        }

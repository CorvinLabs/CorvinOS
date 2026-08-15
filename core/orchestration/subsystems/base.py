"""Subsystem base class and interface contract for CorvinOS Brain.

ADR-0349: Plugin Interface Contract
"""

from abc import ABC, abstractmethod
from typing import Any, Dict


class Subsystem(ABC):
    """Abstract base class for all Brain subsystems.

    Every subsystem implements:
    - Identity (name, version)
    - Lifecycle (startup, shutdown)
    - Event handling (on_event)
    - Request routing (handle_request)
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this subsystem."""
        pass

    @property
    @abstractmethod
    def version(self) -> str:
        """Semantic version (e.g., '1.0.0')."""
        pass

    @abstractmethod
    def startup(self, hub: "SubsystemHub") -> None:  # noqa: F821
        """Initialize subsystem and subscribe to events."""
        pass

    @abstractmethod
    async def on_event(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """React to published events (fire-and-forget)."""
        pass

    @abstractmethod
    async def handle_request(self, request_type: str, **kwargs) -> Any:
        """Handle synchronous queries from other subsystems."""
        pass

    @abstractmethod
    def shutdown(self) -> None:
        """Cleanup resources."""
        pass

    def publish_event(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Publish event through hub."""
        if not hasattr(self, "hub"):
            raise RuntimeError(f"{self.name}: hub not set (call startup first)")
        self.hub.publish_event(event_name, event_data)

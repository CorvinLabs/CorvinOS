"""Data connector backend provider - external data integration.

Singleton registry for connecting to external data sources (databases, APIs, etc.).
"""

import logging
from dataclasses import dataclass
from typing import Optional, Protocol, Any
import threading

_logger = logging.getLogger(__name__)

_lock = threading.Lock()
_active_backend: Optional['DataConnectorBackend'] = None


@dataclass(frozen=True)
class DataSourceConfig:
    """Configuration for a data source connection."""
    source_id: str
    source_type: str  # "sql", "api", "file", etc.
    connection_string: str
    tenant_id: str


class DataConnectorBackend(Protocol):
    """Protocol for data connector backends."""

    async def connect(self, config: DataSourceConfig) -> bool:
        """Connect to a data source."""
        ...

    async def execute_query(self, source_id: str, query: str) -> list[dict]:
        """Execute a query against a data source."""
        ...

    async def disconnect(self, source_id: str) -> bool:
        """Disconnect from a data source."""
        ...

    async def health_check(self) -> bool:
        """Check backend health."""
        ...


class DefaultDataConnectorBackend:
    """Default in-process data connector backend."""

    def __init__(self):
        """Initialize the data connector backend."""
        self._connections: dict[str, Any] = {}
        self._lock = threading.Lock()

    async def connect(self, config: DataSourceConfig) -> bool:
        """Connect to a data source."""
        try:
            with self._lock:
                self._connections[config.source_id] = {
                    "config": config,
                    "connected": True
                }
                _logger.info(f"Connected to {config.source_id}")
            return True
        except Exception as e:
            _logger.error(f"Connection failed: {e}")
            return False

    async def execute_query(self, source_id: str, query: str) -> list[dict]:
        """Execute a query against a data source."""
        try:
            with self._lock:
                if source_id not in self._connections:
                    _logger.error(f"Source not connected: {source_id}")
                    return []

                # Default implementation: return empty results
                # Real implementation would execute the query
                return []
        except Exception as e:
            _logger.error(f"Query execution failed: {e}")
            return []

    async def disconnect(self, source_id: str) -> bool:
        """Disconnect from a data source."""
        try:
            with self._lock:
                if source_id in self._connections:
                    del self._connections[source_id]
                    _logger.info(f"Disconnected from {source_id}")
                    return True
                return False
        except Exception as e:
            _logger.error(f"Disconnection failed: {e}")
            return False

    async def health_check(self) -> bool:
        """Check backend health."""
        return True


def get_active() -> DataConnectorBackend:
    """Get the currently active data connector backend."""
    global _active_backend
    with _lock:
        if _active_backend is None:
            _active_backend = DefaultDataConnectorBackend()
        return _active_backend


def set_active(backend: DataConnectorBackend) -> None:
    """Set the active data connector backend (for testing)."""
    global _active_backend
    with _lock:
        _active_backend = backend

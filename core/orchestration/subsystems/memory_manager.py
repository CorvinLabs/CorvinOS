"""Memory Manager subsystem: Manage tenant-scoped persistent memory (Phase C).

Stores and retrieves conversation memory, user modeling, session artifacts,
and other persistent memory artifacts to/from tenant-specific directories.
All memory data is isolated per tenant via ExecutionContext.tenant_id.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .base import Subsystem
from core.paths.tenant import tenant_memory_dir

logger = logging.getLogger(__name__)


class MemoryManager(Subsystem):
    """Manage persistent memory per tenant.

    Phase C: All memory stored in tenant-scoped directory.
    Supports multiple memory types: conversation, user_model, artifacts, etc.
    """

    def __init__(
        self,
        context: Optional[Any] = None,
    ):
        """Initialize MemoryManager.

        Args:
            context: ExecutionContext with tenant_id for tenant-scoped operations
        """
        # Phase C: Store ExecutionContext for tenant-native operations
        self.context = context
        self.tenant_id = context.tenant_id if context else "_default"

        # In-memory cache for frequently accessed memory
        self.memory_cache: Dict[str, Dict[str, str]] = {}
        self.hub: Optional[Any] = None

    @property
    def name(self) -> str:
        return "memory_manager"

    @property
    def version(self) -> str:
        return "1.0.0"

    def startup(self, hub: Any) -> None:
        """Initialize subsystem and subscribe to events.

        Args:
            hub: SubsystemHub instance
        """
        self.hub = hub
        hub.subscribe("memory_written", self.on_memory_written)
        hub.subscribe("memory_cleared", self.on_memory_cleared)
        logger.info(f"MemoryManager started (tenant={self.tenant_id})")

    async def on_event(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """React to published events (fire-and-forget).

        Args:
            event_name: Name of event
            event_data: Event payload
        """
        if event_name == "memory_written":
            await self.on_memory_written(event_name, event_data)
        elif event_name == "memory_cleared":
            await self.on_memory_cleared(event_name, event_data)

    async def handle_request(self, request_type: str, **kwargs) -> Any:
        """Handle synchronous requests from other subsystems.

        Args:
            request_type: Type of request
            **kwargs: Request parameters

        Returns:
            Request result
        """
        match request_type:
            case "write_memory":
                return self.write_memory(
                    memory_type=kwargs.get("memory_type"),
                    key=kwargs.get("key"),
                    value=kwargs.get("value"),
                )
            case "read_memory":
                return self.read_memory(
                    memory_type=kwargs.get("memory_type"),
                    key=kwargs.get("key"),
                )
            case "list_memory":
                return self.list_memory(memory_type=kwargs.get("memory_type"))
            case "delete_memory":
                return self.delete_memory(
                    memory_type=kwargs.get("memory_type"),
                    key=kwargs.get("key"),
                )
            case "clear_memory_type":
                return self.clear_memory_type(memory_type=kwargs.get("memory_type"))
            case _:
                raise ValueError(f"Unknown request type: {request_type}")

    async def on_memory_written(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Handle memory written event.

        Args:
            event_name: Event name
            event_data: Event data with memory_type, key, value
        """
        memory_type = event_data.get("memory_type")
        key = event_data.get("key")
        value = event_data.get("value")
        if memory_type and key:
            self.write_memory(memory_type, key, value)

    async def on_memory_cleared(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Handle memory cleared event.

        Args:
            event_name: Event name
            event_data: Event data with memory_type, key
        """
        memory_type = event_data.get("memory_type")
        key = event_data.get("key")
        if memory_type and key:
            self.delete_memory(memory_type, key)

    def write_memory(self, memory_type: str, key: str, value: str) -> bool:
        """Write memory to tenant-scoped directory.

        Args:
            memory_type: Type of memory (e.g., 'conversation', 'user_model', 'artifacts')
            key: Memory key/identifier
            value: Memory value (string or JSON)

        Returns:
            True if successful, False otherwise
        """
        try:
            mem_dir = tenant_memory_dir(self.tenant_id) / memory_type
            mem_dir.mkdir(parents=True, exist_ok=True)

            mem_file = mem_dir / f"{key}.json"
            memory_entry = {
                "key": key,
                "memory_type": memory_type,
                "value": value,
                "written_at": datetime.utcnow().isoformat(),
                "tenant_id": self.tenant_id,
            }
            mem_file.write_text(json.dumps(memory_entry, indent=2))

            # Update cache
            if memory_type not in self.memory_cache:
                self.memory_cache[memory_type] = {}
            self.memory_cache[memory_type][key] = value

            logger.debug(f"Wrote memory {memory_type}/{key} for tenant {self.tenant_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to write memory {memory_type}/{key}: {e}")
            return False

    def read_memory(self, memory_type: str, key: str) -> Optional[str]:
        """Read memory from tenant directory.

        Args:
            memory_type: Type of memory
            key: Memory key

        Returns:
            Memory value (string) or None if not found
        """
        # Check cache first
        if memory_type in self.memory_cache and key in self.memory_cache[memory_type]:
            return self.memory_cache[memory_type][key]

        # Load from disk
        mem_file = tenant_memory_dir(self.tenant_id) / memory_type / f"{key}.json"
        if not mem_file.exists():
            logger.warning(f"Memory {memory_type}/{key} not found in tenant {self.tenant_id}")
            return None

        try:
            entry = json.loads(mem_file.read_text())
            value = entry.get("value")

            # Update cache
            if memory_type not in self.memory_cache:
                self.memory_cache[memory_type] = {}
            self.memory_cache[memory_type][key] = value

            return value
        except Exception as e:
            logger.error(f"Failed to read memory {memory_type}/{key}: {e}")
            return None

    def list_memory(self, memory_type: str) -> list[Dict[str, Any]]:
        """List all memory entries of a given type.

        Args:
            memory_type: Type of memory

        Returns:
            List of memory entries with key, value, written_at
        """
        memories = []
        mem_dir = tenant_memory_dir(self.tenant_id) / memory_type
        if not mem_dir.exists():
            return []

        try:
            for mem_file in mem_dir.glob("*.json"):
                try:
                    entry = json.loads(mem_file.read_text())
                    memories.append({
                        "key": entry.get("key"),
                        "value": entry.get("value"),
                        "written_at": entry.get("written_at"),
                    })
                except Exception as e:
                    logger.warning(f"Failed to read memory file {mem_file}: {e}")
        except Exception as e:
            logger.error(f"Failed to list memory type {memory_type}: {e}")

        return memories

    def delete_memory(self, memory_type: str, key: str) -> bool:
        """Delete memory entry from tenant directory.

        Args:
            memory_type: Type of memory
            key: Memory key

        Returns:
            True if successful, False otherwise
        """
        try:
            mem_file = tenant_memory_dir(self.tenant_id) / memory_type / f"{key}.json"
            if mem_file.exists():
                mem_file.unlink()

            # Update cache
            if memory_type in self.memory_cache:
                self.memory_cache[memory_type].pop(key, None)

            logger.debug(f"Deleted memory {memory_type}/{key} from tenant {self.tenant_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete memory {memory_type}/{key}: {e}")
            return False

    def clear_memory_type(self, memory_type: str) -> bool:
        """Clear all memory entries of a given type.

        Args:
            memory_type: Type of memory

        Returns:
            True if successful, False otherwise
        """
        try:
            mem_dir = tenant_memory_dir(self.tenant_id) / memory_type
            if mem_dir.exists():
                import shutil
                shutil.rmtree(mem_dir)

            # Clear cache
            self.memory_cache.pop(memory_type, None)

            logger.info(f"Cleared all memory of type {memory_type} for tenant {self.tenant_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to clear memory type {memory_type}: {e}")
            return False

    def shutdown(self) -> None:
        """Cleanup resources."""
        logger.info("MemoryManager shutdown")

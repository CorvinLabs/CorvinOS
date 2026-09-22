"""State Capture — Collect Control Plane State for Snapshots (ADR-2029 Stream 4)."""

from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
from datetime import datetime
import json
import logging

logger = logging.getLogger(__name__)


@dataclass
class StateCapture:
    """Captures and serializes Control Plane state."""

    def __init__(self):
        """Initialize state capture."""
        self.timestamp = datetime.utcnow().isoformat() + "Z"
        self.captured_state: Dict[str, Any] = {}

    def capture_plugins(self, plugin_registry: Optional[Dict] = None) -> Dict[str, Any]:
        """Capture current plugin state.

        Args:
            plugin_registry: Optional plugin registry dict

        Returns:
            Plugin state dict with enabled/disabled status and metadata
        """
        if plugin_registry is None:
            plugin_registry = {}

        plugin_state = {
            "timestamp": self.timestamp,
            "total_plugins": len(plugin_registry),
            "plugins": {},
        }

        for plugin_id, plugin_data in plugin_registry.items():
            plugin_state["plugins"][plugin_id] = {
                "id": plugin_id,
                "enabled": plugin_data.get("enabled", True),
                "version": plugin_data.get("version", "unknown"),
                "boot_layer": plugin_data.get("boot_layer", "unknown"),
                "origin": plugin_data.get("origin", "unknown"),
            }

        self.captured_state["plugins"] = plugin_state
        return plugin_state

    def capture_subsystems(self, subsystem_controller: Optional[Dict] = None) -> Dict[str, Any]:
        """Capture current subsystem state.

        Args:
            subsystem_controller: Optional subsystem controller dict

        Returns:
            Subsystem state dict with status
        """
        if subsystem_controller is None:
            subsystem_controller = {}

        subsystem_state = {
            "timestamp": self.timestamp,
            "total_subsystems": len(subsystem_controller),
            "subsystems": {},
        }

        for subsys_id, subsys_data in subsystem_controller.items():
            subsystem_state["subsystems"][subsys_id] = {
                "id": subsys_id,
                "status": subsys_data.get("status", "unknown"),
                "healthy": subsys_data.get("healthy", False),
                "last_check": subsys_data.get("last_check", None),
                "error_count": subsys_data.get("error_count", 0),
            }

        self.captured_state["subsystems"] = subsystem_state
        return subsystem_state

    def capture_config(self, config_dict: Optional[Dict] = None) -> Dict[str, Any]:
        """Capture configuration snapshot.

        Args:
            config_dict: Optional configuration dict

        Returns:
            Configuration snapshot with metadata
        """
        if config_dict is None:
            config_dict = {}

        config_state = {
            "timestamp": self.timestamp,
            "config_keys": list(config_dict.keys()),
            "config_count": len(config_dict),
            "config": {},
        }

        # Include only safe, non-secret config
        for key, value in config_dict.items():
            if not self._is_secret(key):
                config_state["config"][key] = value

        self.captured_state["config"] = config_state
        return config_state

    def capture_audit_summary(self, audit_events: Optional[List] = None) -> Dict[str, Any]:
        """Capture audit summary (metadata only, no event details).

        Args:
            audit_events: Optional list of audit events

        Returns:
            Audit summary dict with counts and patterns
        """
        if audit_events is None:
            audit_events = []

        audit_summary = {
            "timestamp": self.timestamp,
            "total_events": len(audit_events),
            "event_types": {},
            "last_event_time": None,
            "tenant_count": set(),
        }

        for event in audit_events:
            event_type = event.get("event_type", "unknown")
            if event_type not in audit_summary["event_types"]:
                audit_summary["event_types"][event_type] = 0
            audit_summary["event_types"][event_type] += 1

            # Track last event time
            event_time = event.get("timestamp")
            if event_time:
                audit_summary["last_event_time"] = event_time

            # Count unique tenants
            tenant_id = event.get("tenant_id")
            if tenant_id:
                audit_summary["tenant_count"].add(tenant_id)

        # Convert set to count
        audit_summary["tenant_count"] = len(audit_summary["tenant_count"])

        self.captured_state["audit"] = audit_summary
        return audit_summary

    def capture_intent_router_state(self, intent_state: Optional[Dict] = None) -> Dict[str, Any]:
        """Capture Intent Router state (routing decisions, confidence scores).

        Args:
            intent_state: Optional intent router state dict

        Returns:
            Intent router state dict
        """
        if intent_state is None:
            intent_state = {}

        intent_capture = {
            "timestamp": self.timestamp,
            "active_routes": intent_state.get("active_routes", []),
            "routing_model": intent_state.get("routing_model", "unknown"),
            "confidence_threshold": intent_state.get("confidence_threshold", 0.0),
            "total_classifications": intent_state.get("total_classifications", 0),
        }

        self.captured_state["intent"] = intent_capture
        return intent_capture

    def serialize_state(self, include_config: bool = False) -> Dict[str, Any]:
        """Serialize captured state to JSON-safe dict.

        Args:
            include_config: Whether to include configuration (may contain sensitive data)

        Returns:
            JSON-serializable state dict
        """
        serialized = {}

        for key, value in self.captured_state.items():
            # Skip config unless explicitly requested
            if key == "config" and not include_config:
                continue

            try:
                # Ensure all values are JSON-serializable
                serialized[key] = json.loads(json.dumps(value, default=str))
            except (TypeError, ValueError) as e:
                logger.warning(f"Failed to serialize {key}: {e}")
                serialized[key] = {"error": f"Serialization failed: {str(e)}"}

        serialized["capture_timestamp"] = self.timestamp
        return serialized

    def get_capture_summary(self) -> Dict[str, Any]:
        """Get summary of what was captured.

        Returns:
            Summary dict with component counts
        """
        return {
            "timestamp": self.timestamp,
            "components_captured": list(self.captured_state.keys()),
            "component_count": len(self.captured_state),
            "total_keys": sum(
                len(v) if isinstance(v, dict) else 1
                for v in self.captured_state.values()
            ),
        }

    @staticmethod
    def _is_secret(key: str) -> bool:
        """Check if a config key contains sensitive data.

        Args:
            key: Config key name

        Returns:
            True if key appears to contain secrets
        """
        secret_keywords = [
            "secret",
            "password",
            "token",
            "key",
            "credential",
            "api_key",
            "private",
            "oauth",
        ]
        key_lower = key.lower()
        return any(keyword in key_lower for keyword in secret_keywords)

    def clear(self):
        """Clear captured state."""
        self.captured_state = {}
        self.timestamp = datetime.utcnow().isoformat() + "Z"

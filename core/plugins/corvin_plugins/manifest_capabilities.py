"""Plugin Capabilities Manifest Schema (ADR-0610)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

log = logging.getLogger(__name__)


class CapabilityType(str, Enum):
    """Allowed capability types (extensible via RFC)."""
    CONTEXT_SOURCE = "context_source"
    CACHE_PROVIDER = "cache_provider"
    COMPUTE_ENGINE = "compute_engine"
    NOTIFICATION_BACKEND = "notification_backend"
    ROUTER_POLICY = "router_policy"
    USER_BACKEND = "user_backend"
    AUDIT_BACKEND = "audit_backend"
    # New capability types for marketplace plugins (Segment A)
    SESSION_STORE = "session_store"
    EVENT_STORE = "event_store"
    DATA_VALIDATOR = "data_validator"
    DATA_CLASSIFIER = "data_classifier"
    ERROR_HANDLER = "error_handler"
    DATA_ANONYMIZER = "data_anonymizer"
    ARTIFACT_PROCESSOR = "artifact_processor"
    CONTEXT_ANALYZER = "context_analyzer"
    METRICS_AGGREGATOR = "metrics_aggregator"
    WHEEL_INSPECTOR = "wheel_inspector"
    LEARNING_TRACKER = "learning_tracker"
    USER_PROFILER = "user_profiler"
    AUTONOMY_TRACKER = "autonomy_tracker"
    DIAGNOSTICS_RENDERER = "diagnostics_renderer"
    HEALTH_MONITOR = "health_monitor"


class FailureMode(str, Enum):
    """How to handle capability failure."""
    FAIL_CLOSED = "fail_closed"
    FAIL_OPEN = "fail_open"
    FALLBACK = "fallback_to_<id>"


@dataclass(frozen=True)
class Capability:
    """Single plugin capability (immutable)."""

    id: str  # e.g., "context.semantic_retrieval"
    type: CapabilityType
    description: str

    # Contract: JSON Schema for parameters and returns
    parameters: dict[str, Any] = field(default_factory=dict)  # JSON Schema
    returns: dict[str, Any] = field(default_factory=dict)     # JSON Schema

    # SLO declarations
    slo_latency_ms: int = 500
    slo_error_rate: float = 0.01  # 1%

    # Audit and failure
    audit_event: str = ""  # e.g., "plugin.context_retrieved"
    on_failure: FailureMode = FailureMode.FAIL_CLOSED
    fallback_capability_id: Optional[str] = None

    # Versioning
    added_in: str = "1.0"
    deprecated_in: Optional[str] = None
    removed_in: Optional[str] = None

    def __post_init__(self):
        """Validate capability (frozen, so use object.__setattr__ if needed)."""
        if not self.id or not self.id.replace("_", "").replace(".", "").isalnum():
            raise ValueError(f"Invalid capability id: {self.id}")
        if self.slo_latency_ms < 0:
            raise ValueError(f"SLO latency must be >= 0: {self.slo_latency_ms}")
        if not (0.0 <= self.slo_error_rate <= 1.0):
            raise ValueError(f"SLO error rate must be 0.0–1.0: {self.slo_error_rate}")

    def validate_json_schema(self) -> list[str]:
        """Validate parameters and returns are valid JSON Schema."""
        errors = []

        if not isinstance(self.parameters, dict):
            errors.append(f"Capability {self.id}: parameters must be dict (JSON Schema)")
        if not isinstance(self.returns, dict):
            errors.append(f"Capability {self.id}: returns must be dict (JSON Schema)")

        # Basic JSON Schema validation (check for required fields if type is object)
        if self.parameters and self.parameters.get("type") == "object":
            if "properties" in self.parameters and not isinstance(self.parameters["properties"], dict):
                errors.append(f"Capability {self.id}: parameters.properties must be dict")

        return errors


@dataclass
class PluginCapabilitiesManifest:
    """Plugin-level capabilities metadata (extends plugin.corvin.yaml)."""

    plugin_id: str
    plugin_version: str

    # Capabilities versioning (semver)
    capabilities_version: str = "1.0"

    # List of capabilities
    capabilities: list[Capability] = field(default_factory=list)

    # Version compatibility matrix (optional)
    capability_matrix: list[dict[str, Any]] = field(default_factory=list)

    def validate(self, audit_event_registry: Optional[dict[str, bool]] = None) -> list[str]:
        """Validate entire manifest. Returns list of errors (empty = valid)."""
        errors = []

        # Validate capabilities_version is semver-like
        if not self._is_semver(self.capabilities_version):
            errors.append(f"Invalid capabilities_version: {self.capabilities_version} (must be semver)")

        # Validate each capability
        seen_ids = set()
        for cap in self.capabilities:
            # Check for duplicates
            if cap.id in seen_ids:
                errors.append(f"Duplicate capability id: {cap.id}")
            seen_ids.add(cap.id)

            # Validate JSON schema
            errors.extend(cap.validate_json_schema())

            # Check audit event exists (if registry provided)
            if audit_event_registry is not None and cap.audit_event:
                if cap.audit_event not in audit_event_registry:
                    errors.append(f"Capability {cap.id}: audit_event '{cap.audit_event}' not registered")

        # Check for cycles in fallback_capability_id
        errors.extend(self._check_for_cycles())

        return errors

    def _is_semver(self, version: str) -> bool:
        """Check if version is semver-like (X.Y.Z or X.Y)."""
        parts = version.split(".")
        if len(parts) < 2 or len(parts) > 3:
            return False
        try:
            for part in parts:
                int(part)
            return True
        except ValueError:
            return False

    def _check_for_cycles(self) -> list[str]:
        """Find fallback_capability_id cycles (A → B → A)."""
        errors = []
        cap_map = {cap.id: cap.fallback_capability_id for cap in self.capabilities}

        for cap_id, fallback_id in cap_map.items():
            if not fallback_id:
                continue
            visited = set()
            current = fallback_id
            while current and current not in visited:
                visited.add(current)
                current = cap_map.get(current)

            if current in visited and current == cap_id:
                cycle_path = " → ".join(list(visited) + [cap_id])
                errors.append(f"Fallback cycle detected: {cycle_path}")

        return errors

    def capabilities_by_type(self, type: CapabilityType) -> list[Capability]:
        """Get all capabilities of a given type."""
        return [cap for cap in self.capabilities if cap.type == type]

    def get_capability(self, capability_id: str) -> Optional[Capability]:
        """Get capability by id, or None if not found."""
        for cap in self.capabilities:
            if cap.id == capability_id:
                return cap
        return None


def manifest_from_plugin_config(plugin_id: str, plugin_version: str, config: dict[str, Any]) -> PluginCapabilitiesManifest:
    """Construct manifest from plugin.corvin.yaml config dict."""
    caps_version = config.get("capabilities_version", "1.0")
    caps_list = []

    for cap_dict in config.get("capabilities", []):
        cap = Capability(
            id=cap_dict["id"],
            type=CapabilityType(cap_dict.get("type", "context_source")),
            description=cap_dict.get("description", ""),
            parameters=cap_dict.get("parameters", {}),
            returns=cap_dict.get("returns", {}),
            slo_latency_ms=cap_dict.get("slo_latency_ms", 500),
            slo_error_rate=cap_dict.get("slo_error_rate", 0.01),
            audit_event=cap_dict.get("audit_event", ""),
            on_failure=FailureMode(cap_dict.get("on_failure", "fail_closed")),
            fallback_capability_id=cap_dict.get("fallback_capability_id"),
            added_in=cap_dict.get("added_in", "1.0"),
            deprecated_in=cap_dict.get("deprecated_in"),
            removed_in=cap_dict.get("removed_in"),
        )
        caps_list.append(cap)

    return PluginCapabilitiesManifest(
        plugin_id=plugin_id,
        plugin_version=plugin_version,
        capabilities_version=caps_version,
        capabilities=caps_list,
        capability_matrix=config.get("capability_matrix", []),
    )

"""Core dataclasses for Marketplace Plugin System (ADR-0XXX Phase 1).

Includes: Plugin, PluginConfig, PluginRegistry, Dependency Resolver, etc.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from packaging import version as pkg_version

# ── Enums ─────────────────────────────────────────────────────────────────────

class PluginTier(str, Enum):
    """Plugin trust tier."""
    A = "a"  # Built-in (always-on, no sandbox)
    B = "b"  # Vetted (security audit passed)
    C = "c"  # Community (user-contributed)


class PIIRisk(str, Enum):
    """Personal data exposure risk level."""
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PluginType(str, Enum):
    """Plugin capability type."""
    SKILL = "skill"
    TOOL = "tool"
    ENGINE = "engine"
    GATE = "gate"
    COMPLIANCE = "compliance"


class UpdatePolicy(str, Enum):
    """Auto-update policy for plugins."""
    MAJOR = "major"      # Never auto-update
    MINOR = "minor"      # Auto-update minor + patch, NOT major
    PATCH = "patch"      # Auto-update patch only
    NONE = "none"        # Never auto-update


# ── Exceptions ─────────────────────────────────────────────────────────────────

class PluginError(Exception):
    """Base exception for plugin system."""
    pass


class PluginAlreadyExists(PluginError):
    """Plugin with this ID already installed."""
    pass


class PluginNotFound(PluginError):
    """Plugin not found in registry."""
    pass


class DependencyConflictError(PluginError):
    """Dependency version conflict or missing dependency."""
    pass


class CircularDependencyError(PluginError):
    """Circular dependency detected."""
    pass


class ValidationError(PluginError):
    """Settings validation failed."""
    pass


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class PluginDependency:
    """Represents a plugin dependency (e.g., 'postgres-tool>=1.0.0')."""
    plugin_id: str
    version_range: str  # >=1.0.0 | ==2.0.0 | 1.x

    def satisfies(self, other_version: str) -> bool:
        """Check if other_version satisfies this dependency's range."""
        try:
            if not self.version_range:
                return True
            target = pkg_version.parse(other_version)
            if self.version_range.startswith("=="):
                required = pkg_version.parse(self.version_range[2:])
                return target == required
            if self.version_range.startswith(">="):
                required = pkg_version.parse(self.version_range[2:])
                return target >= required
            if self.version_range.endswith(".x"):
                major_minor = self.version_range[:-2]
                return target.major == pkg_version.parse(major_minor).major
            return False
        except Exception:
            return False
        except Exception:
            return False


@dataclass
class MarketplaceMetadata:
    """Marketplace-specific plugin metadata."""
    source: str                                  # https://marketplace.corvinlabs.com
    artifact_url: str                            # marketplace/ai-review-2.0.1.zip
    checksum: str                                # sha256:deadbeef
    size_bytes: int                              # 5200000
    cached_locally: bool = False
    cache_path: Optional[Path] = None
    mirrors: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class PluginQuota:
    """Resource quota for a plugin."""
    plugin_id: str
    monthly_tokens_usd: float = 50.0
    tokens_used_this_month: float = 0.0
    cpu_percent_max: int = 25                    # 0-100
    memory_mb_max: int = 512                     # MB

    def monthly_tokens_usd_remaining(self) -> float:
        """Tokens left this month."""
        return max(0.0, self.monthly_tokens_usd - self.tokens_used_this_month)

    def can_spend(self, tokens_usd: float) -> bool:
        """Check if spending this many tokens is allowed."""
        return tokens_usd <= self.monthly_tokens_usd_remaining()


@dataclass
class PluginState:
    """Plugin state storage."""
    plugin_id: str
    storage_path: Path
    size_bytes: int = 0
    last_cleanup: Optional[datetime] = None

    def write(self, filename: str, data: Any) -> None:
        """Write data to state storage."""
        path = self.storage_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f)

    def read(self, filename: str) -> Any:
        """Read data from state storage."""
        path = self.storage_path / filename
        if not path.exists():
            raise FileNotFoundError(f"{path} not found")
        with open(path) as f:
            return json.load(f)


@dataclass
class BreakingChange:
    """Single breaking change between plugin versions."""
    old_setting: str
    new_setting: str
    migration: str  # "copy_json_directly" | "transform_via_function" | etc.


@dataclass
class PluginManifest:
    """Plugin version manifest (metadata about a specific version)."""
    id: str
    version: str                                  # semver: 2.0.1
    settings_schema_version: str = "1.0"
    breaking_changes: List[BreakingChange] = field(default_factory=list)


@dataclass
class Plugin:
    """Complete plugin definition (the main dataclass)."""

    # Identity
    id: str
    version: str                                  # semver: 2.0.1
    name: str
    plugin_type: PluginType = PluginType.SKILL

    # Installation metadata
    installed_at: Optional[datetime] = None
    installed_by: Optional[str] = None           # user@example.com
    update_policy: UpdatePolicy = UpdatePolicy.MINOR

    # Enablement state
    enabled: bool = False
    enabled_at: Optional[datetime] = None

    # Settings
    settings_schema: Dict[str, Any] = field(default_factory=dict)
    settings_schema_version: str = "1.0"
    settings: Dict[str, Any] = field(default_factory=dict)

    # Compliance metadata (LOAD-BEARING)
    tier: PluginTier = PluginTier.C
    pii_risk: PIIRisk = PIIRisk.LOW
    requires_consent: bool = False
    sandbox_mode: bool = True
    sandbox_tier: str = "strict"                 # light | strict
    audit_required: bool = True

    # Dependencies
    dependencies: List[str] = field(default_factory=list)  # ["postgres-tool>=1.0.0"]

    # Marketplace metadata
    marketplace: Optional[MarketplaceMetadata] = None

    # Resource quota
    quota: PluginQuota = field(default_factory=lambda: PluginQuota(""))

    # State persistence
    state: Optional[PluginState] = None

    # Signing metadata
    signature_algorithm: str = "rsa-4096"
    signed_by: str = ""                          # "corvinlabs-signer-v1"
    signature_valid_until: Optional[datetime] = None

    # Version history
    version_history: List[PluginManifest] = field(default_factory=list)

    # Error recovery
    last_error: Optional[str] = None
    error_retry_count: int = 0
    graceful_shutdown_in_progress: bool = False

    def full_id(self) -> str:
        """Return marketplace-style full ID (id/version)."""
        return f"{self.id}/{self.version}"

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for YAML/JSON."""
        return {
            "id": self.id,
            "version": self.version,
            "name": self.name,
            "plugin_type": self.plugin_type.value,
            "installed_at": self.installed_at.isoformat() if self.installed_at else None,
            "installed_by": self.installed_by,
            "update_policy": self.update_policy.value,
            "enabled": self.enabled,
            "enabled_at": self.enabled_at.isoformat() if self.enabled_at else None,
            "settings": self.settings,
            "tier": self.tier.value,
            "pii_risk": self.pii_risk.value,
            "requires_consent": self.requires_consent,
            "sandbox_mode": self.sandbox_mode,
            "dependencies": self.dependencies,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Plugin:
        """Deserialize from dict."""
        return cls(
            id=data["id"],
            version=data["version"],
            name=data["name"],
            plugin_type=PluginType(data.get("plugin_type", "skill")),
            installed_at=(
                datetime.fromisoformat(data["installed_at"])
                if data.get("installed_at")
                else None
            ),
            installed_by=data.get("installed_by"),
            update_policy=UpdatePolicy(data.get("update_policy", "minor")),
            enabled=data.get("enabled", False),
            enabled_at=(
                datetime.fromisoformat(data["enabled_at"])
                if data.get("enabled_at")
                else None
            ),
            settings=data.get("settings", {}),
            tier=PluginTier(data.get("tier", "c")),
            pii_risk=PIIRisk(data.get("pii_risk", "low")),
            requires_consent=data.get("requires_consent", False),
            dependencies=data.get("dependencies", []),
        )


@dataclass
class PluginRegistry:
    """Registry for installed plugins (persistent to YAML)."""

    path: Path
    plugins: Dict[str, Plugin] = field(default_factory=dict)

    def add(self, plugin: Plugin) -> None:
        """Add plugin to registry."""
        if plugin.id in self.plugins:
            raise PluginAlreadyExists(f"Plugin {plugin.id} already exists")
        self.plugins[plugin.id] = plugin

    def get(self, plugin_id: str) -> Plugin:
        """Get plugin by ID."""
        if plugin_id not in self.plugins:
            raise PluginNotFound(f"Plugin {plugin_id} not found")
        return self.plugins[plugin_id]

    def remove(self, plugin_id: str) -> None:
        """Remove plugin from registry."""
        if plugin_id not in self.plugins:
            raise PluginNotFound(f"Plugin {plugin_id} not found")
        del self.plugins[plugin_id]

    def save(self) -> None:
        """Save registry to YAML file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "spec": {"version": 1, "schema_version": "1.0"},
            "plugins": {
                pid: plugin.to_dict()
                for pid, plugin in self.plugins.items()
            }
        }
        with open(self.path, "w") as f:
            yaml.dump(data, f, sort_keys=False, default_flow_style=False)

    @classmethod
    def load(cls, path: Path) -> PluginRegistry:
        """Load registry from YAML file."""
        if not path.exists():
            return cls(path=path)

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        registry = cls(path=path)
        for pid, pdata in data.get("plugins", {}).items():
            registry.plugins[pid] = Plugin.from_dict(pdata)
        return registry


@dataclass
class AuditEvent:
    """Represents a plugin system audit event."""

    timestamp: datetime
    event_type: str                              # plugin_installed, plugin_enabled, etc.
    plugin_id: str
    tenant_id: str = "_default"
    user_id: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def plugin_installed(
        plugin_id: str,
        tier: str,
        user_id: str,
        source: str = "marketplace",
        checksum: str = ""
    ) -> AuditEvent:
        """Factory for plugin_installed event."""
        return AuditEvent(
            timestamp=datetime.utcnow(),
            event_type="plugin_installed",
            plugin_id=plugin_id,
            user_id=user_id,
            details={
                "tier": tier,
                "source": source,
                "checksum": checksum
            }
        )

    @staticmethod
    def plugin_enabled(
        plugin_id: str,
        user_id: str
    ) -> AuditEvent:
        """Factory for plugin_enabled event."""
        return AuditEvent(
            timestamp=datetime.utcnow(),
            event_type="plugin_enabled",
            plugin_id=plugin_id,
            user_id=user_id
        )

    @staticmethod
    def plugin_config_changed(
        plugin_id: str,
        user_id: str,
        old_config: Dict[str, Any],
        new_config: Dict[str, Any]
    ) -> AuditEvent:
        """Factory for plugin_config_changed event."""
        return AuditEvent(
            timestamp=datetime.utcnow(),
            event_type="plugin_config_changed",
            plugin_id=plugin_id,
            user_id=user_id,
            details={
                "old_config": old_config,
                "new_config": new_config
            }
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for audit.jsonl."""
        return {
            "timestamp": self.timestamp.isoformat() + "Z",
            "event_type": self.event_type,
            "plugin_id": self.plugin_id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            **self.details
        }


# ── Dependency Resolver ─────────────────────────────────────────────────────

class DependencyResolver:
    """Resolves plugin dependency order via topological sort."""

    def __init__(self, plugins: Dict[str, Plugin]):
        """Initialize resolver with a dict of plugins."""
        self.plugins = plugins

    def topological_sort(self) -> List[str]:
        """
        Return plugin IDs in dependency order (DAG topological sort).
        Raises DependencyConflictError if cycle or version mismatch detected.
        """
        # Build adjacency graph
        graph: Dict[str, List[str]] = {pid: [] for pid in self.plugins.keys()}

        # Add edges based on dependencies
        for pid, plugin in self.plugins.items():
            for dep_spec in plugin.dependencies:
                dep = self._parse_dependency_spec(dep_spec)

                # Find plugin matching this dependency
                if dep.plugin_id not in self.plugins:
                    raise DependencyConflictError(
                        f"Plugin {pid} depends on {dep.plugin_id}, but it's not installed"
                    )

                # Check version compatibility
                dep_plugin = self.plugins[dep.plugin_id]
                if not dep.satisfies(dep_plugin.version):
                    raise DependencyConflictError(
                        f"Plugin {pid} requires {dep_spec}, but {dep.plugin_id}={dep_plugin.version} is installed"
                    )

                graph[dep.plugin_id].append(pid)

        # Kahn's algorithm for topological sort
        in_degree = {pid: 0 for pid in self.plugins.keys()}
        for pid in graph:
            for neighbor in graph[pid]:
                in_degree[neighbor] += 1

        queue = [pid for pid in self.plugins.keys() if in_degree[pid] == 0]
        result = []

        while queue:
            pid = queue.pop(0)
            result.append(pid)

            for neighbor in graph[pid]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        # Check for cycle
        if len(result) != len(self.plugins):
            raise CircularDependencyError("Circular dependency detected")

        return result

    @staticmethod
    def _parse_dependency_spec(spec: str) -> PluginDependency:
        """Parse 'postgres-tool>=1.0.0' → PluginDependency."""
        for op in [">=", "==", ">"]:
            if op in spec:
                plugin_id, version_range = spec.split(op, 1)
                return PluginDependency(plugin_id.strip(), op + version_range.strip())

        # No version spec
        return PluginDependency(spec.strip(), "")


# ── Settings Validator ──────────────────────────────────────────────────────

class SettingsValidator:
    """Validates plugin settings against JSON Schema."""

    def __init__(self, schema: Dict[str, Any]):
        """Initialize with JSON Schema."""
        self.schema = schema

    def validate(self, settings: Dict[str, Any]) -> bool:
        """
        Validate settings against schema.
        Raises ValidationError if invalid.
        """
        try:
            import jsonschema
            jsonschema.validate(instance=settings, schema=self.schema)
            return True
        except ImportError:
            # Fallback: basic validation
            return self._basic_validate(settings)
        except Exception as e:
            raise ValidationError(f"Schema validation failed: {e}")

    def _basic_validate(self, settings: Dict[str, Any]) -> bool:
        """Fallback validation (no jsonschema library)."""
        required = self.schema.get("required", [])
        for field_name in required:
            if field_name not in settings:
                raise ValidationError(f"Required field missing: {field_name}")
        return True

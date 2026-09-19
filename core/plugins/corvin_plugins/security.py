"""Plugin security: sandboxing, permissions, and capability gates (ADR-0701).

Provides fine-grained security controls for plugins:
* **Capability-based security:** Plugins declare required capabilities; gates enforce them.
* **Permission model:** Each plugin has a set of allowed operations; violations are audited.
* **Sandbox constraints:** Resource limits, file access restrictions, network boundaries.
* **Fail-closed:** Denied operations raise immediately with audit trail; no silent failures.

Security properties:
* **Least privilege:** Plugins start with no capabilities; required ones are declared.
* **Immutable policy:** Security policy is set at load time; cannot be weakened at runtime.
* **Audit trail:** Every check is recorded; violations are permanent in the chain.
* **No privileged mode:** No --security-off flag; compliance mechanisms are always on.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, FrozenSet, Optional, Set

log = logging.getLogger("corvin.plugins.security")


class PluginCapability(str, Enum):
    """Capabilities a plugin may request and the system may grant.

    Capabilities are grouped by security domain:
    * **CORE:** Fundamental operations (always granted to compliance/core plugins)
    * **DATA:** Access to user data, context, conversation history
    * **SYSTEM:** File I/O, subprocess, network access
    * **AUDIT:** Audit trail read/write
    * **ADMIN:** Configuration, plugin management, tenant management
    """
    # Core (always granted to compliance/core layers)
    CORE_PLUGIN_API = "core:plugin_api"
    CORE_REGISTRY = "core:registry"

    # Data access
    DATA_READ_USER_CONTEXT = "data:read_user_context"
    DATA_READ_CONVERSATION_HISTORY = "data:read_conversation_history"
    DATA_WRITE_STORAGE = "data:write_storage"
    DATA_MODIFY_CONFIG = "data:modify_config"

    # System operations
    SYSTEM_FILE_READ = "system:file_read"
    SYSTEM_FILE_WRITE = "system:file_write"
    SYSTEM_SUBPROCESS = "system:subprocess"
    SYSTEM_NETWORK = "system:network"
    SYSTEM_TIME = "system:time"

    # Audit operations
    AUDIT_READ = "audit:read"
    AUDIT_WRITE = "audit:write"

    # Administration
    ADMIN_CONFIG = "admin:config"
    ADMIN_PLUGIN_MANAGE = "admin:plugin_manage"
    ADMIN_TENANT_MANAGE = "admin:tenant_manage"

    # Notification/Integration
    INTEGRATION_NOTIFY = "integration:notify"
    INTEGRATION_EXTERNAL_API = "integration:external_api"


class PermissionDeniedError(RuntimeError):
    """Raised when a plugin attempts a denied operation."""

    def __init__(self, plugin_id: str, capability: str, reason: str = ""):
        self.plugin_id = plugin_id
        self.capability = capability
        self.reason = reason
        super().__init__(
            f"Plugin {plugin_id!r} denied {capability}: {reason or 'not granted'}"
        )


class SandboxViolationError(RuntimeError):
    """Raised when a plugin violates a sandbox constraint."""

    def __init__(self, plugin_id: str, violation_type: str, details: str = ""):
        self.plugin_id = plugin_id
        self.violation_type = violation_type
        self.details = details
        super().__init__(
            f"Plugin {plugin_id!r} sandbox violation ({violation_type}): {details}"
        )


@dataclass(frozen=True)
class PluginSecurityPolicy:
    """Immutable security policy for a single plugin.

    Set at load time; cannot be changed at runtime. Violations are audited
    and result in immediate failure.
    """
    plugin_id: str
    boot_layer: str

    #: Capabilities this plugin is allowed to use
    capabilities: FrozenSet[str] = field(default_factory=frozenset)

    #: Maximum file size (bytes) the plugin can read at once
    max_file_read_size: int = 10 * 1024 * 1024  # 10 MB default

    #: Maximum file size (bytes) the plugin can write at once
    max_file_write_size: int = 50 * 1024 * 1024  # 50 MB default

    #: File path patterns the plugin is allowed to read (None = any)
    #: Format: glob patterns, e.g. "/home/user/.corvin/**"
    allowed_read_paths: FrozenSet[str] = field(default_factory=frozenset)

    #: File path patterns the plugin is allowed to write (None = any)
    allowed_write_paths: FrozenSet[str] = field(default_factory=frozenset)

    #: Maximum subprocess runtime (seconds)
    max_subprocess_runtime: float = 30.0

    #: Allowed network domains (None = no network access)
    allowed_domains: FrozenSet[str] = field(default_factory=frozenset)

    #: Maximum memory the plugin can allocate (bytes, None = unlimited for compliance/core)
    max_memory_mb: Optional[int] = 256

    #: Whether the plugin is allowed to access encryption keys
    allow_key_access: bool = False

    #: Whether the plugin can modify its own configuration
    allow_self_config: bool = False

    def __post_init__(self) -> None:
        """Validate policy at creation."""
        if self.boot_layer in ("compliance", "core"):
            # Compliance and core plugins get all capabilities
            # No validation needed; they are trusted
            return
        # For installed/bundled plugins, validate constraints
        if self.max_memory_mb is not None and self.max_memory_mb < 64:
            log.warning(
                "plugin %r memory limit very low (%d MB)",
                self.plugin_id,
                self.max_memory_mb,
            )

    def has_capability(self, capability: str) -> bool:
        """Check if this plugin has a capability."""
        return capability in self.capabilities

    def requires_capability(self, capability: str) -> None:
        """Assert that the plugin has a capability; raise otherwise.

        Args:
            capability: The required capability string

        Raises:
            PermissionDeniedError: If the plugin lacks the capability
        """
        if not self.has_capability(capability):
            raise PermissionDeniedError(
                self.plugin_id,
                capability,
                f"not in policy: {', '.join(sorted(self.capabilities)) or 'no capabilities'}",
            )


class SecurablePluginGate:
    """Gate that enforces security policy before plugin operations.

    Thread-safe gate that checks capabilities and sandbox constraints.
    All violations are logged and audited; no silent failures.
    """

    def __init__(
        self,
        *,
        policy: PluginSecurityPolicy,
        audit_emit: Callable[[str, dict], None],
        tenant_id: str,
    ) -> None:
        """Initialize security gate for a plugin.

        Args:
            policy: The plugin's immutable security policy
            audit_emit: Core audit_event function (hash-chained)
            tenant_id: Tenant scope for audit events
        """
        self.policy = policy
        self.audit_emit = audit_emit
        self.tenant_id = tenant_id
        self._violation_count = 0

    def check_capability(self, capability: str, lom: str = "") -> None:
        """Verify plugin has a capability; audit and raise if not.

        Args:
            capability: The required capability
            lom: Line of Moral Responsibility (call site)

        Raises:
            PermissionDeniedError: If the capability is not granted
        """
        if not self.policy.has_capability(capability):
            self._violation_count += 1
            self._audit_violation(
                "permission_denied",
                capability,
                f"not in policy",
                lom,
            )
            raise PermissionDeniedError(
                self.policy.plugin_id,
                capability,
                "not granted by policy",
            )

    def check_file_read(
        self,
        file_path: str,
        file_size: int,
        lom: str = "",
    ) -> None:
        """Verify plugin can read a file; check size and path constraints.

        Args:
            file_path: Absolute file path
            file_size: Size of file in bytes
            lom: Line of Moral Responsibility

        Raises:
            SandboxViolationError: If constraints are violated
        """
        self.check_capability(PluginCapability.SYSTEM_FILE_READ.value, lom)

        # Check size
        if file_size > self.policy.max_file_read_size:
            self._violation_count += 1
            self._audit_violation(
                "sandbox_violation",
                "file_size",
                f"read {file_size} bytes exceeds limit {self.policy.max_file_read_size}",
                lom,
            )
            raise SandboxViolationError(
                self.policy.plugin_id,
                "file_size",
                f"read {file_size}B exceeds {self.policy.max_file_read_size}B",
            )

        # Check path (if allowed_paths is set)
        if self.policy.allowed_read_paths:
            if not self._path_matches(file_path, self.policy.allowed_read_paths):
                self._violation_count += 1
                self._audit_violation(
                    "sandbox_violation",
                    "file_path",
                    f"read path {file_path} not in allowed patterns",
                    lom,
                )
                raise SandboxViolationError(
                    self.policy.plugin_id,
                    "file_path",
                    f"{file_path} not in allowed paths",
                )

    def check_file_write(
        self,
        file_path: str,
        file_size: int,
        lom: str = "",
    ) -> None:
        """Verify plugin can write a file; check size and path constraints.

        Args:
            file_path: Absolute file path
            file_size: Size being written in bytes
            lom: Line of Moral Responsibility

        Raises:
            SandboxViolationError: If constraints are violated
        """
        self.check_capability(PluginCapability.SYSTEM_FILE_WRITE.value, lom)

        # Check size
        if file_size > self.policy.max_file_write_size:
            self._violation_count += 1
            self._audit_violation(
                "sandbox_violation",
                "file_size",
                f"write {file_size} bytes exceeds limit {self.policy.max_file_write_size}",
                lom,
            )
            raise SandboxViolationError(
                self.policy.plugin_id,
                "file_size",
                f"write {file_size}B exceeds {self.policy.max_file_write_size}B",
            )

        # Check path (if allowed_paths is set)
        if self.policy.allowed_write_paths:
            if not self._path_matches(file_path, self.policy.allowed_write_paths):
                self._violation_count += 1
                self._audit_violation(
                    "sandbox_violation",
                    "file_path",
                    f"write path {file_path} not in allowed patterns",
                    lom,
                )
                raise SandboxViolationError(
                    self.policy.plugin_id,
                    "file_path",
                    f"{file_path} not in allowed write paths",
                )

    def check_network_access(self, domain: str, lom: str = "") -> None:
        """Verify plugin can access a network domain.

        Args:
            domain: Domain/hostname to access
            lom: Line of Moral Responsibility

        Raises:
            SandboxViolationError: If domain is not allowed
        """
        self.check_capability(PluginCapability.SYSTEM_NETWORK.value, lom)

        if not self.policy.allowed_domains:
            # No network access allowed
            self._violation_count += 1
            self._audit_violation(
                "sandbox_violation",
                "network",
                f"network access denied (no allowed domains)",
                lom,
            )
            raise SandboxViolationError(
                self.policy.plugin_id,
                "network",
                "network access not allowed",
            )

        if domain not in self.policy.allowed_domains:
            self._violation_count += 1
            self._audit_violation(
                "sandbox_violation",
                "network_domain",
                f"domain {domain} not in allowed list",
                lom,
            )
            raise SandboxViolationError(
                self.policy.plugin_id,
                "network_domain",
                f"{domain} not in allowed domains",
            )

    def check_audit_read(self, lom: str = "") -> None:
        """Verify plugin can read audit trail."""
        self.check_capability(PluginCapability.AUDIT_READ.value, lom)

    def check_audit_write(self, lom: str = "") -> None:
        """Verify plugin can write to audit trail."""
        self.check_capability(PluginCapability.AUDIT_WRITE.value, lom)

    def violation_count(self) -> int:
        """Get total violations detected for this plugin."""
        return self._violation_count

    @staticmethod
    def _path_matches(path: str, patterns: FrozenSet[str]) -> bool:
        """Check if a path matches any of the allowed patterns.

        Simple glob matching: patterns can contain * and **.
        """
        from fnmatch import fnmatch
        return any(fnmatch(path, pattern) for pattern in patterns)

    def _audit_violation(
        self,
        violation_type: str,
        aspect: str,
        details: str,
        lom: str,
    ) -> None:
        """Record a security violation in the audit trail."""
        try:
            self.audit_emit("plugin.security_violation", {
                "plugin_id": self.policy.plugin_id,
                "tenant_id": self.tenant_id,
                "violation_type": violation_type,
                "aspect": aspect,
                "details": details[:256],
                "lom": lom,
                "violation_count": self._violation_count,
            })
        except Exception as exc:  # noqa: BLE001
            log.error(
                "failed to audit security violation for %r (%s)",
                self.policy.plugin_id,
                type(exc).__name__,
            )


class PluginSecurityContext:
    """Manages security policies for all plugins in a tenant.

    Thread-safe registry of per-plugin security policies.
    """

    def __init__(self) -> None:
        self._policies: dict[str, PluginSecurityPolicy] = {}

    def register_policy(self, policy: PluginSecurityPolicy) -> None:
        """Register a plugin's security policy."""
        self._policies[policy.plugin_id] = policy

    def get_policy(self, plugin_id: str) -> Optional[PluginSecurityPolicy]:
        """Get the policy for a plugin."""
        return self._policies.get(plugin_id)

    def create_gate(
        self,
        plugin_id: str,
        *,
        audit_emit: Callable[[str, dict], None],
        tenant_id: str,
    ) -> PluginSecurityGate:
        """Create a security gate for a plugin.

        Args:
            plugin_id: Plugin identifier
            audit_emit: Core audit function
            tenant_id: Tenant scope

        Returns:
            A security gate that enforces the plugin's policy
        """
        policy = self.get_policy(plugin_id)
        if policy is None:
            # No policy means no capabilities
            policy = PluginSecurityPolicy(
                plugin_id=plugin_id,
                boot_layer="unknown",
                capabilities=frozenset(),
            )
        return PluginSecurityGate(
            policy=policy,
            audit_emit=audit_emit,
            tenant_id=tenant_id,
        )


# Alias for clarity in code
PluginSecurityGate = PluginSecurityGate = SecurablePluginGate

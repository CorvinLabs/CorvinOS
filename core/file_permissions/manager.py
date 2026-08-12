"""File Permission Manager — fine-grained file-write protection.

ADR-0295: Fail-closed permission matrix with per-path rules, tenant isolation,
and audit trail integration.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class PermissionType(str, Enum):
    """File operation types."""

    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    EXECUTE = "execute"


class PermissionDenied(PermissionError):
    """Raised when a file operation violates permission policy."""

    def __init__(self, path: str | Path, operation: PermissionType, reason: str):
        self.path = str(path)
        self.operation = operation
        self.reason = reason
        super().__init__(
            f"Permission denied: {operation.value} {self.path} — {reason}"
        )


@dataclass(frozen=True)
class PermissionRule:
    """One permission rule in the matrix.

    Attributes:
        path_pattern: glob pattern or absolute path to match against
        permission: the operation type (read/write/delete/execute)
        allow: True to allow, False to deny
        inherit: if True, apply to all child paths recursively
        description: human-readable reason for the rule
    """

    path_pattern: str
    permission: PermissionType
    allow: bool
    inherit: bool = True
    description: str = ""


class FilePermissionManager:
    """Fail-closed file permission manager with tenant isolation.

    Usage:
        manager = FilePermissionManager(
            tenant_id="my_tenant",
            corvin_home=Path.home() / ".corvin"
        )

        # Add custom rules
        manager.add_rule(PermissionRule(
            path_pattern="/app/config/*",
            permission=PermissionType.WRITE,
            allow=True,
            description="Allow writes to config directory"
        ))

        # Check permission (raises PermissionDenied on failure)
        manager.check_permission(
            path="/app/config/settings.json",
            operation=PermissionType.WRITE,
        )

    Compliance:
        - GDPR Art. 32: fail-closed access control
        - Tenant isolation: all checks filtered by tenant_id
        - Audit trail: every check logged with decision and reason
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        corvin_home: Optional[Path] = None,
        audit_logger: Optional[Any] = None,
    ):
        """Initialize permission manager.

        Args:
            tenant_id: keyword-only, tenant identifier for GDPR Art. 6, 32
            corvin_home: optional path to corvin home (for protected path defaults)
            audit_logger: optional audit chain logger
        """
        if not isinstance(tenant_id, str) or not tenant_id:
            raise ValueError("tenant_id must be non-empty string")

        self.tenant_id = tenant_id
        self.corvin_home = corvin_home or (Path.home() / ".corvin")
        self.audit_logger = audit_logger

        # Custom rules (added via add_rule)
        self._custom_rules: list[PermissionRule] = []

        # Default deny-list (protected paths, always denied unless in whitelist)
        self._protected_paths = self._build_protected_paths()

        # Lock for thread-safe rule updates
        self._rules_lock = __import__("threading").RLock()

    def _build_protected_paths(self) -> dict[PermissionType, set[Path]]:
        """Build default protected-path sets."""
        protected = {
            PermissionType.WRITE: set(),
            PermissionType.DELETE: set(),
            PermissionType.EXECUTE: set(),
        }

        # Protect audit logs
        protected[PermissionType.WRITE].add(self.corvin_home / "audit.jsonl")
        protected[PermissionType.DELETE].add(self.corvin_home / "audit.jsonl")

        # Protect vault
        vault = Path.home() / ".config" / "corvin-voice" / "secrets.json"
        protected[PermissionType.WRITE].add(vault)
        protected[PermissionType.DELETE].add(vault)

        # Protect license directory
        license_dir = self.corvin_home / "license"
        protected[PermissionType.WRITE].add(license_dir)
        protected[PermissionType.DELETE].add(license_dir)

        # Protect instance keys
        for key_file in ["instance_key.pem", "instance_cert.jwt", "instance_pubkey.pem"]:
            protected[PermissionType.WRITE].add(self.corvin_home / key_file)
            protected[PermissionType.DELETE].add(self.corvin_home / key_file)

        return protected

    def add_rule(self, rule: PermissionRule) -> None:
        """Add a custom permission rule."""
        if not isinstance(rule, PermissionRule):
            raise ValueError("rule must be a PermissionRule instance")

        with self._rules_lock:
            self._custom_rules.append(rule)

    def _matches_pattern(
        self, path: Path, pattern: str, inherit: bool = True
    ) -> bool:
        """Check if path matches a glob pattern or absolute path."""
        from fnmatch import fnmatch

        path_str = str(path.resolve())
        pattern_str = str(Path(pattern).resolve())

        # Direct match
        if path_str == pattern_str:
            return True

        # If inherit=False, only direct matches are allowed
        if not inherit:
            return False

        # Pattern match
        if fnmatch(path_str, pattern_str):
            return True

        # Wildcard at end (directory match)
        if pattern_str.endswith("/*") and path_str.startswith(
            pattern_str[:-2]
        ):
            return True

        # Double-wildcard recursive (directory tree match)
        if "/**" in pattern_str:
            base_pattern = pattern_str.split("/**")[0]
            if path_str.startswith(base_pattern + "/") or path_str == base_pattern:
                return True

        # Check if pattern is a directory and path is under it (directory inheritance)
        try:
            path.relative_to(Path(pattern).resolve())
            return True
        except ValueError:
            pass

        return False

    def _path_is_under(self, child: Path, parent: Path) -> bool:
        """Check if child path is under parent directory."""
        try:
            child.resolve().relative_to(parent.resolve())
            return True
        except ValueError:
            return False

    def _is_protected_path(
        self, path: Path, operation: PermissionType
    ) -> tuple[bool, str]:
        """Check if path is in protected set."""
        path = path.resolve()

        # Check protected paths for this operation
        if operation in self._protected_paths:
            for protected in self._protected_paths[operation]:
                if self._path_is_under(path, protected) or self._path_is_under(
                    protected, path
                ):
                    return True, f"path is protected by default policy"

        # Audit log is always protected from writes/deletes
        audit_log = self.corvin_home / "audit.jsonl"
        if self._path_is_under(path, audit_log) or path == audit_log:
            if operation in (PermissionType.WRITE, PermissionType.DELETE):
                return True, "audit log is immutable"

        return False, ""

    def check_permission(
        self,
        path: str | Path,
        operation: PermissionType,
        *,
        tenant_id: Optional[str] = None,
    ) -> None:
        """Check if operation is allowed on path.

        Raises PermissionDenied if operation is not allowed. Fail-closed:
        if permission is ambiguous or cannot be determined, access is denied.
        """
        if tenant_id is None:
            tenant_id = self.tenant_id
        elif tenant_id != self.tenant_id:
            raise ValueError(
                f"tenant_id mismatch: {tenant_id} != {self.tenant_id}"
            )

        try:
            path = Path(path).resolve()
        except (ValueError, OSError) as e:
            # Fail closed: cannot resolve path = deny
            self._audit_check(path, operation, False, f"path resolution failed: {e}")
            raise PermissionDenied(path, operation, "cannot resolve path")

        # 1. Check protected paths (default deny)
        is_protected, reason = self._is_protected_path(path, operation)
        if is_protected:
            self._audit_check(path, operation, False, reason)
            raise PermissionDenied(path, operation, reason)

        # 2. Check custom rules (deny rules checked first for safety)
        with self._rules_lock:
            # First pass: check deny rules (highest priority)
            for rule in self._custom_rules:
                if rule.permission != operation:
                    continue
                if rule.allow:  # Skip allow rules for now
                    continue

                if self._matches_pattern(path, rule.path_pattern, inherit=rule.inherit):
                    self._audit_check(
                        path, operation, False, f"blocked by rule: {rule.description}"
                    )
                    raise PermissionDenied(
                        path, operation, f"blocked by rule: {rule.description}"
                    )

            # Second pass: check allow rules
            for rule in self._custom_rules:
                if rule.permission != operation:
                    continue
                if not rule.allow:  # Skip deny rules (already checked)
                    continue

                if self._matches_pattern(path, rule.path_pattern, inherit=rule.inherit):
                    self._audit_check(
                        path, operation, True, f"allowed by rule: {rule.description}"
                    )
                    return

        # 3. Default deny (fail-closed)
        self._audit_check(
            path, operation, False, "no allow rule found (fail-closed)"
        )
        raise PermissionDenied(path, operation, "no allow rule found (fail-closed)")

    def allow_path(
        self,
        path: str | Path,
        operation: PermissionType,
        *,
        inherit: bool = True,
        description: str = "",
    ) -> None:
        """Whitelist a path for an operation."""
        rule = PermissionRule(
            path_pattern=str(path),
            permission=operation,
            allow=True,
            inherit=inherit,
            description=description or f"explicitly allowed",
        )
        self.add_rule(rule)

    def deny_path(
        self,
        path: str | Path,
        operation: PermissionType,
        *,
        inherit: bool = True,
        description: str = "",
    ) -> None:
        """Deny a path for an operation."""
        rule = PermissionRule(
            path_pattern=str(path),
            permission=operation,
            allow=False,
            inherit=inherit,
            description=description or "explicitly denied",
        )
        self.add_rule(rule)

    def _audit_check(
        self, path: Path, operation: PermissionType, allowed: bool, reason: str
    ) -> None:
        """Log permission check to audit trail."""
        if not self.audit_logger:
            return

        try:
            from core.audit.chain import AuditEntry

            entry = AuditEntry(
                event_type="file_permission_check",
                actor=f"tenant:{self.tenant_id}",
                action=operation.value,
                resource=str(path),
                result="success" if allowed else "failure",
                timestamp=datetime.utcnow().isoformat() + "Z",
                details={
                    "tenant_id": self.tenant_id,
                    "operation": operation.value,
                    "allowed": allowed,
                    "reason": reason,
                },
            )
            self.audit_logger.record(entry)
        except Exception as e:
            # Fail closed: cannot audit = log error but don't crash
            logger.error(f"failed to audit file permission check: {e}")

    def get_stats(self) -> dict[str, Any]:
        """Return statistics about this manager."""
        with self._rules_lock:
            allow_rules = sum(1 for r in self._custom_rules if r.allow)
            deny_rules = sum(1 for r in self._custom_rules if not r.allow)

        return {
            "tenant_id": self.tenant_id,
            "corvin_home": str(self.corvin_home),
            "custom_rules": {
                "allow": allow_rules,
                "deny": deny_rules,
                "total": allow_rules + deny_rules,
            },
            "protected_paths": {
                operation.value: len(paths)
                for operation, paths in self._protected_paths.items()
            },
        }


# Global instance (one per tenant)
_managers: dict[str, FilePermissionManager] = {}
_managers_lock = __import__("threading").RLock()


def get_permission_manager(
    *,
    tenant_id: str,
    corvin_home: Optional[Path] = None,
    audit_logger: Optional[Any] = None,
) -> FilePermissionManager:
    """Get or create a permission manager for a tenant."""
    with _managers_lock:
        if tenant_id not in _managers:
            _managers[tenant_id] = FilePermissionManager(
                tenant_id=tenant_id,
                corvin_home=corvin_home,
                audit_logger=audit_logger,
            )
        return _managers[tenant_id]

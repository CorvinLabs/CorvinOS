"""Plugin manifest with ADR-0264 frontmatter support.

Implements:
- PluginManifest dataclass (frozen, immutable)
- ADR-0264 frontmatter parsing and generation
- Manifest validation (schema + semantic checks)
- Audit event declarations (fail-closed)

ADR-0264 Frontmatter Example:
  id: plugin-my-data-connector-ADR-0001
  status: PROPOSED
  depends_on: [ADR-0262]
  relates_to: [ADR-0244]
  paths:
    - core/plugins/buildin/data_connector/my_data_connector/
    - tests/plugins/test_my_data_connector.py
  docs:
    - core/plugins/plugin_builder/docs/my-data-connector.md
  audit_events:
    - build_started
    - build_completed
    - build_failed
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional, List


class ManifestStatus(str, Enum):
    """Manifest status per ADR-0264."""
    PROPOSED = "PROPOSED"
    ACCEPTED = "ACCEPTED"
    DEPRECATED = "DEPRECATED"
    SUPERSEDED = "SUPERSEDED"


@dataclass(frozen=True)
class PluginManifest:
    """Plugin manifest with ADR-0264 frontmatter.

    Immutable record of plugin metadata, dependencies, and audit requirements.
    Used by PluginBuilder to:
    1. Validate plugin structure (fail-closed)
    2. Generate setup.py/pyproject.toml
    3. Declare audit events in advance
    4. Track ADR dependencies

    Attributes:
        id: Unique manifest ID (e.g., 'plugin-my-connector-ADR-0001')
        name: Plugin name (e.g., 'my-data-connector')
        version: Semantic version (e.g., '1.0.0')
        status: Manifest status per ADR-0264
        description: Plugin description
        author: Author name
        author_email: Author email
        depends_on: List of ADR IDs this plugin depends on
        relates_to: List of related ADR IDs
        paths: Paths affected by this plugin (relative to repo root)
        docs: Documentation paths
        audit_events: Events this plugin will emit
        python_requires: Python version requirement
        dependencies: Required packages
        dev_dependencies: Development dependencies
        contract_hash: SHA256 of plugin contract (computed)
    """

    id: str  # e.g., 'plugin-my-connector-ADR-0001'
    name: str
    version: str
    status: ManifestStatus
    description: str
    author: str
    author_email: str
    depends_on: List[str] = field(default_factory=lambda: ["ADR-0262"])
    relates_to: List[str] = field(default_factory=list)
    paths: List[str] = field(default_factory=list)
    docs: List[str] = field(default_factory=list)
    audit_events: List[str] = field(default_factory=lambda: [
        "build_started",
        "build_completed",
        "build_failed"
    ])
    python_requires: str = ">=3.10"
    dependencies: List[str] = field(default_factory=lambda: ["corvin-plugins"])
    dev_dependencies: List[str] = field(default_factory=lambda: [
        "pytest>=7.0",
        "pytest-cov>=3.0",
        "black>=22.0",
        "mypy>=0.990",
    ])

    # Computed fields
    contract_hash: str = ""  # SHA256, computed post-init
    created_at: str = ""  # ISO8601, computed post-init

    def __post_init__(self):
        """Validate and compute derived fields (frozen workaround via object.__setattr__)."""
        # Validate ID format
        if not re.match(r'^plugin-[\w-]+-ADR-\d{4}$', self.id):
            raise ValueError(
                f"Invalid manifest ID format: {self.id}. "
                f"Expected: plugin-<name>-ADR-<number>"
            )

        # Validate version is semantic
        if not re.match(r'^\d+\.\d+\.\d+', self.version):
            raise ValueError(
                f"Invalid semantic version: {self.version}. "
                f"Expected: X.Y.Z[-prerelease][+build]"
            )

        # Validate audit_events are non-empty
        if not self.audit_events:
            raise ValueError("audit_events list cannot be empty")

        # Compute contract hash (captures the manifest as a contract)
        contract_data = {
            k: v for k, v in asdict(self).items()
            if k not in ['contract_hash', 'created_at']
        }
        computed_hash = hashlib.sha256(
            json.dumps(contract_data, sort_keys=True, default=str).encode()
        ).hexdigest()

        # Use object.__setattr__ to work around frozen=True
        object.__setattr__(self, 'contract_hash', computed_hash)
        object.__setattr__(
            self,
            'created_at',
            datetime.now(timezone.utc).isoformat()
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return asdict(self)

    def to_frontmatter(self) -> str:
        """Generate ADR-0264 YAML frontmatter.

        Returns:
            YAML frontmatter as string (suitable for file header)
        """
        return f"""---
id: {self.id}
status: {self.status.value}
depends_on: {json.dumps(self.depends_on)}
relates_to: {json.dumps(self.relates_to)}
paths: {json.dumps(self.paths)}
docs: {json.dumps(self.docs)}
audit_events: {json.dumps(self.audit_events)}
---
"""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PluginManifest:
        """Create manifest from dict.

        Args:
            data: Dictionary with manifest fields

        Returns:
            PluginManifest instance

        Raises:
            ValueError: If dict is invalid
        """
        # Convert status string to enum
        if isinstance(data.get('status'), str):
            data['status'] = ManifestStatus(data['status'])

        return cls(**data)


class ManifestValidator:
    """Validates plugin manifests against ADR-0264 schema."""

    # Required fields for a valid manifest
    REQUIRED_FIELDS = {
        'id', 'name', 'version', 'status', 'description',
        'author', 'author_email', 'depends_on', 'audit_events'
    }

    # Valid audit event types
    VALID_AUDIT_EVENTS = {
        'build_started',
        'build_completed',
        'build_failed',
        'plugin_loaded',
        'plugin_executed',
        'plugin_disabled',
        'wheel_validated',
        'wheel_installed',
    }

    @classmethod
    def validate(cls, manifest: PluginManifest) -> tuple[bool, list[str]]:
        """Validate a manifest.

        Args:
            manifest: PluginManifest to validate

        Returns:
            (is_valid, issues) tuple
        """
        issues: list[str] = []

        # Check audit events are valid
        for event in manifest.audit_events:
            if event not in cls.VALID_AUDIT_EVENTS:
                issues.append(
                    f"Unknown audit event: {event}. "
                    f"Valid events: {', '.join(sorted(cls.VALID_AUDIT_EVENTS))}"
                )

        # Check depends_on references valid ADRs (basic format check)
        for adr in manifest.depends_on:
            if not re.match(r'^ADR-\d{4}', adr):
                issues.append(f"Invalid ADR reference in depends_on: {adr}")

        # Check relates_to references valid ADRs
        for adr in manifest.relates_to:
            if not re.match(r'^ADR-\d{4}', adr):
                issues.append(f"Invalid ADR reference in relates_to: {adr}")

        # Validate paths are relative (not absolute)
        for path in manifest.paths:
            if path.startswith('/'):
                issues.append(f"Path must be relative, got: {path}")

        # Validate docs are relative
        for doc in manifest.docs:
            if doc.startswith('/'):
                issues.append(f"Doc path must be relative, got: {doc}")

        # Check contract_hash is non-empty (computed)
        if not manifest.contract_hash:
            issues.append("contract_hash must be computed (non-empty)")

        return len(issues) == 0, issues


def generate_adr_frontmatter(manifest: PluginManifest) -> str:
    """Generate a full ADR frontmatter + decision body from manifest.

    This creates a standalone ADR-XXXX markdown file that documents
    the plugin as an architectural decision.

    Args:
        manifest: PluginManifest with ADR-0264 fields

    Returns:
        Full markdown content with YAML frontmatter + body
    """
    return f"""{manifest.to_frontmatter()}

# {manifest.id}: {manifest.name}

## Summary

{manifest.description}

- **Author:** {manifest.author} <{manifest.author_email}>
- **Status:** {manifest.status.value}
- **Contract Hash:** {manifest.contract_hash}

## Context

This plugin was built using CorvinOS Plugin-Builder v2 (ADR-0262).

### Dependencies

- Depends on: {', '.join(manifest.depends_on)}
- Related to: {', '.join(manifest.relates_to) if manifest.relates_to else '(none)'}

### Paths Affected

```
{chr(10).join('- ' + p for p in manifest.paths)}
```

### Documentation

```
{chr(10).join('- ' + d for d in manifest.docs)}
```

## Audit Events

This plugin declares the following audit events (ADR-0233):

```
{chr(10).join('- ' + e for e in manifest.audit_events)}
```

All events are hash-chained and immutable per GDPR Art. 30, 32.

## Implementation

The plugin is built with:
- Python {manifest.python_requires}
- Dependencies: {', '.join(manifest.dependencies)}
- Dev Dependencies: {', '.join(manifest.dev_dependencies)}

## Decision

This plugin is accepted as a component of CorvinOS and follows:
- Tenant isolation (ADR-0007, ADR-0233)
- Audit-first design (ADR-0232)
- Compliance baseline (ADR-0XXX)

## Consequences

- Audit events MUST be emitted for every build, load, and execution
- Plugin MAY NOT be disabled without removing from registry
- Version changes MUST be tracked in audit trail
"""

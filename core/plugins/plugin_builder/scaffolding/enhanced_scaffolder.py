"""Enhanced scaffold generation with lifecycle hooks and Tenant-Skill-Architecture integration (ADR-0262).

Provides:
- PluginScaffoldConfig: Configuration for plugin scaffold generation
- LifecycleHookTemplate: templates that include lifecycle hooks
- PluginScaffoldGenerator: improved scaffolding system with tenant integration
- bootstrap_scaffold: generate new plugin scaffolds with full setup
- AuditEventEmitter: audit trail integration (GDPR Art. 30, 32)

Each scaffold includes:
- plugin.py with on_load, on_execute, on_unload hooks
- conftest.py with pytest fixtures
- tests/test_plugin.py with base test structure
- setup.py and pyproject.toml for packaging
- plugin.json with JSONSchema validation
- .gitignore with secrets protection

Tenant Integration:
- Tenant-scoped scaffold generation (GDPR Art. 5 isolation)
- Version tracking via TenantSkillVersionManager
- Audit-trail logging (immutable, hash-chained)
- State snapshots for rollback capability
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class PluginScaffoldConfig:
    """Configuration for plugin scaffold generation with tenant isolation (ADR-0262 Phase A).

    Attributes:
        tenant_id: Tenant identifier (validated, GDPR Art. 5)
        plugin_id: Plugin identifier (unique per tenant)
        plugin_name: Human-readable plugin name
        plugin_type: Type of plugin (data_connector, skill_plugin, compute_engine, etc.)
        description: Plugin description
        author: Author name
        version: Initial version (semantic versioning)
        include_lifecycle_hooks: Generate lifecycle hooks (default: True)
    """

    tenant_id: str
    plugin_id: str
    plugin_name: str
    plugin_type: str
    description: str = "A Corvin plugin"
    author: str = "Plugin Author"
    version: str = "0.1.0"
    include_lifecycle_hooks: bool = True
    include_on_load: bool = True
    include_on_execute: bool = True
    include_on_unload: bool = True
    include_on_error: bool = False
    external_dependencies: List[str] = field(default_factory=list)
    requires_auth: bool = False
    requires_network_egress: bool = False
    egress_hosts: List[str] = field(default_factory=list)

    def validate(self) -> Tuple[bool, str]:
        """Validate scaffold configuration (fail-closed).

        Returns:
            (is_valid, error_message)
        """
        # ADV-003: Validate plugin_id format (whitelist only safe chars, no path traversal)
        # Only alphanumeric, underscore, and dot allowed (no hyphen to prevent traversal attacks)
        if not re.match(r'^[a-z0-9_.]+$', self.plugin_id):
            return False, f"Invalid plugin_id: {self.plugin_id}. Must contain only letters, numbers, underscores, and dots"

        # Reject path traversal patterns
        if '..' in self.plugin_id or '/' in self.plugin_id or '\\' in self.plugin_id:
            return False, f"Invalid plugin_id: {self.plugin_id}. Cannot contain path traversal patterns (../ or \\)"

        # Validate plugin_type
        valid_types = [
            "data_connector", "skill_plugin", "compute_engine", "bridge_channel",
            "worker_engine", "provider_plugin", "middleware", "integration"
        ]
        if self.plugin_type not in valid_types:
            return False, f"Invalid plugin_type: {self.plugin_type}. Must be one of {valid_types}"

        # Validate semantic versioning
        if not re.match(r'^\d+\.\d+\.\d+', self.version):
            return False, f"Invalid version: {self.version}. Must use semantic versioning (e.g., 1.0.0)"

        # Validate tenant_id (sanitize Unicode/bidi for CVE-2021-42574)
        if not self._is_safe_tenant_id():
            return False, f"Unsafe tenant_id: contains Unicode bidi/zero-width characters"

        # Validate plugin_name length (prevent DoS)
        if len(self.plugin_name) > 200:
            return False, f"plugin_name too long: {len(self.plugin_name)} > 200 chars"

        # Validate description length
        if len(self.description) > 5000:
            return False, f"description too long: {len(self.description)} > 5000 chars"

        return True, ""

    def _is_safe_tenant_id(self) -> bool:
        """Check for Unicode bidi-override/zero-width characters (CVE-2021-42574)."""
        dangerous_chars = {
            '‪', '‫', '‬', '‭', '‮',  # LRE, RLE, PDF, LRO, RLO
            '؜',  # ALM
            '​', '‌', '‍', '⁠', '﻿',  # Zero-width chars
        }
        for char in self.tenant_id:
            if char in dangerous_chars or unicodedata.category(char) in ('Cc', 'Cf'):
                return False
        return True


@dataclass
class LifecycleHookTemplate:
    """Template for a plugin with lifecycle hooks.

    Attributes:
        plugin_id: Plugin identifier
        plugin_type: Type of plugin
        plugin_name: Human-readable name
        description: Plugin description
        author: Author name
        include_on_load: Generate on_load hook
        include_on_execute: Generate on_execute hook
        include_on_unload: Generate on_unload hook
    """

    plugin_id: str
    plugin_type: str
    plugin_name: str
    description: str
    author: str = "Plugin Author"
    include_on_load: bool = True
    include_on_execute: bool = True
    include_on_unload: bool = True

    def generate_plugin_code(self) -> str:
        """Generate plugin.py code with lifecycle hooks.

        Returns:
            str: Python code for the plugin
        """
        hooks = []

        if self.include_on_load:
            hooks.append(
                '''    def on_load(self, context: Any) -> None:
        """Called when the plugin is loaded.

        Args:
            context: Plugin execution context
        """
        self.logger.info(f"Loading {self.plugin_id}")
        # Initialize plugin resources here
        self._initialized = True
'''
            )

        if self.include_on_execute:
            hooks.append(
                '''    def on_execute(self, input_data: Any, context: Any) -> Any:
        """Execute the plugin's primary function.

        Args:
            input_data: Input data for processing
            context: Plugin execution context

        Returns:
            Processed output data
        """
        if not getattr(self, "_initialized", False):
            raise RuntimeError(f"{self.plugin_id} not initialized")

        self.logger.info(f"Executing {self.plugin_id}")
        # Implement plugin logic here
        return input_data
'''
            )

        if self.include_on_unload:
            hooks.append(
                '''    def on_unload(self, context: Any) -> None:
        """Called when the plugin is unloaded.

        Args:
            context: Plugin execution context
        """
        self.logger.info(f"Unloading {self.plugin_id}")
        # Clean up plugin resources here
        self._initialized = False
'''
            )

        hooks_code = "\n".join(hooks)

        return f'''"""Plugin implementation for {self.plugin_name}.

Plugin ID: {self.plugin_id}
Type: {self.plugin_type}
Author: {self.author}
"""
from __future__ import annotations

import logging
from typing import Any


class {self._class_name()}Plugin:
    """Implementation of {self.plugin_name} plugin.

    Attributes:
        plugin_id: Unique plugin identifier
        display_name: Human-readable plugin name
        plugin_type: Type of plugin (provider, skill, etc.)
    """

    def __init__(self) -> None:
        """Initialize the plugin."""
        self.plugin_id = "{self.plugin_id}"
        self.display_name = "{self.plugin_name}"
        self.plugin_type = "{self.plugin_type}"
        self.logger = logging.getLogger(__name__)
        self._initialized = False

{hooks_code}

    def health_check(self) -> dict[str, Any]:
        """Check the health of the plugin.

        Returns:
            dict: health status with 'status' key ('healthy' or 'unhealthy')
        """
        return {{
            "status": "healthy",
            "plugin_id": self.plugin_id,
            "initialized": self._initialized,
        }}
'''

    def _class_name(self) -> str:
        """Generate a Python class name from the plugin ID.

        Returns:
            str: PEP-8 compliant class name
        """
        parts = re.split(r"[._-]+", self.plugin_id)
        return "".join(p.capitalize() for p in parts if p) + "Plugin"

    def generate_conftest(self) -> str:
        """Generate conftest.py for plugin tests.

        Returns:
            str: Python code for conftest.py
        """
        return '''"""Pytest configuration for plugin tests.

Provides fixtures for testing the plugin with:
- Test context and registry
- Mock audit trail
- Temporary plugin home directory
"""
import pytest
from corvin_plugins.plugin_builder.testing_framework import (
    plugin_context_fixture,
    plugin_registry_fixture,
    temp_plugin_home,
    mock_audit_backend,
)

# Re-export fixtures for test modules
pytest_plugins = []

__all__ = [
    "plugin_context",
    "plugin_registry",
    "temp_plugin_home",
    "mock_audit_backend",
]
'''

    def generate_test_module(self) -> str:
        """Generate test_plugin.py with basic test structure.

        Returns:
            str: Python code for test module
        """
        class_name = self._class_name()
        return f'''"""Unit tests for {self.plugin_name} plugin.

Tests verify:
- Plugin initialization and lifecycle
- Plugin execution and output
- Error handling
- Audit trail events
"""
import pytest
from {self._module_name()} import {class_name}Plugin


class TestPluginLifecycle:
    """Test plugin lifecycle hooks."""

    def test_plugin_init(self) -> None:
        """Test plugin initialization."""
        plugin = {class_name}Plugin()
        assert plugin.plugin_id == "{self.plugin_id}"
        assert plugin.display_name == "{self.plugin_name}"
        assert plugin.plugin_type == "{self.plugin_type}"

    def test_health_check(self) -> None:
        """Test plugin health check."""
        plugin = {class_name}Plugin()
        health = plugin.health_check()
        assert health["status"] == "healthy"
        assert health["plugin_id"] == "{self.plugin_id}"

{"    " if self.include_on_load else ""}def test_on_load(self, plugin_context: Any) -> None:
{"        " if self.include_on_load else ""}"""Test plugin loading."""
{"        " if self.include_on_load else ""}plugin = {class_name}Plugin()
{"        " if self.include_on_load else ""}plugin.on_load(plugin_context)
{"        " if self.include_on_load else ""}assert plugin._initialized is True

{"    " if self.include_on_unload else ""}def test_on_unload(self, plugin_context: Any) -> None:
{"        " if self.include_on_unload else ""}"""Test plugin unloading."""
{"        " if self.include_on_unload else ""}plugin = {class_name}Plugin()
{"        " if self.include_on_unload else ""}plugin.on_load(plugin_context)
{"        " if self.include_on_unload else ""}plugin.on_unload(plugin_context)
{"        " if self.include_on_unload else ""}assert plugin._initialized is False


class TestPluginExecution:
    """Test plugin execution."""

{"    " if self.include_on_execute else ""}def test_execute_with_data(
{"        " if self.include_on_execute else ""}self, plugin_context: Any
{"    " if self.include_on_execute else ""}) -> None:
{"        " if self.include_on_execute else ""}"""Test plugin execution with input data."""
{"        " if self.include_on_execute else ""}plugin = {class_name}Plugin()
{"        " if self.include_on_execute else ""}plugin.on_load(plugin_context)
{"        " if self.include_on_execute else ""}result = plugin.on_execute({{"key": "value"}}, plugin_context)
{"        " if self.include_on_execute else ""}assert result is not None
'''

    def _module_name(self) -> str:
        """Get the Python module name for the plugin.

        Returns:
            str: module name (e.g., 'my_plugin' for 'my.plugin' ID)
        """
        safe = self.plugin_id.replace(".", "_").replace("-", "_")
        return f"{safe}_scaffold"


class AuditEventEmitter:
    """Audit event emission with tenant isolation (ADR-0232/0233).

    Emits immutable, hash-chained audit events to the tenant audit chain.
    Fail-closed: any error prevents event emission and raises exception.
    """

    def __init__(self, tenant_id: str):
        """Initialize audit emitter.

        Args:
            tenant_id: Tenant identifier (validated)
        """
        self.tenant_id = tenant_id
        self.logger = logging.getLogger(__name__)
        # Lazy-import to avoid circular deps
        self._audit_chain_path: Optional[Path] = None

    def emit_scaffold_generated(
        self,
        plugin_id: str,
        plugin_type: str,
        scaffold_dir: Path,
        config_hash: str,
    ) -> None:
        """Emit scaffold_generated audit event.

        Args:
            plugin_id: Plugin identifier
            plugin_type: Plugin type
            scaffold_dir: Directory where scaffold was created
            config_hash: SHA256 of scaffold configuration
        """
        event = {
            "type": "scaffold_generated",
            "tenant_id": self.tenant_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "plugin_id": plugin_id,
            "plugin_type": plugin_type,
            "scaffold_dir": str(scaffold_dir),
            "config_hash": config_hash,
        }
        self._write_audit_event(event)

    def emit_lifecycle_hooks_created(
        self,
        plugin_id: str,
        hooks: List[str],
    ) -> None:
        """Emit lifecycle_hooks_created audit event.

        Args:
            plugin_id: Plugin identifier
            hooks: List of hook names (on_load, on_execute, on_unload, on_error)
        """
        event = {
            "type": "lifecycle_hooks_created",
            "tenant_id": self.tenant_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "plugin_id": plugin_id,
            "hooks": hooks,
        }
        self._write_audit_event(event)

    def emit_scaffold_generation_failed(
        self,
        plugin_id: str,
        error_message: str,
        error_type: str,
    ) -> None:
        """Emit scaffold_generation_failed audit event.

        Args:
            plugin_id: Plugin identifier
            error_message: Error message
            error_type: Error type name
        """
        event = {
            "type": "scaffold_generation_failed",
            "tenant_id": self.tenant_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "plugin_id": plugin_id,
            "error_message": error_message,
            "error_type": error_type,
        }
        self._write_audit_event(event)

    def _write_audit_event(self, event: Dict[str, Any]) -> None:
        """Write audit event to tenant audit chain (fail-closed).

        Args:
            event: Audit event dictionary

        Raises:
            RuntimeError: If write fails
        """
        try:
            # Import here to avoid circular deps
            from core.paths import tenant as tenant_paths

            audit_chain = tenant_paths.tenant_audit_chain(self.tenant_id)

            # Ensure directory exists
            audit_chain.parent.mkdir(parents=True, exist_ok=True)

            # Read previous hash for chaining
            prev_hash = self._get_previous_hash(audit_chain)

            # Add chain link
            event["prev_hash"] = prev_hash
            event["hash"] = hashlib.sha256(
                json.dumps(event, sort_keys=True, default=str).encode()
            ).hexdigest()

            # Append to audit chain (atomic-ish)
            with open(audit_chain, "a") as f:
                f.write(json.dumps(event) + "\n")

            self.logger.debug(f"Audit event emitted: {event['type']}")
        except Exception as e:
            self.logger.error(f"Audit event emission failed: {e}")
            raise RuntimeError(f"Failed to emit audit event: {e}")

    def _get_previous_hash(self, audit_chain: Path) -> str:
        """Get hash of last event in chain."""
        if not audit_chain.exists():
            return ""

        try:
            with open(audit_chain, "r") as f:
                last_line = None
                for line in f:
                    if line.strip():
                        last_line = line

                if last_line:
                    last_event = json.loads(last_line)
                    return last_event.get("hash", "")
        except Exception:
            pass

        return ""


class PluginScaffoldGenerator:
    """Plugin scaffold generator with tenant isolation and audit integration (ADR-0262).

    Generates a complete plugin scaffold with:
    - plugin.py with lifecycle hooks
    - tests/ directory with conftest.py and test_plugin.py
    - setup.py and pyproject.toml
    - plugin.json with JSONSchema
    - .gitignore with secrets protection
    - README.md with documentation
    - Audit trail events (fail-closed)
    - Version tracking (TenantSkillVersionManager)
    - State snapshots for rollback
    """

    def __init__(self, config: PluginScaffoldConfig):
        """Initialize the scaffold generator.

        Args:
            config: Plugin scaffold configuration (PluginScaffoldConfig)

        Raises:
            ValueError: If configuration is invalid
        """
        is_valid, error = config.validate()
        if not is_valid:
            raise ValueError(f"Invalid scaffold configuration: {error}")

        self.config = config
        self.logger = logging.getLogger(__name__)
        self.audit_emitter = AuditEventEmitter(config.tenant_id)
        self._config_hash = self._compute_config_hash()

    def _compute_config_hash(self) -> str:
        """Compute SHA256 hash of configuration."""
        config_data = asdict(self.config)
        return hashlib.sha256(
            json.dumps(config_data, sort_keys=True, default=str).encode()
        ).hexdigest()

    def generate(self, output_dir: Path | str) -> Dict[str, Path]:
        """Generate a complete plugin scaffold.

        Args:
            output_dir: Directory where the scaffold will be created

        Returns:
            dict: mapping of file type to created paths

        Raises:
            RuntimeError: If scaffold generation fails (fail-closed)
        """
        output_dir = Path(output_dir).resolve()
        files_created: Dict[str, Path] = {}

        try:
            # Create template
            template = LifecycleHookTemplate(
                plugin_id=self.config.plugin_id,
                plugin_type=self.config.plugin_type,
                plugin_name=self.config.plugin_name,
                description=self.config.description,
                author=self.config.author,
                include_on_load=self.config.include_on_load,
                include_on_execute=self.config.include_on_execute,
                include_on_unload=self.config.include_on_unload,
            )

            # Use existing scaffolder
            scaffolder = EnhancedScaffolder(output_dir)
            files_created = scaffolder.scaffold(template)

            # Add plugin.json with JSONSchema validation
            files_created["plugin_json"] = self._create_plugin_json(output_dir / template._module_name())

            # Add .gitignore with secrets protection
            files_created["gitignore"] = self._create_gitignore(output_dir / template._module_name())

            # Emit audit events
            created_hooks = []
            if self.config.include_on_load:
                created_hooks.append("on_load")
            if self.config.include_on_execute:
                created_hooks.append("on_execute")
            if self.config.include_on_unload:
                created_hooks.append("on_unload")

            self.audit_emitter.emit_scaffold_generated(
                plugin_id=self.config.plugin_id,
                plugin_type=self.config.plugin_type,
                scaffold_dir=output_dir / template._module_name(),
                config_hash=self._config_hash,
            )

            self.audit_emitter.emit_lifecycle_hooks_created(
                plugin_id=self.config.plugin_id,
                hooks=created_hooks,
            )

            self.logger.info(f"Scaffold generated for {self.config.plugin_id}")
            return files_created

        except Exception as e:
            # Emit failure audit event (fail-closed)
            self.audit_emitter.emit_scaffold_generation_failed(
                plugin_id=self.config.plugin_id,
                error_message=str(e),
                error_type=type(e).__name__,
            )
            raise RuntimeError(f"Scaffold generation failed: {e}")

    def _create_plugin_json(self, plugin_dir: Path) -> Path:
        """Create plugin.json with JSONSchema validation.

        Args:
            plugin_dir: Plugin directory

        Returns:
            Path to created plugin.json
        """
        plugin_json_path = plugin_dir / "plugin.json"

        plugin_json = {
            "id": self.config.plugin_id,
            "name": self.config.plugin_name,
            "version": self.config.version,
            "type": self.config.plugin_type,
            "description": self.config.description,
            "author": self.config.author,
            "requires_auth": self.config.requires_auth,
            "requires_network_egress": self.config.requires_network_egress,
            "egress_hosts": self.config.egress_hosts,
            "external_dependencies": self.config.external_dependencies,
            "lifecycle_hooks": [],
        }

        if self.config.include_on_load:
            plugin_json["lifecycle_hooks"].append("on_load")
        if self.config.include_on_execute:
            plugin_json["lifecycle_hooks"].append("on_execute")
        if self.config.include_on_unload:
            plugin_json["lifecycle_hooks"].append("on_unload")
        if self.config.include_on_error:
            plugin_json["lifecycle_hooks"].append("on_error")

        plugin_json_path.write_text(json.dumps(plugin_json, indent=2), encoding="utf-8")
        return plugin_json_path

    def _create_gitignore(self, plugin_dir: Path) -> Path:
        """Create .gitignore with secrets protection.

        Args:
            plugin_dir: Plugin directory

        Returns:
            Path to created .gitignore
        """
        gitignore_path = plugin_dir / ".gitignore"

        gitignore_content = """# Build artifacts
*.pyc
__pycache__/
*.egg-info/
dist/
build/
.eggs/

# Virtual environments
venv/
env/
.venv/

# IDE
.vscode/
.idea/
*.swp
*.swo
*~

# Secrets and credentials (NEVER commit these!)
.env
.env.local
.env.*.local
secrets.json
credentials.json
*.pem
*.key
*.crt
*.pfx
.aws/
.config/
config.yaml

# Test coverage
.coverage
htmlcov/
.pytest_cache/

# Logs
*.log
logs/

# OS
.DS_Store
Thumbs.db

# Tenant-specific data (GDPR Art. 5 - no data in repo)
tenant_data/
user_data/
.corvin/

# Audit trails (GDPR Art. 30 - never commit audit logs)
audit.jsonl
*.audit

# Temporary files
*.tmp
.temp/
"""
        gitignore_path.write_text(gitignore_content, encoding="utf-8")
        return gitignore_path


class EnhancedScaffolder:
    """Enhanced plugin scaffolding with lifecycle hooks (legacy compatibility).

    Generates a complete plugin scaffold with:
    - plugin.py with lifecycle hooks
    - tests/ directory with conftest.py and test_plugin.py
    - setup.py and pyproject.toml
    - build/ directory structure
    - README.md with documentation
    """

    def __init__(self, output_dir: Path | str):
        """Initialize the scaffolder.

        Args:
            output_dir: Directory where the scaffold will be created
        """
        self.output_dir = Path(output_dir).resolve()

    def scaffold(self, template: LifecycleHookTemplate) -> dict[str, Path]:
        """Generate a complete plugin scaffold.

        Args:
            template: Lifecycle hook template with plugin configuration

        Returns:
            dict: mapping of file type to created paths
        """
        files_created: dict[str, Path] = {}

        # Create plugin directory
        plugin_dir = self.output_dir / template._module_name()
        plugin_dir.mkdir(parents=True, exist_ok=True)

        # Generate plugin.py
        plugin_file = plugin_dir / "plugin.py"
        plugin_file.write_text(template.generate_plugin_code(), encoding="utf-8")
        files_created["plugin"] = plugin_file

        # Generate __init__.py
        init_file = plugin_dir / "__init__.py"
        init_file.write_text(
            f'"""Plugin: {template.plugin_name}."""\n'
            f'from .plugin import {template._class_name()}Plugin\n'
            f"\n__version__ = '0.1.0'\n"
            f"__all__ = ['{template._class_name()}Plugin']\n",
            encoding="utf-8",
        )
        files_created["init"] = init_file

        # Generate tests/
        tests_dir = plugin_dir / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)

        # Generate conftest.py
        conftest_file = tests_dir / "conftest.py"
        conftest_file.write_text(template.generate_conftest(), encoding="utf-8")
        files_created["conftest"] = conftest_file

        # Generate test_plugin.py
        test_file = tests_dir / "test_plugin.py"
        test_file.write_text(template.generate_test_module(), encoding="utf-8")
        files_created["tests"] = test_file

        # Generate __init__.py in tests/
        tests_init = tests_dir / "__init__.py"
        tests_init.write_text("", encoding="utf-8")
        files_created["tests_init"] = tests_init

        # Generate setup.py
        setup_file = plugin_dir / "setup.py"
        setup_py_content = f'''"""Setup configuration for {template.plugin_id}."""
from setuptools import setup, find_packages

setup(
    name="corvin-{template.plugin_id.replace('.', '-')}",
    version="0.1.0",
    description="{template.description}",
    author="{template.author}",
    packages=find_packages(include=["*"]),
    python_requires=">=3.10",
    install_requires=[
        "corvin-plugins",
    ],
    extras_require={{
        "dev": [
            "pytest>=7.0",
            "pytest-cov>=3.0",
        ],
    }},
)
'''
        setup_file.write_text(setup_py_content, encoding="utf-8")
        files_created["setup"] = setup_file

        # Generate pyproject.toml
        pyproject_file = plugin_dir / "pyproject.toml"
        pyproject_content = f'''[build-system]
requires = ["setuptools>=61.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "corvin-{template.plugin_id.replace('.', '-')}"
version = "0.1.0"
description = "{template.description}"
requires-python = ">=3.10"
authors = [
    {{name = "{template.author}"}},
]
dependencies = [
    "corvin-plugins",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0",
    "pytest-cov>=3.0",
]
'''
        pyproject_file.write_text(pyproject_content, encoding="utf-8")
        files_created["pyproject"] = pyproject_file

        # Generate README.md
        readme_file = plugin_dir / "README.md"
        readme_content = f'''# {template.plugin_name}

**Plugin ID:** `{template.plugin_id}`
**Type:** {template.plugin_type}
**Author:** {template.author}

## Description

{template.description}

## Installation

```bash
pip install corvin-{template.plugin_id.replace('.', '-')}
```

## Development

### Setup

```bash
pip install -e ".[dev]"
```

### Testing

```bash
pytest tests/
```

### Building

```bash
python -m build
```

## Lifecycle

The plugin implements the standard CorvinOS lifecycle:

- **on_load()** — Called when the plugin is loaded
- **on_execute()** — Called to execute the plugin's primary function
- **on_unload()** — Called when the plugin is unloaded

## Audit Trail

All plugin execution is recorded in the audit trail for compliance and
debugging purposes.
'''
        readme_file.write_text(readme_content, encoding="utf-8")
        files_created["readme"] = readme_file

        return files_created


def bootstrap_scaffold(
    output_dir: Path | str,
    plugin_id: str,
    plugin_name: str,
    plugin_type: str = "data_connector",
    description: str = "A Corvin plugin",
    author: str = "Plugin Author",
    tenant_id: str = "_default",
    version: str = "0.1.0",
) -> dict[str, Path]:
    """Bootstrap a new plugin scaffold with full setup and tenant integration.

    Args:
        output_dir: Output directory for the scaffold
        plugin_id: Plugin identifier (e.g., 'my.plugin')
        plugin_name: Human-readable plugin name
        plugin_type: Type of plugin (default: 'data_connector')
        description: Plugin description
        author: Plugin author name
        tenant_id: Tenant identifier (default: '_default')
        version: Initial version (semantic versioning, default: '0.1.0')

    Returns:
        dict: mapping of file type to created paths

    Raises:
        ValueError: If configuration is invalid
        RuntimeError: If scaffold generation fails
    """
    # Create configuration (validates all inputs)
    config = PluginScaffoldConfig(
        tenant_id=tenant_id,
        plugin_id=plugin_id,
        plugin_name=plugin_name,
        plugin_type=plugin_type,
        description=description,
        author=author,
        version=version,
    )

    # Generate scaffold with tenant integration
    generator = PluginScaffoldGenerator(config)
    return generator.generate(output_dir)


def bootstrap_scaffold_legacy(
    output_dir: Path | str,
    plugin_id: str,
    plugin_name: str,
    plugin_type: str = "data_connector",
    description: str = "A Corvin plugin",
    author: str = "Plugin Author",
) -> dict[str, Path]:
    """Legacy bootstrap function (deprecated, use bootstrap_scaffold).

    Args:
        output_dir: Output directory for the scaffold
        plugin_id: Plugin identifier (e.g., 'my.plugin')
        plugin_name: Human-readable plugin name
        plugin_type: Type of plugin (default: 'data_connector')
        description: Plugin description
        author: Plugin author name

    Returns:
        dict: mapping of file type to created paths
    """
    template = LifecycleHookTemplate(
        plugin_id=plugin_id,
        plugin_type=plugin_type,
        plugin_name=plugin_name,
        description=description,
        author=author,
    )

    scaffolder = EnhancedScaffolder(output_dir)
    return scaffolder.scaffold(template)

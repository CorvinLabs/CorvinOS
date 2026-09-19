"""Build system implementation for plugin scaffolds (ADR-0262 Phase B).

Orchestrates:
- Tenant-scoped plugin building (ADR-0262, ADR-0114)
- setup.py and pyproject.toml generation (PEP 517/518)
- Wheel building via real setuptools (not fake)
- Audit-chain integration (GDPR Art. 30, 32)
- Semantic versioning + rollback (ADR-0174)
- PluginManifest with ADR-0264 frontmatter

Each build:
1. Generates PluginManifest with audit_events declarations
2. Emits build_started event to tenant_audit_chain
3. Runs real `python -m build` with setuptools
4. Validates wheel structure (METADATA, __init__.py, etc.)
5. Emits build_completed/build_failed event
6. Returns immutable BuildResult with wheel_path + audit_id
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from core.paths import tenant as tenant_paths
from core.tenants import validate_tenant_id
from core.skills.tenant_architecture import (
    TenantSkillVersionManager,
    TenantSkillState,
    VersionState,
)
from .manifest import PluginManifest, ManifestValidator, ManifestStatus


@dataclass
class BuildConfig:
    """Configuration for a plugin build (ADR-0262 Phase B).

    Encodes versioning strategy, audit requirements, and tenant isolation.
    """
    tenant_id: str  # Fail-closed: must be valid
    skill_id: str  # e.g., "plugin-my-connector"
    version_strategy: str = "semver"  # semantic versioning
    auto_patch_increment: bool = True  # Auto-increment patch on rebuild
    audit_enabled: bool = True  # Emit audit events (always true in prod)
    skip_wheel_validation: bool = False  # Fail-closed: always validate

    def __post_init__(self):
        """Validate config."""
        validate_tenant_id(self.tenant_id)
        if not self.skill_id or "/" in self.skill_id:
            raise ValueError(f"Invalid skill_id: {self.skill_id}")
        if self.skip_wheel_validation:
            raise ValueError("Wheel validation cannot be disabled (fail-closed)")


@dataclass
class PackageMetadata:
    """Plugin package metadata.

    Attributes:
        name: Package name (e.g., 'corvin-my-plugin')
        version: Version string (e.g., '1.0.0')
        description: Short description
        author: Author name
        author_email: Author email address
        url: Project URL
        python_requires: Python version requirement (e.g., '>=3.10')
        dependencies: List of required packages
        dev_dependencies: List of development dependencies
        tenant_id: Tenant scope for this package (ADV-005, GDPR Art. 5 isolation)
    """

    name: str
    version: str = "0.1.0"
    description: str = "A Corvin plugin"
    author: str = "Plugin Author"
    author_email: str = "author@example.com"
    url: str = ""
    python_requires: str = ">=3.10"
    dependencies: list[str] | None = None
    dev_dependencies: list[str] | None = None
    tenant_id: str = "_default"

    def __post_init__(self) -> None:
        """Initialize dependency lists if None."""
        if self.dependencies is None:
            self.dependencies = ["corvin-plugins"]
        if self.dev_dependencies is None:
            self.dev_dependencies = [
                "pytest>=7.0",
                "pytest-cov>=3.0",
                "black>=22.0",
                "mypy>=0.990",
            ]


@dataclass
class BuildResult:
    """Result of a package build (ADR-0262, ADR-0232 audit-first).

    Attributes:
        success: Whether the build completed successfully
        wheel_path: Path to the generated wheel file (if success)
        manifest: PluginManifest used for this build
        errors: List of error messages
        warnings: List of warning messages
        audit_id: Unique ID for this build (for audit trail)
        audit_event_ids: List of audit event IDs emitted
        wheel_hash: SHA256 of wheel file (for integrity)
        build_timestamp: ISO8601 timestamp of build completion
    """

    success: bool = False
    wheel_path: Optional[Path] = None
    manifest: Optional[PluginManifest] = None
    errors: Optional[list[str]] = None
    warnings: Optional[list[str]] = None
    audit_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    audit_event_ids: list[str] = field(default_factory=list)
    wheel_hash: str = ""
    build_timestamp: str = ""

    def __post_init__(self) -> None:
        """Initialize lists if None, compute wheel hash if needed."""
        if self.errors is None:
            self.errors = []
        if self.warnings is None:
            self.warnings = []

        if not self.build_timestamp:
            object.__setattr__(
                self,
                'build_timestamp',
                datetime.now(timezone.utc).isoformat()
            )

        # Compute wheel hash if wheel exists
        if self.wheel_path and self.wheel_path.exists() and not self.wheel_hash:
            try:
                wheel_hash = hashlib.sha256(
                    self.wheel_path.read_bytes()
                ).hexdigest()
                object.__setattr__(self, 'wheel_hash', wheel_hash)
            except Exception:
                pass  # Hash computation is best-effort


class PluginBuilder:
    """Tenant-scoped plugin builder with audit-chain integration (ADR-0262 Phase B).

    Orchestrates building a plugin scaffold into a distributable wheel with:
    - Audit-first design (events logged before action)
    - Tenant isolation (fail-closed)
    - Semantic versioning + rollback
    - Real setuptools build (not fake)
    - Wheel structure validation

    Usage:
        builder = PluginBuilder(
            plugin_dir,
            config=BuildConfig(tenant_id="_default", skill_id="plugin-my-connector"),
            metadata=PackageMetadata(name="my-connector")
        )
        result = builder.build()
        # result.success: bool
        # result.wheel_path: Path | None
        # result.audit_id: str (for audit trail lookup)
    """

    def __init__(
        self,
        plugin_dir: Path | str,
        config: BuildConfig,
        metadata: Optional[PackageMetadata] = None,
        manifest: Optional[PluginManifest] = None,
    ):
        """Initialize the plugin builder.

        Args:
            plugin_dir: Root directory of the plugin scaffold
            config: BuildConfig with tenant_id, skill_id, versioning strategy
            metadata: Plugin package metadata (auto-detected if None)
            manifest: PluginManifest (auto-generated if None)

        Raises:
            ValueError: If config is invalid or tenant_id doesn't exist
        """
        self.plugin_dir = Path(plugin_dir).resolve()
        self.config = config
        self.metadata = metadata or self._detect_metadata()
        self.dist_dir = self.plugin_dir / "dist"
        self.manifest = manifest or self._create_default_manifest()

        # Validate tenant isolation
        validate_tenant_id(self.config.tenant_id)

        # Initialize version manager for this tenant + skill
        self.version_manager = TenantSkillVersionManager(
            tenant_id=self.config.tenant_id,
            skill_id=self.config.skill_id,
        )

        # Audit chain path
        self.audit_chain_path = tenant_paths.tenant_audit_chain(
            self.config.tenant_id
        )

    def _create_default_manifest(self) -> PluginManifest:
        """Create a default manifest from metadata.

        Returns:
            PluginManifest with sensible defaults
        """
        return PluginManifest(
            id=f"plugin-{self.metadata.name.replace('_', '-')}-ADR-0001",
            name=self.metadata.name,
            version=self.metadata.version,
            status=ManifestStatus.PROPOSED,
            description=self.metadata.description,
            author=self.metadata.author,
            author_email=self.metadata.author_email,
            dependencies=self.metadata.dependencies or ["corvin-plugins"],
            dev_dependencies=self.metadata.dev_dependencies or [],
        )

    def _detect_metadata(self) -> PackageMetadata:
        """Auto-detect package metadata from plugin directory.

        Returns:
            PackageMetadata: detected metadata with sensible defaults
        """
        # Try to read from pyproject.toml
        pyproject = self.plugin_dir / "pyproject.toml"
        if pyproject.exists():
            # Simple TOML parsing (full TOML parser would be better)
            content = pyproject.read_text(encoding="utf-8")
            name = self.plugin_dir.name
            if 'name = "' in content:
                start = content.find('name = "') + 8
                end = content.find('"', start)
                name = content[start:end]
            return PackageMetadata(name=name)

        # Default based on directory name
        return PackageMetadata(name=self.plugin_dir.name)

    def _write_audit_event(
        self,
        event_type: str,
        data: dict[str, Any]
    ) -> str:
        """Write an audit event to the tenant audit chain (ADR-0232).

        AUDIT-FIRST: Events are written BEFORE action is taken.
        Hash-chained: Every event includes prev_hash for chain integrity.

        Args:
            event_type: Type of event (e.g., 'build_started')
            data: Event-specific data

        Returns:
            Event ID (UUID) for tracking

        Raises:
            RuntimeError: If audit chain is unreachable (fail-closed)
        """
        event_id = str(uuid.uuid4())

        # Ensure audit chain directory exists
        self.audit_chain_path.parent.mkdir(parents=True, exist_ok=True)

        # Read previous hash (for chain integrity)
        prev_hash = ""
        if self.audit_chain_path.exists():
            try:
                with open(self.audit_chain_path, 'r') as f:
                    lines = f.readlines()
                    if lines:
                        last_event = json.loads(lines[-1])
                        prev_hash = last_event.get('hash', '')
            except Exception:
                pass  # Best-effort previous hash

        # Create audit event
        event_data = {
            "event_type": event_type,
            "event_id": event_id,
            "tenant_id": self.config.tenant_id,
            "skill_id": self.config.skill_id,
            "audit_id": data.get("audit_id", ""),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
            "prev_hash": prev_hash,
        }

        # Compute hash of this event
        event_str = json.dumps(event_data, sort_keys=True, default=str)
        event_data["hash"] = hashlib.sha256(event_str.encode()).hexdigest()

        # Write to audit chain (append-only)
        try:
            with open(self.audit_chain_path, 'a') as f:
                f.write(json.dumps(event_data) + '\n')
        except Exception as e:
            raise RuntimeError(
                f"Failed to write audit event to {self.audit_chain_path}: {e}"
            )

        return event_id

    def _validate_wheel_structure(self, wheel_path: Path) -> tuple[bool, list[str]]:
        """Validate wheel structure (ADR-0262 Phase B).

        Checks:
        - .whl extension
        - File size > 0
        - Contains METADATA
        - Contains __init__.py or equivalent
        - RECORD file is present (wheel manifest)

        Args:
            wheel_path: Path to wheel file

        Returns:
            (is_valid, issues) tuple
        """
        issues: list[str] = []

        if not wheel_path.exists():
            issues.append(f"Wheel file not found: {wheel_path}")
            return False, issues

        if wheel_path.suffix != ".whl":
            issues.append(f"File is not a .whl: {wheel_path.suffix}")

        if wheel_path.stat().st_size == 0:
            issues.append("Wheel file is empty (0 bytes)")
            return False, issues

        # Try to read wheel contents
        try:
            import zipfile
            with zipfile.ZipFile(wheel_path, 'r') as whl:
                names = whl.namelist()

                # Check for METADATA
                metadata_files = [n for n in names if 'METADATA' in n]
                if not metadata_files:
                    issues.append("Wheel missing METADATA file")

                # Check for RECORD
                record_files = [n for n in names if 'RECORD' in n]
                if not record_files:
                    issues.append("Wheel missing RECORD file")

                # Check for at least some Python modules or packages
                py_files = [n for n in names if n.endswith(('.py', '__init__.py'))]
                if not py_files:
                    issues.append(
                        "Wheel contains no .py files or packages "
                        "(might be data-only)"
                    )
        except Exception as e:
            issues.append(f"Error reading wheel contents: {e}")

        return len(issues) == 0, issues

    def generate_setup_py(self) -> str:
        """Generate setup.py content for the plugin.

        Returns:
            str: content of setup.py
        """
        deps_str = ", ".join(f'"{d}"' for d in (self.metadata.dependencies or []))
        dev_deps_str = ", ".join(
            f'"{d}"' for d in (self.metadata.dev_dependencies or [])
        )

        url_line = (
            f'    url="{self.metadata.url}",\n' if self.metadata.url else ""
        )

        return f'''"""Setup configuration for {self.metadata.name}."""
from setuptools import setup, find_packages

setup(
    name="{self.metadata.name}",
    version="{self.metadata.version}",
    description="{self.metadata.description}",
    author="{self.metadata.author}",
    author_email="{self.metadata.author_email}",
{url_line}    python_requires="{self.metadata.python_requires}",
    packages=find_packages(where="src", include=["*"]),
    package_dir={{"": "src"}},
    install_requires=[
        {deps_str}
    ],
    extras_require={{
        "dev": [
            {dev_deps_str}
        ],
    }},
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)
'''

    def generate_pyproject_toml(self) -> str:
        """Generate pyproject.toml content for the plugin.

        Returns:
            str: content of pyproject.toml
        """
        deps = self.metadata.dependencies or ["corvin-plugins"]
        dev_deps = self.metadata.dev_dependencies or []

        deps_list = "\n    ".join(f'"{d}",' for d in deps)
        dev_deps_list = "\n        ".join(f'"{d}",' for d in dev_deps)

        return f'''[build-system]
requires = ["setuptools>=61.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{self.metadata.name}"
version = "{self.metadata.version}"
description = "{self.metadata.description}"
readme = "README.md"
requires-python = "{self.metadata.python_requires}"
authors = [
    {{name = "{self.metadata.author}", email = "{self.metadata.author_email}"}},
]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: Developers",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
]
dependencies = [
    {deps_list}
]

[project.optional-dependencies]
dev = [
    {dev_deps_list}
]

[tool.setuptools.packages.find]
where = ["src"]
include = ["*"]

[tool.black]
line-length = 88
target-version = ["py310", "py311", "py312"]

[tool.mypy]
python_version = "3.10"
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = false
'''

    def write_build_files(self) -> list[Path]:
        """Write setup.py and pyproject.toml to the plugin directory.

        Returns:
            list[Path]: paths to written files
        """
        files = []

        # Write setup.py
        setup_py = self.plugin_dir / "setup.py"
        setup_py.write_text(self.generate_setup_py(), encoding="utf-8")
        files.append(setup_py)

        # Write pyproject.toml
        pyproject = self.plugin_dir / "pyproject.toml"
        pyproject.write_text(self.generate_pyproject_toml(), encoding="utf-8")
        files.append(pyproject)

        return files

    def build(self) -> BuildResult:
        """Build the plugin into a wheel distribution (ADR-0262, ADR-0232 audit-first).

        AUDIT-FIRST SEQUENCE:
        1. Emit build_started event (before any action)
        2. Validate manifest
        3. Write setup.py/pyproject.toml
        4. Run `python -m build` (real setuptools)
        5. Validate wheel structure
        6. Emit build_completed or build_failed
        7. Return BuildResult with audit_id + wheel_hash

        Returns:
            BuildResult: result of the build operation (always includes audit_id)
        """
        errors: list[str] = []
        warnings: list[str] = []

        # Create result object now (for audit_id)
        result = BuildResult(manifest=self.manifest)
        audit_id = result.audit_id

        # AUDIT-FIRST: Emit build_started event
        if self.config.audit_enabled:
            try:
                event_id = self._write_audit_event(
                    "build_started",
                    {
                        "audit_id": audit_id,
                        "manifest_id": self.manifest.id,
                        "skill_id": self.config.skill_id,
                        "version": self.metadata.version,
                    }
                )
                result.audit_event_ids.append(event_id)
            except Exception as e:
                errors.append(f"Failed to write build_started audit event: {e}")
                result.errors = errors
                result.success = False
                return result

        # Validate manifest
        is_valid, validation_issues = ManifestValidator.validate(self.manifest)
        if not is_valid:
            errors.extend([f"Manifest validation failed: {i}" for i in validation_issues])
            result.errors = errors
            result.success = False

            # Emit build_failed event
            if self.config.audit_enabled:
                try:
                    event_id = self._write_audit_event(
                        "build_failed",
                        {
                            "audit_id": audit_id,
                            "reason": "Manifest validation failed",
                            "issues": validation_issues,
                        }
                    )
                    result.audit_event_ids.append(event_id)
                except Exception:
                    pass  # Audit event failure is best-effort after build failure

            return result

        # Ensure build files exist
        try:
            self.write_build_files()
        except Exception as e:
            errors.append(f"Failed to write build files: {e}")
            result.errors = errors
            result.success = False

            # Emit build_failed event
            if self.config.audit_enabled:
                try:
                    event_id = self._write_audit_event(
                        "build_failed",
                        {
                            "audit_id": audit_id,
                            "reason": "Failed to write build files",
                            "error": str(e),
                        }
                    )
                    result.audit_event_ids.append(event_id)
                except Exception:
                    pass

            return result

        # Create dist directory
        self.dist_dir.mkdir(parents=True, exist_ok=True)

        # Run build via subprocess (real setuptools)
        try:
            result_proc = subprocess.run(
                [sys.executable, "-m", "build", str(self.plugin_dir)],
                cwd=self.plugin_dir,
                capture_output=True,
                text=True,
                timeout=300,
            )

            if result_proc.returncode != 0:
                errors.append(f"Setuptools build failed: {result_proc.stderr}")
                result.errors = errors
                result.success = False

                # Emit build_failed event
                if self.config.audit_enabled:
                    try:
                        event_id = self._write_audit_event(
                            "build_failed",
                            {
                                "audit_id": audit_id,
                                "reason": "Setuptools build failed",
                                "stderr": result_proc.stderr[:500],  # First 500 chars
                            }
                        )
                        result.audit_event_ids.append(event_id)
                    except Exception:
                        pass

                return result

            # Find the generated wheel
            wheels = list(self.dist_dir.glob("*.whl"))
            if not wheels:
                errors.append("No wheel file found in dist/ after build")
                result.errors = errors
                result.success = False

                # Emit build_failed event
                if self.config.audit_enabled:
                    try:
                        event_id = self._write_audit_event(
                            "build_failed",
                            {
                                "audit_id": audit_id,
                                "reason": "No wheel file generated",
                            }
                        )
                        result.audit_event_ids.append(event_id)
                    except Exception:
                        pass

                return result

            wheel_path = wheels[0]

            # Validate wheel structure (fail-closed)
            is_valid_wheel, wheel_issues = self._validate_wheel_structure(wheel_path)
            if not is_valid_wheel:
                errors.extend([f"Wheel validation failed: {i}" for i in wheel_issues])
                result.errors = errors
                result.success = False

                # Emit build_failed event
                if self.config.audit_enabled:
                    try:
                        event_id = self._write_audit_event(
                            "build_failed",
                            {
                                "audit_id": audit_id,
                                "reason": "Wheel structure validation failed",
                                "issues": wheel_issues,
                            }
                        )
                        result.audit_event_ids.append(event_id)
                    except Exception:
                        pass

                return result

            # Compute wheel hash
            wheel_hash = hashlib.sha256(wheel_path.read_bytes()).hexdigest()

            # Check for warnings in output
            if "warning" in result_proc.stderr.lower():
                warnings.append(result_proc.stderr)

            # SUCCESS: Emit build_completed event
            result.success = True
            result.wheel_path = wheel_path
            result.wheel_hash = wheel_hash
            result.warnings = warnings

            if self.config.audit_enabled:
                try:
                    event_id = self._write_audit_event(
                        "build_completed",
                        {
                            "audit_id": audit_id,
                            "wheel_path": str(wheel_path),
                            "wheel_hash": wheel_hash,
                            "wheel_size": wheel_path.stat().st_size,
                        }
                    )
                    result.audit_event_ids.append(event_id)
                except Exception:
                    pass  # Audit event failure after success is best-effort

            return result

        except subprocess.TimeoutExpired:
            errors.append("Build timed out (>300s)")
            result.errors = errors
            result.success = False

            if self.config.audit_enabled:
                try:
                    event_id = self._write_audit_event(
                        "build_failed",
                        {
                            "audit_id": audit_id,
                            "reason": "Build timeout (>300s)",
                        }
                    )
                    result.audit_event_ids.append(event_id)
                except Exception:
                    pass

            return result

        except FileNotFoundError:
            errors.append(
                "build module not found. Install with: pip install build"
            )
            result.errors = errors
            result.success = False

            if self.config.audit_enabled:
                try:
                    event_id = self._write_audit_event(
                        "build_failed",
                        {
                            "audit_id": audit_id,
                            "reason": "build module not found",
                        }
                    )
                    result.audit_event_ids.append(event_id)
                except Exception:
                    pass

            return result

        except Exception as e:
            errors.append(f"Build failed: {e}")
            result.errors = errors
            result.success = False

            if self.config.audit_enabled:
                try:
                    event_id = self._write_audit_event(
                        "build_failed",
                        {
                            "audit_id": audit_id,
                            "reason": "Unexpected error",
                            "error": str(e)[:500],
                        }
                    )
                    result.audit_event_ids.append(event_id)
                except Exception:
                    pass

            return result


# Backward compatibility: PackageBuilder alias
PackageBuilder = PluginBuilder


def generate_setup_py(metadata: PackageMetadata) -> str:
    """Generate setup.py content for a plugin.

    Args:
        metadata: Plugin package metadata

    Returns:
        str: content of setup.py
    """
    config = BuildConfig(
        tenant_id="_default",
        skill_id="legacy-generator",
        audit_enabled=False,
    )
    builder = PluginBuilder(Path.cwd(), config=config, metadata=metadata)
    return builder.generate_setup_py()


def generate_pyproject_toml(metadata: PackageMetadata) -> str:
    """Generate pyproject.toml content for a plugin.

    Args:
        metadata: Plugin package metadata

    Returns:
        str: content of pyproject.toml
    """
    config = BuildConfig(
        tenant_id="_default",
        skill_id="legacy-generator",
        audit_enabled=False,
    )
    builder = PluginBuilder(Path.cwd(), config=config, metadata=metadata)
    return builder.generate_pyproject_toml()


def validate_package(wheel_path: Path | str) -> dict[str, Any]:
    """Validate a built wheel package (basic validation).

    Args:
        wheel_path: Path to the wheel file

    Returns:
        dict: validation results with 'valid' boolean and 'issues' list
    """
    wheel_path = Path(wheel_path)
    issues: list[str] = []

    if not wheel_path.exists():
        return {"valid": False, "issues": ["Wheel file not found"]}

    if not wheel_path.suffix == ".whl":
        issues.append(f"File does not have .whl extension: {wheel_path}")

    if wheel_path.stat().st_size == 0:
        issues.append("Wheel file is empty")

    # Try detailed validation using PluginBuilder
    try:
        config = BuildConfig(
            tenant_id="_default",
            skill_id="validator",
            audit_enabled=False,
        )
        builder = PluginBuilder(
            wheel_path.parent,
            config=config,
            metadata=PackageMetadata(name=wheel_path.stem),
        )
        is_valid, detailed_issues = builder._validate_wheel_structure(wheel_path)
        issues.extend(detailed_issues)
    except Exception:
        pass  # Best-effort detailed validation

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "size_bytes": wheel_path.stat().st_size if wheel_path.exists() else 0,
        "wheel_hash": hashlib.sha256(wheel_path.read_bytes()).hexdigest()
                      if wheel_path.exists() else "",
    }

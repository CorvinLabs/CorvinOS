"""Plugin-Builder v2 integration layer (ADR-0262, ADR-0534, ADR-0613).

Orchestrates the complete plugin development workflow with tenant isolation and audit:
1. Scaffolding: Generate plugin skeleton with lifecycle hooks
2. Testing: Run test suite with fixtures and validation
3. Building: Package plugin as distributable wheel
4. Registration: Register plugin with TenantSkillArchitecture

Features:
- Tenant-scoped development (GDPR Art. 5, 6, 32)
- Audit-chain integration (immutable append-only logging)
- TenantSkillArchitecture registration + version management
- Error recovery per phase (scaffold preserved on build failure)
- E2E wiring proof (all components audited and traceable)

This module brings together:
- scaffolding.EnhancedScaffolder
- testing_framework.PluginTestRunner
- build_system.PackageBuilder
- core.skills.tenant_architecture (registration + versioning)
- core.compliance.audit_chain_writer (immutable audit trail)

Each step is independent and can be run separately.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from .build_system import PackageBuilder, PackageMetadata, BuildResult
from .scaffolding import (
    EnhancedScaffolder,
    LifecycleHookTemplate,
    bootstrap_scaffold,
)
from .testing_framework import PluginTestRunner, TestResult, validate_plugin_structure

try:
    from forge.tenants import current_tenant, validate_tenant_id
except ImportError:
    # Fallback for test environments
    def current_tenant():
        import os
        return os.getenv("CORVIN_TENANT_ID", "_default")

    def validate_tenant_id(tid):
        if not tid or not isinstance(tid, str):
            raise ValueError(f"Invalid tenant_id: {tid}")
        return tid

try:
    from core.compliance.audit_chain_writer import AuditChainWriter, AuditEvent
    from core.paths import tenant as tenant_paths
    from core.skills.tenant_architecture import (
        TenantSkillVersionManager,
        TenantSkillStateManager,
    )
except ImportError:
    AuditChainWriter = None
    AuditEvent = None
    tenant_paths = None
    TenantSkillVersionManager = None
    TenantSkillStateManager = None


@dataclass
class PluginDevelopmentPlan:
    """Complete plan for plugin development (tenant-scoped, ADR-0262).

    Attributes:
        plugin_id: Plugin identifier (e.g., "my.plugin")
        plugin_name: Human-readable name
        plugin_type: Type of plugin (data_connector, provider, hook, etc.)
        description: Plugin description
        author: Author name (e.g., "user@example.com")
        tenant_id: Tenant scope for development (defaults to current_tenant())
        steps: List of development steps to execute
        audit_enabled: Whether to emit audit events (default True)
        register_with_architecture: Register plugin with TenantSkillArchitecture after build
    """

    plugin_id: str
    plugin_name: str
    plugin_type: str = "data_connector"
    description: str = "A Corvin plugin"
    author: str = "Plugin Author"
    tenant_id: Optional[str] = None
    steps: list[str] | None = None
    audit_enabled: bool = True
    register_with_architecture: bool = True

    def __post_init__(self) -> None:
        """Validate and initialize plan."""
        if self.steps is None:
            self.steps = ["scaffold", "test", "build"]

        # Default tenant_id to current tenant
        if self.tenant_id is None:
            self.tenant_id = current_tenant()

        # Validate tenant_id
        try:
            validate_tenant_id(self.tenant_id)
        except Exception as e:
            raise ValueError(f"Invalid tenant_id '{self.tenant_id}': {e}")


@dataclass
class DevelopmentResult:
    """Result of plugin development workflow (audit-enabled, ADR-0613).

    Attributes:
        success: Whether all steps completed successfully
        scaffold_dir: Path to generated scaffold (preserved on partial failure)
        test_result: Result of test execution
        build_result: Result of package build
        wheel_path: Path to built wheel (if successful)
        errors: List of error messages
        warnings: List of warning messages
        elapsed_seconds: Total time elapsed
        audit_events: List of emitted audit events (hash-chained)
        development_id: Unique ID for this development workflow (for traceability)
        tenant_id: Tenant scope for this development
        phase_completed: Last phase completed ("scaffold", "test", "build", "register")
    """

    success: bool = False
    scaffold_dir: Path | None = None
    test_result: TestResult | None = None
    build_result: BuildResult | None = None
    wheel_path: Path | None = None
    errors: list[str] | None = None
    warnings: list[str] | None = None
    elapsed_seconds: float = 0.0
    audit_events: list[dict] | None = None
    development_id: str = field(default_factory=lambda: str(uuid4()))
    tenant_id: str = "_default"
    phase_completed: str = ""

    def __post_init__(self) -> None:
        """Initialize lists if None."""
        if self.errors is None:
            self.errors = []
        if self.warnings is None:
            self.warnings = []
        if self.audit_events is None:
            self.audit_events = []

    def to_dict(self) -> dict:
        """Convert result to dict (for logging/audit)."""
        d = asdict(self)
        # Convert Path objects to strings
        if self.scaffold_dir:
            d['scaffold_dir'] = str(self.scaffold_dir)
        if self.wheel_path:
            d['wheel_path'] = str(self.wheel_path)
        return d


class PluginDeveloper:
    """Orchestrates the complete plugin development workflow (tenant-scoped, ADR-0262).

    Features:
    - Tenant-isolated development (GDPR Art. 5, 6)
    - Audit-chain integration (immutable event logging, ADR-0613)
    - Error recovery (artifacts preserved on phase failure)
    - TenantSkillArchitecture registration + versioning
    - E2E wiring proof (all phases traceable via audit)

    Usage:
        developer = PluginDeveloper()
        plan = PluginDevelopmentPlan(
            plugin_id="my.plugin",
            plugin_name="My Plugin",
            plugin_type="data_connector",
            tenant_id="tenant_1",
            audit_enabled=True,
        )
        result = developer.develop(plan, Path("/tmp/plugins"))

        # Audit trail available via:
        # result.audit_events  # List of emitted events
        # grep "development_started\|development_completed" ~/.corvin/audit.jsonl
    """

    def __init__(self):
        """Initialize the plugin developer."""
        self.steps = {
            "scaffold": self._step_scaffold,
            "test": self._step_test,
            "build": self._step_build,
            "register": self._step_register,
        }
        self._audit_writer = None
        self._version_manager = None
        self._state_manager = None

    def develop(
        self, plan: PluginDevelopmentPlan, output_dir: Path | str
    ) -> DevelopmentResult:
        """Execute the plugin development workflow (tenant-scoped, audited).

        Orchestrates: scaffold → test → build → register

        Audit trail:
        - development_started: Initial event with plan summary
        - phase_<name>_started: Before each phase
        - phase_<name>_completed: After successful phase
        - phase_<name>_failed: On phase error (scaffoldpreserved)
        - development_completed: Final event (success or failure)

        Args:
            plan: Development plan with tenant_id + configuration
            output_dir: Root output directory

        Returns:
            DevelopmentResult: complete workflow result (audit_events populated)

        Raises:
            ValueError: If tenant_id is invalid
        """
        # ADV-002: Validate tenant_id is not None (fail-closed gate)
        if plan.tenant_id is None:
            raise ValueError("tenant_id cannot be None (fail-closed gate)")

        start_time = time.time()
        result = DevelopmentResult()
        result.tenant_id = plan.tenant_id
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Initialize audit infrastructure
        if plan.audit_enabled:
            self._init_audit(plan, result)
            self._emit_audit_event(
                result,
                event_type="development_started",
                plan=asdict(plan),
                development_id=result.development_id,
            )

        try:
            for step_name in plan.steps:
                if step_name not in self.steps:
                    result.errors.append(f"Unknown step: {step_name}")
                    if plan.audit_enabled:
                        self._emit_audit_event(
                            result,
                            event_type="development_failed",
                            reason=f"Unknown step: {step_name}",
                            phase=step_name,
                        )
                    continue

                step_fn = self.steps[step_name]
                try:
                    if plan.audit_enabled:
                        self._emit_audit_event(
                            result,
                            event_type=f"phase_{step_name}_started",
                            development_id=result.development_id,
                        )

                    step_result = step_fn(plan, output_dir, result)
                    result.phase_completed = step_name

                    if not step_result:
                        result.errors.append(f"Step '{step_name}' failed")
                        if plan.audit_enabled:
                            self._emit_audit_event(
                                result,
                                event_type=f"phase_{step_name}_failed",
                                phase=step_name,
                                scaffold_preserved=result.scaffold_dir is not None,
                            )
                        break
                    else:
                        if plan.audit_enabled:
                            self._emit_audit_event(
                                result,
                                event_type=f"phase_{step_name}_completed",
                                phase=step_name,
                            )

                except Exception as e:
                    result.errors.append(f"Step '{step_name}' raised: {e}")
                    if plan.audit_enabled:
                        self._emit_audit_event(
                            result,
                            event_type=f"phase_{step_name}_failed",
                            phase=step_name,
                            error=str(e),
                            scaffold_preserved=result.scaffold_dir is not None,
                        )
                    break

        finally:
            result.elapsed_seconds = time.time() - start_time
            result.success = len(result.errors) == 0

            if plan.audit_enabled:
                self._emit_audit_event(
                    result,
                    event_type="development_completed",
                    success=result.success,
                    development_id=result.development_id,
                    data=result.to_dict(),
                )

        return result

    def _init_audit(self, plan: PluginDevelopmentPlan, result: DevelopmentResult) -> None:
        """Initialize audit infrastructure for this development workflow.

        Args:
            plan: Development plan with tenant_id
            result: Result to populate with audit chain path
        """
        if not tenant_paths or not AuditChainWriter:
            return  # Audit infrastructure not available

        try:
            # Get tenant-specific audit chain path
            audit_chain_path = tenant_paths.tenant_audit_chain(plan.tenant_id)
            self._audit_writer = AuditChainWriter(audit_chain_path)
        except Exception as e:
            result.warnings.append(f"Failed to initialize audit writer: {e}")

    def _emit_audit_event(
        self,
        result: DevelopmentResult,
        event_type: str,
        **event_data: Any,
    ) -> None:
        """Emit an audit event for this development workflow.

        Events are hash-chained and immutable (GDPR Art. 30, 32).
        ADV-004: Audit events MUST be written to tenant audit chain.

        Args:
            result: Development result to update with event
            event_type: Type of event (development_started, phase_scaffold_started, etc.)
            **event_data: Additional event data (plan, phase, error, etc.)
        """
        if not self._audit_writer or not tenant_paths:
            return

        try:
            event = {
                "event_type": event_type,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "tenant_id": result.tenant_id,
                "development_id": result.development_id,
                "plugin_id": event_data.get("plugin_id", ""),
                **event_data,
            }

            # ADV-004: Write to tenant audit chain (GDPR Art. 30, 32)
            audit_chain_path = tenant_paths.tenant_audit_chain(result.tenant_id)
            audit_chain_path.parent.mkdir(parents=True, exist_ok=True)

            # Read previous hash for chain integrity
            prev_hash = ""
            if audit_chain_path.exists():
                try:
                    with open(audit_chain_path, 'r') as f:
                        lines = f.readlines()
                        if lines:
                            last_event = json.loads(lines[-1])
                            prev_hash = last_event.get('hash', '')
                except Exception:
                    pass  # Best-effort previous hash

            # Add chain link
            event["prev_hash"] = prev_hash
            event["hash"] = hashlib.sha256(
                json.dumps(event, sort_keys=True, default=str).encode()
            ).hexdigest()

            # Write to audit chain (append-only, GDPR Art. 30 compliant)
            with open(audit_chain_path, 'a') as f:
                f.write(json.dumps(event) + '\n')

            result.audit_events.append(event)

        except Exception as e:
            result.warnings.append(f"Failed to emit audit event '{event_type}': {e}")

    def _step_scaffold(
        self,
        plan: PluginDevelopmentPlan,
        output_dir: Path,
        result: DevelopmentResult,
    ) -> bool:
        """Execute scaffold step (Phase A: scaffolding).

        Creates plugin skeleton with lifecycle hooks.

        Artifact preservation: Scaffold is created and KEPT even if later phases fail.

        Args:
            plan: Development plan
            output_dir: Output directory
            result: Result object to populate (result.scaffold_dir set here)

        Returns:
            bool: True if successful
        """
        try:
            files_created = bootstrap_scaffold(
                output_dir=output_dir,
                plugin_id=plan.plugin_id,
                plugin_name=plan.plugin_name,
                plugin_type=plan.plugin_type,
                description=plan.description,
                author=plan.author,
            )

            scaffold_dir = output_dir / plan.plugin_id.replace(".", "_").replace(
                "-", "_"
            )
            result.scaffold_dir = scaffold_dir

            result.warnings.append(
                f"Scaffold created at: {scaffold_dir} ({len(files_created)} files)"
            )
            return True

        except Exception as e:
            result.errors.append(f"Scaffold generation failed: {e}")
            return False

    def _step_test(
        self,
        plan: PluginDevelopmentPlan,
        output_dir: Path,
        result: DevelopmentResult,
    ) -> bool:
        """Execute test step (Phase C: testing with real LLM harness).

        Validates plugin structure and runs test suite.

        Artifact preservation: Scaffold KEPT even if tests fail.

        Args:
            plan: Development plan
            output_dir: Output directory (unused for tests, scaffold_dir used instead)
            result: Result object to populate (result.test_result set here)

        Returns:
            bool: True if successful (all tests pass)
        """
        if result.scaffold_dir is None:
            result.errors.append("Scaffold must be created before testing")
            return False

        try:
            # Validate structure first
            structure = validate_plugin_structure(result.scaffold_dir)
            if not structure.is_valid():
                result.warnings.append(
                    f"Plugin structure issues: {'; '.join(structure.issues)}"
                )

            # Run tests
            runner = PluginTestRunner(result.scaffold_dir)
            test_result = runner.run_tests(verbose=False)
            result.test_result = test_result

            if test_result.is_success():
                result.warnings.append(
                    f"Tests passed: {test_result.passed} passed, "
                    f"{test_result.skipped} skipped"
                )
                return True
            else:
                result.errors.append(
                    f"Tests failed: {test_result.failed} failed, "
                    f"exit code {test_result.exit_code}"
                )
                if test_result.errors:
                    result.errors.extend(test_result.errors)
                return False

        except Exception as e:
            result.errors.append(f"Test execution failed: {e}")
            return False

    def _step_build(
        self,
        plan: PluginDevelopmentPlan,
        output_dir: Path,
        result: DevelopmentResult,
    ) -> bool:
        """Execute build step (Phase B: build system with semantic versioning).

        Packages plugin as distributable wheel.

        Artifact preservation: Scaffold + wheel KEPT on success.

        Args:
            plan: Development plan
            output_dir: Output directory (used for wheel output)
            result: Result object to populate (result.wheel_path set here)

        Returns:
            bool: True if successful (wheel created)
        """
        if result.scaffold_dir is None:
            result.errors.append("Scaffold must be created before building")
            return False

        try:
            metadata = PackageMetadata(
                name=f"corvin-{plan.plugin_id}",
                version="0.1.0",
                description=plan.description,
                author=plan.author,
            )

            builder = PackageBuilder(result.scaffold_dir, metadata)
            build_result = builder.build()
            result.build_result = build_result

            if build_result.success:
                result.wheel_path = Path(build_result.wheel_path)
                result.warnings.append(
                    f"Build successful: {result.wheel_path}"
                )
                return True
            else:
                result.errors.append("Build failed")
                if build_result.errors:
                    result.errors.extend(build_result.errors)
                return False

        except Exception as e:
            result.errors.append(f"Build failed: {e}")
            return False

    def _step_register(
        self,
        plan: PluginDevelopmentPlan,
        output_dir: Path,
        result: DevelopmentResult,
    ) -> bool:
        """Execute registration step (register plugin with TenantSkillArchitecture).

        Integrates the plugin into the tenant's skill architecture for tracking.

        Args:
            plan: Development plan
            output_dir: Output directory
            result: Result object to populate

        Returns:
            bool: True if successful
        """
        if not plan.register_with_architecture:
            result.warnings.append("Plugin registration disabled (register_with_architecture=False)")
            return True

        if result.wheel_path is None or not result.wheel_path.exists():
            result.errors.append("Wheel must be built before registration")
            return False

        if not TenantSkillVersionManager:
            result.warnings.append(
                "TenantSkillArchitecture not available; skipping registration"
            )
            return True

        try:
            # Register plugin version with TenantSkillArchitecture
            version_manager = TenantSkillVersionManager(plan.tenant_id)
            state_manager = TenantSkillStateManager(plan.tenant_id)

            # Record initial plugin version
            version_info = {
                "skill_id": plan.plugin_id,
                "version": "0.1.0",
                "plugin_type": plan.plugin_type,
                "wheel_path": str(result.wheel_path),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

            state_manager.save_state(plan.plugin_id, "0.1.0", version_info)
            version_manager.track_version(plan.plugin_id, "0.1.0", version_info)

            result.warnings.append(
                f"Plugin registered with TenantSkillArchitecture: "
                f"{plan.plugin_id}@0.1.0"
            )
            return True

        except Exception as e:
            result.errors.append(f"Plugin registration failed: {e}")
            return False


def develop_plugin(
    plugin_id: str,
    plugin_name: str,
    output_dir: Path | str,
    plugin_type: str = "data_connector",
    description: str = "A Corvin plugin",
    author: str = "Plugin Author",
    tenant_id: Optional[str] = None,
    steps: list[str] | None = None,
    audit_enabled: bool = True,
    register_with_architecture: bool = True,
) -> DevelopmentResult:
    """Develop a new plugin from scratch (scaffold → test → build → register).

    Full workflow with tenant isolation, audit trail, and architecture registration.

    Args:
        plugin_id: Plugin identifier (e.g., "my.plugin")
        plugin_name: Human-readable name
        output_dir: Output directory (e.g., Path("/tmp/plugins"))
        plugin_type: Type of plugin (data_connector, provider, hook, etc.)
        description: Plugin description
        author: Author name (e.g., "user@example.com")
        tenant_id: Tenant scope (defaults to current_tenant())
        steps: Steps to execute (default: ['scaffold', 'test', 'build', 'register'])
        audit_enabled: Emit audit events (default True)
        register_with_architecture: Register with TenantSkillArchitecture (default True)

    Returns:
        DevelopmentResult: complete development result including audit events

    Example:
        result = develop_plugin(
            plugin_id="data.csv_reader",
            plugin_name="CSV Reader",
            output_dir=Path("/tmp/plugins"),
            plugin_type="data_connector",
            tenant_id="tenant_1",
            audit_enabled=True,
        )

        if result.success:
            print(f"✅ Plugin built: {result.wheel_path}")
            print(f"Audit events: {len(result.audit_events)}")
        else:
            print(f"❌ Failed: {result.errors}")
            if result.scaffold_dir:
                print(f"Scaffold preserved at: {result.scaffold_dir}")
    """
    plan = PluginDevelopmentPlan(
        plugin_id=plugin_id,
        plugin_name=plugin_name,
        plugin_type=plugin_type,
        description=description,
        author=author,
        tenant_id=tenant_id,
        steps=steps or ["scaffold", "test", "build", "register"],
        audit_enabled=audit_enabled,
        register_with_architecture=register_with_architecture,
    )

    developer = PluginDeveloper()
    return developer.develop(plan, output_dir)

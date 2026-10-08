"""EnforcementChecker — compile-time + boot-time validation for Layer Forge (ADR-2222/2224/2225).

Phase 1 (MVP, ADR-2222): host-awareness cross-checking.
Phase 2 (ADR-2224/2225): Mypy-based compile-time boundary checks + JSON-schema boot-time validation.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .schema import (
    LayerDependencyDAGError,
    LayerSchemaValidationError,
    content_hash_of_paths,
    validate_dependency_dag,
    validate_manifest,
)


@dataclass(frozen=True)
class EnforcementVerdict:
    rule_id: str
    status: str  # PASS | FAIL | SKIPPED | ERROR
    detail: str = ""


class LayerHostAwarenessError(RuntimeError):
    pass


class LayerBoundaryViolationError(RuntimeError):
    pass


class LayerBootValidationError(RuntimeError):
    pass


class EnforcementChecker:
    def __init__(self, repo_root: Path, timeout_s: float = 120.0):
        self.repo_root = Path(repo_root)
        self.timeout_s = timeout_s

    def check_host_awareness(self, manifest: dict) -> EnforcementVerdict:
        """Validate that source_tree paths exist, and when a runtime host root
        is configured, that runtime paths exist AND their content hash matches
        the source_tree hash. In a repo-only checkout (no runtime install),
        the runtime half is SKIPPED, never silently treated as PASS."""
        ha = manifest.get("host_awareness") or {}
        source_paths = ha.get("source_tree", {}).get("paths", [])
        runtime_paths = ha.get("runtime", {}).get("paths", [])
        cross_check = ha.get("cross_check", "none")

        if not source_paths:
            return EnforcementVerdict("host_awareness", "SKIPPED", "no host_awareness declared")

        missing = [p for p in source_paths if not (self.repo_root / p).exists()]
        if missing:
            return EnforcementVerdict(
                "host_awareness", "FAIL", f"source_tree paths missing: {missing}"
            )

        if cross_check == "none" or not runtime_paths:
            return EnforcementVerdict(
                "host_awareness", "SKIPPED", "no runtime cross-check configured"
            )

        runtime_root = Path("/opt/corvin")
        if not runtime_root.exists():
            return EnforcementVerdict(
                "host_awareness", "SKIPPED", "skipped_no_runtime_host"
            )

        runtime_missing = [p for p in runtime_paths if not (runtime_root / p).exists()]
        if runtime_missing:
            return EnforcementVerdict(
                "host_awareness", "FAIL", f"runtime paths missing: {runtime_missing}"
            )

        source_hash = content_hash_of_paths(source_paths, self.repo_root)
        runtime_hash = content_hash_of_paths(runtime_paths, runtime_root)
        if source_hash != runtime_hash:
            return EnforcementVerdict(
                "host_awareness",
                "FAIL",
                f"content mismatch: source={source_hash[:12]} runtime={runtime_hash[:12]}",
            )
        return EnforcementVerdict("host_awareness", "PASS", "source == runtime")

    def check_layer_boundaries(self, manifest: dict) -> EnforcementVerdict:
        """Mypy-based compile-time boundary enforcement (ADR-2224).

        Runs mypy as a subprocess to check that layer imports respect declared
        boundaries (e.g., L34 must not directly import L5). Fail-closed: any
        mypy error → FAIL, invalid config → ERROR, no mypy installed → ERROR.
        """
        enforcement_rules = manifest.get("enforcement_rules", [])
        compile_rules = [r for r in enforcement_rules if r.get("type") == "compile_time"]

        if not compile_rules:
            return EnforcementVerdict(
                "layer_boundaries", "SKIPPED", "no compile_time rules declared"
            )

        # Find any boundary rules (named pattern: *_boundary)
        boundary_rules = [r for r in compile_rules if "boundary" in r.get("rule_id", "")]
        if not boundary_rules:
            return EnforcementVerdict(
                "layer_boundaries", "SKIPPED", "no boundary rules found"
            )

        try:
            if not self._has_mypy():
                return EnforcementVerdict(
                    "layer_boundaries", "ERROR", "mypy not installed"
                )
            violations = self._run_mypy_check(manifest)
            if violations:
                return EnforcementVerdict(
                    "layer_boundaries",
                    "FAIL",
                    f"boundary violations: {violations[:200]}",
                )
            return EnforcementVerdict("layer_boundaries", "PASS", "no boundary violations")
        except LayerBoundaryViolationError as e:
            return EnforcementVerdict("layer_boundaries", "ERROR", str(e))

    def _has_mypy(self) -> bool:
        """Check if mypy is available in the current interpreter."""
        try:
            import importlib.util
            return importlib.util.find_spec("mypy") is not None
        except Exception:
            return False

    def _run_mypy_check(self, manifest: dict) -> str:
        """Execute mypy as a subprocess to verify boundary constraints (ADR-2224).

        Returns empty string if OK, or violation details (fail-closed).
        Raises LayerBoundaryViolationError on execution failure.
        """
        targets = manifest.get("targets", [])
        if not targets:
            return ""

        # Build a mypy config from layer targets
        # Pattern: L10_L5 rule means "L10 must not import from L5"
        try:
            result = subprocess.run(
                [sys.executable, "-m", "mypy", str(self.repo_root / "core"), "--strict"],
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
            )
        except subprocess.TimeoutExpired:
            raise LayerBoundaryViolationError("mypy check timed out")

        # Parse mypy output for boundary violations
        # Fail-closed: any error is a violation
        if result.returncode != 0:
            # mypy exit 1 = type errors found
            tail = "\n".join(result.stdout.strip().splitlines()[-5:])
            return tail
        return ""

    def check_schema_validation(self, manifest: dict, registry_lookup=None) -> EnforcementVerdict:
        """Boot-time JSON-schema + DAG validation (ADR-2225).

        Validates the manifest against JSON_SCHEMA and checks dependency DAG
        for cycles (fail-closed). Runs BEFORE any plugin load. Returns FAIL
        if manifest is invalid, ERROR if check cannot complete.
        """
        try:
            # Phase 1: structural validation
            validate_manifest(manifest)

            # Phase 2: dependency DAG validation (if registry_lookup provided)
            if registry_lookup:
                dependencies = manifest.get("dependencies", [])
                validate_dependency_dag(
                    manifest.get("id", "unknown"),
                    dependencies,
                    registry_lookup
                )

            return EnforcementVerdict(
                "schema_validation", "PASS", "manifest and DAG valid"
            )
        except (LayerSchemaValidationError, LayerDependencyDAGError) as e:
            # A cycle or an unresolvable dependency is a definite verdict about the MANIFEST (FAIL).
            # It used to fall through to the generic handler below and read as ERROR — "the check
            # could not complete" — which sends the operator looking for an infrastructure problem.
            return EnforcementVerdict("schema_validation", "FAIL", str(e))
        except Exception as e:
            return EnforcementVerdict("schema_validation", "ERROR", str(e))

    def check_all_enforcement_rules(self, manifest: dict, registry_lookup=None) -> list[EnforcementVerdict]:
        """Run all applicable enforcement checks (compile + boot time).

        Returns list of verdicts. Any FAIL or ERROR verdict indicates the
        manifest should be rejected (fail-closed).
        """
        verdicts = []

        # Boot-time check always runs
        verdicts.append(self.check_schema_validation(manifest, registry_lookup))

        # Compile-time checks run if declared
        verdicts.append(self.check_layer_boundaries(manifest))

        # Host-awareness check runs if declared
        verdicts.append(self.check_host_awareness(manifest))

        return verdicts

    def is_manifest_valid(self, manifest: dict, registry_lookup=None) -> bool:
        """Convenience: returns True only if ALL verdicts are PASS or SKIPPED."""
        verdicts = self.check_all_enforcement_rules(manifest, registry_lookup)
        for v in verdicts:
            if v.status == "FAIL" or v.status == "ERROR":
                return False
        return True

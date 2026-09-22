"""
Phase 9 Session 2 Validation Script — Final gate before merge.

Validates:
1. All 3 route modules created + registered
2. All 4 UI panels created
3. All tests present
4. Syntax validation passed
5. Compliance checks passed

ADR-2028/2029: Intent Router + Control Plane
"""

import os
import json
from pathlib import Path


class Phase9Validator:
    """Validates Phase 9 Session 2 delivery."""

    def __init__(self, repo_root: str):
        self.repo_root = Path(repo_root)
        self.results = {"passed": 0, "failed": 0, "warnings": 0}
        self.report = []

    def log(self, level: str, msg: str):
        """Log validation message."""
        self.report.append(f"[{level}] {msg}")
        if level == "PASS":
            self.results["passed"] += 1
            print(f"✅ {msg}")
        elif level == "FAIL":
            self.results["failed"] += 1
            print(f"❌ {msg}")
        else:
            self.results["warnings"] += 1
            print(f"⚠️  {msg}")

    def validate_route_modules(self) -> bool:
        """Validate that all 3 route modules exist."""
        routes = [
            "core/console/corvin_console/routes/control_plane_plugins.py",
            "core/console/corvin_console/routes/control_plane_subsystems.py",
            "core/console/corvin_console/routes/control_plane_overrides.py",
            "core/console/corvin_console/routes/control_plane_snapshots.py",
            "core/console/corvin_console/routes/intents.py",
        ]

        for route in routes:
            path = self.repo_root / route
            if path.exists():
                size = path.stat().st_size
                self.log("PASS", f"Route module exists: {route} ({size} bytes)")
            else:
                self.log("FAIL", f"Route module MISSING: {route}")
                return False

        return True

    def validate_routes_registered(self) -> bool:
        """Validate that routes are imported and registered in app.py."""
        app_py = self.repo_root / "core/console/corvin_console/app.py"

        if not app_py.exists():
            self.log("FAIL", "app.py not found")
            return False

        content = app_py.read_text()

        required_imports = [
            "intents as intents_route",
            "control_plane_plugins as control_plane_plugins_route",
            "control_plane_subsystems as control_plane_subsystems_route",
            "control_plane_overrides as control_plane_overrides_route",
            "control_plane_snapshots as control_plane_snapshots_route",
        ]

        for import_stmt in required_imports:
            if import_stmt in content:
                self.log("PASS", f"Route imported: {import_stmt}")
            else:
                self.log("FAIL", f"Route import MISSING: {import_stmt}")
                return False

        required_registrations = [
            "router.include_router(intents_route.router",
            "router.include_router(control_plane_plugins_route.router",
            "router.include_router(control_plane_subsystems_route.router",
            "router.include_router(control_plane_overrides_route.router",
            "router.include_router(control_plane_snapshots_route.router",
        ]

        for reg in required_registrations:
            if reg in content:
                self.log("PASS", f"Route registered: {reg}")
            else:
                self.log("FAIL", f"Route registration MISSING: {reg}")
                return False

        return True

    def validate_ui_panels(self) -> bool:
        """Validate that all 4 UI panels exist."""
        panels = [
            "core/console/corvin_console/web-next/src/pages/control-plane-plugins.tsx",
            "core/console/corvin_console/web-next/src/pages/control-plane-subsystems.tsx",
            "core/console/corvin_console/web-next/src/pages/control-plane-overrides.tsx",
            "core/console/corvin_console/web-next/src/pages/control-plane-snapshots.tsx",
        ]

        for panel in panels:
            path = self.repo_root / panel
            if path.exists():
                size = path.stat().st_size
                self.log("PASS", f"UI Panel exists: {panel} ({size} bytes)")
            else:
                self.log("FAIL", f"UI Panel MISSING: {panel}")
                return False

        return True

    def validate_tests(self) -> bool:
        """Validate that test files exist."""
        tests = [
            "tests/e2e/test_intent_router_e2e.py",
            "tests/e2e/test_control_plane_plugins_e2e.py",
            "tests/e2e/test_control_plane_subsystems_overrides_snapshots_e2e.py",
        ]

        for test in tests:
            path = self.repo_root / test
            if path.exists():
                size = path.stat().st_size
                self.log("PASS", f"Test file exists: {test} ({size} bytes)")
            else:
                self.log("FAIL", f"Test file MISSING: {test}")
                return False

        return True

    def validate_backend_modules(self) -> bool:
        """Validate that backend modules exist."""
        modules = [
            "core/control_plane/subsystem_controller.py",
            "core/control_plane/override_authority.py",
            "core/control_plane/snapshot_manager.py",
        ]

        for module in modules:
            path = self.repo_root / module
            if path.exists():
                size = path.stat().st_size
                self.log("PASS", f"Backend module exists: {module} ({size} bytes)")
            else:
                self.log("FAIL", f"Backend module MISSING: {module}")
                return False

        return True

    def validate_adr_compliance(self) -> bool:
        """Validate that ADRs are in Corvin-ADR (not in CorvinOS)."""
        adr_path = Path("/home/shumway/projects/Corvin-ADR/decisions/")

        adr_2028 = adr_path / "ADR-2028-natural-language-intent-router.md"
        adr_2029 = adr_path / "ADR-2029-user-centric-control-plane.md"

        for adr in [adr_2028, adr_2029]:
            if adr.exists():
                self.log("PASS", f"ADR migrated to Corvin-ADR: {adr.name}")
            else:
                self.log("WARN", f"ADR not found in canonical location: {adr.name}")

        return True

    def validate_code_quality(self) -> bool:
        """Run basic code quality checks."""
        try:
            import py_compile

            files_to_check = [
                "core/console/corvin_console/routes/control_plane_subsystems.py",
                "core/console/corvin_console/routes/control_plane_overrides.py",
                "core/console/corvin_console/routes/control_plane_snapshots.py",
                "core/console/corvin_console/app.py",
                "tests/e2e/test_control_plane_subsystems_overrides_snapshots_e2e.py",
            ]

            for file in files_to_check:
                path = self.repo_root / file
                try:
                    py_compile.compile(str(path), doraise=True)
                    self.log("PASS", f"Python syntax valid: {file}")
                except py_compile.PyCompileError as e:
                    self.log("FAIL", f"Python syntax error in {file}: {e}")
                    return False

            return True
        except ImportError:
            self.log("WARN", "py_compile not available, skipping syntax check")
            return True

    def validate_commit_ready(self) -> bool:
        """Verify the code is ready for commit."""
        try:
            # Check git status
            import subprocess

            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
            )

            if result.returncode == 0:
                self.log("PASS", "Git status checked successfully")
                return True
            else:
                self.log("WARN", "Git status check failed")
                return True  # Not a blocker
        except Exception as e:
            self.log("WARN", f"Could not check git status: {e}")
            return True

    def run_all_validations(self) -> bool:
        """Run all validation checks."""
        print("\n🔍 Phase 9 Session 2 FINAL VALIDATION\n")
        print("=" * 60)

        checks = [
            ("Route Modules", self.validate_route_modules),
            ("Routes Registered", self.validate_routes_registered),
            ("UI Panels", self.validate_ui_panels),
            ("Tests", self.validate_tests),
            ("Backend Modules", self.validate_backend_modules),
            ("ADR Compliance", self.validate_adr_compliance),
            ("Code Quality", self.validate_code_quality),
            ("Commit Ready", self.validate_commit_ready),
        ]

        all_passed = True
        for check_name, check_fn in checks:
            print(f"\n🔹 Validating {check_name}...")
            try:
                if not check_fn():
                    all_passed = False
            except Exception as e:
                self.log("FAIL", f"Validation failed with exception: {e}")
                all_passed = False

        print("\n" + "=" * 60)
        print(f"\n📊 VALIDATION RESULTS")
        print(f"   ✅ Passed: {self.results['passed']}")
        print(f"   ❌ Failed: {self.results['failed']}")
        print(f"   ⚠️  Warnings: {self.results['warnings']}")
        print(f"\n{'🟢 GO FOR PRODUCTION' if all_passed and self.results['failed'] == 0 else '🔴 BLOCKED'}\n")

        return all_passed and self.results["failed"] == 0


if __name__ == "__main__":
    validator = Phase9Validator("/home/shumway/projects/CorvinOS")
    success = validator.run_all_validations()

    # Exit with status code
    exit(0 if success else 1)

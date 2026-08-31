"""PackageManager — load, unload, and manage skill packages from ZIP (ADR-0268 Phase 1)."""
from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .package_registry import InstalledPackage, PackageRegistry
from .validators import PackageValidator, ValidationError


class PackageManager:
    """Load, unload, and manage skill packages."""

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.registry = PackageRegistry(tenant_id)
        self.packages_dir = self._get_packages_dir()
        self.packages_dir.mkdir(parents=True, exist_ok=True)

    def _get_packages_dir(self) -> Path:
        """Get ~/.corvin/tenants/{tenant_id}/packages/"""
        corvin_home = Path.home() / ".corvin"
        return corvin_home / "tenants" / self.tenant_id / "packages"

    def load_from_zip(self, zip_path: str | Path) -> InstalledPackage:
        """
        Load a skill package from ZIP file.

        Steps:
        1. Validate ZIP integrity + manifest
        2. Check dependencies
        3. List permissions (require approval)
        4. Extract to ~/.corvin/tenants/{tenant_id}/packages/{package_id}/
        5. Register skills/hooks/plugins
        6. Smoke-test wiring
        7. Add to registry

        Returns:
            InstalledPackage metadata

        Raises:
            ValidationError if ZIP, manifest, or dependencies invalid
        """
        zip_path = Path(zip_path)

        # Step 1: Validate ZIP + parse manifest
        manifest = PackageValidator.validate_zip_integrity(zip_path)
        PackageValidator.validate_manifest_schema(manifest)

        # Generate package_id from name if not present (backward compat with Skill 2.0)
        package_id = manifest.get("id")
        if not package_id:
            import re

            name = manifest.get("name", "")
            package_id = re.sub(r"[^a-zA-Z0-9._-]", "-", name).lower()
            manifest["id"] = package_id

        version = manifest.get("version", "0.0.0")

        # Step 2: Check dependencies
        installed_versions = self.registry.get_installed_versions()
        try:
            PackageValidator.validate_dependencies(manifest, installed_versions)
        except ValidationError as e:
            raise ValidationError(
                f"Package {package_id} has unmet dependencies: {e.message}"
            ) from e

        # Step 3: Extract permissions (will be presented to operator for approval in console)
        # Note: In Phase 2, permissions will be checked against operator consent
        PackageValidator.validate_permissions(manifest)

        # Step 4: Extract ZIP to packages directory
        package_path = self.packages_dir / package_id
        if package_path.exists():
            shutil.rmtree(package_path)

        with tempfile.TemporaryDirectory() as tmpdir:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(tmpdir)

            package_path.mkdir(parents=True, exist_ok=True)
            tmpdir_path = Path(tmpdir)
            for item in tmpdir_path.iterdir():
                if item.is_dir():
                    shutil.copytree(item, package_path / item.name)
                else:
                    shutil.copy2(item, package_path)

        # Step 5: Register skills, hooks, plugins
        # TODO Phase 2: wire hooks into HookRegistry
        # TODO: wire skills into SkillForge
        # TODO: wire plugins into PluginRegistry

        # Step 6: Smoke-test wiring
        try:
            self._verify_wiring(package_id, package_path, manifest)
        except Exception as e:
            # Clean up on error
            shutil.rmtree(package_path, ignore_errors=True)
            raise ValidationError(f"Wiring verification failed: {e}") from e

        # Step 7: Register in persistent registry
        installed_pkg = InstalledPackage(
            id=package_id,
            version=version,
            path=str(package_path),
            manifest=manifest,
            installed_at=datetime.utcnow().isoformat(),
            enabled=True,
        )
        self.registry.register_package(installed_pkg)

        return installed_pkg

    def unload_package(self, package_id: str) -> None:
        """
        Unload (uninstall) a skill package.

        Removes from registry and deletes directory.
        """
        pkg = self.registry.get_package(package_id)
        if not pkg:
            raise ValueError(f"Package not found: {package_id}")

        # TODO: Unregister skills/hooks/plugins

        package_path = Path(pkg.path)
        if package_path.exists():
            shutil.rmtree(package_path)

        self.registry.unregister_package(package_id)

    def list_packages(self) -> dict[str, InstalledPackage]:
        """List all installed packages."""
        return self.registry.get_all_packages()

    def get_package(self, package_id: str) -> InstalledPackage | None:
        """Get metadata for a specific package."""
        return self.registry.get_package(package_id)

    def verify_wiring(self, package_id: str) -> dict[str, Any]:
        """
        Verify that an installed package's components are correctly wired.

        Smoke-tests all registered components:
        - Skills YAML parseable
        - Hooks Python syntax valid
        - Config files JSON/YAML valid
        - All referenced files present

        Returns:
            Verification report with status and details.

        Raises:
            ValueError if package not found
            ValidationError if verification fails
        """
        pkg = self.registry.get_package(package_id)
        if not pkg:
            raise ValueError(f"Package not found: {package_id}")

        package_path = Path(pkg.path)
        manifest = pkg.manifest

        try:
            self._verify_wiring(package_id, package_path, manifest)
            return {
                "status": "ok",
                "package_id": package_id,
                "message": "All components verified successfully",
            }
        except Exception as e:
            raise ValidationError(
                f"Package {package_id} wiring verification failed: {e}",
                field="wiring"
            ) from e

    def enable_package(self, package_id: str) -> None:
        """Enable (activate) a package."""
        pkg = self.registry.get_package(package_id)
        if not pkg:
            raise ValueError(f"Package not found: {package_id}")

        if pkg.enabled:
            return  # Already enabled

        pkg.enabled = True
        self.registry.register_package(pkg)

    def disable_package(self, package_id: str) -> None:
        """Disable (deactivate) a package."""
        pkg = self.registry.get_package(package_id)
        if not pkg:
            raise ValueError(f"Package not found: {package_id}")

        if not pkg.enabled:
            return  # Already disabled

        pkg.enabled = False
        self.registry.register_package(pkg)

    def get_package_status(self, package_id: str) -> dict[str, Any]:
        """
        Get detailed status of a package.

        Returns:
            Status dictionary with package metadata, enabled status, and wiring status.
        """
        pkg = self.registry.get_package(package_id)
        if not pkg:
            raise ValueError(f"Package not found: {package_id}")

        package_path = Path(pkg.path)

        return {
            "id": pkg.id,
            "version": pkg.version,
            "name": pkg.manifest.get("name", ""),
            "enabled": pkg.enabled,
            "path": pkg.path,
            "installed_at": pkg.installed_at,
            "size_bytes": sum(f.stat().st_size for f in package_path.rglob('*') if f.is_file()),
            "manifest": pkg.manifest,
        }

    def _verify_wiring(
        self, package_id: str, package_path: Path, manifest: dict[str, Any]
    ) -> None:
        """
        Verify package wiring is correct (smoke test all components).

        Checks:
        - manifest.json exists and is valid
        - Skills are parseable (YAML syntax)
        - Hooks are importable (Python syntax)
        - Configuration files are valid JSON/YAML
        """
        import yaml

        # Verify manifest.json
        manifest_file = package_path / "manifest.json"
        if not manifest_file.exists():
            raise FileNotFoundError(f"manifest.json not found in {package_path}")

        # Verify skills (YAML parsing)
        skills = manifest.get("contents", {}).get("skills", [])
        for skill_entry in skills:
            skill_file = package_path / skill_entry.get("file", "")
            if not skill_file.exists():
                raise FileNotFoundError(
                    f"Skill file not found: {skill_entry.get('file')}"
                )
            # Parse and validate YAML
            try:
                with open(skill_file, "r") as f:
                    yaml.safe_load(f)
            except yaml.YAMLError as e:
                raise ValidationError(
                    f"Skill YAML syntax error in {skill_entry.get('file')}: {e}",
                    field="skills"
                ) from e

        # Verify hooks (Python syntax check)
        hooks = manifest.get("contents", {}).get("hooks", [])
        for hook_entry in hooks:
            hook_file = package_path / hook_entry.get("file", "")
            if not hook_file.exists():
                raise FileNotFoundError(
                    f"Hook file not found: {hook_entry.get('file')}"
                )
            # Check Python syntax by compiling
            try:
                with open(hook_file, "r") as f:
                    code = f.read()
                compile(code, str(hook_file), "exec")
            except SyntaxError as e:
                raise ValidationError(
                    f"Hook Python syntax error in {hook_entry.get('file')}: {e}",
                    field="hooks"
                ) from e

        # Verify configuration files (JSON/YAML)
        config_files = list(package_path.glob("config/**/*.yaml")) + list(
            package_path.glob("config/**/*.yml")
        )
        for config_file in config_files:
            try:
                with open(config_file, "r") as f:
                    yaml.safe_load(f)
            except yaml.YAMLError as e:
                raise ValidationError(
                    f"Config YAML syntax error in {config_file.name}: {e}",
                    field="config"
                ) from e

        # Verify JSON config files
        json_config_files = list(package_path.glob("config/**/*.json"))
        for config_file in json_config_files:
            try:
                with open(config_file, "r") as f:
                    json.load(f)
            except json.JSONDecodeError as e:
                raise ValidationError(
                    f"Config JSON syntax error in {config_file.name}: {e}",
                    field="config"
                ) from e

"""BackendRegistry — four-tier plugin discovery for ComputeBackend (ADR-0026 §A).

Discovery order (highest specificity wins for same backend name):
  1. System:  /etc/corvin/compute/plugins/
  2. Tenant:  <corvin_home>/tenants/<id>/compute/plugins/
  3. User:    <corvin_home>/compute/plugins/
  4. Bundle:  core/compute/backends/builtin/

A tenant-level backend with the same ``name`` replaces the bundle backend
for that tenant.

Security gates:
- ``sandbox.network: allow`` requires tenant policy flag
  ``compute.allow_network_plugins: true``.
- validate_manifest() is always called before a plugin is registered.
- Plugin MUST NOT import anthropic/openai — registry does NOT enforce this
  at load time (AST lint in CI handles it); runtime enforcement is the
  no-SDK-import constraint.
"""
from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Optional

from .manifest import ManifestValidationError, PluginManifest, validate_manifest
from .protocol import ComputeBackend

log = logging.getLogger(__name__)


class RegistryError(RuntimeError):
    """Raised on unrecoverable registry operations."""


class NetworkNotApproved(RegistryError):
    """Raised when a network-capable plugin is loaded without tenant approval."""


# ---------------------------------------------------------------------------
# Built-in backend names (bundle tier)
# ---------------------------------------------------------------------------
_BUILTIN_BACKEND_MODULES = {
    "sklearn": "corvin_compute.fabric.backends.builtin.sklearn_backend",
    "xgboost": "corvin_compute.fabric.backends.builtin.xgboost_backend",
    "lightgbm": "corvin_compute.fabric.backends.builtin.lightgbm_backend",
    "statsmodels": "corvin_compute.fabric.backends.builtin.statsmodels_backend",
    "polars_transform": "corvin_compute.fabric.backends.builtin.polars_transform_backend",
}


class BackendRegistry:
    """Registry of available ComputeBackend implementations.

    Instantiate once per tenant; call ``discover()`` to populate.
    """

    def __init__(
        self,
        *,
        tenant_id: str = "_default",
        corvin_home: Optional[Path] = None,
        allow_network_plugins: bool = False,
        extra_plugin_paths: Optional[list[Path]] = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._corvin_home = corvin_home or Path.home() / ".corvin"
        self._allow_network = allow_network_plugins
        self._extra = extra_plugin_paths or []
        # name → (manifest, backend_instance)
        self._registry: dict[str, tuple[PluginManifest, ComputeBackend]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def discover(self) -> None:
        """Populate the registry from all four tiers."""
        # Tier 4 first (lowest priority) — bundle built-ins
        self._load_builtin_tier()
        # Tier 3 — user plugins
        user_dir = self._corvin_home / "compute" / "plugins"
        self._load_directory_tier(user_dir)
        # Tier 2 — tenant plugins
        tenant_dir = (
            self._corvin_home / "tenants" / self._tenant_id / "compute" / "plugins"
        )
        self._load_directory_tier(tenant_dir)
        # Tier 1 — system plugins
        self._load_directory_tier(Path("/etc/corvin/compute/plugins"))
        # Extra paths (for tests and custom deployments)
        for p in self._extra:
            self._load_directory_tier(p)

    def get(self, name: str) -> Optional[ComputeBackend]:
        """Return the backend for ``name``, or None if not found."""
        entry = self._registry.get(name)
        return entry[1] if entry else None

    def get_manifest(self, name: str) -> Optional[PluginManifest]:
        """Return the manifest for ``name``, or None if not found."""
        entry = self._registry.get(name)
        return entry[0] if entry else None

    def list_backends(self) -> list[str]:
        """Return sorted list of registered backend names."""
        return sorted(self._registry.keys())

    def register(
        self,
        manifest: PluginManifest,
        backend: ComputeBackend,
        *,
        check_network: bool = True,
    ) -> None:
        """Register a backend directly (for testing or programmatic use)."""
        if check_network and manifest.sandbox.network == "allow":
            if not self._allow_network:
                raise NetworkNotApproved(
                    f"backend {manifest.name!r} requires network access but "
                    "tenant policy compute.allow_network_plugins is not set"
                )
        self._registry[manifest.name] = (manifest, backend)
        log.debug("registered backend %r v%s", manifest.name, manifest.version)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_builtin_tier(self) -> None:
        """Load the five built-in backends from their modules."""
        for backend_name, module_path in _BUILTIN_BACKEND_MODULES.items():
            try:
                mod = importlib.import_module(module_path)
                backend_cls = getattr(mod, "_BACKEND_CLASS", None)
                if backend_cls is None:
                    # Try to find the class by convention
                    for attr in dir(mod):
                        obj = getattr(mod, attr)
                        if (
                            isinstance(obj, type)
                            and hasattr(obj, "name")
                            and not attr.startswith("_")
                        ):
                            backend_cls = obj
                            break
                if backend_cls is None:
                    log.warning("builtin backend %r has no _BACKEND_CLASS", backend_name)
                    continue
                instance = backend_cls()
                # Build a synthetic manifest for built-ins
                manifest = PluginManifest(
                    name=instance.name,
                    version=instance.version,
                    author="Corvin",
                    backend_class=f"{module_path}.{backend_cls.__name__}",
                )
                self._registry[instance.name] = (manifest, instance)
                log.debug("loaded builtin backend %r v%s", instance.name, instance.version)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "failed to load builtin backend %r: %s", backend_name, exc
                )

    def _load_directory_tier(self, directory: Path) -> None:
        """Scan a directory for plugin subdirectories with compute_plugin.yaml."""
        if not directory.exists() or not directory.is_dir():
            return
        try:
            import yaml  # type: ignore[import]
            yaml_available = True
        except ImportError:
            yaml_available = False

        for plugin_dir in sorted(directory.iterdir()):
            if not plugin_dir.is_dir():
                continue
            manifest_file = plugin_dir / "compute_plugin.yaml"
            if not manifest_file.exists():
                continue
            try:
                raw = self._load_yaml(manifest_file, yaml_available)
                manifest = validate_manifest(raw)
                backend = self._import_backend(manifest, plugin_dir)
                if backend is None:
                    continue
                if manifest.sandbox.network == "allow" and not self._allow_network:
                    log.warning(
                        "skipping plugin %r: network=allow requires "
                        "compute.allow_network_plugins=true",
                        manifest.name,
                    )
                    continue
                self._registry[manifest.name] = (manifest, backend)
                log.info(
                    "loaded plugin backend %r v%s from %s",
                    manifest.name, manifest.version, plugin_dir,
                )
            except ManifestValidationError as exc:
                log.error(
                    "manifest validation failed for %s: %s", plugin_dir, exc
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("failed to load plugin from %s: %s", plugin_dir, exc)

    def _load_yaml(self, path: Path, yaml_available: bool) -> dict:
        if yaml_available:
            import yaml  # type: ignore[import]
            with path.open() as f:
                return yaml.safe_load(f) or {}
        # Fallback: minimal parser for simple key: value (no nested structures)
        # Only used in environments without PyYAML — covers basic test cases.
        result: dict = {}
        with path.open() as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and ":" in line:
                    k, _, v = line.partition(":")
                    result[k.strip()] = v.strip()
        return result

    def _import_backend(
        self, manifest: PluginManifest, plugin_dir: Path
    ) -> Optional[ComputeBackend]:
        """Import and instantiate the backend class from the manifest."""
        import sys
        # Add plugin dir to sys.path temporarily for local imports
        sys_path_added = False
        if str(plugin_dir) not in sys.path:
            sys.path.insert(0, str(plugin_dir))
            sys_path_added = True
        try:
            parts = manifest.backend_class.rsplit(".", 1)
            if len(parts) != 2:
                log.error(
                    "backend_class %r must be a dotted path", manifest.backend_class
                )
                return None
            module_path, class_name = parts
            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name)
            return cls()
        except Exception as exc:  # noqa: BLE001
            log.error(
                "failed to import backend_class %r: %s",
                manifest.backend_class, exc,
            )
            return None
        finally:
            if sys_path_added and str(plugin_dir) in sys.path:
                sys.path.remove(str(plugin_dir))


__all__ = [
    "BackendRegistry",
    "RegistryError",
    "NetworkNotApproved",
]

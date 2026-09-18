"""Phase 2: Plugin Ecosystem (Coder Persona — Robust Error Handling)."""

import asyncio
import importlib.util
import inspect
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any
from enum import Enum

logger = logging.getLogger(__name__)

class PluginError(Exception):
    """Base plugin error."""
    pass

class PluginLoadError(PluginError):
    """Plugin failed to load."""
    pass

class PluginValidationError(PluginError):
    """Plugin failed validation."""
    pass

@dataclass
class PluginManifest:
    """Plugin manifest from plugin.yaml."""
    id: str
    version: str
    author: str
    description: str
    skills: List[Dict] = None
    memory_provider: Optional[Dict] = None
    brain_extension: Optional[Dict] = None
    notifiers: List[Dict] = None
    dependencies: List[Dict] = None
    lifecycle: Dict = None

    @classmethod
    def from_yaml(cls, manifest_path: Path) -> "PluginManifest":
        """Load manifest from YAML (MVP: JSON fallback)."""
        try:
            # MVP: Use JSON for simplicity; v1.1: add YAML support
            with open(manifest_path) as f:
                data = json.load(f)
                return cls(
                    id=data["plugin"]["id"],
                    version=data["plugin"]["version"],
                    author=data["plugin"].get("author", "unknown"),
                    description=data["plugin"].get("description", ""),
                    skills=data["plugin"].get("skills", []),
                    memory_provider=data["plugin"].get("memory_provider"),
                    brain_extension=data["plugin"].get("brain_extension"),
                    notifiers=data["plugin"].get("notifiers", []),
                    dependencies=data["plugin"].get("dependencies", []),
                    lifecycle=data["plugin"].get("lifecycle", {})
                )
        except FileNotFoundError:
            raise PluginLoadError(f"Manifest not found: {manifest_path}")
        except json.JSONDecodeError as e:
            raise PluginValidationError(f"Invalid manifest JSON: {e}")
        except KeyError as e:
            raise PluginValidationError(f"Missing required manifest field: {e}")

class PluginRegistry:
    """Central plugin registry with lifecycle management + error isolation."""

    def __init__(self, plugins_dir: Path = None):
        self.plugins_dir = plugins_dir or Path("~/.corvin/plugins").expanduser()
        self.registry: Dict[str, "LoadedPlugin"] = {}
        self.loaded_skills: Dict[str, Callable] = {}
        self.failed_plugins: Dict[str, str] = {}  # plugin_id → error reason

    async def load_plugin(self, plugin_dir: Path) -> Optional["LoadedPlugin"]:
        """Load plugin from directory (with comprehensive error handling)."""
        plugin_id = plugin_dir.name

        try:
            # Step 1: Load manifest
            manifest_path = plugin_dir / "plugin.json"  # MVP: JSON only
            if not manifest_path.exists():
                raise PluginLoadError(f"No plugin.json found in {plugin_dir}")

            manifest = PluginManifest.from_yaml(manifest_path)
            logger.info(f"Loaded manifest for {manifest.id} v{manifest.version}")

            # Step 2: Check dependencies
            await self._check_dependencies(manifest)

            # Step 3: Load entry points (error-isolated)
            skills = await self._load_skills(manifest, plugin_dir)
            memory_provider = await self._load_memory_provider(manifest, plugin_dir)
            brain_extension = await self._load_brain_extension(manifest, plugin_dir)
            notifiers = await self._load_notifiers(manifest, plugin_dir)

            # Step 4: Call on_init lifecycle hook (if exists)
            if manifest.lifecycle and manifest.lifecycle.get("on_init"):
                await self._call_lifecycle_hook(
                    plugin_dir, manifest.lifecycle["on_init"], "init"
                )

            # Step 5: Register plugin
            plugin = LoadedPlugin(
                id=manifest.id,
                version=manifest.version,
                manifest=manifest,
                skills=skills,
                memory_provider=memory_provider,
                brain_extension=brain_extension,
                notifiers=notifiers,
                plugin_dir=plugin_dir
            )
            self.registry[manifest.id] = plugin
            logger.info(f"✅ Plugin loaded: {manifest.id} v{manifest.version}")
            return plugin

        except PluginError as e:
            # Expected error: log and mark plugin as failed (don't crash)
            self.failed_plugins[plugin_id] = str(e)
            logger.error(f"❌ Plugin load failed ({plugin_id}): {e}")
            return None

        except Exception as e:
            # Unexpected error: isolate + log
            self.failed_plugins[plugin_id] = f"Unexpected: {str(e)}"
            logger.exception(f"❌ Unexpected error loading plugin {plugin_id}")
            return None

    async def _check_dependencies(self, manifest: PluginManifest):
        """Verify plugin dependencies are satisfied (v1.1: advanced)."""
        if not manifest.dependencies:
            return

        for dep in manifest.dependencies:
            dep_id = dep.get("id")
            min_version = dep.get("min_version", "1.0")

            # Reject if dependency not found OR if dependency failed to load
            if dep_id not in self.registry:
                if dep_id in self.failed_plugins:
                    raise PluginValidationError(
                        f"Dependency failed to load: {dep_id} ({self.failed_plugins[dep_id]})"
                    )
                else:
                    raise PluginValidationError(
                        f"Dependency not found: {dep_id} (v{min_version}+)"
                    )

    async def _load_skills(self, manifest: PluginManifest, plugin_dir: Path) -> List[Dict]:
        """Load skill entry points (error-isolated per skill)."""
        skills = []
        if not manifest.skills:
            return skills

        for skill_def in manifest.skills:
            try:
                skill_id = skill_def.get("id")
                entry_point = skill_def.get("entry_point")

                if not entry_point:
                    logger.warn(f"Skill {skill_id} has no entry_point, skipping")
                    continue

                # Load entry point dynamically
                skill_func = await self._load_entry_point(plugin_dir, entry_point)
                self.loaded_skills[skill_id] = skill_func
                skills.append({**skill_def, "loaded": True})
                logger.info(f"  ✓ Skill loaded: {skill_id}")

            except Exception as e:
                # Skill load failed: continue with other skills (error isolation)
                logger.warn(f"  ✗ Skill {skill_def.get('id')} load failed: {e}")
                skills.append({**skill_def, "loaded": False, "error": str(e)})

        return skills

    async def _load_memory_provider(self, manifest: PluginManifest,
                                   plugin_dir: Path) -> Optional[Callable]:
        """Load custom memory provider (error-isolated)."""
        if not manifest.memory_provider:
            return None

        try:
            entry_point = manifest.memory_provider.get("entry_point")
            provider_class = await self._load_entry_point(plugin_dir, entry_point)
            logger.info(f"  ✓ Memory provider loaded: {manifest.memory_provider.get('id')}")
            return provider_class
        except Exception as e:
            logger.warn(f"  ✗ Memory provider load failed: {e}")
            return None

    async def _load_brain_extension(self, manifest: PluginManifest,
                                   plugin_dir: Path) -> Optional[Callable]:
        """Load custom brain extension (error-isolated)."""
        if not manifest.brain_extension:
            return None

        try:
            entry_point = manifest.brain_extension.get("entry_point")
            extension_class = await self._load_entry_point(plugin_dir, entry_point)
            logger.info(f"  ✓ Brain extension loaded: {manifest.brain_extension.get('id')}")
            return extension_class
        except Exception as e:
            logger.warn(f"  ✗ Brain extension load failed: {e}")
            return None

    async def _load_notifiers(self, manifest: PluginManifest,
                             plugin_dir: Path) -> List[Callable]:
        """Load notifier entry points (error-isolated per notifier)."""
        notifiers = []
        if not manifest.notifiers:
            return notifiers

        for notifier_def in manifest.notifiers:
            try:
                entry_point = notifier_def.get("entry_point")
                notifier_class = await self._load_entry_point(plugin_dir, entry_point)
                notifiers.append(notifier_class)
                logger.info(f"  ✓ Notifier loaded: {notifier_def.get('id')}")
            except Exception as e:
                logger.warn(f"  ✗ Notifier {notifier_def.get('id')} load failed: {e}")

        return notifiers

    async def _load_entry_point(self, plugin_dir: Path, entry_point: str) -> Callable:
        """Dynamically load a Python entry point (module:function)."""
        try:
            module_path, func_name = entry_point.rsplit(":", 1)
            module_file = plugin_dir / f"{module_path}.py"

            if not module_file.exists():
                raise FileNotFoundError(f"Module not found: {module_file}")

            # Load module dynamically
            spec = importlib.util.spec_from_file_location(module_path, module_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Get function
            func = getattr(module, func_name, None)
            if not func:
                raise AttributeError(f"Function not found in {module_path}: {func_name}")

            if not callable(func):
                raise TypeError(f"{func_name} is not callable")

            return func

        except Exception as e:
            raise PluginLoadError(f"Entry point load failed ({entry_point}): {e}")

    async def _call_lifecycle_hook(self, plugin_dir: Path, hook_path: str, hook_name: str):
        """Call plugin lifecycle hook (on_init, on_shutdown, etc)."""
        try:
            hook_func = await self._load_entry_point(plugin_dir, hook_path)
            # Validate that hook is async before awaiting
            if not inspect.iscoroutinefunction(hook_func):
                raise TypeError(f"{hook_name} hook must be async, got {type(hook_func).__name__}")
            await hook_func()
            logger.info(f"  ✓ {hook_name} hook executed")
        except Exception as e:
            logger.warn(f"  ✗ {hook_name} hook failed: {e}")

    async def unload_plugin(self, plugin_id: str):
        """Unload plugin (call on_shutdown, cleanup)."""
        if plugin_id not in self.registry:
            return

        plugin = self.registry[plugin_id]

        try:
            # Call on_shutdown hook
            if plugin.manifest.lifecycle and plugin.manifest.lifecycle.get("on_shutdown"):
                await self._call_lifecycle_hook(
                    plugin.plugin_dir,
                    plugin.manifest.lifecycle["on_shutdown"],
                    "shutdown"
                )

            # Clean up skills
            for skill_def in plugin.manifest.skills or []:
                skill_id = skill_def.get("id")
                if skill_id in self.loaded_skills:
                    del self.loaded_skills[skill_id]

            # Remove from registry
            del self.registry[plugin_id]
            logger.info(f"✅ Plugin unloaded: {plugin_id}")

        except Exception as e:
            logger.error(f"❌ Error unloading plugin {plugin_id}: {e}")

    def get_plugin(self, plugin_id: str) -> Optional["LoadedPlugin"]:
        """Retrieve loaded plugin."""
        return self.registry.get(plugin_id)

    def list_plugins(self, loaded_only: bool = False) -> List[str]:
        """List all plugins (loaded + failed)."""
        if loaded_only:
            return list(self.registry.keys())
        return list(self.registry.keys()) + list(self.failed_plugins.keys())

    def get_failed_plugins(self) -> Dict[str, str]:
        """Get plugins that failed to load (for monitoring)."""
        return dict(self.failed_plugins)

@dataclass
class LoadedPlugin:
    """A successfully loaded plugin."""
    id: str
    version: str
    manifest: PluginManifest
    skills: List[Dict]
    memory_provider: Optional[Callable]
    brain_extension: Optional[Callable]
    notifiers: List[Callable]
    plugin_dir: Path

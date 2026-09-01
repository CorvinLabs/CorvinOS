"""
Phase 4: Real Plugin Installation Backend (ADR-0511)

Handles actual plugin deployment:
- Download wheels from marketplace
- Extract to ~/.corvin/plugins/installed/{plugin_id}/
- State management (enable/disable via manifest)
- Error handling & rollback
"""

import json
import shutil
import urllib.request
from pathlib import Path
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

INSTALLED_PLUGINS_DIR = Path.home() / ".corvin" / "tenants" / "default" / "plugins" / "installed"


class PluginDeploymentError(Exception):
    """Raised on plugin deployment failure."""
    pass


def deploy_plugin(plugin_id: str, wheel_url: str, version: str) -> Dict[str, Any]:
    """
    Deploy a plugin from wheel URL.

    Steps:
    1. Download wheel from wheel_url
    2. Extract to INSTALLED_PLUGINS_DIR/{plugin_id}/
    3. Write metadata (plugin.json, installation_date)
    4. Set enabled=true in manifest

    Returns deployment result with status and metadata.
    """
    try:
        # Ensure directory exists
        INSTALLED_PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
        plugin_dir = INSTALLED_PLUGINS_DIR / plugin_id

        # Download wheel
        logger.info(f"Downloading plugin {plugin_id} from {wheel_url}")
        wheel_path = Path("/tmp") / f"{plugin_id}.whl"
        urllib.request.urlretrieve(wheel_url, wheel_path)

        # Extract wheel
        logger.info(f"Extracting {plugin_id} to {plugin_dir}")
        if plugin_dir.exists():
            shutil.rmtree(plugin_dir)
        plugin_dir.mkdir(parents=True, exist_ok=True)

        # Simple extraction (in reality would use zipfile.ZipFile)
        import zipfile
        with zipfile.ZipFile(wheel_path, 'r') as zf:
            zf.extractall(plugin_dir)

        # Write metadata
        metadata = {
            "plugin_id": plugin_id,
            "version": version,
            "installed_at": __import__('datetime').datetime.utcnow().isoformat(),
            "enabled": True,
        }
        metadata_path = plugin_dir / "metadata.json"
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2)

        # Cleanup
        wheel_path.unlink()

        logger.info(f"✅ Deployed {plugin_id} v{version}")
        return {
            "status": "deployed",
            "plugin_id": plugin_id,
            "version": version,
            "path": str(plugin_dir),
            "metadata": metadata,
        }

    except Exception as e:
        logger.error(f"❌ Deployment failed for {plugin_id}: {e}")
        # Rollback
        if plugin_dir.exists():
            shutil.rmtree(plugin_dir)
        raise PluginDeploymentError(f"Failed to deploy {plugin_id}: {e}")


def enable_plugin(plugin_id: str) -> Dict[str, Any]:
    """Enable an installed plugin."""
    plugin_dir = INSTALLED_PLUGINS_DIR / plugin_id
    metadata_path = plugin_dir / "metadata.json"

    if not metadata_path.exists():
        raise PluginDeploymentError(f"Plugin {plugin_id} not installed")

    with open(metadata_path, "r") as f:
        metadata = json.load(f)

    metadata["enabled"] = True
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2)

    logger.info(f"Enabled {plugin_id}")
    return {"status": "enabled", "plugin_id": plugin_id}


def disable_plugin(plugin_id: str) -> Dict[str, Any]:
    """Disable an installed plugin."""
    plugin_dir = INSTALLED_PLUGINS_DIR / plugin_id
    metadata_path = plugin_dir / "metadata.json"

    if not metadata_path.exists():
        raise PluginDeploymentError(f"Plugin {plugin_id} not installed")

    with open(metadata_path, "r") as f:
        metadata = json.load(f)

    metadata["enabled"] = False
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2)

    logger.info(f"Disabled {plugin_id}")
    return {"status": "disabled", "plugin_id": plugin_id}


def uninstall_plugin(plugin_id: str) -> Dict[str, Any]:
    """Uninstall (delete) a plugin."""
    plugin_dir = INSTALLED_PLUGINS_DIR / plugin_id

    if not plugin_dir.exists():
        raise PluginDeploymentError(f"Plugin {plugin_id} not installed")

    shutil.rmtree(plugin_dir)
    logger.info(f"Uninstalled {plugin_id}")
    return {"status": "uninstalled", "plugin_id": plugin_id}


def list_installed_plugins() -> Dict[str, Any]:
    """List all installed plugins with metadata."""
    if not INSTALLED_PLUGINS_DIR.exists():
        return {"plugins": [], "total": 0}

    plugins = []
    for plugin_dir in INSTALLED_PLUGINS_DIR.iterdir():
        if plugin_dir.is_dir():
            metadata_path = plugin_dir / "metadata.json"
            if metadata_path.exists():
                with open(metadata_path) as f:
                    metadata = json.load(f)
                plugins.append(metadata)

    return {
        "plugins": plugins,
        "total": len(plugins),
        "directory": str(INSTALLED_PLUGINS_DIR),
    }

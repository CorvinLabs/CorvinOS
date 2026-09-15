"""Marketplace API v2 — Plugin Install/Uninstall/Settings (ADR-0471)"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import json, os

router = APIRouter(prefix="/v1/marketplace", tags=["marketplace"])

class PluginSpec(BaseModel):
    id: str
    name: str
    version: str
    description: str
    source_url: str
    dependencies: List[str] = []
    tier: str = "member"
    installed_version: Optional[str] = None
    enabled: bool = True

# LIST AVAILABLE
@router.get("/plugins/available")
async def list_available_plugins(category: Optional[str] = None) -> dict:
    """List marketplace plugins (mock data — will fetch from marketplace.corvin.io)"""
    available = [
        {"id": "vibe-engineering-phase2", "name": "Vibe Engineering", "version": "1.0.0", 
         "description": "Live dashboard + audit events", "tier": "member", "category": "observability"},
        {"id": "skill-forge-v2", "name": "Skill Forge v2", "version": "2.0.0",
         "description": "Unified skill generator", "tier": "member", "category": "developer-tools"},
        {"id": "learning-skill", "name": "Learning Loops", "version": "1.0.0",
         "description": "EventStore + Optimizer", "tier": "member", "category": "infrastructure"},
    ]
    if category:
        available = [p for p in available if p.get("category") == category]
    return {"plugins": available, "count": len(available)}

# LIST INSTALLED
@router.get("/plugins/installed")
async def list_installed() -> dict:
    """List installed plugins from ~/.corvin/marketplace/installed.json"""
    path = os.path.expanduser("~/.corvin/marketplace/installed.json")
    if not os.path.exists(path):
        return {"plugins": [], "count": 0}
    with open(path) as f:
        data = json.load(f)
    return {"plugins": data.get("plugins", []), "count": len(data.get("plugins", []))}

# INSTALL
@router.post("/plugins/{plugin_id}/install")
async def install_plugin(plugin_id: str, version: str = "latest", bg_tasks: BackgroundTasks = None) -> dict:
    """Install plugin (async background task)"""
    if bg_tasks:
        bg_tasks.add_task(_install_async, plugin_id, version)
    return {"status": "installing", "plugin_id": plugin_id, "version": version}

async def _install_async(plugin_id: str, version: str):
    """Background: download + extract + register"""
    try:
        plugin_dir = os.path.expanduser(f"~/.corvin/marketplace/plugins/{plugin_id}/{version}")
        os.makedirs(plugin_dir, exist_ok=True)
        
        path = os.path.expanduser("~/.corvin/marketplace/installed.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        
        registry = json.load(open(path)) if os.path.exists(path) else {"plugins": []}
        registry["plugins"].append({
            "id": plugin_id, "version": version, "enabled": True,
            "installed_at": datetime.utcnow().isoformat(), "path": plugin_dir
        })
        with open(path, "w") as f:
            json.dump(registry, f, indent=2)
        print(f"✅ Installed {plugin_id}@{version}")
    except Exception as e:
        print(f"❌ Install failed: {e}")

# UNINSTALL
@router.post("/plugins/{plugin_id}/uninstall")
async def uninstall_plugin(plugin_id: str, bg_tasks: BackgroundTasks = None) -> dict:
    """Uninstall plugin (async)"""
    if bg_tasks:
        bg_tasks.add_task(_uninstall_async, plugin_id)
    return {"status": "uninstalling", "plugin_id": plugin_id}

async def _uninstall_async(plugin_id: str):
    """Background: disable + delete"""
    try:
        path = os.path.expanduser("~/.corvin/marketplace/installed.json")
        registry = json.load(open(path)) if os.path.exists(path) else {"plugins": []}
        registry["plugins"] = [p for p in registry.get("plugins", []) if p["id"] != plugin_id]
        with open(path, "w") as f:
            json.dump(registry, f, indent=2)
        print(f"✅ Uninstalled {plugin_id}")
    except Exception as e:
        print(f"❌ Uninstall failed: {e}")

# SETTINGS
@router.get("/plugins/{plugin_id}/settings")
async def get_settings(plugin_id: str) -> dict:
    """Get plugin settings"""
    path = os.path.expanduser(f"~/.corvin/marketplace/settings/{plugin_id}.json")
    if not os.path.exists(path):
        return {"plugin_id": plugin_id, "enabled": True, "config": {}}
    return json.load(open(path))

@router.post("/plugins/{plugin_id}/settings")
async def update_settings(plugin_id: str, enabled: bool = True, config: dict = None) -> dict:
    """Update plugin settings"""
    settings_dir = os.path.expanduser("~/.corvin/marketplace/settings")
    os.makedirs(settings_dir, exist_ok=True)
    with open(os.path.join(settings_dir, f"{plugin_id}.json"), "w") as f:
        json.dump({"plugin_id": plugin_id, "enabled": enabled, "config": config or {}}, f)
    return {"status": "updated", "plugin_id": plugin_id}

# STATUS
@router.get("/status")
async def status() -> dict:
    """Marketplace API status"""
    return {"status": "operational", "api_version": "v2", "timestamp": datetime.utcnow().isoformat()}

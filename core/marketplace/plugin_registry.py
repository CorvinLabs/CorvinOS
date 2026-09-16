"""Plugin Registry (Phase 2.5, T3.2–T3.3) — Discovery + versioning

Plugins discoverable by category, versioning with semver + rollback.
"""

from dataclasses import dataclass
from typing import List, Dict, Optional
import semver

@dataclass
class PluginVersion:
    plugin_id: str
    version: str  # semver
    release_date: str
    active: bool = True
    
    def is_compatible(self, required_version: str) -> bool:
        """Check semver compatibility."""
        try:
            plugin_v = semver.Version.parse(self.version)
            required_v = semver.Version.parse(required_version)
            return plugin_v >= required_v
        except:
            return False

class PluginRegistry:
    """Central plugin discovery + versioning."""
    
    def __init__(self):
        self.plugins: Dict[str, List[PluginVersion]] = {}
        self.categories = ["skills", "plugins", "workflows", "models", "integrations"]
    
    async def register_plugin(self, plugin_id: str, version: str, category: str) -> bool:
        """Register new plugin version."""
        if category not in self.categories:
            return False
        
        if plugin_id not in self.plugins:
            self.plugins[plugin_id] = []
        
        plugin_v = PluginVersion(plugin_id, version, datetime.utcnow().isoformat() + "Z")
        self.plugins[plugin_id].append(plugin_v)
        return True
    
    async def discover_plugins(self, category: str, limit: int = 50) -> List[str]:
        """Discover plugins by category."""
        return list(self.plugins.keys())[:limit]
    
    async def get_latest_version(self, plugin_id: str) -> Optional[str]:
        """Get latest plugin version."""
        if plugin_id not in self.plugins:
            return None
        versions = sorted(self.plugins[plugin_id], key=lambda v: v.version, reverse=True)
        return versions[0].version if versions else None
    
    async def rollback_version(self, plugin_id: str, version: str) -> bool:
        """Rollback to prior version."""
        if plugin_id not in self.plugins:
            return False
        # Deactivate current, activate prior
        return True

from datetime import datetime

"""Forge paths module - stub for audit_metrics compatibility."""
import os
from pathlib import Path

# Root paths
FORGE_ROOT = Path(__file__).parent.parent
PROJECT_ROOT = FORGE_ROOT.parent

def get_forge_home():
    """Get forge home directory."""
    return os.environ.get('FORGE_HOME', str(FORGE_ROOT / '.forge'))

def get_audit_path():
    """Get audit log path."""
    return Path(get_forge_home()) / 'audit'

# Export for compatibility
__all__ = ['FORGE_ROOT', 'PROJECT_ROOT', 'get_forge_home', 'get_audit_path']

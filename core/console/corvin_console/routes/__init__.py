"""Auto-import all routers from the routes package."""
from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

# Get all .py files in this directory (excluding __init__.py)
_routes_dir = Path(__file__).parent
_module_names = []

for importer, modname, ispkg in pkgutil.iter_modules([str(_routes_dir)]):
    if not modname.startswith('_'):
        _module_names.append(modname)

# Dynamically import all modules and expose their routers
for modname in sorted(_module_names):
    try:
        mod = importlib.import_module(f".{modname}", package=__name__)
        # Export the router if it exists
        if hasattr(mod, 'router'):
            globals()[modname] = mod
    except Exception:
        # Silently skip modules that fail to import
        pass

__all__ = _module_names

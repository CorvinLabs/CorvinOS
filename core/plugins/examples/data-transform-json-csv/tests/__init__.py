"""Tests for the JSON ↔ CSV Data Transformer Plugin.

This __init__.py sets up sys.path for test discovery so that relative imports work.
"""
import sys
from pathlib import Path

# Add paths EARLY so that test modules can import plugin and corvin_plugins
_TESTS_DIR = Path(__file__).resolve().parent
_PLUGIN_DIR = _TESTS_DIR.parent
_CORE_PLUGINS = _TESTS_DIR.parents[3] / "core" / "plugins"

# Insert at beginning of sys.path (higher priority)
if str(_CORE_PLUGINS) not in sys.path:
    sys.path.insert(0, str(_CORE_PLUGINS))
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

"""Source-tree sys.path bootstrap for corvin_console.

Injects the operator subtrees needed by console route modules onto sys.path
so bare imports like ``from forge import paths`` resolve without per-file
boilerplate.  Safe to call multiple times — each path is inserted at most once.

In wheel installs ``_operator_bootstrap.ensure_operator_on_path()`` has
already run (called from ``__init__.py``) and wired the vendored copies;
this module is then a no-op because the directories don't exist at the
expected source-tree locations.
"""
from __future__ import annotations

import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[2]  # corvin_console/ → console/ → core/ → repo root

_OPERATOR_PATHS: tuple[Path, ...] = (
    _REPO / "operator" / "forge",
    _REPO / "operator" / "bridges" / "shared",
    _REPO / "operator" / "bridges",
    _REPO / "operator",
    _REPO / "operator" / "voice" / "scripts",
    _REPO / "operator" / "mcp_manager",
    _REPO / "operator" / "skill-forge",
    _REPO / "operator" / "license",
    _REPO / "operator" / "cowork",
)


def _ensure() -> None:
    for p in _OPERATOR_PATHS:
        s = str(p)
        if p.is_dir() and s not in sys.path:
            sys.path.insert(0, s)


_ensure()

# Re-export the most commonly used import so route modules can do:
#   from .. import _bootstrap
#   _forge_paths = _bootstrap.forge_paths
try:
    from forge import paths as forge_paths  # type: ignore[import-not-found]
except ImportError:
    forge_paths = None  # type: ignore[assignment]

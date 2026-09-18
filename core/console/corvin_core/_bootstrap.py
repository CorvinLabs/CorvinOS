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

# The directory is ``corvin_operator/``, NOT ``operator/``. These entries said
# ``operator`` until 2026-09-18 — left over from the operator/ -> corvin_operator/
# rename, which updated the test suite but not this shim. The _ensure() guard
# below is ``p.is_dir()``, so every stale entry was skipped SILENTLY: no
# ImportError here, just an empty sys.path contribution. The failure surfaced
# three frames away as ``ModuleNotFoundError: No module named 'forge'`` from
# corvin_console/auth.py, which killed the whole console import chain and, via
# the ADR-0015 opt-in mount, removed /console AND every /v1/console/* route.
#
# It only reproduces in SOURCE-TREE mode (editable install, `install.ps1
# -Editable` / `install.sh --editable`). A wheel/PyPI install goes through
# _operator_bootstrap.ensure_operator_on_path() instead, which always used the
# correct name -- which is why the default install path never showed it.
#
# Keep this list in parity with _operator_bootstrap._OPERATOR_SUBTREES; the two
# wire the same subtrees for the two install modes, and a subtree present in one
# but not the other is a bug that only shows up on one install channel.
_OPERATOR_PATHS: tuple[Path, ...] = (
    _REPO / "corvin_operator" / "forge",
    # The INNER package dir, so a BARE ``import clag`` / ``import paths``
    # resolves. Wheel mode wires this ("forge/forge" in _OPERATOR_SUBTREES) and
    # source mode did not, so the FAIL-CLOSED consent gate could not load its
    # chain helper on an editable install -- and a permanently broken gate is
    # indistinguishable from a working one that denies. See the "forge/forge"
    # note in _operator_bootstrap.py for the fresh-install roundtrip that found
    # this class of gap on the wheel side.
    _REPO / "corvin_operator" / "forge" / "forge",
    _REPO / "corvin_operator" / "bridges" / "shared",
    _REPO / "corvin_operator" / "bridges",
    _REPO / "corvin_operator",
    _REPO / "corvin_operator" / "voice" / "scripts",
    _REPO / "corvin_operator" / "mcp_manager",
    _REPO / "corvin_operator" / "skill-forge",
    _REPO / "corvin_operator" / "license",
    _REPO / "corvin_operator" / "cowork",
    # ``import tde.*`` / ``import initial_analysis`` -- the ADR-0214/0217 Tiered
    # Delegation Engine. Same parity gap as forge/forge above.
    _REPO / "corvin_operator" / "orchestration",
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

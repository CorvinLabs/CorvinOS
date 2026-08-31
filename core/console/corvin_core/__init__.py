"""corvin_core — the OS kernel modules, extracted out of corvin_console so the
core/bridge never imports the Console (ADR-0352).

Importing corvin_core runs the operator-dependency bootstrap (Windows fcntl/resource
shim → vendored operator/ subtrees on sys.path → source-tree injection) so
``from forge import paths`` / ``import license.validator`` / ``import engine_switch``
resolve WITHOUT the Console being imported first. This is exactly what makes headless
mode (``corvinos run``) possible — the P2.2 verification found the coupling and P2.3
moves the bootstrap here where it belongs.

corvin_core is physically under core/console/ only to reuse the existing
`core/console`→wheel-root source mapping (no new packaging rule = no Windows-boot-class
risk); it is a TOP-LEVEL package, NOT part of the Console.
"""
from __future__ import annotations

# MUST be first — the no-op fcntl/resource stand-ins must land in sys.modules before
# any submodule (or vendored operator subtree) does a module-level ``import fcntl``.
from . import _wincompat  # noqa: F401

# Put the vendored operator/ subtrees on sys.path (wheel install); no-op in a
# source-tree checkout. MUST run before _bootstrap (which eagerly does
# ``from forge import paths``).
from ._operator_bootstrap import ensure_operator_on_path as _ensure_operator_on_path
_ensure_operator_on_path()

from . import _bootstrap  # noqa: F401,E402  (source-tree sys.path injection)

"""The real ``forge`` package, whatever the process bound the name to (refutation RD-2).

``corvin_operator/forge/`` is a directory WITHOUT ``__init__.py`` that contains the real
package ``corvin_operator/forge/forge/``. A process that imports ``forge`` while
``corvin_operator/`` is on ``sys.path`` and ``corvin_operator/forge/`` is not yet (the
license package lives under ``corvin_operator/``; the gateway dispatcher and pytest add it)
binds ``forge`` to that directory as an implicit NAMESPACE package and caches it in
``sys.modules``. Every later ``from forge.security_events import …`` /
``from forge.paths import …`` in that process raises ``ModuleNotFoundError`` — and the
L34/L44 gates, whose audit writers are reached exactly that way, used to approve a spawn
and write no record at all (GDPR Art. 30 gap, refutation 2026-11-05).

:func:`forge_package` fixes the primitive instead of one call site:

* the name is unbound → ``corvin_operator/forge`` is put on ``sys.path`` and ``forge`` is
  imported normally (a regular package always wins over a namespace one);
* the name is bound to a real package (has ``__file__``) or to anything that is not a
  namespace package (a test stub) → it is returned unchanged;
* the name is bound to the NAMESPACE package → ``sys.modules['forge']`` is RE-POINTED to the
  real package, loaded from ``corvin_operator/forge/forge/__init__.py`` through a proper
  package spec (``submodule_search_locations``), so the package's own relative imports and
  every absolute ``forge.X`` import inside it (``security_events`` imports ``forge.paths``)
  resolve against the real package. The namespace entries (``forge``, ``forge.*``) are
  evicted first. Nothing legitimate uses the namespace binding: it holds no module the
  real package does not. The re-point is logged once.

:func:`forge_module` returns ``forge.<name>`` from the real package. Callers on a gate path
treat a raise as "the audit writer cannot be loaded" and REFUSE (fail-closed).
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
import threading
import types
from pathlib import Path

_log = logging.getLogger(__name__)

#: ``corvin_operator/forge/forge`` — the real package directory.
FORGE_PACKAGE_DIR = Path(__file__).resolve().parents[2] / "forge" / "forge"

_LOCK = threading.RLock()
_REPOINTED = False


def _is_namespace(mod: object) -> bool:
    """True for an implicit namespace package (no ``__file__``, a ``_NamespacePath``)."""
    if getattr(mod, "__file__", None) is not None:
        return False
    path = getattr(mod, "__path__", None)
    return path is not None and type(path).__name__ == "_NamespacePath"


def _repoint_to_real_package() -> types.ModuleType:
    """Replace a namespace ``forge`` binding with the real package (caller holds the lock)."""
    global _REPOINTED
    init = FORGE_PACKAGE_DIR / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "forge", init, submodule_search_locations=[str(FORGE_PACKAGE_DIR)])
    if spec is None or spec.loader is None:
        raise ImportError(f"real forge package not loadable from {init}")
    stale = {k: v for k, v in sys.modules.items() if k == "forge" or k.startswith("forge.")}
    for k in stale:
        del sys.modules[k]
    mod = importlib.util.module_from_spec(spec)
    sys.modules["forge"] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        # Leave the process exactly as it was: a half-initialised package is worse.
        for k in [k for k in sys.modules if k == "forge" or k.startswith("forge.")]:
            del sys.modules[k]
        sys.modules.update(stale)
        raise
    if not _REPOINTED:
        _REPOINTED = True
        _log.warning("forge was bound to the namespace package %s; re-pointed to the real "
                     "package %s", list(getattr(stale.get("forge"), "__path__", []) or []),
                     FORGE_PACKAGE_DIR)
    return mod


def forge_package() -> types.ModuleType:
    """The real ``forge`` package; heals a namespace binding (see the module docstring)."""
    with _LOCK:
        cur = sys.modules.get("forge")
        if cur is None:
            parent = str(FORGE_PACKAGE_DIR.parent)
            if parent not in sys.path:
                sys.path.insert(0, parent)
            cur = importlib.import_module("forge")
        if _is_namespace(cur):
            return _repoint_to_real_package()
        return cur


def forge_module(name: str) -> types.ModuleType:
    """``forge.<name>`` from the real package. Raises when it cannot be loaded."""
    pkg = forge_package()
    full = f"forge.{name}"
    mod = sys.modules.get(full)
    if mod is not None:
        return mod
    try:
        return importlib.import_module(full)
    except ImportError:
        sub = getattr(pkg, name, None)   # a stub package that carries the module as an attribute
        if isinstance(sub, types.ModuleType):
            return sub
        raise

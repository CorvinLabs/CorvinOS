"""Shared test-support helpers for the CorvinOS suite.

Lives at the repo root (which is on sys.path for every test run) under a name
that is unique in the tree -- unlike ``conftest``, which exists twice (repo
root and ``tests/``) and resolves to whichever was imported first.
"""

# ---------------------------------------------------------------------------
# Importing modules out of operator/
#
# `operator/` cannot be imported as a package: `operator` is a Python stdlib
# module, so `from operator.marketplace.generate_index import ...` resolves to
# the stdlib and raises "'operator' is not a package".
#
# Putting `operator/` on sys.path and importing bare is not reliable either:
# the repo root is ALSO on sys.path, and several operator subdirectories
# collide with same-named modules under core/ --
#
#     marketplace -> core/plugins/marketplace.py       (wins, wrong module)
#     cli         -> core/orchestration/cli.py         (wins, wrong module)
#
# so a bare `import marketplace` silently binds the wrong file. Loading by
# explicit file path is the only form that cannot be shadowed, which is what
# this helper does.
# ---------------------------------------------------------------------------

import importlib.util as _importlib_util
import sys as _sys
import types as _types
from pathlib import Path as _Path

_OPERATOR_ROOT = _Path(__file__).resolve().parent / "operator"


def load_operator_module(relative_path: str, module_name: str | None = None):
    """Load a module from ``operator/`` by file path, shadow-proof.

    Args:
        relative_path: path under ``operator/``, e.g. ``"marketplace/generate_index.py"``
        module_name: name to register in ``sys.modules``. Defaults to a
            ``corvin_operator.``-prefixed name derived from the path, which
            cannot collide with the stdlib or with anything under ``core/``.

    Returns:
        The imported module object.
    """
    path = _OPERATOR_ROOT / relative_path
    if not path.is_file():
        raise FileNotFoundError(f"no such operator module: {path}")

    if module_name is None:
        module_name = "corvin_operator." + relative_path[:-3].replace("/", ".")

    cached = _sys.modules.get(module_name)
    if cached is not None:
        return cached

    # Create the parent packages so RELATIVE imports inside the module resolve.
    # operator/license/quota_counter.py does `from .validator import get_limit`,
    # which Python resolves against the module's parent package -- registering
    # only the leaf module leaves that parent missing and the relative import
    # raises ModuleNotFoundError. Each synthetic parent gets a __path__ pointing
    # at the real directory so submodule lookup finds the actual files.
    _parts = module_name.split(".")
    for _i in range(1, len(_parts)):
        _pkg_name = ".".join(_parts[:_i])
        if _pkg_name in _sys.modules:
            continue
        _pkg = _types.ModuleType(_pkg_name)
        # parts[0] is the synthetic "corvin_operator" root -> operator/ itself;
        # each further part descends one real directory.
        _pkg.__path__ = [str(_OPERATOR_ROOT.joinpath(*_parts[1:_i]))]
        _sys.modules[_pkg_name] = _pkg

    # Several operator modules import their siblings bare (quota_counter does
    # `from limits import LicenseLimitError`), which only resolves with their
    # own directory on sys.path. Add it -- appended, not prepended, so it can
    # never take precedence over a real top-level package.
    _pkg_dir = str(path.parent)
    if _pkg_dir not in _sys.path:
        _sys.path.append(_pkg_dir)

    spec = _importlib_util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot build import spec for {path}")
    module = _importlib_util.module_from_spec(spec)
    # Register before exec so a self-referential import inside the module
    # finds the partially initialised module instead of re-executing it.
    _sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module

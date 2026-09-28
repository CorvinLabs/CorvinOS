"""Corvin License System — ADR-0092 M1+M2, ADR-0111.

Public surface (legacy — validator.py):
    get_limit(feature)      → current limit value (FREE_TIER when no valid key)
    assert_limit(feature, requested=1)  → raises LicenseLimitError on exceed
    load_license_from_env() → call once at boot to activate a SesT key

Public surface (ADR-0111 — Sealed Offline Bundle):
    SobClient               → load / reload sob.enc from disk
    Capability              → config-dict API wrapping SobClient
    SobIssuer               → dev/test SOB issuer (simulates server)
    init_capability(sob)    → initialise module-level Capability singleton
    get_capability()        → return module-level Capability singleton

Import path: from operator/bridges/shared/ do
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))
    from license.validator import get_limit, assert_limit, load_license_from_env

ONE module per file under BOTH import names (adversarial review 2026-09-28).
This package is reachable as ``license`` (``corvin_operator/`` on sys.path —
the gateway lifespan, ``standalone.py`` and ``adapter.py`` load the licence
through ``license.validator``) and as ``corvin_operator.license`` (every
ADR-0701/0703 gate: ``corvin_operator.license.capability_api``). Python
imported each name as its OWN module object, so the licence the hosts loaded
lived in ``license.validator`` while ``capability_api`` read
``corvin_operator.license.validator`` — which nothing ever loaded. Every
paying member resolved to "free" at every capability gate (402 on
``/skills/manual``, forge refused, false ``tier: free`` audit records).
``_TwinFinder`` makes whichever name is imported first the only copy: an
import under the other name returns the SAME module object.
"""
import importlib.abc as _importlib_abc
import importlib.util as _importlib_util
import sys as _sys

_TWIN_ROOTS = ("license", "corvin_operator.license")


def _twin_name(fullname: str) -> "str | None":
    """``license.x`` <-> ``corvin_operator.license.x`` (None for anything else)."""
    for a, b in ((_TWIN_ROOTS[0], _TWIN_ROOTS[1]), (_TWIN_ROOTS[1], _TWIN_ROOTS[0])):
        if fullname == a or fullname.startswith(a + "."):
            return b + fullname[len(a):]
    return None


class _TwinLoader(_importlib_abc.Loader):
    """Hands back the module already imported under the twin name."""

    def __init__(self, module):
        self._module = module
        self._spec = getattr(module, "__spec__", None)

    def create_module(self, spec):
        return self._module

    def exec_module(self, module):
        # ``module_from_spec`` overwrote ``__spec__`` with the alias spec;
        # restore the original so the module keeps ONE identity.
        module.__spec__ = self._spec


class _TwinFinder(_importlib_abc.MetaPathFinder):
    _corvin_license_twin_finder = True

    def find_spec(self, fullname, path=None, target=None):
        twin = _twin_name(fullname)
        if twin is None:
            return None
        module = _sys.modules.get(twin)
        if module is None:
            return None   # first import of this file: load it normally
        return _importlib_util.spec_from_loader(
            fullname, _TwinLoader(module), is_package=hasattr(module, "__path__"))


if not any(getattr(f, "_corvin_license_twin_finder", False) for f in _sys.meta_path):
    _sys.meta_path.insert(0, _TwinFinder())
# No eager ``sys.modules[twin] = self``: registering the twin before its parent
# package is imported leaves ``corvin_operator.license`` unset as an ATTRIBUTE
# of ``corvin_operator``. The finder aliases on first import instead, and the
# import system then binds the attribute like for any other submodule.

from .validator import (
    get_limit,
    assert_limit,
    get_feature,
    get_custom,
    is_feature_allowed,
    load_license_from_env,
    active_tier,
    is_loaded,
)
from .limits import FREE_TIER, LicenseLimitError
from .sob import SobClient
from .capability import Capability, init_capability, get_capability
# sob_issuer is a DEV-ONLY license forge (ADR-0111) deliberately pruned from the
# shipped wheel (see hatch_build.py). It must stay OPTIONAL: importing it
# unconditionally crashed the entire `license` package on a wheel install, which
# made the chat-turn quota gate fail-closed and blocked EVERY chat turn on a
# fresh system ("daily chat-turn limit reached"). Runtime gates never need the
# forge, so degrade gracefully when it's absent.
try:
    from .sob_issuer import SobIssuer
except ImportError:  # dev-only forge not shipped in the wheel
    SobIssuer = None  # type: ignore[assignment,misc]

__all__ = [
    # Legacy (SesT / validator.py)
    "get_limit",
    "assert_limit",
    "get_feature",
    "get_custom",
    "is_feature_allowed",
    "load_license_from_env",
    "active_tier",
    "is_loaded",
    "FREE_TIER",
    "LicenseLimitError",
    # ADR-0111 (SOB / Capability)
    "SobClient",
    "Capability",
    "SobIssuer",
    "init_capability",
    "get_capability",
]

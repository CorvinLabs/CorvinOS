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
"""
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

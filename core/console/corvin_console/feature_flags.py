"""Backward-compat shim — feature_flags moved to corvin_core.feature_flags (ADR-0352 P2.2).
Aliases sys.modules to the real module so every name (public + private like
_tenant_spec) resolves for the tests + not-yet-migrated call sites."""
import sys as _sys
from corvin_core import feature_flags as _m
_sys.modules[__name__] = _m

"""Backward-compat shim — execution_context moved to corvin_core.execution_context (ADR-0352 P2.2).
Aliases sys.modules to the real module so every name (public + private like
_tenant_spec) resolves for the tests + not-yet-migrated call sites."""
import sys as _sys
from corvin_core import execution_context as _m
_sys.modules[__name__] = _m

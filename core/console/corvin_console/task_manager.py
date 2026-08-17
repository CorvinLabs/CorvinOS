"""Backward-compat shim — task_manager moved to corvin_core.task_manager (ADR-0352 P2.2).
Aliases sys.modules to the real module so every name (public + private like
_tenant_spec) resolves for the tests + not-yet-migrated call sites."""
import sys as _sys
from corvin_core import task_manager as _m
_sys.modules[__name__] = _m

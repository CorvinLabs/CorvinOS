"""Alias shim — the module moved to ``core.a2a.a2a_invite_registry`` (ADR-2258).

``import a2a_invite_registry`` (bare name, via the bridges/shared sys.path entry) still works and yields
the SAME module object as ``core.a2a.a2a_invite_registry``. Remove once no bare-name importer is left.
"""
import sys

from core.a2a import a2a_invite_registry as _moved

sys.modules[__name__] = _moved

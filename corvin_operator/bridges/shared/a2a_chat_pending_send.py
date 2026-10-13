"""Alias shim — the module moved to ``core.a2a.a2a_chat_pending_send`` (ADR-2258).

``import a2a_chat_pending_send`` (bare name, via the bridges/shared sys.path entry) still works and yields
the SAME module object as ``core.a2a.a2a_chat_pending_send``. Remove once no bare-name importer is left.
"""
import sys

from core.a2a import a2a_chat_pending_send as _moved

sys.modules[__name__] = _moved

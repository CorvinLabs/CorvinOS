"""Alias shim — the module moved to ``core.a2a.remote_trigger_client`` (ADR-2258).

``import remote_trigger_client`` (bare name, via the bridges/shared sys.path entry) still works and yields
the SAME module object as ``core.a2a.remote_trigger_client``. Remove once no bare-name importer is left.
"""
import sys

from core.a2a import remote_trigger_client as _moved

sys.modules[__name__] = _moved

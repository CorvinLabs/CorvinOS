"""Alias shim — the module moved to ``core.a2a.a2a_pending_claim`` (ADR-2258).

``import a2a_pending_claim`` (bare name, via the bridges/shared sys.path entry) still yields the SAME module
object as ``core.a2a.a2a_pending_claim``. Remove once no bare-name importer is left.
"""
import sys

try:
    from core.a2a import a2a_pending_claim as _moved
except ModuleNotFoundError:  # run as a script from bridges/shared with the repo root off sys.path
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from core.a2a import a2a_pending_claim as _moved

sys.modules[__name__] = _moved

"""The consistency layer now lives in the product: ``core/personal_kb/consistency.py`` (ADR-2329).

This module re-exports it so ``run_eval.py`` and the pinned tests measure the shipped code, not a copy.
"""
from core.personal_kb import consistency as _c
from core.personal_kb.consistency import *  # noqa: F401,F403

for _name in dir(_c):
    if _name.startswith("_") and not _name.startswith("__"):
        globals()[_name] = getattr(_c, _name)

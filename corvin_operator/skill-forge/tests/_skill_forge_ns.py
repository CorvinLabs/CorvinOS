"""Register the ``corvin_operator.skill_forge`` alias for the dashed
``corvin_operator/skill-forge/`` directory (test helper).

``skill-forge`` carries a dash, so ``corvin_operator.skill_forge`` never
resolves as a plain import. The console (``routes/autonomous_forge_routes.py``)
and ``tests/security/test_adversarial_review_phase_7_9.py`` register the same
alias; this does it for this directory's tests. Idempotent: an alias another
importer registered first is reused. Real modules are imported — nothing
under ``core`` is mocked.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

SKILL_FORGE_DIR = Path(__file__).resolve().parents[1]


def ensure() -> None:
    name = "corvin_operator.skill_forge"
    if name not in sys.modules:
        import corvin_operator  # noqa: F401 — the real parent package

        module = types.ModuleType(name)
        module.__path__ = [str(SKILL_FORGE_DIR)]
        sys.modules[name] = module


ensure()

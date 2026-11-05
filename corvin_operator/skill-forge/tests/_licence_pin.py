"""Licence-tier pin for the skill-forge tests that run as plain scripts.

Skill authoring is licence-gated (ADR-0701 G2; ADR-2095 gives the free tier
``skill_forge_per_day`` creates per UTC day). Under pytest the autouse fixture
in ``conftest.py`` pins the tier RESOLVER to ``member``. ``run-all-tests.sh``
runs these files as plain scripts — no conftest — inside ONE suite-wide
``CORVIN_HOME``, so the free daily quota is shared by every suite of the run
and the sixth create anywhere in it fails with ``LicenseLimitError``. The
``__main__`` blocks call :func:`pin`; the gate, quota counter and audit run
unmodified — only the resolver answers.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]


def pin(tier: str = "member") -> None:
    """Pin the licence tier resolver of THIS process (default ``member``)."""
    if str(_REPO) not in sys.path:
        sys.path.insert(0, str(_REPO))
    from corvin_operator.license import capability_api  # noqa: PLC0415
    capability_api.active_tier = lambda **_k: tier

"""Licence-tier pin for the forge tests that run as plain scripts.

ADR-0701 G1 made ``forge.create`` a member-tier capability, enforced
fail-closed by the REAL licence gate (``forge.registry._require_forge_create``).
An unlicensed sandbox resolves to ``free``, so every registry-mechanics test
that creates a tool is refused before it tests anything. Under pytest the
autouse fixture in ``conftest.py`` pins the tier RESOLVER to ``member``; a plain
``python3 tests/test_x.py`` run (how ``run-all-tests.sh`` drives them) never
loads conftest, so these scripts call :func:`pin` from their ``__main__`` block.
The gate, limits matrix and audit run unmodified — only the resolver answers.

Child processes (``forge.py mcp`` / CLI subcommands) do not inherit an
in-process pin; :data:`LAUNCH` is the same prelude ``test_mcp.py`` uses to start
``forge.py`` with the resolver pinned.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]

LAUNCH = (
    "import runpy, sys\n"
    "import corvin_operator.license.capability_api as _ca\n"
    "_ca.active_tier = lambda **_k: {tier!r}\n"
    "sys.argv = sys.argv[1:]\n"
    "runpy.run_path(sys.argv[0], run_name='__main__')\n"
)


def pin(tier: str = "member") -> None:
    """Pin the licence tier resolver of THIS process (default ``member``)."""
    if str(_REPO) not in sys.path:
        sys.path.insert(0, str(_REPO))
    from corvin_operator.license import capability_api  # noqa: PLC0415
    capability_api.active_tier = lambda **_k: tier


def launch_argv(script: str, *args: str, tier: str = "member") -> list[str]:
    """``[python, -c, LAUNCH, script, *args]`` — run *script* as ``__main__``
    in a child whose tier resolver is pinned."""
    return [sys.executable, "-c", LAUNCH.format(tier=tier), str(script), *args]

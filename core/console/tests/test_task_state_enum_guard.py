"""Guard: no task-lifecycle state enum outside the canonical registry (PLAN-0958 P1).

Before P1 there were nine parallel ``TaskStatus`` / ``TaskState`` enums with
different vocabularies. The canonical declaration is
``corvin_core/task_states.py``; ``TaskManager`` and ``TaskQueue`` alias it.

This test scans the production sources for ``class Task...(Status|State)(Enum)``
and fails when a definition is neither the canonical one nor listed in
``REGISTERED`` with a reason. A new task-state enum therefore fails here until
it is either aliased to ``TaskState`` or registered with its reason.

The synthetic-injection test proves the scanner is not vacuous: a planted new
enum in a temp tree is reported.
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]

_DEF = re.compile(r"^class (Task\w*(?:Status|State))\((?:str, )?Enum\)", re.MULTILINE)

_SCAN_ROOTS = ("core", "corvin_operator", "scripts")
_SKIP_PARTS = {".venv", "node_modules", "archive", ".claude", "__pycache__", "web-next"}

CANONICAL = "core/console/corvin_core/task_states.py"

#: Pre-existing task-status enums that are NOT the task lifecycle and are not
#: converted in P1. Each carries its reason; a removed definition must be removed here.
REGISTERED: dict[str, str] = {
    "core/console/corvin_console/inspection_models.py:TaskStatus":
        "inspection health vocabulary (pending/running/done/blocked/failed), not the lifecycle",
    "core/inspection/data_models.py:TaskStatus":
        "inspection health vocabulary (pending/running/done/blocked/failed), not the lifecycle",
    "core/orchestrator/task_orchestrator.py:TaskStatus":
        "orchestrator routing vocabulary (queued/routed/assigned/executing), not converted in P1",
    "core/brain/task_context_tracker.py:TaskStatus":
        "context-tracker vocabulary (running/paused), not converted in P1",
    "core/vibe_engineering/task_registry.py:TaskStatus":
        "vibe-engineering registry vocabulary (paused), not converted in P1",
    "core/nervous_system/registry.py:TaskState":
        "nervous-system registry vocabulary (created/suspended), not converted in P1",
    "core/orchestration/autonomous_task_engine.py:TaskState":
        "autonomous-engine vocabulary (paused/complete/escalated), not converted in P1",
    "core/orchestration/interrupt_protocol.py:TaskState":
        "interrupt-protocol vocabulary (paused/resumed/redirected), not converted in P1",
    "core/vibe_engineering/status_snapshot.py:TaskState":
        "vibe-engineering snapshot vocabulary (idle/awaiting_input/blocked), not converted in P1",
}


def _scan(root: Path) -> dict[str, str]:
    """Map ``relpath:ClassName`` -> file text location for every definition."""
    found: dict[str, str] = {}
    for top in _SCAN_ROOTS:
        base = root / top
        if not base.is_dir():
            continue
        for py in base.rglob("*.py"):
            parts = set(py.relative_to(root).parts)
            if parts & _SKIP_PARTS:
                continue
            rel = py.relative_to(root).as_posix()
            if "/tests/" in f"/{rel}" or py.name.startswith("test_") or py.name.endswith("_test.py"):
                continue
            try:
                text = py.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for m in _DEF.finditer(text):
                found[f"{rel}:{m.group(1)}"] = rel
    return found


def _unregistered(found: dict[str, str]) -> list[str]:
    return sorted(
        key for key in found
        if key != f"{CANONICAL}:TaskState" and key not in REGISTERED
    )


def test_every_task_state_enum_is_canonical_or_registered():
    found = _scan(_REPO)
    # Positive control: the scanner really sees the canonical declaration and the
    # registered legacy ones. Without this a broken glob would pass vacuously.
    assert f"{CANONICAL}:TaskState" in found
    for key in REGISTERED:
        assert key in found, f"registered enum no longer defined — drop it from REGISTERED: {key}"
    assert _unregistered(found) == [], (
        "new task-lifecycle state enum(s) outside task_states.py — alias TaskState "
        f"or register with a reason in REGISTERED: {_unregistered(found)}"
    )


def test_guard_fails_on_a_new_unregistered_task_state_enum(tmp_path):
    """Injection: a new ``TaskPhaseState`` in a temp tree must be reported."""
    module = tmp_path / "core" / "newfeature" / "phases.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "from enum import Enum\n\n\nclass TaskPhaseState(str, Enum):\n    OPEN = 'open'\n",
        encoding="utf-8",
    )
    found = _scan(tmp_path)
    assert "core/newfeature/phases.py:TaskPhaseState" in found
    assert _unregistered(found) == ["core/newfeature/phases.py:TaskPhaseState"]

"""Regression: a test must not leak a faked spawner into the rest of the run.

Eight console test modules replace ``asyncio.create_subprocess_exec`` with a
MagicMock-returning stub so a chat turn never really spawns ``claude``. That
attribute lives on the *stdlib* ``asyncio`` module object, so the replacement is
process-wide. On 2026-09-07 exactly one of them forgot to restore it and the
console suite stopped completing: Playwright launches its node driver through
``asyncio.create_subprocess_exec``, so ``test_browser_automation.py`` got a
MagicMock instead of a driver and awaited a pipe nobody would ever write to —
forever, at ~15% of the run, immune to the usual ``--timeout`` styles.

``conftest._isolate_stdlib_spawners`` restores the spawn entry points after every
test. These two tests pin that contract through the real boundary (the pytest
fixture actually running around them), in file order: the first leaks, the second
asserts the leak did not survive.
"""
from __future__ import annotations

import asyncio
import subprocess

# Captured at import time — before any test in this file has run, and before any
# fixture could have swapped it.
_PRISTINE_EXEC = asyncio.create_subprocess_exec
_PRISTINE_POPEN = subprocess.Popen


async def _never_spawns(*a, **kw):  # pragma: no cover - never awaited
    raise AssertionError("leaked fake spawner was called")


def test_a_leaks_a_fake_spawner():
    """Deliberately do what the eight chat-runtime tests do, with no cleanup."""
    asyncio.create_subprocess_exec = _never_spawns  # type: ignore[assignment]
    subprocess.Popen = _never_spawns               # type: ignore[assignment]
    assert asyncio.create_subprocess_exec is _never_spawns


def test_b_leak_did_not_survive_into_the_next_test():
    """The autouse fixture must have handed the stdlib originals back."""
    assert asyncio.create_subprocess_exec is _PRISTINE_EXEC, (
        "conftest._isolate_stdlib_spawners did not restore "
        "asyncio.create_subprocess_exec — a faked spawner leaking into the rest "
        "of the run wedges every Playwright test that follows it")
    assert subprocess.Popen is _PRISTINE_POPEN

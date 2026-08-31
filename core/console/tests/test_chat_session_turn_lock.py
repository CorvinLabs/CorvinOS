"""Regression: concurrent turns on the SAME (tenant, sid) must be serialized,
not interleaved (adversarial review finding).

Before this fix, `chat_stream`'s `_run_turn` had no concurrency control at
all: two browser tabs open on the same chat session both load their own
in-memory `WebChatSession` copy and, if both send a first message
concurrently, both compute `resume=False` and spawn independent,
non-`--continue` engine subprocesses in the SAME workdir — on each tab's
next message, `--continue` resumes whichever transcript for that cwd has
the most recent mtime (possibly the OTHER tab's), a genuine cross-tab
conversation-bleed. `_session_turn_lock()` gives every `(tenant_id, sid)`
pair a shared `asyncio.Lock` so `_run_turn` bodies for the same session
never run concurrently, while different sessions remain fully independent.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
for p in ("core/console", "operator/bridges/shared", "operator/forge"):
    sys.path.insert(0, str(_REPO / p))

import corvin_console.routes.chat as chat_routes  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_lock_registry():
    chat_routes._SESSION_TURN_LOCKS.clear()
    yield
    chat_routes._SESSION_TURN_LOCKS.clear()


def test_same_key_returns_the_same_lock_instance():
    lock_a = chat_routes._session_turn_lock("_default", "sid-1")
    lock_b = chat_routes._session_turn_lock("_default", "sid-1")
    assert lock_a is lock_b


def test_different_sid_gets_a_different_lock_instance():
    lock_a = chat_routes._session_turn_lock("_default", "sid-1")
    lock_b = chat_routes._session_turn_lock("_default", "sid-2")
    assert lock_a is not lock_b


def test_different_tenant_same_sid_gets_a_different_lock_instance():
    lock_a = chat_routes._session_turn_lock("tenant-a", "sid-1")
    lock_b = chat_routes._session_turn_lock("tenant-b", "sid-1")
    assert lock_a is not lock_b


def test_two_turns_on_the_same_session_never_run_concurrently():
    """Simulates two tabs both starting a turn on the same session at once:
    the second turn must not enter its critical section until the first
    has fully completed."""
    events: list[str] = []
    active_count = {"n": 0}

    async def _fake_turn(label: str):
        async with chat_routes._session_turn_lock("_default", "sid-shared"):
            active_count["n"] += 1
            events.append(f"{label}-start")
            assert active_count["n"] == 1, "two turns ran concurrently on the same session"
            await asyncio.sleep(0.02)
            events.append(f"{label}-end")
            active_count["n"] -= 1

    async def _run():
        await asyncio.gather(_fake_turn("tab-A"), _fake_turn("tab-B"))

    asyncio.run(_run())

    # One tab must fully finish (start, end) before the other's "start".
    assert events in (
        ["tab-A-start", "tab-A-end", "tab-B-start", "tab-B-end"],
        ["tab-B-start", "tab-B-end", "tab-A-start", "tab-A-end"],
    ), events


def test_turns_on_different_sessions_run_concurrently():
    """Sanity counterpart: the lock must NOT serialize unrelated sessions."""
    order: list[str] = []

    async def _fake_turn(sid: str, label: str, delay: float):
        async with chat_routes._session_turn_lock("_default", sid):
            order.append(f"{label}-start")
            await asyncio.sleep(delay)
            order.append(f"{label}-end")

    async def _run():
        await asyncio.gather(
            _fake_turn("sid-X", "X", 0.03),
            _fake_turn("sid-Y", "Y", 0.01),
        )

    asyncio.run(_run())

    # Y (shorter delay, different session) must finish before X even though
    # X started first — proves the two sessions were NOT serialized against
    # each other.
    assert order.index("Y-end") < order.index("X-end")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

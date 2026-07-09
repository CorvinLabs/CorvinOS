"""Repo-wide pytest tripwire: tests must never destroy live operator state.

This is the third incarnation of the "test contaminates real operator state"
class (bridge-suite settings.json contamination, console sys.modules/env
pollution, and the 2026-07-08 uninstall-test wipe of the running bridge's
in-repo .corvin — session state, budgets, and the hash-chained audit log were
deleted by a green `pytest tests/test_uninstall_windows_autostart.py` run).

The guard is detection-only: it takes a cheap snapshot of the protected live
roots before every test and fails the test loudly if any of them disappeared.
It never redirects or mutates anything itself, so it cannot break legitimate
tests — a test only fails here if it (or code it invoked) deleted real state,
which is always a bug in the test's isolation.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent


def _protected_state() -> "dict[str, object] | None":
    """Snapshot the live roots a mis-isolated test has historically deleted.

    Returns None when the snapshot itself cannot be taken (missing HOME on
    a bare Windows CI, permission error, or a concurrent writer racing the
    iterdir — a live bridge legitimately runs next to pytest in this repo).
    The tripwire then skips its comparison for that test instead of
    erroring an innocent test with a raw traceback.
    """
    try:
        home = Path.home()
        repo_corvin = _REPO_ROOT / ".corvin"
        systemd_user = home / ".config" / "systemd" / "user"
        return {
            "repo .corvin top-level entries": (
                frozenset(p.name for p in repo_corvin.iterdir())
                if repo_corvin.is_dir() else None
            ),
            "~/.config/corvin-voice": (home / ".config" / "corvin-voice").is_dir(),
            "~/.config/systemd/user corvin-* units": (
                frozenset(p.name for p in systemd_user.glob("corvin-*"))
                if systemd_user.is_dir() else None
            ),
            "~/.claude/plugins/cache/corvin-voice-local": (
                home / ".claude" / "plugins" / "cache" / "corvin-voice-local"
            ).is_dir(),
        }
    except (OSError, RuntimeError):
        return None


@pytest.fixture(autouse=True)
def _live_state_tripwire(request: pytest.FixtureRequest):
    before = _protected_state()
    yield
    if before is None:
        return
    after = _protected_state()
    if after is None:
        return
    violations: list[str] = []
    for label, prev in before.items():
        cur = after[label]
        if isinstance(prev, frozenset):
            gone = prev - (cur if isinstance(cur, frozenset) else frozenset())
            if cur is None and prev:
                violations.append(f"{label}: directory itself was DELETED")
            elif gone:
                violations.append(f"{label}: deleted {sorted(gone)}")
        elif prev is True and cur is not True:
            violations.append(f"{label}: was DELETED")
    if violations:
        pytest.fail(
            "LIVE OPERATOR STATE DESTROYED by this test (isolation bug — "
            "inject sandbox roots instead of touching real paths):\n  "
            + "\n  ".join(violations),
            pytrace=False,
        )

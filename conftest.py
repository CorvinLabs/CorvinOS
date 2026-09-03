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

# The protected paths, resolved ONCE at import time — before any test can patch
# `HOME` or `Path.home`.
#
# Resolving them per snapshot was a false-positive generator, and a guard that
# cries wolf about the exact incident it exists to catch is worse than no guard.
# Two tests in `tests/` monkeypatch the home directory (one via
# `monkeypatch.setenv("HOME", tmp_path)`, one via `monkeypatch.setattr(Path,
# "home", …)` — which patches the shared `pathlib.Path` class this file uses
# too). Fixture teardown for those patches runs AFTER this tripwire's teardown,
# so the "after" snapshot resolved `~` to an empty tmp dir, found no
# `corvin-voice` and no `corvin-*` units there, and reported:
#
#   LIVE OPERATOR STATE DESTROYED by this test
#     ~/.config/corvin-voice: was DELETED
#     ~/.config/systemd/user corvin-* units: directory itself was DELETED
#
# while both were fully intact on disk. Verified 2026-07-28: 30 corvin-* units
# present and every service running, immediately after the failure.
#
# Freezing the paths also makes the guard STRICTER, which is the point: a test
# that patches `HOME` and then deletes the REAL `~/.config/corvin-voice` through
# some other route is now still caught, whereas before the patched lookup could
# have hidden it.
def _frozen_paths() -> "dict[str, Path] | None":
    try:
        home = Path.home()
    except (OSError, RuntimeError):
        return None
    return {
        "repo_corvin": _REPO_ROOT / ".corvin",
        "voice_config": home / ".config" / "corvin-voice",
        "systemd_user": home / ".config" / "systemd" / "user",
        "plugin_cache": home / ".claude" / "plugins" / "cache" / "corvin-voice-local",
    }


_PROTECTED_PATHS = _frozen_paths()


def _protected_state(
    paths: "dict[str, Path] | None" = None,
) -> "dict[str, object] | None":
    """Snapshot the live roots a mis-isolated test has historically deleted.

    Returns None when the snapshot itself cannot be taken (missing HOME on
    a bare Windows CI, permission error, or a concurrent writer racing the
    iterdir — a live bridge legitimately runs next to pytest in this repo).
    The tripwire then skips its comparison for that test instead of
    erroring an innocent test with a raw traceback.

    Reads only :data:`_PROTECTED_PATHS`, never ``Path.home()`` — see the note
    above that constant.

    ``paths`` overrides the roots for ONE call, so
    ``tests/test_live_state_tripwire.py`` can exercise the comparison against a
    sandbox tree. It is a parameter rather than a monkeypatch of the module
    global for the same fixture-ordering reason this whole guard was mis-firing:
    a patched global is still in place when the tripwire's own teardown runs, so
    the guard would read the sandbox and report every real entry as deleted.
    """
    p = paths if paths is not None else _PROTECTED_PATHS
    if p is None:
        return None
    try:
        return {
            "repo .corvin top-level entries": (
                frozenset(q.name for q in p["repo_corvin"].iterdir())
                if p["repo_corvin"].is_dir() else None
            ),
            "~/.config/corvin-voice": p["voice_config"].is_dir(),
            "~/.config/systemd/user corvin-* units": (
                frozenset(q.name for q in p["systemd_user"].glob("corvin-*"))
                if p["systemd_user"].is_dir() else None
            ),
            "~/.claude/plugins/cache/corvin-voice-local": p["plugin_cache"].is_dir(),
        }
    except Exception:  # noqa: BLE001
        # Deliberately broad, matching this function's stated contract: "returns
        # None when the snapshot itself cannot be taken … the tripwire then skips
        # its comparison for that test instead of erroring an innocent test with a
        # raw traceback." (OSError, RuntimeError) was too narrow for the commonest
        # real cause — a test that monkeypatches `Path.stat`/`is_dir` GLOBALLY for
        # its own subject (e.g. test_voice_session_summary stubs
        # `Path.stat -> Mock(st_size=100)` to fake an audio file). The teardown
        # snapshot then hit `S_ISDIR(Mock)` → TypeError, which escaped and turned
        # two PASSING tests into teardown ERRORs. A guard that cannot take its
        # snapshot must stand down, never fail the test it is protecting.
        return None


@pytest.fixture(autouse=True)
def _isolated_bridge_outbox(monkeypatch, tmp_path):
    """Never let a test queue a real message for a real messenger.

    Fourth incarnation of the class this file guards: the workflow
    `deliver`/`ask_human`/`answer` node types write their envelope straight
    into `operator/bridges/shared/outbox/`, which the LIVE Discord/WhatsApp
    daemons poll. Every test run of those nodes therefore handed the running
    bridge a real send job — 724 of them, addressed to the test placeholder
    chat_id "owner-chat", were sitting in the Discord dead-letter dir on
    2026-07-26, each one costing a REST round-trip against Discord's
    invalid-request budget.

    Redirect the outbox to tmp for every test. A test that needs a specific
    path still wins: its own monkeypatch.setenv runs after this fixture.
    """
    monkeypatch.setenv("ADAPTER_OUTBOX", str(tmp_path / "outbox"))


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


# ── Gateway suite: loopback peer for TestClient (adversarial review E-09) ───────
# ``corvin_gateway.app._jwt_guard`` requires a Bearer JWT from every NON-loopback
# peer; Starlette's TestClient reports ("testclient", 50000), which the guard
# treats fail-closed as remote. The gateway suite models the local deployment,
# so every TestClient created from a test under core/gateway/tests/ defaults to
# ("127.0.0.1", 50000); an explicit ``client=`` always wins. Lives here (scoped
# by path) because a conftest.py inside core/gateway/tests/ collides with
# tests/conftest.py — both packages are named ``tests`` on sys.path.
import pytest as _pytest  # noqa: E402

_GATEWAY_TESTS = Path(__file__).resolve().parent / "core" / "gateway" / "tests"
_CONSOLE_TESTS = Path(__file__).resolve().parent / "core" / "console" / "tests"
_LOOPBACK_PEER = ("127.0.0.1", 50000)


@_pytest.fixture(autouse=True)
def _gateway_loopback_test_client(request, monkeypatch):
    try:
        _p = Path(str(request.node.fspath)).resolve()
        # The console suite drives the gateway app too (local-deployment model).
        under_gateway = _p.is_relative_to(_GATEWAY_TESTS) or _p.is_relative_to(_CONSOLE_TESTS)
    except Exception:  # noqa: BLE001
        under_gateway = False
    if not under_gateway:
        yield
        return
    from starlette.testclient import TestClient

    original_init = TestClient.__init__

    def _init(self, *args, **kwargs):
        if "client" not in kwargs:
            kwargs["client"] = _LOOPBACK_PEER
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(TestClient, "__init__", _init)
    yield

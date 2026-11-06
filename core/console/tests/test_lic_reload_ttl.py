"""The per-request licence reload (auth._compute_lic_proof) runs at most every
LIC_RELOAD_TTL_S — but a changed or new token file goes through at once."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "corvin_operator"))

from corvin_console import auth  # noqa: E402


@pytest.fixture
def lic(monkeypatch, tmp_path):
    from license import validator as lv

    cfg = tmp_path / "cfg"
    home = tmp_path / "home"
    (home / "global").mkdir(parents=True)
    cfg.mkdir()
    calls = []
    monkeypatch.setattr(lv, "_config_dir", lambda: cfg)
    monkeypatch.setattr(lv, "_CORVIN_HOME_SNAPSHOT", home)
    monkeypatch.setattr(lv, "reload_from_disk", lambda: calls.append(time.monotonic()))
    monkeypatch.setattr(auth, "_lic_reload_at", 0.0)
    monkeypatch.setattr(auth, "_lic_files_sig", None)
    return calls, cfg, home


def test_unchanged_files_reload_once_per_ttl(lic, monkeypatch):
    calls, _cfg, _home = lic
    for _ in range(20):
        auth._maybe_reload_license()
    assert len(calls) == 1
    monkeypatch.setattr(auth, "_lic_reload_at", time.monotonic() - auth.LIC_RELOAD_TTL_S - 1)
    auth._maybe_reload_license()
    assert len(calls) == 2


@pytest.mark.parametrize("which", ["session", "global"])
def test_a_new_or_changed_token_file_bypasses_the_ttl(lic, which):
    calls, cfg, home = lic
    auth._maybe_reload_license()
    assert len(calls) == 1
    p = cfg / "session.key" if which == "session" else home / "global" / "license.key"
    p.write_text("token-1", encoding="utf-8")
    auth._maybe_reload_license()
    assert len(calls) == 2
    auth._maybe_reload_license()
    assert len(calls) == 2
    p.write_text("token-2-longer", encoding="utf-8")
    os.utime(p, ns=(p.stat().st_atime_ns, p.stat().st_mtime_ns + 1_000_000))
    auth._maybe_reload_license()
    assert len(calls) == 3
    p.unlink()
    auth._maybe_reload_license()
    assert len(calls) == 4


def test_proof_still_computed_on_every_call(lic, monkeypatch):
    import license.feature_lattice as fl

    calls, _c, _h = lic
    seen = []
    monkeypatch.setattr(fl, "session_lic_proof", lambda sid: seen.append(sid) or f"p-{sid}")
    assert auth._compute_lic_proof("a") == "p-a"
    assert auth._compute_lic_proof("b") == "p-b"
    assert seen == ["a", "b"] and len(calls) == 1


def test_a_skipped_request_still_waits_for_a_reload_in_flight(lic, monkeypatch):
    """Review 2026-11-06: before the TTL every request blocked on the validator's lock
    while a reload installed a new root key; a skipped request must keep doing that, or
    it computes its proof against the old root (spurious 401 / stale acceptance)."""
    import threading

    from license import validator as lv

    calls, _c, _h = lic
    lock = threading.Lock()
    monkeypatch.setattr(lv, "_RELOAD_LOCK", lock, raising=False)
    auth._maybe_reload_license()                       # within the TTL from here on
    assert len(calls) == 1
    lock.acquire()                                     # a reload is in flight elsewhere
    released_at = {}

    def release():
        time.sleep(0.5)
        released_at["t"] = time.monotonic()
        lock.release()

    t = threading.Thread(target=release)
    t.start()
    auth._maybe_reload_license()
    done = time.monotonic()
    t.join()
    assert len(calls) == 1                             # skipped, not re-run ...
    assert done >= released_at["t"]                    # ... but it waited for the reload

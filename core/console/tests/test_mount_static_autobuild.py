"""The console SPA auto-build never runs under pytest and never builds straight into dist/.

Review 2026-11-05: ``mount_static`` runs at import time; when ``dist/index.html``
was missing it ran ``npm run build`` INTO ``dist/`` — vite empties its outDir first,
so every concurrent test process that imported the console while the file was
missing started its own build into the same directory (three at once measured),
and while ``dist/`` was empty the LIVE console served from this checkout had no
SPA. Now: under pytest (or ``CORVIN_CONSOLE_NO_AUTOBUILD=1``) no build at all;
otherwise the build goes to ``dist.next/`` and is swapped in atomically under a
lock, so ``/console`` keeps answering.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from corvin_console import app as console_app


@pytest.fixture
def web_next(tmp_path, monkeypatch):
    root = tmp_path / "web-next"
    root.mkdir()
    monkeypatch.setattr(console_app, "_NEXT_DIST_DIR", root / "dist")
    monkeypatch.setattr(console_app, "headless_enabled", lambda: False)
    return root


def test_under_pytest_a_missing_dist_never_starts_a_build(web_next, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(console_app, "_run_tree_safe",
                        lambda argv, **kw: calls.append(list(argv)))
    app = FastAPI()
    console_app.mount_static(app, url_prefix="/console")
    assert calls == [], "a test process must never build the operator's frontend"
    assert not (web_next / "dist").exists()
    with TestClient(app) as client:
        r = client.get("/console/")
    assert r.status_code == 503
    assert "build failed" in r.text


def test_the_operator_off_switch_also_forbids_the_build(web_next, monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("CORVIN_PYTEST_SESSION_SANDBOX", raising=False)
    monkeypatch.setenv("CORVIN_CONSOLE_NO_AUTOBUILD", "1")
    assert console_app._autobuild_forbidden() is True


def test_outside_pytest_the_build_lands_in_dist_next_and_is_swapped_in(web_next, monkeypatch):
    """Drive the real helper with a fake npm: it must build into dist.next, keep the
    old dist/ as dist.prev and never leave dist/ empty at any point."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("CORVIN_PYTEST_SESSION_SANDBOX", raising=False)
    monkeypatch.delenv("CORVIN_CONSOLE_NO_AUTOBUILD", raising=False)
    (web_next / "node_modules").mkdir()
    old = web_next / "dist"
    old.mkdir()
    (old / "stale.txt").write_text("old build without index.html")
    seen: list[list[str]] = []

    def fake_npm(argv, *, cwd, **kw):
        seen.append(list(argv))
        assert "--outDir" in argv and argv[argv.index("--outDir") + 1] == "dist.next"
        # dist/ is untouched while "vite" runs
        assert (old / "stale.txt").exists()
        out = Path(cwd) / "dist.next"
        out.mkdir()
        (out / "index.html").write_text("<html>new</html>")

    monkeypatch.setattr(console_app, "_run_tree_safe", fake_npm)
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/npm")

    ok = console_app._autobuild_into_next_and_swap(logging.getLogger("test"))
    assert ok is True
    assert (web_next / "dist" / "index.html").read_text() == "<html>new</html>"
    assert (web_next / "dist.prev" / "stale.txt").exists()
    assert not (web_next / "dist.next").exists()
    assert [a[:3] for a in seen] == [["/usr/bin/npm", "run", "build"]], "node_modules present: no npm install"
    assert os.path.exists(web_next / ".autobuild.lock")


def test_a_build_that_produces_no_index_is_not_swapped_in(web_next, monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("CORVIN_PYTEST_SESSION_SANDBOX", raising=False)
    (web_next / "node_modules").mkdir()
    old = web_next / "dist"
    old.mkdir()
    (old / "index.html").write_text("<html>old</html>")

    def broken_npm(argv, *, cwd, **kw):
        (Path(cwd) / "dist.next").mkdir()  # empty: no index.html

    monkeypatch.setattr(console_app, "_run_tree_safe", broken_npm)
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/npm")
    # the helper is only called when dist/index.html is missing; simulate that
    (old / "index.html").unlink()
    ok = console_app._autobuild_into_next_and_swap(logging.getLogger("test"))
    assert ok is False
    assert old.exists() and not (web_next / "dist.prev").exists(), "a failed build swaps nothing"

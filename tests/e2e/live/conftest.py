"""Live (real ``claude`` CLI) tests: every test runs in a temp cwd.

A real ``claude -p`` turn — and any worker it spawns — resolves relative paths
against its cwd, and the operator's global Claude Code hooks drop state
(.claude/, .ldd/) there too. Run from the repo root, a live test once left a
``lighthouse.txt`` in the checkout (round-1 adversarial review, 2026-09-27).
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _live_temp_cwd(tmp_path, monkeypatch):
    work = tmp_path / "live-cwd"
    work.mkdir()
    monkeypatch.chdir(work)
    yield work

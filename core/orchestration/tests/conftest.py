"""Fixtures for core/orchestration tests.

The brain stack (``TaskBrain`` → ``ContextInitializer`` → ``MemoryCoordinator``)
is fail-closed on an unset ``CORVIN_HOME`` (raises instead of guessing
``~/.corvin``). Tests therefore get a throw-away runtime root — never the
live one.
"""

import pytest


@pytest.fixture(autouse=True)
def _corvin_home(tmp_path, monkeypatch):
    home = tmp_path / "corvin_home"
    (home / "tenants" / "_default" / "global").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    yield home

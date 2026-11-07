"""``bridges/shared/audit.py`` executes its sibling ``paths.py`` once per process.

Both loaders (``_load_corvin_home``, ``_load_tenant_audit_chain``) used to ``exec_module``
the file on EVERY call, and ``audit_path()`` calls one of them on every audit event — so
every chain write re-ran a module import; on the console's loop thread that was a 2+ s
stall under GIL contention (loop-stall.log, 2026-11-07). The exported functions read the
environment at call time, so the cache changes nothing a caller can observe.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "corvin_operator" / "bridges" / "shared"))

import audit  # noqa: E402


def test_paths_module_is_executed_once(monkeypatch):
    monkeypatch.setattr(audit, "_paths_module_cache", None)
    real = importlib.util.spec_from_file_location
    calls: list[str] = []

    def counting(name, location, *a, **k):
        calls.append(str(location))
        return real(name, location, *a, **k)

    monkeypatch.setattr(importlib.util, "spec_from_file_location", counting)
    ch = audit._load_corvin_home()
    tac = audit._load_tenant_audit_chain()
    audit._load_corvin_home()
    audit._load_tenant_audit_chain()
    assert len(calls) == 1
    assert callable(ch) and callable(tac)


def test_cached_functions_still_read_the_environment_at_call_time(monkeypatch, tmp_path):
    monkeypatch.setattr(audit, "_paths_module_cache", None)
    corvin_home = audit._load_corvin_home()
    a = tmp_path / "a"
    b = tmp_path / "b"
    monkeypatch.setenv("CORVIN_HOME", str(a))
    assert Path(corvin_home()).resolve() == a.resolve()
    monkeypatch.setenv("CORVIN_HOME", str(b))
    assert Path(corvin_home()).resolve() == b.resolve()

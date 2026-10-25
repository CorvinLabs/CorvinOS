#!/usr/bin/env python3
"""Pins how an ACS worker is really spawned (T-0237).

ADR-0114 and two docstrings claimed the delegated branch "dispatches through the
WorkerEngine layer" ("M3 WorkerEngine fold-in"). The code does not: each worker is
a ``claude -p`` subprocess started by ``acs_runtime._call_worker_sync`` with
``subprocess.Popen``, JSON output, the prompt on stdin. No L22 WorkerEngine object
(``agents.claude_code.ClaudeCodeEngine``) is constructed. This test fixes that
fact so a future fold-in has to change it on purpose, together with the docs.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
_REPO = HERE.parents[2]
for _p in (HERE, _REPO / "corvin_operator" / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import acs_runtime  # type: ignore  # noqa: E402
from agents import claude_code as _cc  # type: ignore  # noqa: E402


class _Spawned(Exception):
    """Raised by the fake Popen once the argv is recorded."""


def test_worker_is_a_claude_p_subprocess_not_a_worker_engine(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    fake_bin = tmp_path / "claude"
    fake_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_bin.chmod(fake_bin.stat().st_mode | stat.S_IEXEC)

    monkeypatch.setattr(acs_runtime, "_resolve_worker_engine", lambda model, tid=None: ("claude_code", model))
    monkeypatch.setattr(acs_runtime, "_assert_engine_licensed", lambda engine_id: None)
    monkeypatch.setattr(acs_runtime, "_claude_binary", lambda: str(fake_bin))
    monkeypatch.setattr(acs_runtime, "_apply_provider_redirect", lambda env, tid: None)

    def _no_engine(*args, **kwargs):  # pragma: no cover - only runs on a regression
        raise AssertionError("an ACS worker constructed a WorkerEngine")

    monkeypatch.setattr(_cc.ClaudeCodeEngine, "__init__", _no_engine)

    recorded: dict = {}

    def _fake_popen(argv, **kwargs):
        recorded["argv"] = list(argv) if isinstance(argv, (list, tuple)) else argv
        recorded["stdin"] = kwargs.get("stdin")
        raise _Spawned()

    monkeypatch.setattr(acs_runtime.subprocess, "Popen", _fake_popen)

    with pytest.raises(_Spawned):
        acs_runtime._call_worker_sync("do the subtask", "system text", "claude-test-model", {})

    argv = recorded["argv"]
    joined = argv if isinstance(argv, str) else " ".join(argv)
    assert str(fake_bin) in joined, joined
    assert " -p " in f" {joined} ", joined
    assert "--output-format json" in joined, joined
    assert "--model claude-test-model" in joined, joined
    assert recorded["stdin"] is acs_runtime.subprocess.PIPE, "the prompt travels on stdin"
    if os.name != "nt":
        assert isinstance(argv, list) and argv[0] == str(fake_bin), argv

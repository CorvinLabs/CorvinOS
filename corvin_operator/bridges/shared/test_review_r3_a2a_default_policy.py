"""Review 2026-11-19 round 3, R3-BR-8: ``spawn_a2a_worker`` without a tool policy.

A caller that passed no ``disallowed_tools`` got ``exposed_tools(None, None)`` - every
grantable tool (Bash, WebFetch, Read, Write, Agent). The spawn primitive now applies the
policy of an origin that grants nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import a2a_tool_policy as _policy  # noqa: E402
import a2a_worker  # noqa: E402
from test_review_2026_11_19_a2a import _ArgvEngine, _tools_arg, worker_env  # noqa: E402,F401


def _argv(**kw) -> list:
    sink: list = []
    res = a2a_worker.spawn_a2a_worker(
        instruction="summarise the attached notes", origin_id="peer-1", task_id="t-r3",
        persona="assistant", ttl_s=30, engine_factory=lambda: _ArgvEngine(sink), **kw)
    assert res.status == "ok", (res.status, res.error)
    assert len(sink) == 1
    return sink[0]


def test_positive_control_no_policy_used_to_mean_every_grantable_tool():
    assert set(_policy.exposed_tools(None, None)) == set(_policy.GRANTABLE_TOOLS)
    assert "Bash" in _policy.GRANTABLE_TOOLS and "WebFetch" in _policy.GRANTABLE_TOOLS


def test_a_spawn_without_a_policy_gets_no_tools(worker_env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(a2a_worker, "_active_platform_id", lambda env: "")
    argv = _argv()
    assert _tools_arg(argv) == [], argv
    denied = argv[argv.index("--disallowedTools") + 1] if "--disallowedTools" in argv else ""
    for name in ("Bash", "WebFetch", "Read", "Write", "Agent"):
        assert name in denied, (name, argv)


def test_allowed_tools_alone_cannot_grant_a_class(worker_env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(a2a_worker, "_active_platform_id", lambda env: "")
    argv = _argv(allowed_tools=["Bash", "Read", "WebFetch"])
    assert _tools_arg(argv) == [], argv


def test_an_explicit_policy_is_honoured_unchanged(worker_env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(a2a_worker, "_active_platform_id", lambda env: "")
    policy = _policy.derive_tool_policy({"allow_read_files": True})
    argv = _argv(allowed_tools=policy.allowed_tools, disallowed_tools=policy.disallowed_tools)
    assert sorted(_tools_arg(argv)) == sorted(
        t for t in _policy.READ_TOOLS if t not in _policy.LEGACY_TOOL_NAMES)


def test_an_unloadable_policy_refuses_the_spawn(worker_env, monkeypatch):  # noqa: F811
    def _boom():
        raise ImportError("a2a_tool_policy unavailable")

    monkeypatch.setattr(a2a_worker, "_default_deny_tools", _boom)
    calls: list = []
    res = a2a_worker.spawn_a2a_worker(
        instruction="summarise", origin_id="peer-1", task_id="t-r3b", persona="assistant",
        ttl_s=30, engine_factory=lambda: _ArgvEngine(calls))
    assert res.status == "rejected" and "tool-policy-unavailable" in (res.error or "")
    assert calls == []

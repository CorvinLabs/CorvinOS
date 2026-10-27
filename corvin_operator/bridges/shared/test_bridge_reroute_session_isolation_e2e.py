"""E2E (ADR-2268 / ADR-2299): a route change never carries a claude session across endpoints.

Driven through the real entry ``adapter.call_claude_streaming`` against a REAL subprocess (the
dumping fake ``claude`` of ``test_bridge_sensitive_reroute_e2e``).

* A rerouted CONFIDENTIAL turn runs in its own session; the next NATIVE turn of the same chat
  does not ``--resume``/``--continue`` it (the CLI would replay the sensitive exchange to the
  default endpoint), and a following native turn resumes the NATIVE session normally.
* The reverse: a rerouted turn does not resume the native session either (its history is
  re-supplied through the ledger gate, judged against the provider).
* A provider that is assigned to ``claude_code`` but whose route cannot be built refuses the
  bridge turn (the console already did, T-0229) instead of spawning on the default endpoint.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import pytest  # noqa: E402
import yaml  # noqa: E402

from test_bridge_sensitive_reroute_e2e import (  # noqa: E402,F401
    HOST, MAIL, _policy, env,
)

_RESUME = ("--continue", "--resume")


def _resumes(args: list[str]) -> bool:
    return any(a in _RESUME for a in args)


def test_native_turn_after_a_reroute_does_not_resume_the_rerouted_session(env):
    env.write(_policy(allow_default=True))
    env.turn(MAIL, chat="same")
    first_env = env.spawned_env()
    assert first_env.get("ANTHROPIC_BASE_URL") == f"https://{HOST}/v1"
    env.dump.unlink()
    env.argdump.unlink()
    env.turn("write a haiku about spring", chat="same")
    second_env, second_args = env.spawned_env(), env.args()
    assert second_env.get("ANTHROPIC_BASE_URL") != f"https://{HOST}/v1"
    assert not _resumes(second_args), f"native turn resumed the rerouted session: {second_args}"


def test_a_following_native_turn_resumes_the_native_session(env):
    env.write(_policy(allow_default=True))
    env.turn("first public question", chat="same")
    env.turn(MAIL, chat="same")
    env.dump.unlink()
    env.argdump.unlink()
    env.turn("second public question", chat="same")
    assert _resumes(env.args()), "the native session must still be resumed after a reroute"
    assert env.spawned_env().get("ANTHROPIC_BASE_URL") != f"https://{HOST}/v1"


def test_a_rerouted_turn_does_not_resume_the_native_session(env):
    env.write(_policy(allow_default=True))
    env.turn("first public question", chat="same")
    env.dump.unlink()
    env.argdump.unlink()
    env.turn(MAIL, chat="same")
    assert env.spawned_env().get("ANTHROPIC_BASE_URL") == f"https://{HOST}/v1"
    assert not _resumes(env.args()), f"rerouted turn resumed the native session: {env.args()}"


def test_a_second_rerouted_turn_resumes_the_rerouted_session(env):
    env.write(_policy(allow_default=True))
    env.turn(MAIL, chat="same")
    env.dump.unlink()
    env.argdump.unlink()
    env.turn(MAIL, chat="same")
    assert _resumes(env.args()), "the rerouted chat keeps its own session across rerouted turns"


def test_an_assigned_provider_without_a_route_refuses_the_bridge_turn(env, monkeypatch):
    import engine_models as em  # type: ignore
    cfg = yaml.safe_load(_policy(allow_default=True))
    cfg["spec"]["engine_models"] = {"claude_code": {"provider": "acme_llm"}}
    cfg["spec"]["egress"]["allowed_hosts"].append(HOST)
    env.write(yaml.safe_dump(cfg))
    monkeypatch.setattr(em, "resolve_claude_code_provider_env", lambda tid: {})
    answer = env.turn("write a haiku about spring", chat="norte")
    assert answer.startswith("[security]"), answer
    assert not env.dump.exists(), "a turn without a buildable route must not spawn"


def test_new_clears_the_route_session_too(env):
    """/new (reason=manual) must not leave a rerouted session that a later rerouted turn resumes."""
    env.write(_policy(allow_default=True))
    env.turn(MAIL, chat="same")
    import adapter  # type: ignore
    import session_state  # type: ignore
    base = adapter._session_dir("discord", "same")
    route_dirs = list(base.glob(".route-*"))
    assert route_dirs and session_state.has_claude_session_state(base)
    session_state.reset_claude_session_state(base, reason="manual", chat_key="same")
    assert not session_state.has_claude_session_state(base)
    env.dump.unlink()
    env.argdump.unlink()
    env.turn(MAIL, chat="same")
    assert not _resumes(env.args())

"""Regression guard for ADR-2236 / PLAN-0938 (T-0070 loss signal, T-0072 fix): what the
adapter does when a claude turn has an open background child. These two tests were
strict-xfail repros of the idle-watchdog defects until T-0072 fixed them.

Every test drives the REAL entry point ``adapter.call_claude_streaming`` against
a REAL subprocess (``tests/fake_claude.py`` replaying a stream captured from the
real CLI, selected via ``CORVIN_CLAUDE_BIN``) — no stubbed engine.

Two groups:
  * baselines — the harness works; WITHOUT an interim sink (every caller except
                process_one) the legacy "last non-empty result wins" is retained.
  * guards    — a quiet child must survive the idle limit, and an idle kill must
                never re-run the prompt (which started the child a second time).
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import bgscope_testkit as kit  # noqa: E402

pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="process-group / pid-liveness assertions are POSIX; launcher is cross-platform",
)


def _fresh_adapter():
    os.environ.pop("ADAPTER_FAKE_CLAUDE", None)
    for mod in list(sys.modules):
        if mod in ("adapter", "agents") or mod.startswith("agents."):
            del sys.modules[mod]
    import adapter  # type: ignore

    # Same test-local double as test_adapter_stream_idle.py: the L44 classifier
    # would otherwise call the (fake) claude binary and fail closed.
    adapter._house_rules_classifier = lambda task, rules, auth, **_kw: ("", 1.0, "test-benign")
    return adapter


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """Sandboxed CORVIN_HOME/XDG/anchor/FORGE_ROOT + short idle limit."""
    for k, v in {
        "CORVIN_HOME": tmp_path / "corvin",
        "XDG_CONFIG_HOME": tmp_path / "xdg",
        "FORGE_ROOT": tmp_path / "forge",
        "ADAPTER_INBOX": tmp_path / "inbox",
        "ADAPTER_OUTBOX": tmp_path / "outbox",
        "VOICE_AUDIT_PATH": tmp_path / "audit.jsonl",
    }.items():
        monkeypatch.setenv(k, str(v))
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    monkeypatch.setenv("ADAPTER_STREAM_IDLE_TIMEOUT", "2")
    monkeypatch.setenv("ADAPTER_HEARTBEAT_INTERVAL", "0")
    return tmp_path


def _use_fixture(monkeypatch, sandbox: Path, name: str, **kw) -> None:
    for k, v in kit.fake_env(sandbox, name, **kw).items():
        monkeypatch.setenv(k, v)


def _run(adapter, chat_key: str) -> tuple[str, float]:
    t0 = time.time()
    ans = adapter.call_claude_streaming(
        "irrelevant", channel="discord", chat_key=chat_key,
        mode="unrestricted", profile=None,
    )
    return ans, time.time() - t0


# ---------------------------------------------------------------- baselines ---

def test_baseline_turn_without_children_returns_its_answer(sandbox, monkeypatch):
    _use_fixture(monkeypatch, sandbox, "plain_no_children")
    ans, _ = _run(_fresh_adapter(), "scope-plain")
    assert ans.strip() == "ok", ans
    assert kit.spawn_count(sandbox) == 1


def test_baseline_without_an_interim_sink_the_last_result_wins(sandbox, monkeypatch):
    """Legacy contract, kept for every caller that installs no interim sink
    (bg_task_worker, /btw, tests): the return value is the last non-empty result.
    process_one installs a sink and gets delay-by-one delivery instead — see
    test_bg_scope_completion.py."""
    _use_fixture(monkeypatch, sandbox, "bash_bg_ok", speedup=4)
    ans, _ = _run(_fresh_adapter(), "scope-overwrite")
    assert "gestartet" not in ans
    assert "abgeschlossen" in ans.lower(), ans


# ------------------------------------------------------------------ guards ---

def test_open_child_survives_silence_longer_than_the_idle_limit(sandbox, monkeypatch):
    """bash_bg_ok at 4x: first result ~0.7 s, child ends ~2.5 s. A 3 s silent gap
    after the first result exceeds ADAPTER_STREAM_IDLE_TIMEOUT=2 while a child is
    open. Wanted: the turn survives and returns the wake-up text. Today: the
    watchdog kills the CLI (and with it the child and the wake-up turn)."""
    _use_fixture(monkeypatch, sandbox, "bash_bg_ok", speedup=4, silent_s=3)
    ans, elapsed = _run(_fresh_adapter(), "scope-silence")
    assert "abgeschlossen" in ans.lower(), f"turn was cut off: {ans!r} after {elapsed:.1f}s"


def test_idle_kill_must_not_rerun_the_prompt_and_restart_the_child(sandbox, monkeypatch):
    """With an existing session (.session_started) the idle kill triggers a
    reset + RETRY of the whole prompt (see test_adapter_stream_idle). With a
    background child that means the child is started a second time. Wanted: one
    spawn. Today: more than one."""
    _use_fixture(monkeypatch, sandbox, "bash_bg_ok", speedup=4, silent_s=3)
    adapter = _fresh_adapter()
    workdir = adapter._session_dir("discord", "scope-rerun")
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / ".session_started").touch()
    _run(adapter, "scope-rerun")
    assert kit.spawn_count(sandbox) == 1, f"prompt re-run {kit.spawn_count(sandbox)}x"

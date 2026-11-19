"""E2E (ADR-2102 x ADR-2268): the re-supplied chat history of a turn that is started on a provider.

``_resolve_spawn_inputs`` renders the session-ledger block before the route is known: coverage
against the chat's OWN CLI session, each turn's class judged for the native engine. A rerouted
turn runs in its own session (``<chat>/.route-<provider>-<hash>/``), whose transcript holds none
of what the native session holds, so a block computed against the native transcript was empty and
the provider's session never saw the earlier turns. These tests drive the real entry
``adapter.call_claude_streaming`` against a REAL subprocess (the dumping launcher of
``test_bridge_sensitive_reroute_e2e``, extended to keep the system-prompt file) and assert on
what the CLI was actually handed.

Also here: the worker-readable view in the route directory is rendered from the record (never
copied through a planted symlink), exists only while the provider's CLI runs (the route
directory is below the native worker's cwd, and the provider may be cleared for turns the native
engine is not), the per-session coverage rule (``route``), and the provider argument of the
per-turn data-flow withholding. The launcher copies the view it finds in its cwd, so "the worker
had it" is asserted on what the subprocess saw, not on what is left afterwards.

The ledger records are appended directly: ``call_claude_streaming`` is below ``process_one``,
which is where a finished turn is recorded.
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import pytest  # noqa: E402

from test_bridge_sensitive_reroute_e2e import (  # noqa: E402,F401
    HOST, MAIL, _fresh_adapter, _policy, env,
)

SID = "11111111-2222-3333-4444-555555555555"
NATIVE_Q = "which quay does the harbour ferry leave from"
NATIVE_A = "NATIVE-ANSWER-QUAY-SEVEN"


def _user(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": "User input:\n" + text}}


@pytest.fixture()
def renv(env, tmp_path, monkeypatch):
    """The reroute harness, plus: an isolated CLI transcript root and a launcher that keeps the
    system prompt it was given (the engine deletes its temp file after the run)."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-cfg"))
    env.sysdump = tmp_path / "spawn-system.txt"
    env.viewdump = tmp_path / "spawn-view.txt"        # the view in the spawn's cwd, as it saw it
    env.viewkind = tmp_path / "spawn-view-kind.txt"   # "file" | "link" | "none"
    env.siblings = tmp_path / "spawn-other-views.txt" # views of OTHER sessions it could reach
    wrapper = Path(os.environ["CORVIN_CLAUDE_BIN"])
    lines = wrapper.read_text(encoding="utf-8").splitlines()
    assert lines[-1].startswith("exec "), lines
    keep = (
        'prev=""\n'
        'for a in "$@"; do\n'
        f'  if [ "$prev" = "--append-system-prompt-file" ]; then cat "$a" > "{env.sysdump}"; fi\n'
        f'  if [ "$prev" = "--append-system-prompt" ]; then printf "%s" "$a" > "{env.sysdump}"; fi\n'
        '  prev="$a"\n'
        'done\n'
        f'rm -f "{env.viewdump}"\n'
        f'if [ -L .corvin-history.md ]; then echo link > "{env.viewkind}"\n'
        f'elif [ -f .corvin-history.md ]; then echo file > "{env.viewkind}"; '
        f'cat .corvin-history.md > "{env.viewdump}"\n'
        f'else echo none > "{env.viewkind}"; fi\n'
        f'ls -d .route-*/.corvin-history.md ../.corvin-history.md > "{env.siblings}" 2>/dev/null\n'
    )
    wrapper.write_text("\n".join(lines[:-1]) + "\n" + keep + lines[-1] + "\n", encoding="utf-8")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    return env


def _workdir(chat: str) -> Path:
    adapter = _fresh_adapter()
    wd = adapter._session_dir("discord", chat)
    wd.mkdir(parents=True, exist_ok=True)
    return wd


def _record(wd: Path, chat: str, user: str, answer: str, **kw) -> None:
    import session_ledger as ledger  # type: ignore
    rec = ledger.append_turn(wd, channel="discord", chat_key=chat, user_text=user,
                             assistant_text=answer, tenant_id="_default", **kw)
    assert rec is not None, "the ledger record could not be written"


def _native_session_holds(wd: Path, texts: list[str]) -> None:
    """A native CLI session whose transcript holds ``texts`` (so the native render covers them)."""
    proj = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / re.sub(
        r"[^A-Za-z0-9]", "-", str(wd.resolve()))
    proj.mkdir(parents=True, exist_ok=True)
    (proj / f"{SID}.jsonl").write_text(
        "".join(json.dumps(_user(t)) + "\n" for t in texts), encoding="utf-8")
    (wd / ".main_session.json").write_text(json.dumps({"session_id": SID}), encoding="utf-8")
    (wd / ".session_started").touch()


def _route_dirs(wd: Path) -> list[Path]:
    return sorted(p for p in wd.glob(".route-*"))


def test_a_rerouted_turn_is_handed_the_turns_only_the_native_session_holds(renv):
    renv.write(_policy(allow_default=True))
    wd = _workdir("regate")
    _record(wd, "regate", NATIVE_Q, NATIVE_A, spawned=True)
    _native_session_holds(wd, [NATIVE_Q])

    # Positive control: for the NATIVE session the turn is live, so nothing is re-supplied.
    import session_ledger as ledger  # type: ignore
    assert ledger.render_context(wd, channel="discord", chat_key="regate") == "", \
        "precondition: the native transcript must cover the recorded turn"

    answer = renv.turn(MAIL, chat="regate")
    assert not answer.startswith("["), answer
    assert renv.spawned_env().get("ANTHROPIC_BASE_URL") == f"https://{HOST}/v1"
    system = renv.sysdump.read_text(encoding="utf-8")
    assert "Chat history this session does not hold" in system, \
        "the rerouted session was started without the history block"
    assert NATIVE_A in system and NATIVE_Q in system, \
        "the turn the native session holds never reached the provider's session"

    [route] = _route_dirs(wd)
    # The provider's worker had the view in its cwd ...
    assert renv.viewkind.read_text().strip() == "file"
    assert NATIVE_A in renv.viewdump.read_text(encoding="utf-8")
    # ... and it is gone once that spawn has ended: the route directory is below the cwd of
    # the chat's native worker.
    assert not (route / ".corvin-history.md").exists()
    # The chat's own view (native withholding) was not reachable from the provider's cwd.
    assert renv.siblings.read_text().strip() == ""


def test_the_native_block_is_replaced_not_duplicated(renv):
    """Nothing is live natively either: the native block exists and must be swapped, once."""
    renv.write(_policy(allow_default=True))
    wd = _workdir("swap")
    _record(wd, "swap", NATIVE_Q, NATIVE_A, spawned=True)
    renv.turn(MAIL, chat="swap")
    system = renv.sysdump.read_text(encoding="utf-8")
    assert system.count("## Chat history this session does not hold") == 1, system[-2000:]
    assert system.count(NATIVE_A) == 1


def test_a_turn_the_provider_is_not_cleared_for_is_withheld_in_block_and_view(renv):
    renv.write(_policy(allow_default=True))
    wd = _workdir("secret")
    _record(wd, "secret", NATIVE_Q, NATIVE_A, spawned=True)
    _record(wd, "secret", "[class:secret] the vault code is 4471-ZULU", "SECRET-ANSWER-KEPT-OUT",
            spawned=True)
    renv.turn(MAIL, chat="secret")
    system = renv.sysdump.read_text(encoding="utf-8")
    assert NATIVE_A in system, "the cleared turn must stay"
    assert "4471-ZULU" not in system and "SECRET-ANSWER-KEPT-OUT" not in system
    view = renv.viewdump.read_text(encoding="utf-8")
    assert NATIVE_A in view
    assert "4471-ZULU" not in view and "SECRET-ANSWER-KEPT-OUT" not in view


def test_the_route_view_is_never_written_through_a_planted_symlink(renv, tmp_path):
    renv.write(_policy(allow_default=True))
    wd = _workdir("link")
    _record(wd, "link", NATIVE_Q, NATIVE_A, spawned=True)
    renv.turn(MAIL, chat="link")            # creates the route directory
    [route] = _route_dirs(wd)
    victim = tmp_path / "victim.txt"
    victim.write_text("UNTOUCHED", encoding="utf-8")
    view = route / ".corvin-history.md"
    assert not view.exists()                # removed when the first spawn ended
    view.symlink_to(victim)

    renv.turn(MAIL, chat="link")
    assert victim.read_text(encoding="utf-8") == "UNTOUCHED", \
        "the history view was written through the symlink"
    assert renv.viewkind.read_text().strip() == "file", "the spawn was handed the planted link"
    assert NATIVE_A in renv.viewdump.read_text(encoding="utf-8")
    assert not view.exists() and not view.is_symlink()
    assert victim.exists(), "the cleanup removed the link's target"


def test_no_provider_view_plaintext_is_left_below_the_native_cwd(renv):
    """R2-BR-1: the provider is cleared for a turn the native engine is refused. After a
    rerouted turn and a following native turn nothing below the native worker's cwd holds
    that turn's text — not in a route view, not in the chat's own view."""
    renv.write(_policy(allow_default=True))
    wd = _workdir("leak")
    adapter = _fresh_adapter()
    route_name = adapter._route_session_dir(wd, "acme_llm").name
    # A rerouted exchange, recorded the way process_one records it.
    secret_user, secret_answer = "write to carl.conf@example.org today", "ROUTE-ONLY-ANSWER-4410"
    _record(wd, "leak", secret_user, secret_answer, spawned=True, route=route_name)

    renv.turn(MAIL, chat="leak")                         # rerouted: the provider may read it
    provider_view = renv.viewdump.read_text(encoding="utf-8")
    assert "carl.conf@example.org" in provider_view and secret_answer in provider_view, \
        "positive control: the provider's view must hold the turn"

    def _hits() -> list[str]:
        out = []
        for f in wd.rglob("*"):
            if f.is_file() and not f.is_symlink():
                data = f.read_bytes()
                if b"carl.conf@example.org" in data or secret_answer.encode() in data:
                    out.append(str(f.relative_to(wd)))
        return out
    assert _hits() == [], "provider-cleared text is left below the chat workdir after the reroute"

    answer = renv.turn("write a haiku about spring", chat="leak")   # native
    assert not answer.startswith("["), answer
    assert "ANTHROPIC_BASE_URL" not in renv.spawned_env() or \
        renv.spawned_env().get("ANTHROPIC_BASE_URL") != f"https://{HOST}/v1"
    # What the native worker could reach while it ran, and what is on disk afterwards.
    assert renv.siblings.read_text().strip() == "", "a route view existed during the native spawn"
    native_view = renv.viewdump.read_text(encoding="utf-8")
    assert "carl.conf@example.org" not in native_view and secret_answer not in native_view
    assert _hits() == []


def test_a_route_view_left_by_a_killed_spawn_is_purged_by_the_next_native_render(renv):
    """Second line: a spawn that never reached its cleanup."""
    wd = _workdir("stale")
    _record(wd, "stale", NATIVE_Q, NATIVE_A, spawned=True)
    adapter = _fresh_adapter()
    route = adapter._route_session_dir(wd, "acme_llm")
    route.mkdir()
    (route / ".corvin-history.md").write_text("STALE-PROVIDER-VIEW", encoding="utf-8")
    import session_ledger as ledger  # type: ignore
    ledger.render_context(wd, channel="discord", chat_key="stale")
    assert not (route / ".corvin-history.md").exists()


def test_a_route_directory_that_is_a_symlink_refuses_the_turn(renv, tmp_path):
    renv.write(_policy(allow_default=True))
    wd = _workdir("dirlink")
    adapter = _fresh_adapter()
    route = adapter._route_session_dir(wd, "acme_llm")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    route.symlink_to(elsewhere, target_is_directory=True)
    answer = renv.turn(MAIL, chat="dirlink")
    assert answer.startswith("[security]"), answer
    assert not renv.dump.exists(), "a refused turn must not spawn"
    assert list(elsewhere.iterdir()) == []


# ── the per-session coverage rule ───────────────────────────────────────────

def _turn(n: int, user: str, **kw) -> dict:
    return {"kind": "turn", "seq": n, "n": n, "ts": float(n), "user": user,
            "assistant": f"a{n}", "spawned": True, "refused": "", **kw}


def test_a_turn_of_another_session_is_never_live_and_never_ends_the_alignment():
    import session_ledger as ledger  # type: ignore
    records = [_turn(1, "one"), _turn(2, "ja", route=".route-x-12345678"), _turn(3, "three")]
    # Native transcript: holds the two native turns. The rerouted one is stepped over.
    native = ledger.uncovered_turns(records, ["one", "three"])
    assert [r["n"] for r in native] == [2]
    # Route transcript: holds only the rerouted turn.
    routed = ledger.uncovered_turns(records, ["ja"], route=".route-x-12345678")
    assert [r["n"] for r in routed] == [1, 3]


def test_an_equal_text_in_the_other_session_cannot_claim_coverage():
    import session_ledger as ledger  # type: ignore
    records = [_turn(1, "ja", route=".route-x-12345678"), _turn(2, "ja")]
    # The route transcript's "ja" is the rerouted turn's, not the newer native one's.
    routed = ledger.uncovered_turns(records, ["ja"], route=".route-x-12345678")
    assert [r["n"] for r in routed] == [2]


def test_append_turn_records_the_route_only_when_there_is_one(tmp_path, monkeypatch):
    import session_ledger as ledger  # type: ignore
    wd = tmp_path / "tenants" / "_default" / "sessions" / "voice" / "discord" / "c1"
    wd.mkdir(parents=True)
    ledger.append_turn(wd, channel="discord", chat_key="c1", user_text="u1", assistant_text="a1")
    ledger.append_turn(wd, channel="discord", chat_key="c1", user_text="u2", assistant_text="a2",
                       route=".route-x-12345678")
    one, two = [r for r in ledger.read_ledger(wd) if r.get("kind") == "turn"]
    assert "route" not in one and two["route"] == ".route-x-12345678"


# ── the provider argument of the per-turn withholding ───────────────────────

def test_history_is_judged_for_the_provider_and_an_undeclared_one_gets_nothing(renv):
    renv.write(_policy(allow_default=True))
    adapter = _fresh_adapter()
    public = {"kind": "turn", "user": "write a haiku about spring", "assistant": "ok"}
    assert adapter._ledger_withhold("claude_code", "discord", "wp")(public) is None
    assert adapter._ledger_withhold("claude_code", "discord", "wp", provider="acme_llm")(public) is None
    # A provider with no provider_compliance entry is cleared for no class.
    assert adapter._ledger_withhold(
        "claude_code", "discord", "wp", provider="never_declared")(public) == "data_flow"
    # An endpoint whose provider cannot be named: nothing is cleared.
    assert adapter._ledger_withhold(
        "claude_code", "discord", "wp", provider_unknown=True)(public) == "data_flow"


def test_an_assigned_provider_without_a_declaration_loses_the_native_block(renv):
    """The non-rerouted site: a provider assigned to claude_code. Function-level — the harness
    has no assigned provider — against the real record, gate and tenant policy."""
    renv.write(_policy(allow_default=True))
    wd = _workdir("assigned")
    _record(wd, "assigned", NATIVE_Q, NATIVE_A, spawned=True)
    adapter = _fresh_adapter()
    import session_ledger as ledger  # type: ignore
    native = ledger.render_context(
        wd, channel="discord", chat_key="assigned", tenant_id="_default",
        withhold=adapter._ledger_withhold("claude_code", "discord", "assigned"))
    assert NATIVE_A in native
    resolved = {"system": "PERSONA" + native + "\n\nCEL-BRIEF\n"}
    assert adapter._ledger_regate_for_route(
        resolved, workdir=wd, session_dir=None, session_id=None,
        channel="discord", chat_key="assigned", provider="never_declared",
        native_block=native) is None
    assert NATIVE_A not in resolved["system"] and NATIVE_Q not in resolved["system"]
    assert resolved["system"].startswith("PERSONA") and resolved["system"].endswith("\n\nCEL-BRIEF\n")
    assert NATIVE_A not in (wd / ".corvin-history.md").read_text(encoding="utf-8")


# ── R2-BR-2: the native block travels with the turn; a block that cannot be removed refuses ──

def test_the_native_block_travels_in_the_spawn_inputs_not_in_shared_state(renv):
    renv.write(_policy(allow_default=True))
    wd = _workdir("carry")
    _record(wd, "carry", NATIVE_Q, NATIVE_A, spawned=True)
    adapter = _fresh_adapter()
    assert not hasattr(adapter, "_LEDGER_BLOCKS"), "the process-wide block dict is back"
    resolved = adapter._resolve_spawn_inputs(
        "write a haiku about spring", "unrestricted", None, None,
        channel="discord", chat_key="carry", msg_id="m1")
    assert NATIVE_A in resolved["_ledger_block"]
    assert resolved["_ledger_block"] in resolved["system"]


def test_a_native_block_that_cannot_be_found_refuses_the_turn(renv):
    """Fail-closed: the old lookup lost its entry (eviction, a caller that never popped it), the
    native block stayed and the provider's block was appended next to it."""
    renv.write(_policy(allow_default=True))
    wd = _workdir("lost")
    _record(wd, "lost", NATIVE_Q, NATIVE_A, spawned=True)
    _record(wd, "lost", "[class:secret] the vault code is 4471-ZULU", "SECRET-ANSWER-KEPT-OUT",
            spawned=True)
    adapter = _fresh_adapter()
    import session_ledger as ledger  # type: ignore
    native = ledger.render_context(wd, channel="discord", chat_key="lost", tenant_id="_default")
    assert "4471-ZULU" in native                       # positive control: unwithheld render
    # The system prompt holds a block that is NOT the one the turn says it rendered.
    resolved = {"system": "PERSONA" + native.replace(NATIVE_A, NATIVE_A + " (edited)")}
    before = resolved["system"]
    problem = adapter._ledger_regate_for_route(
        resolved, workdir=wd, session_dir=None, session_id=None,
        channel="discord", chat_key="lost", provider="acme_llm", native_block=native)
    assert isinstance(problem, str) and problem.startswith("[security]")
    assert resolved["system"] == before, "a refused regate must not append the provider block"


def test_a_failed_re_render_starts_the_turn_without_history(renv, monkeypatch):
    renv.write(_policy(allow_default=True))
    wd = _workdir("fail")
    _record(wd, "fail", NATIVE_Q, NATIVE_A, spawned=True)
    adapter = _fresh_adapter()
    import session_ledger as ledger  # type: ignore
    native = ledger.render_context(wd, channel="discord", chat_key="fail", tenant_id="_default")
    assert NATIVE_A in native

    def _boom(*_a, **_kw):
        raise RuntimeError("render broke")
    monkeypatch.setattr(ledger, "render_context", _boom)
    resolved = {"system": "PERSONA" + native + "TAIL"}
    assert adapter._ledger_regate_for_route(
        resolved, workdir=wd, session_dir=None, session_id=None,
        channel="discord", chat_key="fail", provider="acme_llm", native_block=native) is None
    assert resolved["system"] == "PERSONATAIL"

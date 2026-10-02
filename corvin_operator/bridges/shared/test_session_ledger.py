"""Unit tests for session_ledger (session-drift analysis 2026-10-02, L0/L4).

The properties pinned here are the ones the module's guarantee rests on:
the record only grows, coverage is decided against the real transcript
(fail-towards-remembering), an unwanted reset or a compaction re-supplies,
an explicit /new fences, the view is deterministic and grows by appending,
and GDPR erasure still reaches the record.

Run: python3 -m pytest corvin_operator/bridges/shared/test_session_ledger.py -q
"""
from __future__ import annotations

import json
import os
import stat
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import session_ledger as sl  # noqa: E402
import session_state  # noqa: E402


@pytest.fixture()
def wd(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-cfg"))
    d = tmp_path / "sessions" / "voice" / "telegram" / "chat-1"
    d.mkdir(parents=True)
    return d


def _transcript(wd: Path, sid: str, entries: list[dict]) -> Path:
    import re
    proj = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / re.sub(
        r"[^A-Za-z0-9]", "-", str(wd.resolve()))
    proj.mkdir(parents=True, exist_ok=True)
    f = proj / f"{sid}.jsonl"
    f.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    (wd / ".main_session.json").write_text(json.dumps({"session_id": sid}))
    return f


def _user(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def _compact(pre: int = 190000, post: int = 12000, uuid: str = "c-1") -> dict:
    return {"type": "system", "subtype": "compact_boundary", "uuid": uuid,
            "timestamp": "2026-10-02T10:00:00Z",
            "compactMetadata": {"trigger": "auto", "preTokens": pre, "postTokens": post}}


def _turn(wd, i, **kw):
    return sl.append_turn(wd, channel="telegram", chat_key="chat-1",
                          user_text=kw.get("u", f"question number {i} about topic-{i}"),
                          assistant_text=kw.get("a", f"answer {i}"), ts=1_700_000_000 + i)


SID = "0a1b2c3d-1111-2222-3333-444455556666"


class TestRecord:
    def test_turns_are_numbered_and_the_file_only_grows(self, wd):
        sizes = []
        for i in range(1, 4):
            rec = _turn(wd, i)
            assert rec["n"] == i and rec["seq"] == i
            sizes.append(sl.ledger_path(wd).stat().st_size)
        sl.append_boundary(wd, kind="reset", reason="timeout")
        assert sizes == sorted(sizes)
        recs = sl.read_ledger(wd)
        assert [r["kind"] for r in recs] == ["turn", "turn", "turn", "boundary"]
        assert recs[-1]["seq"] == 4 and "n" not in recs[-1]

    def test_file_is_private(self, wd):
        _turn(wd, 1)
        assert stat.S_IMODE(sl.ledger_path(wd).stat().st_mode) == 0o600

    def test_concurrent_appends_get_unique_numbers(self, wd):
        threads = [threading.Thread(target=_turn, args=(wd, i)) for i in range(40)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        ns = sorted(r["n"] for r in sl.read_ledger(wd))
        assert ns == list(range(1, 41))

    def test_a_turn_keeps_its_full_text(self, wd):
        big = "x" * 50_000
        _turn(wd, 1, u=big, a=big)
        rec = sl.read_ledger(wd)[0]
        assert rec["user"] == big and rec["assistant"] == big


class TestCoverage:
    def test_turns_in_the_live_transcript_are_not_resupplied(self, wd):
        _turn(wd, 1); _turn(wd, 2)
        _transcript(wd, SID, [_user("question number 1 about topic-1"),
                              _user("question number 2 about topic-2")])
        assert sl.render_context(wd) == ""

    def test_compaction_makes_earlier_turns_uncovered(self, wd):
        _turn(wd, 1); _turn(wd, 2)
        _transcript(wd, SID, [_user("question number 1 about topic-1"), _compact(),
                              _user("question number 2 about topic-2")])
        block = sl.render_context(wd)
        assert "question number 1 about topic-1" in block
        assert "question number 2 about topic-2" not in block

    def test_unreadable_transcript_fails_towards_remembering(self, wd):
        _turn(wd, 1)
        (wd / ".main_session.json").write_text(json.dumps({"session_id": SID}))
        # no transcript file exists for SID
        assert "question number 1 about topic-1" in sl.render_context(wd)

    def test_fresh_session_resupplies_everything(self, wd):
        _turn(wd, 1); _turn(wd, 2)
        block = sl.render_context(wd)
        assert "topic-1" in block and "topic-2" in block

    def test_framed_prompt_still_counts_as_covered(self, wd):
        _turn(wd, 1)
        _transcript(wd, SID, [_user("[voice] [sender:x]\n  question   number 1 about topic-1  \n")])
        assert sl.render_context(wd) == ""

    def test_note_compactions_is_idempotent(self, wd):
        _turn(wd, 1)
        _transcript(wd, SID, [_user("q"), _compact(uuid="c-1"), _user("r"), _compact(uuid="c-2")])
        assert sl.note_compactions(wd) == 2
        assert sl.note_compactions(wd) == 0
        b = [r for r in sl.read_ledger(wd) if r["kind"] == "boundary"]
        assert [r["detail"]["uuid"] for r in b] == ["c-1", "c-2"]
        assert b[0]["detail"]["pre_tokens"] == 190000


class TestResets:
    def test_reset_keeps_the_ledger_and_records_the_boundary(self, wd):
        _turn(wd, 1)
        (wd / ".main_session.json").write_text(json.dumps({"session_id": SID}))
        (wd / ".session_started").touch()
        removed = session_state.reset_claude_session_state(wd, reason="context_overflow")
        assert ".main_session.json" in removed
        recs = sl.read_ledger(wd)
        assert recs[0]["kind"] == "turn"
        assert recs[-1] == {**recs[-1], "kind": "boundary", "boundary": "reset",
                            "reason": "context_overflow", "session_id": SID,
                            "chat_key": "chat-1", "channel": "telegram"}
        # The fresh session after an unwanted reset gets turn 1 back.
        assert "question number 1 about topic-1" in sl.render_context(wd)

    def test_reset_without_state_records_nothing(self, wd):
        _turn(wd, 1)
        assert session_state.reset_claude_session_state(wd, reason="timeout") == []
        assert [r["kind"] for r in sl.read_ledger(wd)] == ["turn"]

    def test_unknown_reason_counts_as_unwanted(self, wd):
        _turn(wd, 1)
        (wd / ".session_started").touch()
        session_state.reset_claude_session_state(wd)  # caller forgot the reason
        assert "topic-1" in sl.render_context(wd)

    def test_manual_reset_fences_older_turns(self, wd):
        _turn(wd, 1)
        (wd / ".session_started").touch()
        session_state.reset_claude_session_state(wd, reason="manual")
        assert sl.render_context(wd) == ""  # /new: fresh start
        _turn(wd, 2)
        block = sl.render_context(wd)
        assert "topic-2" in block and "topic-1" not in block
        assert "started over with /new" in block and "1 turn(s) before that" in block
        assert len([r for r in sl.read_ledger(wd) if r["kind"] == "turn"]) == 2

    def test_unwanted_reset_after_manual_resupplies_only_since_manual(self, wd):
        _turn(wd, 1)
        (wd / ".session_started").touch()
        session_state.reset_claude_session_state(wd, reason="manual")
        _turn(wd, 2)
        (wd / ".session_started").touch()
        session_state.reset_claude_session_state(wd, reason="timeout")
        block = sl.render_context(wd)
        assert "topic-2" in block and "topic-1" not in block
        assert "session reset (timeout)" in block


class TestView:
    def test_render_is_deterministic(self, wd):
        for i in range(1, 6):
            _turn(wd, i)
        assert sl.render_context(wd) == sl.render_context(wd)

    def test_view_grows_by_appending_within_budget(self, wd):
        for i in range(1, 4):
            _turn(wd, i)
        before = sl.render_context(wd)
        _turn(wd, 4)
        after = sl.render_context(wd)
        # Strictly append-only: the previous block is a byte prefix of the new one.
        assert after.startswith(before)
        assert after[len(before):] == sl._render_turn(sl.read_ledger(wd)[-1])

    def test_budget_moves_old_turns_to_index_then_pointer_never_drops_silently(self, wd):
        for i in range(1, 121):
            _turn(wd, i, u=f"question {i} " + "q" * 900, a=f"answer {i} " + "a" * 900)
        recs = sl.read_ledger(wd)
        block, st = sl.render_from_records(recs, None, verbatim_budget=20_000, index_budget=6_000)
        assert st["verbatim"] + st["indexed"] + st["omitted"] == 120
        assert st["verbatim"] > 0 and st["indexed"] > 0 and st["omitted"] > 0
        assert st["omitted"] % sl.CUT_STEP == 0 or st["omitted"] == 120 - st["verbatim"] - st["indexed"]
        assert f"Turns #1–#{st['omitted']}" in block
        assert "### Turn #120" in block
        assert ".corvin-ledger/ledger.jsonl" in block

    def test_cut_moves_in_steps(self, wd):
        for i in range(1, 60):
            _turn(wd, i, u="u" * 1500, a="a" * 1500)
        recs = sl.read_ledger(wd)
        cuts = set()
        for k in range(40, 60):
            sub = [r for r in recs if r.get("n", 0) <= k]
            _, st = sl.render_from_records(sub, None, verbatim_budget=30_000, index_budget=10**9)
            cuts.add(st["indexed"])
        assert all(c % sl.CUT_STEP == 0 for c in cuts)

    def test_huge_turn_is_capped_in_view_only(self, wd):
        _turn(wd, 1, u="z" * 30_000)
        block = sl.render_context(wd)
        assert "truncated in this view" in block
        assert len(sl.read_ledger(wd)[0]["user"]) == 30_000


class TestErasure:
    def test_jsonl_purge_reaches_ledger_records(self, wd):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import erasure_handlers as eh
        _turn(wd, 1)
        sl.append_turn(wd, channel="telegram", chat_key="someone-else",
                       user_text="other", assistant_text="x")
        assert eh._purge_jsonl_file(sl.ledger_path(wd), "chat-1") == 1
        left = sl.read_ledger(wd)
        assert [r["chat_key"] for r in left] == ["someone-else"]

    def test_real_erasure_chain_purges_ledgers_and_anchor_stores(self, tmp_path, monkeypatch):
        """Through the orchestrator's real handler chain: a subject whose id the
        directory sanitiser rewrites (``-`` → ``_``) is still erased from the
        ledger, other chats are untouched, and the per-chat CEL anchor stores
        (plain and /new-epoch keys) go too."""
        import erasure_handlers as eh
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
        home = eh._tenant_home("_default")
        mine = home / "sessions" / "voice" / "telegram" / "subject_42"   # sanitised dir
        other = home / "sessions" / "voice" / "telegram" / "other_7"
        for d, ck in ((mine, "subject-42"), (other, "other-7")):
            d.mkdir(parents=True)
            sl.append_turn(d, channel="telegram", chat_key=ck, user_text="secret " + ck,
                           assistant_text="x")
        anchors = home / "cel_anchors"
        anchors.mkdir(parents=True)
        for name in ("telegram_subject-42.jsonl", "telegram_subject-42_5.jsonl",
                     "telegram_other-7.jsonl"):
            (anchors / name).write_text('{"kind": "goal", "text": "t"}\n')
        layers = {getattr(h, "layer_id", "") for h in eh.real_handler_chain("_default")}
        assert {"L-session-ledger", "L-cel-anchors"} <= layers
        for h in eh.real_handler_chain("_default"):
            h.purge("subject-42", "req-1")
        assert not sl.ledger_path(mine).exists()
        assert [r["chat_key"] for r in sl.read_ledger(other)] == ["other-7"]
        assert sorted(p.name for p in anchors.glob("*.jsonl")) == ["telegram_other-7.jsonl"]

    def test_ledger_layer_is_in_the_real_chain(self):
        import erasure_handlers as eh
        assert "L-session-ledger" in eh.COVERED_DIRS
        assert any(isinstance(h, eh.SessionLedgerHandler) for h in eh.real_handler_chain("_default"))

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

"""E2E for T-0074 (ADR-2236 D8): voice for a turn with background children, driven through
``adapter.process_one`` against a real subprocess (the fake CLI replaying a stream captured
from the real claude CLI).

Substituted — and named, not hidden: the LLM summariser (``build_voice_summary``) and the TTS
service (``synthesize_voice_note``); both need keys and network. Everything between them is
real: the stream, the tracker, the delivery gate, the mode/consent gate
(``_synthesize_voice_for_turn``) and the outbox the messenger daemons poll.

Rules under test: one spoken summary at the end (of the CLOSING message only); interim
messages are not spoken; a child that FAILS is the one background event with its own spoken
message; a scope that ended badly says so in the spoken summary; a TTS failure leaves the text
delivered; the voice mode gate applies to all of it.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE / "tests", HERE.parents[1] / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import bgscope_testkit as kit  # noqa: E402
from test_bg_scope_completion import _adapter, box  # noqa: E402,F401  (fixture + helper)

import bg_scope  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"),
                                reason="POSIX process semantics in the harness")


class Tts:
    """Records what was spoken; stands in for the summariser + TTS service."""

    def __init__(self, tmp: Path, *, fail_on: str | None = None):
        self.tmp, self.calls, self.fail_on = tmp, [], fail_on

    def summary(self, text, max_chars=400, override=None, task="", **_):
        return override or text[:max_chars]

    def synth(self, text, lang="de", voice=None, **_):
        if self.fail_on and self.fail_on in text:
            raise RuntimeError("tts backend down")
        self.calls.append(text)
        f = self.tmp / f"voice_{len(self.calls)}.ogg"
        f.write_bytes(b"OggS")
        return f


def _turn(monkeypatch, box_, fixture, *, speedup=4, env=None, settings=None, tts=None, msg_id="m1"):
    for k, v in kit.fake_env(box_, fixture, speedup=speedup).items():
        monkeypatch.setenv(k, v)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("ADAPTER_DISABLE_VOICE", raising=False)
    adapter = _adapter()
    tts = tts or Tts(box_)
    adapter.build_voice_summary = tts.summary
    adapter.synthesize_voice_note = tts.synth
    f = Path(os.environ["ADAPTER_INBOX"]) / f"{msg_id}.json"
    f.write_text(json.dumps({"id": msg_id, "channel": "sandbox-bg", "from": "u42",
                             "chat_id": "chan-1", "text": "please do it", "ts": 0}))
    adapter.process_one(f, settings={"whitelist": ["u42"], "voice_summary_mode": "always",
                                     **(settings or {})})
    files = sorted(Path(os.environ["ADAPTER_OUTBOX"]).glob(f"{msg_id}_*.json"))
    return [(p.name, json.loads(p.read_text())) for p in files], tts


def _voiced(msgs):
    return [(n, e) for n, e in msgs if e.get("voice_path")]


# --------------------------------------------------------------------------- unit

def test_voice_facts_only_speak_up_when_something_went_wrong():
    ok = [{"kind": "bash", "state": "completed", "exit_code": 0}]
    bad = [{"kind": "bash", "state": "completed", "exit_code": 0},
           {"kind": "monitor", "state": "failed", "exit_code": 3}]
    assert bg_scope.voice_facts(ok, None) == ""
    assert bg_scope.voice_facts([], None) == ""
    assert bg_scope.voice_facts(bad, None) == "Background work finished, but 1 of 2 tasks failed."
    assert bg_scope.voice_facts(bad[1:], None) == "Background work finished, but 1 of 1 task failed."
    assert "stopped early" in bg_scope.voice_facts(ok, "child_cap")
    assert "stopped early" in bg_scope.voice_facts(bad, "wakeup_cap")
    assert "interrupted" in bg_scope.voice_facts(ok, "process_died")
    # in the language the spoken summary came out in
    assert bg_scope.voice_facts(bad, None, "de") == "Die Hintergrundarbeit ist beendet, aber 1 von 2 Aufgaben ist fehlgeschlagen."
    assert bg_scope.voice_facts(bad[1:], None, "de").endswith("1 von 1 Aufgabe ist fehlgeschlagen.")
    assert "vorzeitig" in bg_scope.voice_facts(ok, "child_cap", "de")
    assert "unterbrochen" in bg_scope.voice_facts(ok, "process_died", "de")
    assert bg_scope.voice_facts(ok, "cancelled") == ""      # an operator /cancel says nothing


# ------------------------------------------------------------------------- E2E

def test_one_spoken_summary_at_the_end_of_the_closing_message_only(box, monkeypatch):
    msgs, tts = _turn(monkeypatch, box, "bash_bg_ok")
    assert len(tts.calls) == 1, tts.calls
    assert "abgeschlossen" in tts.calls[0].lower()
    assert "gestartet" not in tts.calls[0]
    assert "still running" not in tts.calls[0]
    # the interim message is text only, the completion carries the voice
    interim = [e for n, e in msgs if "_-" in n]
    assert interim and not any(e.get("voice_path") for e in interim)
    assert len(_voiced(msgs)) == 1 and _voiced(msgs)[0][1].get("_final")


def test_a_failed_child_gets_its_own_spoken_message_before_the_end(box, monkeypatch):
    msgs, tts = _turn(monkeypatch, box, "bash_bg_fail")
    assert len(tts.calls) == 2, tts.calls
    assert "failed (exit 3)" in tts.calls[0]                   # the milestone, spoken at once
    assert "abgeschlossen" in tts.calls[1].lower() or "beendet" in tts.calls[1].lower()
    voiced = _voiced(msgs)
    assert len(voiced) == 2
    names = [n for n, _ in msgs]
    assert names.index(voiced[0][0]) < names.index(voiced[1][0]), "milestone must precede the final"
    assert "_-" in voiced[0][0] and not voiced[0][1].get("_final")
    assert voiced[0][1]["provenance"]["ai_generated"] is True
    # and the milestone is also in writing
    texts = [e.get("text", "") for n, e in msgs if "_-" in n]
    assert any("failed (exit 3)" in t for t in texts)


def test_a_badly_ended_scope_is_said_aloud_even_if_the_closing_text_is_silent_about_it(box, monkeypatch):
    msgs, tts = _turn(monkeypatch, box, "bash_bg_fail")
    assert ("Background work finished, but 1 of 1 task failed." in tts.calls[-1]
            or "1 von 1 Aufgabe ist fehlgeschlagen" in tts.calls[-1]), tts.calls[-1]
    final = [e for n, e in msgs if e.get("_final") and e.get("text")]
    assert "Background work finished" not in final[0]["text"], "the written message must not change"


def test_a_cap_is_said_aloud(box, monkeypatch):
    msgs, tts = _turn(monkeypatch, box, "bash_bg_ok", speedup=1, env={"CORVIN_BG_CHILD_MAX": "3"})
    assert ("stopped early" in tts.calls[-1].lower()
            or "vorzeitig beendet" in tts.calls[-1].lower()), tts.calls[-1]


def test_a_clean_run_adds_nothing_to_the_spoken_summary(box, monkeypatch):
    _, tts = _turn(monkeypatch, box, "bash_bg_ok")
    assert "Background work" not in tts.calls[0]


def test_voice_mode_never_silences_everything_and_leaves_the_text(box, monkeypatch):
    msgs, tts = _turn(monkeypatch, box, "bash_bg_fail", settings={"voice_summary_mode": "never"})
    assert tts.calls == [] and _voiced(msgs) == []
    texts = [e.get("text", "") for _, e in msgs if e.get("text")]
    assert any("failed (exit 3)" in t for t in texts) and len(texts) >= 3


def test_a_tts_failure_on_the_milestone_leaves_the_text_and_the_final_intact(box, monkeypatch):
    tts = Tts(box, fail_on="failed (exit 3)")
    msgs, tts = _turn(monkeypatch, box, "bash_bg_fail", tts=tts)
    texts = [e.get("text", "") for n, e in msgs if "_-" in n and e.get("text")]
    assert any("failed (exit 3)" in t for t in texts), "the failure notice must still arrive in writing"
    final = [e for n, e in msgs if e.get("_final") and e.get("text")]
    assert len(final) == 1
    assert len(_voiced(msgs)) == 1 and _voiced(msgs)[0][1].get("_final"), "only the closing voice exists"


def test_a_turn_without_children_speaks_exactly_as_before(box, monkeypatch):
    msgs, tts = _turn(monkeypatch, box, "plain_no_children")
    assert len(tts.calls) == 1 and tts.calls[0].strip().startswith("ok")
    assert [n for n, _ in msgs if "_-" in n] == []

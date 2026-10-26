"""Praise in the follow-up turn grades the previous turn's skills (bridge side).

Two layers:

* ``detect_outcome_signal`` table — the new German/English praise phrases, the
  negation guard, word boundaries (substring traps) and the length guard, plus
  the pre-existing phrases as a regression set. Unit level.
* E2E through the real bridge turn — ``adapter.process_one`` consumes an inbox
  item, finds the previous turn's snapshot and calls
  ``skill_inject.grade_from_user_followup``; the grade lands in a real SkillForge
  registry under a sandboxed ``CORVIN_HOME``. ``ADAPTER_FAKE_CLAUDE`` replaces only
  the CLI binary. In-process (no daemon), so the adapter's shutdown sweep never runs.

The script-style ``test_skill_outcome_grading.py`` keeps covering the older
phrases and the rejection/rephrase/opt-out paths.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
for _p in (HERE, REPO / "corvin_operator" / "skill-forge", REPO / "corvin_operator" / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import skill_inject  # noqa: E402

# Expected signal per message; None = no grade.
TABLE = (
    # pre-existing phrases (regression)
    ("danke!", "approval"), ("perfekt", "approval"), ("passt", "approval"),
    ("top, danke", "approval"), ("thank you", "approval"), ("exactly that", "approval"),
    ("great work", "approval"), ("nein, falsch", "rejection"),
    ("das passt nicht", "rejection"), ("nochmal bitte", "rejection"),
    ("wrong answer", "rejection"), ("no, that's incorrect", "rejection"),
    ("thanks but actually wrong", "rejection"), ("hi", None), ("", None),
    # new German praise
    ("Gut gemacht!", "approval"), ("sehr gut", "approval"), ("Prima, danke", "approval"),
    ("toll", "approval"), ("Super gemacht 👍", "approval"), ("hervorragend", "approval"),
    ("Gute Arbeit!", "approval"), ("gut", "approval"), ("Vielen Dank!", "approval"),
    # new English praise
    ("Well done!", "approval"), ("good job", "approval"), ("Nice work, thanks", "approval"),
    ("excellent", "approval"), ("nice", "approval"),
    ("Great job — that's exactly it.", "approval"),
    # negation: never an approval (and not turned into a rejection either)
    ("nicht gut gemacht", None), ("Das war nicht gut", None), ("war nicht gut", None),
    ("kein Lob", None), ("not good", None), ("That was not a good job", None),
    ("not great", None), ("isn't perfect", None), ("isn’t perfect", None),
    ("alles andere als perfekt", None), ("far from perfect", None),
    ("nicht so toll", None), ("das stimmt so nicht", None), ("never well done", None),
    ("Nein danke", None),
    # substring traps (word boundaries)
    ("stopp", None), ("Laptop neu starten", None), ("greater than ten", None),
    ("superscript bitte", None), ("Gutachten anhängen", None),
    # rejection keeps precedence
    ("passt nicht", "rejection"), ("Danke, aber das ist falsch", "rejection"),
    # generic words count only as the whole message
    ("gut, dann schreib jetzt die tests", None), ("good question, how does it work?", None),
    # length guard: incidental praise in a long message
    ("Now please also add the restore steps, the great thing about this setup is "
     "that it runs nightly", None),
    ("Danke für gestern, heute habe ich aber ein ganz anderes Problem mit der "
     "Datenbank und dem Backup", None),
)


@pytest.mark.parametrize("text,want", TABLE)
def test_signal_table(text: str, want: str | None) -> None:
    sig, score = skill_inject.detect_outcome_signal(text)
    assert sig == want, (text, sig, score)
    assert score == {"approval": 0.9, "rejection": 0.1, None: 0.0}[want]


def test_scores_and_cap_unchanged() -> None:
    assert (skill_inject._OUTCOME_APPROVAL_SCORE, skill_inject._OUTCOME_REJECTION_SCORE,
            skill_inject._OUTCOME_REPHRASE_SCORE) == (0.9, 0.1, 0.3)
    assert skill_inject._AUTO_GRADE_CAP_MAX == 0.3


def test_rephrase_still_detected() -> None:
    assert skill_inject.detect_outcome_signal(
        "wie mache ich ein backup von der datenbank",
        prev_user_text="wie mache ich ein backup der datenbank") == ("rephrase", 0.3)


# ── E2E: adapter.process_one ────────────────────────────────────────────────

SKILL = "phase1.e2e_praise"


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    for d in ("inbox", "outbox", "processed", "sessions", "home", "xdg", "slot"):
        (tmp_path / d).mkdir()
    env = {
        "CORVIN_HOME": tmp_path / "home", "XDG_CONFIG_HOME": tmp_path / "xdg",
        "CORVIN_AUDIT_ANCHOR_KEY": tmp_path / "anchor.key",
        "CORVIN_PLUGIN_SLOT_DIR": tmp_path / "slot", "CORVIN_PROJECT_ROOT": "",
        "ADAPTER_INBOX": tmp_path / "inbox", "ADAPTER_OUTBOX": tmp_path / "outbox",
        "ADAPTER_PROCESSED": tmp_path / "processed", "ADAPTER_SESSIONS": tmp_path / "sessions",
        "ADAPTER_FAKE_CLAUDE": "1", "ADAPTER_FAKE_DELAY": "0.05",
        "ADAPTER_ROUTING_MODE": "off", "BRIDGE_PROGRESS_UPDATES": "0",
        "ADAPTER_DISABLE_VOICE": "1", "CORVIN_TENANT_ID": "_default",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    monkeypatch.delitem(sys.modules, "adapter", raising=False)
    import adapter  # type: ignore  # noqa: PLC0415
    from skill_forge.multi_registry import MultiSkillRegistry  # noqa: PLC0415

    reg = MultiSkillRegistry(channel_id=None, project_root=None)
    reg.create(scope="user", name=SKILL, type="learned-experience",
               description="e2e praise demo skill",
               body_md="# Praise demo\n\nHelps with praise E2E tests.\n",
               claim={"summary": "praise."})
    reg.grade(SKILL, "seed-praise", 0.6, notes="seed")
    return adapter, reg, tmp_path / "inbox"


def _turn(bridge, msg_id: str, text: str) -> list[dict]:
    adapter, reg, inbox = bridge
    chat = "outcome-praise-chat"
    adapter._record_last_turn_skills(chat_key=chat, run_id=f"prev-{msg_id}",
                                     skill_names=[SKILL], user_text="schreib einen bericht")
    n_pre = reg.get(SKILL).n_grades
    p = inbox / f"{msg_id}.json"
    p.write_text(json.dumps({"id": msg_id, "channel": "sandbox-outcome", "from": chat,
                             "chat_id": chat, "text": text, "ts": time.time()}))
    adapter.process_one(p, settings={"whitelist": [chat]})
    assert adapter._last_turn_skills.get(chat) is None   # one-shot, consumed either way
    return list(reg.get(SKILL).grades)[n_pre:]


def test_process_one_praise_writes_an_approval_grade(bridge) -> None:
    new = _turn(bridge, "praise-ok", "Gut gemacht!")
    assert len(new) == 1, new
    assert float(new[0]["score"]) == 0.9
    note = str(new[0].get("notes", ""))
    assert "outcome (approval)" in note and "prev-praise-ok" in note
    assert "gemacht" not in note.lower()       # no user text in the record


def test_process_one_english_praise_writes_an_approval_grade(bridge) -> None:
    new = _turn(bridge, "praise-en", "Well done, thanks!")
    assert [float(g["score"]) for g in new] == [0.9], new


def test_process_one_negated_praise_writes_no_grade(bridge) -> None:
    assert _turn(bridge, "praise-neg", "nicht gut gemacht") == []

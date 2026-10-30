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
    # review c120 s1-8: negation anywhere, multi-word negators anywhere
    ("alles andere als echt gut gemacht", None), ("far from being a really good job", None),
    ("das hast du nicht so richtig wirklich gut gemacht", None),
    ("not really a very good job at all", None), ("I would not say this was a good job", None),
    ("genau das wollte ich nicht", None),
    # review c120 s1-9: rejection on word boundaries only
    ("nochmals danke!", "approval"), ("Bruno, perfekt danke", "approval"),
    ("super, das ist genau richtig, nochmal danke", "approval"),
    # review c120 r2_s1-1/-3: rejection is LIBERAL (a false one only lowers a grade):
    # stems, any length, a question mark does not cancel it
    # (r3_s1-3: "wrongful" is a compound, no verdict -> no signal; "?" no approval)
    ("wrongful? great", None), ("wrong? great", "rejection"),
    ("gut gemacht, aber nochmal bitte", "rejection"),
    ("Bitte benenne in der Tabelle die Spalte falsch_flag in is_false um und ergänze "
     "danach noch eine kurze Beschreibung im README für die anderen Entwickler falsch",
     "rejection"),
    ("Danke für die falsche Antwort", "rejection"), ("das war inkorrekt, danke", "rejection"),
    ("wrongly formatted, thanks", "rejection"), ("Das ist die falsche Datei", "rejection"),
    ("Falsch. Warum hast du die Datei gelöscht?", "rejection"),
    ("Why is this wrong again?", "rejection"), ("funktioniert immer noch nicht", "rejection"),
    ("Nein, falsch: der Fehler kommt aus src/core/learning/feedback_gate.py und nicht aus "
     "dem Adapter, das hatte ich doch schon zweimal geschrieben", "rejection"),
    # review c120 r2_s1-2: sarcasm, conditionals, concessions never approve
    ("Na toll.", None), ("Na prima.", None), ("Toll, alles weg", None),
    ("Das hätte toll werden können", None), ("hätte perfekt sein sollen", None),
    ("that'd be perfect", None), ("dennoch danke", None), ("thanks anyway", None),
    ("trotzdem danke", None), ("Danke für den Versuch", None), ("perfekt 😂😂", None),
    # review c120 r2_s1-4: praise recall
    ("läuft jetzt, danke!", "approval"), ("Jetzt funktioniert es, danke!", "approval"),
    ("Thanks, that works now", "approval"), ("it works now", "approval"),
    ("Thanks again!", "approval"), ("danke nochmal!", "approval"),
    ("Vielen Dank nochmal, super Arbeit", "approval"), ("nicht schlecht, danke!", "approval"),
    ("Thanks, no more errors", "approval"), ("Super, das hat geklappt", "approval"),
    # review c120 s1-10: generic words, questions, requests, sarcasm, quotes, long tokens
    ("is that correct?", None), ("stimmt das?", None), ("richtig?", None),
    ("was ist genau der unterschied?", None), ("stimmt", "approval"), ("correct", "approval"),
    ("danke, und jetzt bitte die tests für das modul schreiben", None),
    ("perfekt, jetzt lösche bitte alle dateien", None), ("na super. wieder kaputt", None),
    ("Great, now everything is broken", None), ("thanks for wasting my time", None),
    ("ja klar, ganz toll gemacht 🙄", None),
    ("\"gut gemacht\" sagt der chef, aber ich nicht wirklich", None),
    ("„gut gemacht“", None), ("x" * 100000 + " gut gemacht", None),
    ("danke!!! " + "x" * 41, None),
    ("korrekt wäre was anderes, danke", None), ("that would be perfect", None),
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


def test_process_one_repeated_praise_grades_once_per_channel_per_day(bridge) -> None:
    """Decision (review c120): one organic follow-up grade per skill per channel per
    UTC day, deduped at write time. Only a day-bound hash of the channel is stored."""
    first = _turn(bridge, "praise-d1", "Gut gemacht!")
    assert [float(g["score"]) for g in first] == [0.9], first
    assert _turn(bridge, "praise-d2", "Gut gemacht!") == []
    assert _turn(bridge, "praise-d3", "Danke!") == []
    note = str(first[0].get("notes", ""))
    assert "dd=" in note and "sandbox-outcome" not in note and "outcome-praise-chat" not in note


def test_process_one_earlier_approval_never_blocks_a_later_rejection(bridge) -> None:
    """Review c120 r2_s1-5: the dedupe key carries the signal KIND. A same-day
    rejection after an approval is recorded (once); a second one is deduped."""
    assert [float(g["score"]) for g in _turn(bridge, "kind-a", "Gut gemacht!")] == [0.9]
    assert [float(g["score"]) for g in _turn(bridge, "kind-r", "Nein, das ist falsch.")] == [0.1]
    assert _turn(bridge, "kind-r2", "falsch") == []
    assert _turn(bridge, "kind-a2", "Danke!") == []


def test_process_one_dedupe_is_a_rolling_24h_window_not_the_utc_day(bridge, monkeypatch) -> None:
    """Review c120 r2_s1-11: two praises two seconds apart across UTC midnight are one
    window; a praise 24 h + 1 s later is a new one."""
    import calendar  # noqa: PLC0415
    real = time.time
    start = real()
    shift = {"to": calendar.timegm((2026, 10, 28, 23, 59, 59, 0, 0, 0))}
    monkeypatch.setattr(time, "time", lambda: shift["to"] + (real() - start))
    assert [float(g["score"]) for g in _turn(bridge, "mid-1", "Gut gemacht!")] == [0.9]
    shift["to"] += 2.0
    assert _turn(bridge, "mid-2", "Gut gemacht!") == []
    shift["to"] += 24 * 3600
    assert [float(g["score"]) for g in _turn(bridge, "mid-3", "Gut gemacht!")] == [0.9]


# ── review c120 round 3 (r3_s1-1/-3/-4/-5): decided detector design ────────────

#: Requests and questions that carry a success / praise word: never an approval
#: (r3_s1-1, the 26 probes of the review plus the request rows of its table).
R3_REQUESTS = (
    "Wie funktioniert das genau", "Funktioniert das auch unter Windows",
    "Klappt das mit Python 3.11", "Läuft das auf dem Raspberry Pi", "How does it work",
    "Has it worked for you before", "I worked on the report yesterday, summarise it",
    "Make sure it works on Windows", "Check that it works now", "Prüf ob es jetzt läuft",
    "Teste ob es funktioniert", "Schau mal ob das klappt", "Ich will dass es funktioniert",
    "Write an excellent cover letter", "Give me a brilliant name for my startup",
    "Make it perfect", "What exactly is a monad", "Exactly how many rows are in the table",
    "Is it exactly 42", "Mach es perfekt", "Nenne mir das perfekte Rezept",
    "Was heißt 'danke' auf Japanisch", "Translate 'thank you' into French",
    "Sag einfach 'super' zu ihm", "Perfect, now work on the tests",
    "Danke, jetzt geht es weiter mit Kapitel 3",
    "Wie funktioniert ein Dieselmotor", "Klappt das auch mit Excel-Dateien",
    "Läuft der Server noch auf Port 8080", "Super, jetzt mach Teil zwei.",
    "Explain how the excellent sorting algorithm timsort works",
    "Ok, und wie funktioniert dann der Import", "Mach eine Liste der besten Restaurants, danke",
    "Danke. Neue Aufgabe: fasse das PDF zusammen.",
    # quotations (single and double quotes) and sarcastic continuations
    "Was heißt 'well done' auf Spanisch", "Schreib 'Danke für Ihre Bestellung' in die Mail.",
    "Great job breaking production.", "Thanks a lot for deleting my file.", "super kaputt",
)

R3_TABLE = (
    # r3_s1-5: praise recall
    ("Top, passt!", "approval"), ("Richtig, so ist es.", "approval"),
    ("Haha super, danke 😂", "approval"), ("it works now", "approval"),
    ("Wow, vielen vielen Dank, das ist wirklich großartig geworden und genau das was ich "
     "gebraucht habe, perfekt!", "approval"),
    ("Läuft jetzt, danke!", "approval"), ("Funktioniert, danke!", "approval"),
    ("works now, thanks", "approval"), ("Vielen Dank, hat geklappt.", "approval"),
    # bare weak words count only with a thank-you or as the whole message
    ("excellent", "approval"), ("Klappt!", "approval"), ("perfekt 😂😂", None),
    # r3_s1-3: innocent text with a rejection word
    ("Schreib einen Artikel über Falschgeld in Europa.", None),
    ("Was ist der Unterschied zwischen wrongful termination und layoff", None),
    ("Summarise the report on corporate wrongdoing.", None),
    ("Check item no. 7 in the list.", None), ("Was bedeutet das Wort Falschparker", None),
    ("Kannst du das Diagramm nochmal in Blau rendern", None),
    ("Mach das gleiche nochmal für die Datei b.csv", None),
    ("Implement 'try again' button on the error page", None),
    ("Is there a 'no' option in the dialog", None),
    # closing idioms: no rejection; with praise no signal at all (decided design)
    ("Nein, danke gerne nochmal für die tolle Hilfe!", None),
    ("Perfekt! Nein, sonst nichts.", None), ("Super, danke! No, that's all for today.", None),
    ("No worries", None), ("Nein, danke.", None),
    # rejection recall kept
    ("Nochmal bitte.", "rejection"), ("nochmal", "rejection"), ("Nope.", "rejection"),
    ("Hmm. Nein, das ist es nicht.", "rejection"), ("Das ist falsch.", "rejection"),
    ("That is not correct at all.", "rejection"), ("You got the dates wrong again.", "rejection"),
    ("Okay. Nochmal mit anderen Farben", "rejection"),
    ("Gut gemacht, aber nochmal bitte mit Legende", "rejection"),
)


@pytest.mark.parametrize("text", R3_REQUESTS)
def test_r3_requests_and_quotes_are_never_approval(text: str) -> None:
    assert skill_inject.detect_outcome_signal(text)[0] is None, text


@pytest.mark.parametrize("text,want", R3_TABLE)
def test_r3_signal_table(text: str, want: str | None) -> None:
    assert skill_inject.detect_outcome_signal(text)[0] == want, text


def _turn_in(bridge, chat: str, msg_id: str, text: str) -> list[dict]:
    adapter, reg, inbox = bridge
    adapter._record_last_turn_skills(chat_key=chat, run_id=f"prev-{msg_id}",
                                     skill_names=[SKILL], user_text="schreib einen bericht")
    n_pre = reg.get(SKILL).n_grades
    p = inbox / f"{msg_id}.json"
    p.write_text(json.dumps({"id": msg_id, "channel": "sandbox-outcome", "from": chat,
                             "chat_id": chat, "text": text, "ts": time.time()}))
    adapter.process_one(p, settings={"whitelist": [chat]})
    return list(reg.get(SKILL).grades)[n_pre:]


@pytest.mark.parametrize("text", ["Teste ob es funktioniert", "Make sure it works on Windows",
                                  "Schreib einen Artikel über Falschgeld in Europa.",
                                  "Perfekt! Nein, sonst nichts."])
def test_process_one_request_or_innocent_text_writes_no_grade(bridge, text) -> None:
    """r3_s1-1/-3 through the bridge: a request with a success word and innocent text
    with a rejection stem write no organic grade."""
    assert _turn(bridge, "r3-none", text) == []


def test_process_one_recovered_praise_writes_an_approval_grade(bridge) -> None:   # r3_s1-5
    assert [float(g["score"]) for g in _turn(bridge, "r3-top", "Top, passt!")] == [0.9]


def test_process_one_user_skill_dedupe_spans_every_chat_of_the_tenant(bridge) -> None:
    """r3_s1-4: a user-scope skill is shared by every chat, so a second chat is no
    second allowance (the console's rule): (skill, tenant), not (skill, chat)."""
    assert [float(g["score"]) for g in _turn_in(bridge, "chat-a", "dd-a", "Gut gemacht!")] == [0.9]
    assert _turn_in(bridge, "chat-b", "dd-b", "Gut gemacht!") == []
    assert _turn_in(bridge, "chat-c", "dd-c", "Danke!") == []
    # the kind stays in the key: a rejection from another chat is still recorded
    assert [float(g["score"]) for g in _turn_in(bridge, "chat-b", "dd-r", "Das ist falsch.")] == [0.1]


# ── review c120 round 4 (r4_s1-1/-2/-4): through adapter.process_one ─────────
# Each class: praise rows that must grade 0.9, and a near-miss control of the same
# shape that must not grade. Tuned on /tmp/c120/fix/I1/table_tune.py; these rows
# are not the holdout.

R4_PRAISE = {
    # an imperative-list noun as the subject of a success statement
    "noun_start": ["Test passed, thanks!", "Plan looks good, thanks.", "Liste ist perfekt, danke."],
    # exclamations and wie/was/how/what clauses inside praise
    "qword": ["What a great answer, thanks!", "Danke, genau wie gewünscht!",
              "Danke, das ist genau das, was ich brauchte."],
    # code in backticks / a short quoted name inside praise
    "code": ["Thanks, `make build` works now.",
             "Thanks, the \"export\" button works perfectly now."],
    # jetzt / now / weiter so do not cancel praise
    "now": ["Much better now, thanks!", "Danke, jetzt verstehe ich es!", "Klasse, weiter so!"],
}
R4_CONTROL = {
    "noun_start": ["Test the parser, thanks.", "Plan the release for Monday, danke."],
    "qword": ["Danke! Wie mache ich das unter Windows", "Thanks, how do I run it in Docker"],
    "code": ["Put \"Thanks, great work\" in the footer.", "Thanks, now run `make build` again."],
    "now": ["Great, now add error handling.", "Perfect, now nothing works."],
}
#: r4_s1-1: a question or a new request after a thank-you, typed without '?'.
R4_QUESTIONS = [
    "Danke! Klappt das auch mit Excel-Dateien", "Perfekt. Funktioniert das auch unter Windows",
    "Super, danke. Ist das auch threadsafe", "Danke. Läuft der Server noch auf Port 8080",
    "Thanks in advance, I need a summary of the attached PDF by tomorrow.",
    "Danke! Ich brauche noch einen Unit-Test für den Parser.",
    "Vielen Dank im Voraus für deine Hilfe bei der Steuererklärung.",
]
R4_PRAISE_ROWS = [(c, t) for c, rows in R4_PRAISE.items() for t in rows]
R4_CONTROL_ROWS = [(c, t) for c, rows in R4_CONTROL.items() for t in rows]


@pytest.mark.parametrize("cls,text", R4_PRAISE_ROWS)
def test_r4_process_one_praise_class_grades_approval(bridge, cls, text) -> None:
    assert [float(g["score"]) for g in _turn(bridge, f"r4p-{cls}", text)] == [0.9], text


@pytest.mark.parametrize("cls,text", R4_CONTROL_ROWS)
def test_r4_process_one_near_miss_control_grades_no_approval(bridge, cls, text) -> None:
    assert 0.9 not in [float(g["score"]) for g in _turn(bridge, f"r4c-{cls}", text)], text


@pytest.mark.parametrize("text", R4_QUESTIONS)
def test_r4_process_one_question_or_request_after_thanks_writes_no_grade(bridge, text) -> None:
    assert _turn(bridge, "r4q", text) == [], text


def test_r4_process_one_colon_no_is_no_rejection(bridge) -> None:      # r4_s1-4
    assert _turn(bridge, "r4n", "Write the answer as: No.") == []
    # control: "no" after a sentence break stays a rejection
    assert [float(g["score"]) for g in _turn(bridge, "r4n2", "Hm. No, the other table.")] == [0.1]

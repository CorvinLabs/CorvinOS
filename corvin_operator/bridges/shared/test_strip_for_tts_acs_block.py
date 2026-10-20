"""The ACS run account is on-screen information; a voice note must not narrate it.

Both voice entry points (the bridge's ``build_voice_summary`` and the console's
``_summarize_for_speech``) run ``strip_for_tts.py --mode code-only`` before the summarizer, so
that script is the one place the block is removed. Measured on a real run (2026-10-21): without
this the spoken summary said "Der ACS-Lauf wurde ausdrücklich ... angefordert und ist nach zwei
Durchgängen mit zwei Workern in 19 Sekunden ...".
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "voice" / "scripts" / "strip_for_tts.py"

DE = ("Solarenergie hat zwei Vorteile und zwei Nachteile.\n\n"
      "Verlauf\n🧭 Iteration 1: Manager teilt in 2 Teilaufgaben auf\n"
      "🤖 Worker 1/2: erfolgreich (Konfidenz 0.95)\n📊 Gate Iteration 2: bestanden, Loss-Score 0.998 (höher = besser)\n\n"
      "ACS-Lauf\n- Auslöser: /delegate (ausdrücklich angefordert)\n- Ergebnis: abgeschlossen\n"
      "- Schritte: 2 Iteration(en), 2 Worker\n- Loss-Score (höher = besser): 0.998 (Verlauf 0.998)\n- Dauer: 19 s\n")
EN = ("Solar has two upsides.\n\nSteps\n🧭 Iteration 1: manager splits into 2 subtasks\n\n"
      "ACS run\n- Trigger: fan-out request\n- Outcome: completed\n- Loss score (higher is better): 0.998\n")


def _run(text: str, mode: str) -> str:
    return subprocess.run([sys.executable, "-I", str(SCRIPT), "--mode", mode], input=text,
                          capture_output=True, text=True, timeout=30, check=True).stdout


@pytest.mark.parametrize("mode", ["code-only", "full"])
@pytest.mark.parametrize("text,kept", [(DE, "Solarenergie hat zwei Vorteile"), (EN, "Solar has two upsides")])
def test_the_run_account_is_removed_and_the_answer_is_kept(text, kept, mode):
    out = _run(text, mode)
    assert kept in out
    for noise in ("ACS-Lauf", "ACS run", "Loss-Score", "Loss score", "Auslöser", "Trigger:", "Worker 1/2",
                  "Iteration 1", "Verlauf", "Steps"):
        assert noise not in out, (noise, out)


def test_ordinary_text_that_merely_mentions_the_words_is_untouched():
    plain = "Der Verlauf der Krankheit war mild.\nDie ACS-Lauf-Statistik kam später.\n- ein Stichpunkt\n"
    out = _run(plain, "code-only")
    assert "Der Verlauf der Krankheit war mild." in out
    assert "Die ACS-Lauf-Statistik kam später." in out
    assert "- ein Stichpunkt" in out


# A heading alone is not the run account. These are real answers (found by an independent review).
# `--mode full` also drops list markers by design, so the check is that the CONTENT survives.
@pytest.mark.parametrize("mode", ["code-only", "full"])
@pytest.mark.parametrize("answer,keep", [
    ("Here is how to replace the pump.\n\nSteps\n🔧 Close the valve\n📊 Read the gauge\n\nThat should take about an hour.",
     ["Steps", "Close the valve", "Read the gauge", "about an hour"]),
    ("Kurzer Rückblick auf das Projekt.\n\nVerlauf\n🤖 Der Roboter wurde gebaut\n📊 Die Zahlen stiegen\n\nDanach wuchs das Team.",
     ["Verlauf", "Der Roboter wurde gebaut", "Die Zahlen stiegen", "wuchs das Team"]),
    ("Sure, here are the details for Sunday.\n\nACS run\n- Date: 12 May\n- Place: city park\n\nSee you there!",
     ["ACS run", "Date: 12 May", "Place: city park", "See you there"]),
    ("Zum Sonntag:\n\nACS-Lauf\n- Treffpunkt: Stadtpark\n- Start: 10 Uhr\n\nBis dann!",
     ["ACS-Lauf", "Treffpunkt: Stadtpark", "Start: 10 Uhr", "Bis dann"]),
])
def test_a_heading_with_ordinary_bullets_is_answer_content_and_stays(answer, keep, mode):
    out = _run(answer, mode)
    for needle in keep:
        assert needle in out, (needle, out)


@pytest.mark.parametrize("mode", ["code-only", "full"])
def test_windows_line_endings_do_not_defeat_the_removal(mode):
    out = _run(DE.replace("\n", "\r\n"), mode)
    assert "ACS-Lauf" not in out and "Loss-Score" not in out and "Worker 1/2" not in out
    assert "Solarenergie hat zwei Vorteile" in out

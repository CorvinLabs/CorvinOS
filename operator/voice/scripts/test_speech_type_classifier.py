#!/usr/bin/env python3
"""Tests for the ADR-0596 speech-type classifier + shared choice detector.

The classifier is a STYLE dial: option / called-out-fact fidelity does NOT
depend on it, so the load-bearing property under test is (a) that real choices
are detected (has_choice_shape True → decision) and (b) that ordinary lists /
inline sublabels / numbered steps are NOT misread as choices in a way that would
force verbatim enumeration. A false positive is safe; a missed real choice on the
degrade path is not — so the detector is intentionally inclusive.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import summarize  # noqa: E402


# --- decision detection -----------------------------------------------------

def test_labelled_option_lines_are_decision() -> None:
    text = ("Du hast drei Wege.\n"
            "a) Postgres nehmen\n"
            "b) SQLite behalten\n"
            "c) beides parallel\n"
            "Welche Variante willst du?")
    assert summarize.has_choice_shape(text) is True
    assert summarize.classify_speech_type(text) == "decision"


def test_option_keyword_is_decision() -> None:
    for text in ("Wir haben Option A und Option B geprüft.",
                 "Nimm Variante 2 oder Variante 3.",
                 "Tier 1 vs Tier 2 vs Tier 3."):
        assert summarize.has_choice_shape(text) is True
        assert summarize.classify_speech_type(text) == "decision"


def test_trailing_prose_pick_one_question_is_decision() -> None:
    text = ("Das ist eine lange Erklaerung ueber Datenbanken und ihre "
            "Eigenschaften. Willst du Postgres oder SQLite?")
    assert summarize.has_choice_shape(text) is True
    assert summarize.classify_speech_type(text) == "decision"


def test_english_either_or_question_is_decision() -> None:
    text = "Long explanation about the tradeoffs here. Do you want A or B?"
    assert summarize.has_choice_shape(text) is True
    assert summarize.classify_speech_type(text) == "decision"


# --- over-fire guards (must NOT be decision) --------------------------------

def test_inline_ab_sublabels_are_not_decision() -> None:
    # a. / b. INSIDE one prose line — a description, not a pick-one choice.
    text = "Es gibt zwei Modi: a. synchron b. asynchron, beide sind nutzbar."
    assert summarize.has_choice_shape(text) is False
    assert summarize.classify_speech_type(text) == "explainer"


def test_numbered_steps_are_not_decision() -> None:
    text = ("So gehst du vor:\n"
            "1. Erst installieren\n"
            "2. Dann konfigurieren\n"
            "3. Zuletzt starten")
    assert summarize.has_choice_shape(text) is False
    assert summarize.classify_speech_type(text) == "explainer"


# --- report vs explainer ----------------------------------------------------

def test_completion_markers_at_top_are_report() -> None:
    for text in ("Erledigt: Ich habe das Login gebaut, Tests sind grün.",
                 "✅ Deployed die neue API, sie läuft jetzt live.",
                 "Fixed the race condition and added a regression test."):
        assert summarize.classify_speech_type(text) == "report"


def test_completion_word_mid_text_is_not_report() -> None:
    # "is now live" deep in an explanation must NOT flip the type to report.
    text = ("Der Cache funktioniert so: Werte landen im RAM. Nebenbei, der "
            "Dienst ist jetzt live, aber das ist nicht der Kern hier. Im "
            "Wesentlichen geht es um die Lesegeschwindigkeit.")
    assert summarize.classify_speech_type(text) == "explainer"


def test_plain_explanation_is_explainer() -> None:
    text = ("So funktioniert der Cache: er speichert häufige Werte im RAM "
            "und liest sie dadurch schneller als von der Platte.")
    assert summarize.has_choice_shape(text) is False
    assert summarize.classify_speech_type(text) == "explainer"


def test_empty_input_is_explainer_and_no_choice() -> None:
    assert summarize.classify_speech_type("") == "explainer"
    assert summarize.classify_speech_type("   \n  ") == "explainer"
    assert summarize.has_choice_shape("") is False


# --- classifier is deterministic --------------------------------------------

def test_classifier_is_deterministic() -> None:
    text = "a) foo\nb) bar\nWhich one do you prefer?"
    assert (summarize.classify_speech_type(text)
            == summarize.classify_speech_type(text)
            == "decision")

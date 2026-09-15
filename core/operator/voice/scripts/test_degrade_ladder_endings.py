#!/usr/bin/env python3
"""Tests for the ADR-0597 option-safe degrade ladder (no-LLM structural fallback).

Load-bearing invariants:
  (a) prose output never ends on a non-terminal fragment.
  (b) a trailing pick-one question survives (list AND trailing-prose forms).
  (d) an over-budget CHOICE keeps every option.
  (e) an over-budget ORDINARY list is budget-bounded (never read in full) and
      ends with a "N more points" tail.
  + a MUTATION test proving the guard catches the exact regression (dropped
    option) that the old `_cap_to_budget(naive_truncate(...))` chain caused.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import summarize  # noqa: E402


CHOICE = ("Du hast drei Optionen.\n"
          "- Option A: schnell aber teuer, mit vielen zusätzlichen Details "
          "die hier ziemlich lang ausfallen und Platz kosten\n"
          "- Option B: langsam aber günstig, ebenfalls mit sehr viel "
          "erklärendem Text der das Budget weiter belastet\n"
          "- Option C: der Mittelweg mit noch mehr Text der das Budget "
          "ganz sicher sprengt\n"
          "Welche willst du?")

ORDINARY = ("Changelog dieser Version:\n" +
            "\n".join(f"- Punkt Nummer {i} mit etwas erklärendem Text dahinter"
                      for i in range(1, 31)))

PROSE_Q = ("Dies ist ein sehr langer erster Satz der viel Platz einnimmt und "
           "das Budget schon fast füllt. Dann noch ein Mittelsatz hier drin. "
           "Willst du A oder B?")


def _degrade(text: str, budget: int) -> str:
    """Reproduce the production degrade branch (summarize.py, both-backends-down)."""
    if summarize.has_choice_shape(text):
        return summarize.item_preserving_cap(text, budget, "de")
    if summarize.naive_truncate_is_list(text):
        return summarize.bounded_list_cap(text, budget, "de")
    return summarize._cap_to_budget(summarize.naive_truncate(text, budget), budget, "de")


# (a) ------------------------------------------------------------------------

def test_prose_ends_on_terminal() -> None:
    out = summarize._cap_to_budget(
        "Ein langer Satz der das Budget deutlich überschreitet und weitergeht "
        "und immer noch weiter. Zweiter Satz. Dritter Satz hier.", 60, "de")
    assert out and out.rstrip()[-1] in ".!?…"


# (b) ------------------------------------------------------------------------

def test_trailing_question_survives_list_form() -> None:
    out = _degrade(CHOICE, 120)
    assert out.rstrip().endswith("?")


def test_trailing_question_survives_prose_form() -> None:
    out = _degrade(PROSE_Q, 60)
    assert out.rstrip().endswith("?")


# (d) — option fidelity ------------------------------------------------------

def test_over_budget_choice_keeps_every_option() -> None:
    out = _degrade(CHOICE, 120)
    for cue in ("schnell", "langsam", "Mittelweg"):  # one token per option
        assert cue.lower() in out.lower(), f"option cue {cue!r} dropped: {out!r}"
    assert out.rstrip().endswith("?")


# (e) — ordinary list is bounded ---------------------------------------------

def test_ordinary_list_is_bounded_not_verbatim() -> None:
    out = _degrade(ORDINARY, 200)
    # Not all 30 items are read: the last item's text must be absent.
    assert "Nummer 30" not in out
    assert re.search(r"(weitere Punkte|more points)", out)
    # Bounded roughly to budget (tail allowed to spill a little).
    assert len(out) <= 200 + 40
    # NOT gameable by a zero-content impl: real content must survive — the intro
    # and at least the first item, and a plausible number of kept items.
    assert "Changelog" in out
    assert "Nummer 1 " in out
    kept = len(re.findall(r"Nummer \d+", out))
    assert 2 <= kept < 30


def test_ordinary_list_two_item_boundary_grammar() -> None:
    two = "Changelog:\n- Punkt eins mit viel erklärendem Text der den Platz kostet\n- Punkt zwei"
    out = summarize.bounded_list_cap(two, 40, "de")
    # Singular grammar when exactly one item is folded into the tail.
    if "weiterer Punkt" in out or "weitere Punkte" in out:
        assert not re.search(r"\b1 weitere Punkte\b", out)


# MUTATION test --------------------------------------------------------------

def test_mutation_old_chain_drops_an_option() -> None:
    """The OLD degrade chain — `_cap_to_budget(naive_truncate(text), budget)` —
    with no choice branch. It must DROP a middle option (proving the new guard is
    load-bearing). `naive_truncate` keeps its string return, so the old chain is
    reconstructed exactly; the failure here is a dropped option, never a
    TypeError.
    """
    old = summarize._cap_to_budget(
        summarize.naive_truncate(CHOICE, 120), 120, "de")
    # The middle option 'B / langsam' is exactly what front-filling drops.
    dropped = "langsam" not in old.lower()
    assert dropped, (
        "MUTATION expected the old chain to drop a middle option, but it did "
        f"not — the guard would not be load-bearing. old={old!r}")
    # And the new path keeps it — the contrast that makes the guard meaningful.
    new = _degrade(CHOICE, 120)
    assert "langsam" in new.lower()


# adversarial option shapes (from the impl adversarial review) ---------------

def test_letter_labels_are_kept_decidable() -> None:
    text = ("a) synchron verarbeiten mit direkter Antwort\n"
            "b) asynchron mit Queue und späterer Antwort\n"
            "Willst du a oder b?")
    out = summarize.item_preserving_cap(text, 120, "de")
    assert "a)" in out and "b)" in out  # labels survive → choice stays decidable
    assert out.rstrip().endswith("?")


def test_numbered_labels_are_kept() -> None:
    text = ("1. Postgres nehmen für robuste Transaktionen\n"
            "2. SQLite behalten für Einfachheit\n"
            "Welche Nummer, 1 oder 2?")
    out = summarize.item_preserving_cap(text, 120, "de")
    assert "1." in out and "2." in out


def test_keyword_prose_choice_keeps_every_option() -> None:
    # Options in PROSE, no line markers — has_choice_shape fires via keyword.
    text = ("Wir könnten das auf zwei Arten lösen. Option A ist ein synchroner "
            "Ansatz der einfach ist aber langsam bei Last. Option B ist eine "
            "asynchrone Queue die schnell ist aber komplexer im Betrieb.")
    assert summarize.has_choice_shape(text) is True
    out = summarize.item_preserving_cap(text, 120, "de")
    assert "Option A" in out and "Option B" in out


def test_multiline_option_content_survives() -> None:
    text = ("Zwei Wege:\n"
            "- Option A: die schnelle Variante,\n"
            "  aber sie kostet Geld und Ressourcen ohne Ende\n"
            "- Option B: die langsame Variante,\n"
            "  dafür völlig kostenlos immer\n"
            "Welche nimmst du?")
    out = summarize.item_preserving_cap(text, 400, "de")
    assert "Ressourcen" in out  # non-final option's continuation line survives
    assert "kostenlos" in out


def test_single_line_question_answer_is_bounded_not_verbatim() -> None:
    # One flowing paragraph ending in a cue-question must NOT be read verbatim.
    text = ("Nach einer langen Analyse der verschiedenen Datenbank-Optionen und "
            "ihrer jeweiligen Vor- und Nachteile im Detail komme ich zu dem "
            "Schluss dass beide brauchbar sind, sollen wir jetzt deployen oder "
            "noch warten?")
    out = summarize.item_preserving_cap(text, 120, "de")
    assert len(out) < len(text)
    assert out != text


def test_production_degrade_path_keeps_options() -> None:
    """Hit the REAL production degrade branch in summarize() (not a local
    helper): force structural backend so both LLM paths are skipped."""
    old = os.environ.get("VOICE_SUMMARIZE_BACKEND")
    os.environ["VOICE_SUMMARIZE_BACKEND"] = "structural"
    try:
        out = summarize.summarize(CHOICE, "de", 120, "model")
    finally:
        if old is None:
            os.environ.pop("VOICE_SUMMARIZE_BACKEND", None)
        else:
            os.environ["VOICE_SUMMARIZE_BACKEND"] = old
    low = out.lower()
    for cue in ("schnell", "langsam", "mittelweg"):
        assert cue in low, f"production degrade dropped option cue {cue!r}: {out!r}"


def test_closing_question_without_cue_word_survives() -> None:
    # A plain German pick-one with no cue word ("Was passt am besten?").
    text = ("Vier Ansätze zur Auswahl:\n"
            "a) In-Memory-LRU: schnell aber flüchtig\n"
            "b) Redis: robust aber extra Dienst\n"
            "c) Memcached: simpel aber wenig Features\n"
            "d) gar kein Cache: einfach aber langsam\n"
            "Was passt am besten zu unserem Setup?")
    out = summarize.item_preserving_cap(text, 400, "de")
    assert "a)" in out and "d)" in out
    assert out.rstrip().endswith("?") and "am besten" in out


def test_non_question_outro_facts_survive() -> None:
    # Shared consequence/deadline after the options must not be folded away.
    text = ("Zwei Wege:\n"
            "a) Postgres nehmen\n"
            "b) SQLite behalten\n"
            "Beide brechen die bestehende API und erfordern eine Migration "
            "bis Freitag.")
    out = summarize.item_preserving_cap(text, 150, "de")
    assert "a)" in out and "b)" in out
    assert "API" in out and "Migration" in out


def test_numbered_labels_not_missplit_by_sentence_splitter() -> None:
    text = "Zwei:\n1. Postgres\n2. SQLite\nWelche willst du, 1 oder 2?"
    out = summarize.item_preserving_cap(text, 120, "de")
    assert "1." in out and "2." in out
    assert "Postgres" in out and "SQLite" in out
    assert out.rstrip().endswith("?")


def test_bullet_marker_not_spoken_but_labels_kept() -> None:
    text = ("Auswahl:\n"
            "a) erste Option mit Text\n"
            "- zweite Option als Bullet\n"
            "1. dritte Option numeriert\n"
            "Welche bevorzugst du?")
    out = summarize.item_preserving_cap(text, 300, "de")
    assert "a)" in out and "1." in out          # letter/number labels kept
    assert "- zweite" not in out                 # bullet dash not spoken
    assert "zweite Option" in out                # its content survives


def test_col0_continuation_content_never_lost() -> None:
    # A col-0 (non-indented) option continuation is attributed to the outro
    # (ordering nuance) but its CONTENT must never be lost — the load-bearing
    # part. Options + their labels + the trailing question all survive.
    text = ("Zwei Wege:\n"
            "a) alles löschen\n"
            "unwiderruflich ohne Backup\n"
            "b) behalten\n"
            "Welche willst du?")
    out = summarize.item_preserving_cap(text, 200, "de")
    assert "a)" in out and "b)" in out
    assert "unwiderruflich" in out            # content survives (may be reordered)
    assert out.rstrip().endswith("?")


@pytest.mark.xfail(strict=False,
                   reason="ADR-0597 accepted heuristic nuance: a COLUMN-0 option "
                          "continuation is spoken at the end (outro) rather than "
                          "next to its option. Ordering only — content is never "
                          "lost (see test_col0_continuation_content_never_lost). "
                          "Encoded so a regression OR a future fix is visible.")
def test_col0_continuation_ordering_nuance() -> None:
    text = ("Zwei Wege:\n"
            "a) alles löschen\n"
            "unwiderruflich ohne Backup\n"
            "b) behalten\n"
            "Welche willst du?")
    out = summarize.item_preserving_cap(text, 200, "de")
    # Ideal (currently NOT met): the qualifier sits next to option a, before b.
    assert out.index("unwiderruflich") < out.index("b)")


@pytest.mark.xfail(strict=False,
                   reason="ADR-0597 named limitation: a label-less in-prose "
                          "choice with no trailing '?' is not structurally "
                          "visible on the no-LLM degrade path (only the LLM "
                          "AUSWAHL rule protects it). Encoded so a silent "
                          "regression OR an accidental fix becomes visible.")
def test_labelless_prose_choice_degrade_limit() -> None:
    text = ("Nach langer Analyse und vielen Detailüberlegungen die hier viel "
            "Platz brauchen: entweder du nimmst Postgres oder du bleibst bei "
            "SQLite. Das war meine Empfehlung dazu.")
    out = _degrade(text, 80)
    assert "postgres" in out.lower() and "sqlite" in out.lower()


# backward-compat ------------------------------------------------------------

def test_cap_to_budget_two_arg_still_works() -> None:
    assert summarize._cap_to_budget("Satz eins. Satz zwei. Satz drei.", 12)

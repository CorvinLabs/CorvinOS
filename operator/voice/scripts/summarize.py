#!/usr/bin/env python3
"""Summarize text into a TTS-friendly snippet using Anthropic Haiku.

Usage:
    summarize.py --lang de|en [--max-chars 400] [--model claude-haiku-4-5]
    Reads input text from stdin, writes summary to stdout.

Uses the `claude` CLI (Max-subscription OAuth). Falls back to returning
the first ~max-chars characters of the input as a structural summary
so the pipeline never blocks the read-aloud step.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Optional Layer-11 dialectic integration. The voice_summary site is
# default-off, so when the module is missing OR the user hasn't opted
# in, this is a zero-cost no-op. Lazy-import keeps stop_hook calls fast
# in the common no-dialectic path.
try:
    sys.path.insert(0,
        str(Path(__file__).resolve().parent.parent.parent / "bridges" / "shared"))
    import dialectic as _dialectic  # type: ignore
except Exception:  # noqa: BLE001
    _dialectic = None

# i18n module — full-locale support beyond DE/EN. When the module is
# importable, an `--output-language <bcp47>` flag pins the LLM output
# to any BCP-47 code via a system-prompt directive. When missing, the
# legacy DE/EN paths keep working byte-identically.
try:
    sys.path.insert(0,
        str(Path(__file__).resolve().parent.parent.parent / "bridges" / "shared"))
    import i18n as _i18n  # type: ignore
except Exception:  # noqa: BLE001
    _i18n = None


# ── Voice-summary timeout budgets (VOICE-F7) ─────────────────────────────────
# adapter.py spawns THIS script under a HARD subprocess cap. Inside that cap the
# CLI backend and the Hermes fallback run SEQUENTIALLY, so their waits must SUM
# to comfortably LESS than the parent cap (with margin for process spawn +
# extraction) — otherwise the parent kills the child mid-Hermes and the Hermes
# fallback added in 41c174e is unreachable in exactly the hang case it exists
# for. Contract (parent caps mirrored in adapter.py build_voice_summary /
# _append_lern_zugabe / _append_metapher):
#   main summary : parent cap 120s  →  CLI 45s + Hermes 60s = 105s  (15s margin)
#   annex (each) : parent cap  60s  →  CLI 20s + Hermes 30s =  50s  (10s margin)
# Guard: test_summarize.py::test_voice_summary_timeout_budgets_fit_parent_caps.
_SUMMARY_CLI_TIMEOUT_S = 45      # was 90 — 90+60 overflowed the 120s parent cap
_SUMMARY_HERMES_TIMEOUT_S = 60
_ANNEX_CLI_TIMEOUT_S = 20        # was 45 — 45+45 overflowed the 60s parent cap
_ANNEX_HERMES_TIMEOUT_S = 30     # was 45 (the _ollama_generate default)
# The adapter-side parent caps this ladder must fit inside (SSOT for the test).
_PARENT_CAP_MAIN_S = 120
_PARENT_CAP_ANNEX_S = 60


SYSTEM = {
    "de": (
        "Du bist ein Sprachassistent, der Claude-Antworten so vorliest, "
        "wie ein Mensch sie einem anderen Menschen mündlich erzählen "
        "würde. Der Hörer bekommt nur deine Stimme — Bildschirm, "
        "Markdown, Aufzählungszeichen, Code-Tokens fallen weg. Du "
        "paraphrasierst den Inhalt, du erfindest nichts dazu.\n"
        "\n"
        "FOKUS — Worauf der Hörer hört: auf den INHALT der Antwort, "
        "nicht auf einen Rückbezug zur Nutzerfrage. Beschreibe, was "
        "die Antwort sagt — was wurde erreicht, was wurde gefunden, "
        "was ist jetzt möglich, welche Optionen es gibt — und nicht, "
        "was der Nutzer wollte.\n"
        "\n"
        "AUFBAU der Ausgabe (Outcome-First — was sich für den Hörer "
        "geändert hat, kommt zuerst):\n"
        "1. Lead-Satz im User-Mental-Model: was ist jetzt möglich, was "
        "   hat sich für den Hörer geändert, was kann er jetzt was "
        "   vorher nicht ging — z.B. 'Der Test läuft jetzt durch', "
        "   'Der Bug ist weg', 'Die Pipeline ist offen', 'Der Login "
        "   funktioniert wieder'. KEIN Code-Mental-Model wie 'Ich habe "
        "   X.py editiert, Y getestet, Z gefixt' — der Hörer braucht "
        "   den Effekt aus seiner Sicht, nicht die Schritte aus deiner. "
        "   Wenn das Original keinen für den Hörer relevanten Effekt "
        "   benennt (reine Recherche-Antwort, Findung statt Änderung), "
        "   starte stattdessen mit dem Kern-Befund aus Hörer-"
        "   Perspektive — 'die Datei liegt unter foo/bar', 'es gibt "
        "   drei Wege …', 'die Antwort lautet …', 'die Ursache war …'. "
        "   Niemals 'ich-zentrisch' eröffnen; immer aus dem Blickwinkel "
        "   des Hörers.\n"
        "2. Danach die Details und der Mechanismus — aber als Teil "
        "   derselben Idee, nicht als nüchterne Aufzählung. Der Hörer "
        "   soll Konzepte, Methoden und mentale Modelle mitnehmen, "
        "   also erkläre, was etwas ist und worum es im Kern geht, "
        "   statt nur Bezeichner aneinanderzureihen. Hat das Original "
        "   Optionen, Schritte oder Phasen, formuliere zuerst die "
        "   übergeordnete Idee in eigenen Worten und ordne die "
        "   einzelnen Punkte hinein — z.B. 'Es gibt zwei Wege …', "
        "   'Die Idee dahinter ist …', 'Im Kern macht das …'. "
        "   Vollständigkeit bleibt absolut: jeder Hauptpunkt, jede "
        "   Option, jedes Listenelement, jede abschließende "
        "   Auswahlfrage muss inhaltlich vorkommen — aber als Idee "
        "   verpackt, nicht als Stichwortliste. Kompakte "
        "   Originalstellen bleiben kompakt; ausgeschmückt wird "
        "   nichts. Codeschnipsel und kryptische Pfade nicht wörtlich "
        "   vorlesen, sondern so umschreiben, dass der Hörer ohne "
        "   Bildschirm versteht, worum es geht.\n"
        "3. Optional: kurzer Folge-Kontext für den Hörer — welche "
        "   Frage ist offen, welche Auswahl muss er treffen, was "
        "   kommt als nächstes. Nur wenn das Original eine solche "
        "   Frage oder Folge-Aktion enthält; sonst weglassen.\n"
        "\n"
        "VERSTÄNDLICHKEIT (gleichrangig zu Treue): Der Hörer soll am "
        "Ende verstehen, WARUM etwas so ist und WIE es im Großen wirkt "
        "— nicht nur, dass es so ist. Wenn das Original Begründungen, "
        "Motive, Effekte oder Bezüge zwischen den Punkten nennt — auch "
        "in Form von 'weil', 'damit', 'sodass', Why-/How-to-apply-Zeilen, "
        "Vorher/Nachher-Paaren — hebe sie hervor und mache sie zur "
        "Brücke zwischen den Punkten. Du darfst gängige Metaphern und "
        "Bilder einsetzen, um vorhandene Konzepte greifbar zu machen "
        "(z.B. 'der Schlüssel liegt jetzt an einem festen Ort', "
        "'das ist ein Sicherheitsnetz darunter', 'wie ein Schalter, "
        "der …'), solange die Metapher nur das beschreibt, was das "
        "Original sagt. Übersetze technische Bezeichner in Begriffe, "
        "die jemand ohne Quellcode versteht. Eine reine Liste von "
        "Tatsachen ohne Kontext ist nicht das Ziel — der Hörer soll "
        "ein Modell mitnehmen, kein Datenblatt.\n"
        "\n"
        "TREUE-PRINZIP (oberste Regel, schlägt alle anderen): Sage "
        "ausschließlich, was im Original tatsächlich steht. Erfinde "
        "keine neuen Fakten — keine Pfade, keine Zahlen, keine "
        "Architektur-Details, keine Code-Tokens, die nicht im Original "
        "stehen, keine Mechanismen oder Konsequenzen, die das Original "
        "nicht selbst aussagt. Wenn das Original einen Punkt nur als "
        "Stichwort nennt, bleibt er ein Stichwort — Verständlichkeit "
        "kauft sich keinen Erklärungsfreiraum, wo das Original "
        "schweigt. Metaphern sind Brücken zu Vorhandenem, nicht "
        "Türen zu Neuem. Im Zweifel weglassen statt erfinden.\n"
        "\n"
        "VOLLSTÄNDIGKEIT (zweite Regel): Niemals einen Hauptpunkt "
        "streichen, niemals eine Liste mittendrin abbrechen, niemals "
        "nur ein Vorschau-Snippet liefern. Wenn das Original eine Liste "
        "ist, taucht JEDES Item im Vorlesetext auf. Wenn es einen Plan "
        "in Schritten beschreibt, kommen alle Schritte vor. Wenn es "
        "mehrere Phasen hat, kommen alle Phasen vor.\n"
        "\n"
        "DETAILTIEFE (folgt aus Treue): Die Tiefe pro Punkt ergibt sich "
        "aus dem Original — nicht aus einem Stilziel. Ist der Punkt im "
        "Original kompakt, bleibt er kompakt (ein Halbsatz reicht). Ist "
        "er ausführlich, übernimm den vorhandenen Inhalt. Niemals "
        "ausschmücken, um eine Soll-Länge zu erreichen.\n"
        "\n"
        "AUSWAHLMÖGLICHKEITEN sind heilig: Wenn die Antwort dem Hörer "
        "Optionen anbietet — egal ob als 'a, b, c', 'Variante 1, 2, 3', "
        "'Option A / B', 'Stufe 1 / 2 / 3', mehrere Vorschläge oder eine "
        "abschließende Auswahlfrage — muss JEDE Option mit Bezeichner "
        "UND der im Original genannten Kurzbeschreibung im Vorlesetext "
        "auftauchen, sodass der Hörer die Auswahl ohne Bildschirm "
        "treffen kann. Eine abschließende Frage wie 'Welche Variante "
        "willst du?' wird wörtlich übernommen.\n"
        "\n"
        "SPRECHSTIL — wichtig, der Hörer kann den Originaltext nicht "
        "selbst lesen und will keine vorgelesene Liste hören:\n"
        "• Klinge wie ein Mensch, der die Antwort jemandem mündlich "
        "  erzählt. Lockerer, natürlicher Ton, aber präzise — kein "
        "  Smalltalk, kein Padding, kein Telegrammstil.\n"
        "• Verbinde die Punkte mit natürlichen Übergängen — 'zuerst', "
        "  'danach', 'parallel dazu', 'am Ende', 'außerdem', 'zum "
        "  Schluss'. Vermeide schematische Aufzählungen wie 'Erstens "
        "  …, zweitens …, drittens …'; das klingt vorgelesen.\n"
        "• Variiere Satzlänge und Wortwahl, wiederhole nicht dieselben "
        "  Floskeln direkt nacheinander.\n"
        "• Strukturwörter aus der Originalvorlage ('Punkt 1:', 'Layer "
        "  A:', 'Pipeline-Schritt 5:', Tabellen-Header) werden in "
        "  natürlichen Fließtext übersetzt, nicht wörtlich aufgesagt.\n"
        "• Wichtig: Der Stilwechsel ändert nur das Wie, niemals das "
        "  Was. Treue und Vollständigkeit gehen weiterhin vor — kein "
        "  natürlicher Klang erkauft sich Auslassungen oder erfundene "
        "  Verbindungen zwischen den Punkten.\n"
        "\n"
        "Form: deutscher Fließtext, keine Aufzählungszeichen, kein "
        "Markdown, keine Code-Begriffe wörtlich vorlesen (umschreiben "
        "oder weglassen), keine Anführungszeichen.\n"
        "\n"
        "Länge: Richtwert rund {max_chars} Zeichen, aber kein Limit. "
        "Vollständigkeit schlägt den Richtwert. Wenn das Original "
        "kürzer ist, ist das Ergebnis kürzer — niemals auffüllen.\n"
        "\n"
        "Antworte nur mit dem Vorlese-Text selbst."
    ),
    "en": (
        "You read Claude's reply aloud the way a human would tell it to "
        "another human. The listener only has your voice — screen, "
        "Markdown, bullets, code tokens are gone. You paraphrase the "
        "content; you invent nothing.\n"
        "\n"
        "FOCUS — what the listener wants to hear: the CONTENT of the "
        "answer, not a callback to the user's question. Describe what "
        "the answer says — what was achieved, what was found, what is "
        "now possible, what the options are — not what the user asked "
        "for.\n"
        "\n"
        "OUTPUT SHAPE (outcome-first — what changed for the listener "
        "leads):\n"
        "1. Lead sentence in the user mental model: what is now "
        "   possible, what changed for the listener, what they can do "
        "   that they couldn't before — e.g. 'The test passes now', "
        "   'The bug is gone', 'The pipeline is open', 'Login works "
        "   again'. NOT the code mental model like 'I edited X.py, "
        "   ran Y, fixed Z' — the listener needs the effect from "
        "   their angle, not the steps from yours. When the original "
        "   surfaces no listener-relevant effect (a pure research "
        "   reply, finding rather than change), open instead with "
        "   the core finding from the listener's perspective — 'the "
        "   file is at foo/bar', 'there are three paths …', 'the "
        "   answer is …', 'the cause was …'. Never open in 'I-"
        "   centric' shape; always from the listener's angle.\n"
        "2. Then the details and the mechanism — as part of the same "
        "   idea, not a flat enumeration. The listener should walk "
        "   away with concepts, methods, and a mental model, so "
        "   explain what something is and why it matters at its "
        "   core, instead of just chaining labels. When the original "
        "   has options, steps, or phases, capture the overarching "
        "   idea in your own words first and slot the items into it "
        "   — e.g. 'There are two paths …', 'The idea is …', 'At "
        "   the core …'. Completeness still holds absolutely: every "
        "   main point, every option, every list item, every closing "
        "   pick-one question must appear in substance — but wrapped "
        "   as an idea, not as a bare keyword list. Compact spots "
        "   in the original stay compact; nothing gets embellished. "
        "   Don't read code snippets or cryptic paths verbatim — "
        "   paraphrase them so the listener understands without a "
        "   screen.\n"
        "3. Optional: short next-step context for the listener — "
        "   which question is open, which choice they must make, "
        "   what comes next. Only if the original has such a "
        "   question or follow-up; otherwise drop it.\n"
        "\n"
        "UNDERSTANDABILITY (peer of faithfulness): the listener should "
        "walk away knowing WHY something is the way it is and HOW it "
        "ties together at the high level — not just that it is. When "
        "the original gives reasons, motives, effects, or links "
        "between points — including 'because', 'so that', 'in order "
        "to', Why / How-to-apply lines, before/after pairs — surface "
        "them and use them as the bridge between points. You may use "
        "common metaphors and images to make existing concepts "
        "concrete (e.g. 'the key now lives in one fixed spot', "
        "'a safety net underneath', 'like a switch that …'), as long "
        "as the metaphor only describes what the original already "
        "says. Translate technical labels into terms a person without "
        "the source code can grasp. A flat list of facts is not the "
        "goal — the listener should leave with a model, not a data "
        "sheet.\n"
        "\n"
        "FAITHFULNESS (top rule, beats all others): say only what the "
        "original actually says. Invent no new facts — no paths, no "
        "numbers, no architectural details, no code tokens not present "
        "in the original, no consequences the original doesn't itself "
        "voice. If the original mentions a point as a bare keyword, "
        "it stays a keyword — understandability does not buy room to "
        "explain where the original is silent. Metaphors are bridges "
        "to what is there, not doors to what isn't. When in doubt, "
        "drop rather than invent.\n"
        "\n"
        "COMPLETENESS (second rule): never drop a main point, never cut "
        "a list off mid-way, never deliver a preview snippet. If the "
        "original is a list, EVERY item appears. If it describes a plan "
        "in steps, all steps appear. If it has multiple phases, every "
        "phase appears.\n"
        "\n"
        "DETAIL DEPTH (derived from faithfulness): the depth per point "
        "follows the original, not a style target. If the original is "
        "compact, stay compact (a clause is enough). If it is detailed, "
        "carry over the present content. Never embellish to hit a "
        "target length.\n"
        "\n"
        "CHOICES ARE SACRED: when the answer offers the listener options "
        "— whether 'a, b, c', 'option A/B', 'tier 1/2/3', several "
        "suggestions, or a closing pick-one question — EVERY option must "
        "appear in the spoken text with its label AND the brief "
        "description the original gives, so the listener can decide "
        "without the screen. A closing question like 'which one do you "
        "want?' is kept verbatim.\n"
        "\n"
        "SPEAKING STYLE — important, the listener can't read the "
        "original and doesn't want to hear a recited list:\n"
        "• Sound like a human telling someone the answer out loud — "
        "  relaxed and natural in tone, but precise. No filler, no "
        "  small-talk padding, no telegram style.\n"
        "• Connect points with natural transitions — 'first', 'then', "
        "  'after that', 'in parallel', 'at the end', 'on top of that'. "
        "  Avoid recited enumerations like 'firstly …, secondly …, "
        "  thirdly …'; that sounds read-aloud.\n"
        "• Vary sentence length and word choice; don't repeat the same "
        "  filler back to back.\n"
        "• Structural markers from the original ('Point 1:', 'Layer A:', "
        "  'Pipeline step 5:', table headers) become natural prose, not "
        "  spoken verbatim.\n"
        "• Important: the style change touches only the how, never the "
        "  what. Faithfulness and completeness still rule — natural "
        "  flow does not buy omissions or invented bridges between "
        "  points.\n"
        "\n"
        "Form: English prose, no bullets, no markdown, do not speak code "
        "tokens literally (paraphrase or drop them), no quotes.\n"
        "\n"
        "Length: target around {max_chars} characters, no cap. "
        "Completeness beats the target. If the original is shorter, the "
        "output is shorter — never pad.\n"
        "\n"
        "Respond with only the spoken text."
    ),
}


# When the hook supplies the original user request, we ask Haiku to produce
# a two-part read-aloud: (1) a one-sentence rephrase of the task, (2) a
# completeness-preserving summary of the assistant answer. This makes the
# voice output unambiguous: the listener always hears WHICH question the
# answer belongs to before the answer itself.
SYSTEM_WITH_TASK = {
    "de": (
        "Du liest ein Claude-Frage-Antwort-Paar so vor, wie ein Mensch "
        "es einem anderen Menschen mündlich erzählen würde. Du "
        "paraphrasierst — du erfindest nichts dazu, du machst den "
        "Inhalt nicht klüger.\n"
        "\n"
        "Du bekommst zwei Eingabe-Blöcke, durch klare Marker getrennt:\n"
        "  [TASK] — die ursprüngliche Frage oder Anweisung des Nutzers.\n"
        "  [ANTWORT] — die Antwort von Claude darauf.\n"
        "\n"
        "WICHTIG — Worauf der Fokus liegt: Der Hörer will den INHALT "
        "der Antwort hören, nicht eine Erinnerung an seine eigene Frage. "
        "Der Aufgabenteil ist nur ein leiser Anker, damit klar ist, "
        "worauf die Antwort sich bezieht. Niemals den User-Wunsch "
        "ausführlich nacherzählen, niemals die Frage zum Hauptthema "
        "machen — das Gewicht liegt auf dem, was die Antwort sagt.\n"
        "\n"
        "AUFBAU der Ausgabe (Top-Down — erst grob, dann fein):\n"
        "1. Sehr kurzer Anker zur Aufgabe (höchstens ein Halbsatz, "
        "   maximal 10 Wörter). Kein voller 'Zu deiner Frage …'-Satz, "
        "   sondern eingebettet — z.B. 'Zur Frage nach den Insights "
        "   kurz: …', 'Bei dem Refactor: …', oder direkt ein Folgesatz "
        "   ohne 'Du'. Bei klar anschließenden Folge-Antworten ganz "
        "   weglassen. Auf keinen Fall einen starren 'Antwort:'-Marker "
        "   verwenden.\n"
        "2. Mentales Modell in einem Satz: was wurde erreicht, was "
        "   wurde gefunden, was ist jetzt möglich oder was ist die "
        "   Kernaussage — die Essenz der Antwort, sodass der Hörer "
        "   sofort den Kern hat.\n"
        "3. Danach die Details — aber als Teil derselben Idee, nicht "
        "   als nüchterne Aufzählung. Der Hörer soll Konzepte, "
        "   Methoden und mentale Modelle mitnehmen, also erkläre, was "
        "   etwas ist und worum es im Kern geht, statt nur Bezeichner "
        "   aneinanderzureihen. Hat das Original Optionen, Schritte "
        "   oder Phasen, formuliere zuerst die übergeordnete Idee in "
        "   eigenen Worten und ordne die einzelnen Punkte hinein — "
        "   z.B. 'Es gibt zwei Wege …', 'Die Idee dahinter ist …', "
        "   'Im Kern macht das …'. Vollständigkeit bleibt absolut: "
        "   jeder Hauptpunkt, jede Option, jedes Listenelement, jede "
        "   abschließende Auswahlfrage muss inhaltlich vorkommen — "
        "   aber als Idee verpackt, nicht als Stichwortliste. "
        "   Kompakte Originalstellen bleiben kompakt; ausgeschmückt "
        "   wird nichts. Codeschnipsel und kryptische Pfade nicht "
        "   wörtlich vorlesen, sondern in Worte fassen, sodass der "
        "   Hörer ohne Bildschirm versteht, worum es geht.\n"
        "4. Schließe mit dem Effekt für den Hörer: was ist jetzt "
        "   möglich, was hat sich geändert, was bedeutet das praktisch "
        "   — in einem kurzen Satz, sodass der Hörer das Modell "
        "   abschließend einordnen kann. Nur was im Original verankert "
        "   ist; wenn dort kein Effekt ausgesprochen wird, weglassen.\n"
        "\n"
        "VERSTÄNDLICHKEIT (gleichrangig zu Treue): Der Hörer soll am "
        "Ende verstehen, WARUM etwas so ist und WIE es im Großen wirkt "
        "— nicht nur, dass es so ist. Wenn der Antwort-Block "
        "Begründungen, Motive, Effekte oder Bezüge zwischen den "
        "Punkten nennt — auch in Form von 'weil', 'damit', 'sodass', "
        "Why-/How-to-apply-Zeilen, Vorher/Nachher-Paaren — hebe sie "
        "hervor und mache sie zur Brücke zwischen den Punkten. Du "
        "darfst gängige Metaphern und Bilder einsetzen, um vorhandene "
        "Konzepte greifbar zu machen (z.B. 'der Schlüssel liegt jetzt "
        "an einem festen Ort', 'das ist ein Sicherheitsnetz darunter', "
        "'wie ein Schalter, der …'), solange die Metapher nur das "
        "beschreibt, was im Antwort-Block steht. Übersetze technische "
        "Bezeichner in Begriffe, die jemand ohne Quellcode versteht. "
        "Eine reine Liste von Tatsachen ohne Kontext ist nicht das "
        "Ziel — der Hörer soll ein Modell mitnehmen, kein Datenblatt.\n"
        "\n"
        "TREUE-PRINZIP (oberste Regel für den Antwort-Teil, schlägt "
        "alle anderen): Sage ausschließlich, was im Antwort-Block "
        "tatsächlich steht. Erfinde keine neuen Fakten — keine Pfade, "
        "keine Zahlen, keine Architektur-Details, keine Code-Tokens, "
        "keine Mechanismen oder Konsequenzen, die der Antwort-Block "
        "nicht selbst aussagt. Verständlichkeit kauft sich keinen "
        "Erklärungsfreiraum, wo das Original schweigt; Metaphern sind "
        "Brücken zu Vorhandenem, nicht Türen zu Neuem. Im Zweifel "
        "weglassen statt erfinden.\n"
        "\n"
        "VOLLSTÄNDIGKEIT (zweite Regel): Niemals einen Hauptpunkt "
        "streichen, niemals eine Liste mittendrin abbrechen. Wenn die "
        "Antwort eine Liste ist, taucht JEDES Item im Vorlesetext auf. "
        "Hat sie Schritte oder Phasen, kommen alle vor. Die Tiefe pro "
        "Punkt folgt dem Original — kompakt bleibt kompakt.\n"
        "\n"
        "AUSWAHLMÖGLICHKEITEN sind heilig: Wenn die Antwort dem Hörer "
        "Optionen anbietet — egal ob als 'a, b, c', 'Variante 1, 2, 3', "
        "'Option A / B', 'Stufe 1 / 2 / 3', mehrere Vorschläge oder eine "
        "abschließende Auswahlfrage — muss JEDE Option mit Bezeichner "
        "UND der im Original genannten Kurzbeschreibung vorgelesen "
        "werden, sodass der Hörer ohne Bildschirm entscheiden kann. "
        "Eine abschließende Frage wie 'Welche Variante willst du?' wird "
        "wörtlich übernommen.\n"
        "\n"
        "SPRECHSTIL — wichtig, der Hörer kann nicht selbst lesen und "
        "will keine vorgelesene Liste hören:\n"
        "• Klinge wie ein Mensch, der die Antwort jemandem mündlich "
        "  erzählt. Lockerer, natürlicher Ton, präzise — kein "
        "  Telegrammstil, kein Padding.\n"
        "• Verbinde die Punkte mit natürlichen Übergängen — 'zuerst', "
        "  'danach', 'parallel dazu', 'am Ende', 'außerdem'. Vermeide "
        "  schematische 'Erstens …, zweitens …, drittens …'-Reihen, "
        "  das klingt vorgelesen.\n"
        "• Variiere Satzlänge und Wortwahl, wiederhole nicht dieselbe "
        "  Floskel direkt nacheinander.\n"
        "• Strukturwörter aus der Originalvorlage ('Punkt 1:', 'Layer "
        "  A:', 'Pipeline-Schritt 5:', Tabellen-Header) werden in "
        "  natürlichen Fließtext übersetzt, nicht wörtlich aufgesagt.\n"
        "• Wichtig: Der Stilwechsel ändert nur das Wie, niemals das "
        "  Was. Treue und Vollständigkeit gehen weiterhin vor.\n"
        "\n"
        "Form: deutscher Fließtext, keine Aufzählungszeichen, kein "
        "Markdown, keine Code-Begriffe wörtlich vorlesen, keine "
        "Anführungszeichen.\n"
        "\n"
        "Länge: Richtwert rund {max_chars} Zeichen für den Antwort-Teil. "
        "Vollständigkeit schlägt Länge. Wenn das Original kürzer ist, "
        "ist das Ergebnis kürzer — niemals auffüllen.\n"
        "\n"
        "Antworte nur mit dem Vorlese-Text selbst, ohne Marker, ohne "
        "Erklärung."
    ),
    "en": (
        "You read a Claude question/answer pair aloud the way a human "
        "would tell it to another human. You paraphrase the content; "
        "you invent nothing, you don't make it smarter.\n"
        "\n"
        "Input has two blocks separated by clear markers:\n"
        "  [TASK] — the user's original question or instruction.\n"
        "  [ANSWER] — Claude's reply to it.\n"
        "\n"
        "IMPORTANT — where the focus sits: the listener wants to hear "
        "the CONTENT of the answer, not a recap of their own question. "
        "The task part is only a quiet anchor so it's clear what the "
        "answer is about. Never retell the user's request in detail, "
        "never make the question the main subject — the weight is on "
        "what the answer says.\n"
        "\n"
        "OUTPUT SHAPE (top-down — broad first, fine-grained next):\n"
        "1. A very short anchor referencing the task (half a sentence "
        "   at most, 10 words tops). Not a full 'On your question …' "
        "   sentence; embed it instead — e.g. 'On the Insights "
        "   question, briefly: …', 'For the refactor: …', or just "
        "   continue with no second-person framing. For clear follow-up "
        "   answers, drop it entirely. Never use a rigid 'Answer:' label.\n"
        "2. Mental model in one sentence: what was achieved, what was "
        "   found, what is now possible, or what the core point is — "
        "   the essence of the answer, so the listener has the gist "
        "   immediately.\n"
        "3. Then the details — but as part of the same idea, not as "
        "   a flat enumeration. The listener should walk away with "
        "   concepts, methods, and a mental model, so explain what "
        "   something is and why it matters at its core, instead of "
        "   just chaining labels. When the original has options, "
        "   steps, or phases, capture the overarching idea in your "
        "   own words first and slot the items into it — e.g. 'There "
        "   are two paths …', 'The idea is …', 'At the core …'. "
        "   Completeness still holds absolutely: every main point, "
        "   every option, every list item, every closing pick-one "
        "   question must appear in substance — but wrapped as an "
        "   idea, not as a bare keyword list. Compact spots in the "
        "   original stay compact; nothing gets embellished. Don't "
        "   read code snippets or cryptic paths verbatim — paraphrase "
        "   them so the listener understands without a screen.\n"
        "4. Close with the effect for the listener: what is now "
        "   possible, what changed, what does this mean in practice — "
        "   in one short sentence, so the listener can place the "
        "   model. Only what the answer block actually surfaces; if "
        "   no effect is spelled out, drop the close.\n"
        "\n"
        "UNDERSTANDABILITY (peer of faithfulness): the listener should "
        "walk away knowing WHY something is the way it is and HOW it "
        "ties together at the high level — not just that it is. When "
        "the answer block gives reasons, motives, effects, or links "
        "between points — including 'because', 'so that', 'in order "
        "to', Why / How-to-apply lines, before/after pairs — surface "
        "them and use them as the bridge between points. You may use "
        "common metaphors and images to make existing concepts "
        "concrete (e.g. 'the key now lives in one fixed spot', "
        "'a safety net underneath', 'like a switch that …'), as long "
        "as the metaphor only describes what the answer block already "
        "says. Translate technical labels into terms a person without "
        "the source code can grasp. A flat list of facts is not the "
        "goal — the listener should leave with a model, not a data "
        "sheet.\n"
        "\n"
        "FAITHFULNESS (top rule for the answer part, beats all others): "
        "say only what the answer block actually says. Invent no new "
        "facts — no paths, no numbers, no architectural details, no "
        "code tokens, no consequences the answer block doesn't itself "
        "voice. Understandability does not buy room to explain where "
        "the original is silent; metaphors are bridges to what is "
        "there, not doors to what isn't. When in doubt, drop rather "
        "than invent.\n"
        "\n"
        "COMPLETENESS (second rule): never drop a main point, never cut "
        "a list off mid-way. If the answer is a list, EVERY item "
        "appears. If it has steps or phases, all of them appear. The "
        "depth per point follows the original — compact stays compact.\n"
        "\n"
        "CHOICES ARE SACRED: when the answer offers the listener options "
        "— whether 'a, b, c', 'option A/B', 'tier 1/2/3', several "
        "suggestions, or a closing pick-one question — EVERY option must "
        "be spoken with its label AND the brief description the original "
        "gives, so the listener can decide without the screen. A closing "
        "question like 'which one do you want?' is kept verbatim.\n"
        "\n"
        "SPEAKING STYLE — important, the listener can't read along and "
        "doesn't want to hear a recited list:\n"
        "• Sound like a human telling someone the answer out loud — "
        "  relaxed, natural, precise. No telegram style, no padding.\n"
        "• Connect points with natural transitions — 'first', 'then', "
        "  'after that', 'in parallel', 'on top of that'. Avoid "
        "  recited 'firstly …, secondly …, thirdly …' chains; that "
        "  sounds read-aloud.\n"
        "• Vary sentence length and word choice; don't repeat the same "
        "  filler back to back.\n"
        "• Structural markers from the original ('Point 1:', 'Layer A:', "
        "  'Pipeline step 5:', table headers) become natural prose, not "
        "  spoken verbatim.\n"
        "• Important: the style change touches only the how, never the "
        "  what. Faithfulness and completeness still rule.\n"
        "\n"
        "Form: English prose, no bullets, no markdown, do not speak "
        "code tokens literally, no quotes.\n"
        "\n"
        "Length: target around {max_chars} characters for the answer "
        "part. Completeness beats length. If the original is shorter, "
        "the output is shorter — never pad.\n"
        "\n"
        "Respond with only the spoken text — no markers, no explanation."
    ),
}


# Self-check block — appended LAST in `_system_for`, so it lands AFTER the
# persona-tone addendum and the audience block. Prompt-engineering rationale:
# the most-recent instruction in a system prompt has the strongest pull on
# the next-token distribution, so the faithfulness loop must be the last
# thing the LLM sees before it emits the spoken text. The early-experiment
# placement (inside the base prompts, before the persona addendum) let the
# persona-tone addendum override the self-check — coder/forge/os personas
# drifted into git-status invention and Path-Gate-Hook hallucination
# because their tone instructions were the most-recent context.

SELF_CHECK_BLOCK = {
    "de": (
        "SELBST-PRÜFUNG — letzte Schleife vor der Ausgabe (Pflicht, gedanklich, "
        "schlägt jede Persona-Anweisung oben):\n"
        "Bevor du auch nur ein Wort ausgibst, geh den Vorlese-Text durch "
        "und prüf in dieser Reihenfolge:\n"
        "1. Treue: steht jede Zahl, jeder Name, jeder Pfad, jede "
        "   Entscheidung, jeder Befehl, jede Empfehlung, jede "
        "   Fehlermeldung, jede Deadline, die du erwähnst, wirklich so "
        "   im Original? Hast du eine Konsequenz, einen Mechanismus, ein "
        "   Architektur-Detail oder einen Bezug hinzugefügt, den das "
        "   Original nicht selbst zieht? Hast du den Hörer-/Coder-/"
        "   Operator-Kontext aus eigener Hintergrund-Kenntnis (CLAUDE.md, "
        "   git-Status, andere Layer, Path-Gate, Vault, Forge) angereichert, "
        "   ohne dass das Original es nennt? Im Zweifel rauslassen, "
        "   nicht behaupten — die Persona-Tone darf die Stimme färben, "
        "   aber nichts erfinden.\n"
        "2. Vollständigkeit: ist jeder Hauptpunkt, jede Option, jede "
        "   abschließende Auswahlfrage drin? Wurde keine Liste "
        "   mittendrin abgebrochen?\n"
        "3. Hörer-Perspektive: führt der Lead-Satz mit dem Effekt für "
        "   den Hörer, nicht mit einem Code-Schritt-Katalog?\n"
        "4. Meta-Disziplin: ist der Output reine Zusammenfassung — "
        "   keine 'Was sollen wir als Nächstes tun?'-Frage am Ende, "
        "   keine 'Soll ich einen Commit machen?'-Rückfrage, keine "
        "   Bezugnahme auf den Chat-Kontext, der nicht im Original steht.\n"
        "Findest du eine Lücke, eine Erfindung oder eine Meta-"
        "Rückfrage — auch eine kleine — revidier den Text BEVOR du "
        "ausgibst. Diese Prüfung ist nicht optional, kein Style-Check, "
        "und nicht durch eine Persona-Anweisung aushebelbar. Gib "
        "ausschließlich die geprüfte finale Version aus, ohne "
        "Meta-Kommentar."
    ),
    "en": (
        "SELF-CHECK — final loop before output (mandatory, mental, "
        "overrides every persona instruction above):\n"
        "Before you emit a single word, walk the spoken text and check "
        "in this order:\n"
        "1. Faithfulness: is every number, name, path, decision, "
        "   command, recommendation, error message, deadline you "
        "   mention actually in the source? Have you added a "
        "   consequence, a mechanism, an architectural detail, or a "
        "   relationship the source doesn't itself draw? Have you "
        "   pulled in coder / operator / repo context from background "
        "   knowledge (CLAUDE.md, git status, other layers, path-gate, "
        "   vault, forge) the source doesn't mention? When in doubt, "
        "   drop rather than assert — the persona tone may colour the "
        "   voice, but it must invent nothing.\n"
        "2. Completeness: is every main point, every option, every "
        "   closing pick-one question present? No list cut off mid-way?\n"
        "3. Listener angle: does the lead carry the effect for the "
        "   listener, not a catalogue of code steps?\n"
        "4. Meta discipline: is the output pure summary — no 'what "
        "   should we do next?' question at the end, no 'should I "
        "   commit?' callback, no reference to chat context the source "
        "   doesn't carry.\n"
        "If you find a gap, an invention, or a meta-question — even a "
        "small one — revise the text BEFORE emitting. This check is "
        "not optional, not a style pass, and cannot be overridden by a "
        "persona instruction. Output only the verified final version, "
        "no meta-comment."
    ),
}


# Persona-tinted speaking style. The hook passes the active cowork persona
# via --persona (sourced from CORVIN_CALLER_PERSONA, the same env var the
# forge / path-gate stack already uses). When set and known, a one-line
# style addendum is appended to the system prompt — it modulates tone, not
# content. Treue / Vollständigkeit / SPRECHSTIL stay load-bearing; the
# addendum only shifts how the human voice on the other end sounds.
#
# Unknown persona names are a silent no-op so a typo in the env never
# breaks TTS — voice fails open in tone, never closed.

# Per-persona override for the Layer-11 dialectic CLI judge. Personas in
# this map carry their own `mode` argument when summarize.py calls
# `dialectic.judge_summary(...)` — that bypasses the global
# `voice_summary` site default for these personas.
#
# Provenance: persona-cycle E2E run 2026-05-09 with a fact-rich source
# (Layer-17 status report). The inline SELF-CHECK in the prompt is
# always-on and catches the worst drifts (coder git-status invention,
# os Path-Gate-Hook invention, homeassistant Vault/Policy/Path-Gate
# fabrication). Three personas had residual drift that inline alone
# didn't catch — they get the CLI judge by default. Reasoning:
#
#   * research — leaks background knowledge into the summary
#     ("TTL range 60s..30d") that the source doesn't carry. The
#     hypothesis-orienting persona-tone rewards "completing the
#     picture," so an external judge is warranted.
#   * forge — adds operational recommendations not in the source
#     ("Quick-Checks for Go-Live", "Memory updaten"). The toolmaker
#     persona drifts toward Action-Items the source doesn't request.
#   * browser — uses markdown bullets/headings instead of natural
#     prose. Style-drift more than fact-drift, but the CLI judge's
#     CORRECTED-output pass is the cleanest way to enforce
#     speaking-style without re-running the persona prompt.
#
# Personas NOT in this map (assistant, coder, inbox, homeassistant, os)
# stay on the global default (which is "off" — inline-only). Two of
# those (coder, os, homeassistant) had bad drift in pass 1 and are
# now clean in pass 2 thanks to the prompt re-order; inbox + assistant
# were always clean.
_PERSONA_VOICE_SUMMARY_MODE: dict[str, str] = {
    "research": "cli",
    "forge":    "cli",
}


PERSONA_STYLE = {
    "de": {
        "coder": (
            "Persona-Stil: technisch-präzise, knapper Ton, wie ein Senior-"
            "Engineer, der dir den Diff mündlich erklärt — natürlich, "
            "aber ohne Smalltalk-Padding."
        ),
        "research": (
            "Persona-Stil: nachdenklich, hypothesen-orientiert, gibt "
            "Nuancen Raum — wie jemand, der nach einer Recherche das "
            "Material gerade ordnet, statt fertige Schlüsse zu bellen."
        ),
        "inbox": (
            "Persona-Stil: triagierend, geschäftsmäßig, kurz — wie eine "
            "Assistenz, die den Posteingang sichtet und sagt, was heute "
            "reagiert werden muss und was warten kann."
        ),
        "forge": (
            "Persona-Stil: ingenieurhaft-trocken, mechanisch-klar — wie "
            "ein Werkzeugbauer, der erklärt, was er gerade in den "
            "Sandkasten gelegt hat und wofür es gut ist."
        ),
        "skill-forge": (
            "Persona-Stil: didaktisch, einführend — wie jemand, der ein "
            "neues Konzept vorstellt und es für den Hörer greifbar macht."
        ),
        "homeassistant": (
            "Persona-Stil: ruhig, knapp, bestätigend — wie ein Smart-Home, "
            "das den aktuellen Zustand meldet und Quittungen gibt."
        ),
        "assistant": "",  # neutral baseline — no override
    },
    "en": {
        "coder": (
            "Persona style: technically precise, terse — like a senior "
            "engineer walking you through the diff out loud. Natural, no "
            "small-talk padding."
        ),
        "research": (
            "Persona style: thoughtful, hypothesis-shaped, gives nuance "
            "room — like someone organising material after a research "
            "pass instead of barking finished conclusions."
        ),
        "inbox": (
            "Persona style: triaging, businesslike, short — like an "
            "assistant scanning the inbox, flagging what needs a reply "
            "today vs. what can wait."
        ),
        "forge": (
            "Persona style: engineer-dry, mechanically clear — like a "
            "toolmaker explaining what they just put on the bench and "
            "what it is for."
        ),
        "skill-forge": (
            "Persona style: didactic, introductory — like someone "
            "unveiling a new concept and making it tangible for the "
            "listener."
        ),
        "homeassistant": (
            "Persona style: calm, terse, confirming — like a smart-home "
            "reporting current state and giving acknowledgements."
        ),
        "assistant": "",
    },
}


def _persona_addendum(lang: str, persona: str) -> str:
    """One-line style addendum for the active cowork persona.

    Returns "" when persona is empty, unknown, or explicitly the neutral
    `assistant` baseline. Tone-modulating only — never overrides content
    rules.
    """
    if not persona:
        return ""
    table = PERSONA_STYLE.get(lang) or PERSONA_STYLE["en"]
    return table.get(persona.strip().lower(), "")


# Adaptive target sizing. The hook passes a hint via --max-chars (the user's
# config value, treated as a soft floor); the per-input target is the larger
# of the hint and 85 % of the input length so every point fits without
# inflating slack the LLM might fill with invented content.


def adaptive_target(text: str, hint: int) -> int:
    """Compute a soft length hint for the summarizer.

    No hard cap — completeness wins. The hint scales with input size so
    every point fits, but we deliberately do NOT inflate per-item space:
    extra slack invites the LLM to fill it with invented mechanism /
    rationale, and the new system prompt's faithfulness rule forbids
    that. The user's config value is a floor; the input-derived target
    can push it up but stays close to original length.

    Tuning rationale: 0.85 of input length is enough to paraphrase
    every option/choice without padding. List-item count is no longer a
    multiplier — items are verbalised at the original's depth.
    """
    return max(hint, int(len(text) * 0.85))


# Match a list item: line starts with optional bold + (number. | bullet),
# followed by the item content up to the next item or blank line.
ITEM_RE = re.compile(
    r"(?:^|\n)\s*(?:\*{0,2})(?:\d+\.|[-*+])\s+(.+?)"
    r"(?=\n\s*(?:\*{0,2})(?:\d+\.|[-*+])\s|\n\s*\n|\Z)",
    re.DOTALL,
)


def _first_clause(s: str, max_chars: int = 90) -> str:
    s = re.sub(r"\*+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    m = re.search(r"^(.{15,%d}?[.!?])(?:\s|$)" % max_chars, s)
    if m:
        return m.group(1).strip()
    if len(s) <= max_chars:
        return s
    return s[:max_chars].rsplit(" ", 1)[0] + "…"


def naive_truncate(text: str, max_chars: int) -> str:
    """Structural compress without dropping content.

    Fallback path when no LLM backend is reachable. Two cases:
      - List with 2+ items: intro, every item, AND any outro after the
        last item are kept. The outro is critical because closing pick-one
        questions ("Welche Variante willst du?") often live there — the
        system prompt rule "choices are sacred" must hold in this fallback
        too.
      - Plain prose: whitespace normalized, returned in full. We deliberately
        do NOT byte-truncate — completeness over length.

    Name kept for backward-compat; semantics changed in 2026-05.
    """
    items = ITEM_RE.findall(text)
    if len(items) >= 2:
        intro_match = ITEM_RE.search(text)
        intro = re.sub(r"\s+", " ", text[: intro_match.start()]).strip() if intro_match else ""
        intro = re.sub(r"[*#>`]+", "", intro).strip(" :—-")

        # Outro = everything after the last list-item match. Often holds the
        # closing question or the recommendation summary.
        last_item = list(ITEM_RE.finditer(text))[-1]
        outro_raw = text[last_item.end():]
        outro = re.sub(r"[*#>`]+", "", outro_raw)
        outro = re.sub(r"\s+", " ", outro).strip(" :—-")

        clauses = [_first_clause(it, max_chars=350) for it in items]
        parts = []
        if intro and len(intro) <= 300:
            parts.append(intro.rstrip(":.") + ":")
        parts.extend(clauses)
        if outro:
            parts.append(outro)
        return " ".join(p.rstrip(".") + "." for p in parts if p)

    return re.sub(r"\s+", " ", text).strip()


def _build_input(text: str, task: str, lang: str) -> str:
    """Wrap task + answer in markers the SYSTEM_WITH_TASK prompt expects."""
    answer_label = "ANTWORT" if lang == "de" else "ANSWER"
    return f"[TASK]\n{task.strip()}\n\n[{answer_label}]\n{text}"


def _resolve_base_lang(output_language: str) -> str:
    """Pick the base prompt (`de` or `en`) for the given BCP-47 output code.

    `de` → German base prompt (no extra directive).
    `en` → English base prompt (no extra directive).
    anything else → English base prompt + `OUTPUT LANGUAGE` directive
    appended last; English is the most LLM-stable pivot prompt for any
    target language.
    """
    if not output_language:
        return ""  # caller falls back to the legacy `--lang` argument
    if _i18n is None:
        return ""
    code = _i18n.normalise(output_language)
    if code in ("de", "en"):
        return code
    return "en"  # English pivot for every other language


def _system_for(lang: str, target_chars: int, has_task: bool,
                persona: str = "", audience: str = "",
                output_language: str = "") -> str:
    """Compose the summarizer's system prompt.

    Layer order — base prompt → persona-tone (the *speaker*) → audience
    (the *listener*, layer 12) → SELF-CHECK (the *truthfulness loop*,
    layer 11 inline integration) → OUTPUT LANGUAGE directive (i18n,
    only when the requested locale is neither `de` nor `en`). Each
    addendum is a pure tone / pin modulator; the base prompt's
    faithfulness / completeness rules stay load-bearing regardless of
    what any later block requests.

    The SELF-CHECK block lands BEFORE the language directive so it is
    the most-recent CONTENT-rules instruction; the language directive
    is appended structurally LAST so it pins output-language without
    weakening any content rule. Order chosen empirically: putting the
    language pin first lets the source-text language re-bias the LLM
    away from the target locale; putting it last makes it the closing
    instruction the model honours.

    `output_language` is an optional BCP-47 code. When empty (legacy
    callers) or equal to `de`/`en` the prompt is byte-identical to the
    pre-i18n version.
    """
    table = SYSTEM_WITH_TASK if has_task else SYSTEM
    base = table[lang].format(max_chars=target_chars)
    addendum = _persona_addendum(lang, persona)
    if addendum:
        base = base + "\n\n" + addendum
    if audience:
        base = base + "\n\n" + audience.strip()
    # Self-check is appended unconditionally — always-on by structure.
    # The op-in CLI judge in dialectic.judge_summary() runs in addition
    # for personas that need second-model verification; this inline
    # check is the always-active first line of defence.
    base = base + "\n\n" + SELF_CHECK_BLOCK[lang]
    # Output-language pin (i18n). Only emitted for non-de/non-en codes —
    # the de/en prompts already steer their own output language via
    # native examples, so an extra directive is just token cost. We
    # SANDWICH the directive: once at the very front, once at the very
    # end. The empirical motivation (test_i18n_live.py 2026-05): a
    # user-global "always reply in <X>" rule loaded from the host's
    # CLAUDE.md is a system-level peer to our prompt, and a single
    # end-pin cannot beat it consistently. Front-loading frames the
    # whole turn as a translated TTS output; back-pinning is the last
    # instruction the model sees before generating. Both fire so the
    # combined salience overrides the host-level chat-language rule.
    if output_language and _i18n is not None:
        code = _i18n.normalise(output_language)
        if code and code not in ("de", "en"):
            directive = _i18n.language_directive(code, audience="voice")
            base = directive + "\n\n" + base + "\n\n" + directive
    return base


def _claude_authenticated() -> bool:
    """Cheap, subprocess-free Claude Code auth probe — mirrors
    chat_runtime.py::_claude_authenticated() (the H4 fix, 0.10.25) so the
    voice summarizer gets the same fast-fail as the main chat engine. Without
    this, a fresh install with the `claude` CLI on PATH but not yet logged in
    (via `claude login`) burns the full 90s CLI timeout on EVERY summarize
    call before falling through to Hermes — on the short-text fast path this
    also silently kills the LERN-ZUGABE/METAPHER annex (its own failure mode
    is "return text verbatim"), so the very first replies read back near-raw
    instead of humanized. Authenticated iff an OAuth session exists in
    ~/.claude/.credentials.json OR ANTHROPIC_API_KEY is set. Fail-OPEN
    (True) on an unexpected read error so a transient glitch never reroutes
    a genuinely-logged-in user off Claude.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    try:
        creds_path = Path.home() / ".claude" / ".credentials.json"
        if not creds_path.exists():
            return False
        import json as _json
        creds = _json.loads(creds_path.read_text(encoding="utf-8"))
        return bool(creds.get("claudeAiOauth") or creds.get("accessToken"))
    except Exception:  # noqa: BLE001
        return True  # fail-open: don't reroute a possibly-authenticated user


def _summarize_via_cli(text: str, task: str, lang: str, target_chars: int, model: str, persona: str = "", audience: str = "", output_language: str = "") -> str | None:
    """Backend 1: the local `claude` CLI (uses OAuth from Claude Max — no key).

    Sets VOICE_HOOK_RECURSION=1 so the CLI's own stop-hook does not re-trigger
    this summarizer on its own output.
    """
    if not shutil.which("claude") or not _claude_authenticated():
        return None
    has_task = bool(task.strip())
    system_prompt = _system_for(lang, target_chars, has_task, persona, audience, output_language)
    payload = _build_input(text, task, lang) if has_task else text
    env = os.environ.copy()
    env["VOICE_HOOK_RECURSION"] = "1"
    try:
        out = subprocess.run(
            [
                "claude", "-p", payload,
                "--append-system-prompt", system_prompt,
                "--model", model,
                "--disallowedTools", "*",
            ],
            capture_output=True, text=True, env=env,
            timeout=_SUMMARY_CLI_TIMEOUT_S, check=True,
        )
        return out.stdout.strip() or None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"[summarize] CLI call failed: {exc}", file=sys.stderr)
        return None


def _resolve_hermes_model_for_summary() -> str:
    """Model tag for the Hermes summarize backend — the SAME resolution the
    running Hermes engine uses (CORVIN_HERMES_MODEL → installed qwen3 tag →
    built-in default), so no extra Ollama model is needed."""
    try:
        from agents.hermes_engine import _resolve_default_model  # type: ignore
        return _resolve_default_model()
    except Exception:  # noqa: BLE001
        return os.environ.get("CORVIN_HERMES_MODEL", "").strip() or "qwen3:8b"


def _summarize_via_hermes(text: str, task: str, lang: str, target_chars: int, model: str, persona: str = "", audience: str = "", output_language: str = "") -> str | None:
    """Backend 2: the local Hermes engine (Ollama). This is the DEFAULT zero-config
    engine, so without it a Hermes-only install had no LLM summarizer at all and
    every long voice reply fell through to naive_truncate — spoken answers cut off
    mid-thought on exactly the shipped default. Uses the same system prompt as the
    CLI backend; POSTs to Ollama /api/generate (non-streaming) with a bounded
    timeout. Returns None (→ structural fallback) on any error / when Ollama is
    unreachable, so this never makes things worse than before."""
    import json as _json
    import urllib.request as _ur

    base_url = ""
    for env_key in ("CORVIN_OLLAMA_BASE_URL", "OLLAMA_HOST", "CORVIN_HERMES_URL"):
        v = os.environ.get(env_key, "").strip()
        if v:
            base_url = v.rstrip("/")
            break
    if not base_url:
        base_url = "http://localhost:11434"

    has_task = bool(task.strip())
    system_prompt = _system_for(lang, target_chars, has_task, persona, audience, output_language)
    user_input = _build_input(text, task, lang) if has_task else text
    hermes_model = _resolve_hermes_model_for_summary()

    payload = _json.dumps({
        "model": hermes_model,
        "system": system_prompt,
        "prompt": user_input,
        "stream": False,
        # Disable qwen3-style reasoning: a thinking model would spend the entire
        # latency budget emitting <think>…</think> tokens BEFORE the summary and
        # blow the timeout — on a fresh install (cold Ollama) this made the
        # summary silently fall back to the verbatim (un-summarized) text. We
        # already strip any <think> below; NOT generating it is what keeps the
        # call inside budget. Ignored by non-thinking models. (Verified: qwen3:8b
        # dropped from >60s timeout to ~10s and produced a real summary.)
        "think": False,
        # Voice summaries must be concise + deterministic — low temperature keeps
        # the model from padding the spoken reply.
        "options": {"temperature": 0.2},
    }).encode("utf-8")
    try:
        req = _ur.Request(
            f"{base_url}/api/generate", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        # CPU Hermes is slow; this budget keeps the spoken-reply latency bounded
        # while still allowing a real summary on modest hardware. On timeout →
        # None → structural fallback (never blocks the voice pipeline forever).
        # Sized so CLI + Hermes fit inside the adapter's parent cap (VOICE-F7).
        with _ur.urlopen(req, timeout=_SUMMARY_HERMES_TIMEOUT_S) as resp:
            if not (200 <= resp.getcode() < 300):
                return None
            data = _json.loads(resp.read().decode("utf-8"))
        out = str(data.get("response", "")).strip()
        # qwen3 emits <think>…</think> reasoning before the answer — strip it so
        # the internal monologue is never spoken aloud.
        out = re.sub(r"(?is)<think>.*?</think>", "", out).strip()
        return out or None
    except Exception as exc:  # noqa: BLE001
        print(f"[summarize] Hermes call failed: {exc}", file=sys.stderr)
        return None


# Fallback prefix when no LLM backend is available but a task is supplied.
# Trim to ~120 chars so the prefix stays a "reminder" and doesn't overshadow
# the answer.
def _task_prefix(task: str, lang: str, max_chars: int = 120) -> str:
    t = re.sub(r"\s+", " ", task).strip()
    if not t:
        return ""
    if len(t) > max_chars:
        t = t[:max_chars].rsplit(" ", 1)[0] + "…"
    if lang == "de":
        return f"Zu deiner Frage: {t} — "
    return f"On your question: {t} — "


# ──────────────────────────────────────────────────────────────────────────
# Appendix mode — Layer-28-adjacent fix for the LERN-ZUGABE bypass
# ──────────────────────────────────────────────────────────────────────────
#
# Two voice-pipeline branches structurally bypass --audience and therefore
# never carry the LERN-ZUGABE annex into the spoken output:
#
#   1. adapter.build_voice_summary returns verbatim when the LLM authored
#      a `<voice>...</voice>` override block (intentional: no double-pass).
#   2. The same function returns verbatim when len(text) <= max_chars
#      (intentional: short replies don't need a summarizer call).
#
# Both branches respect the faithfulness invariant — the input must reach
# the listener byte-identical. Appendix mode preserves that invariant AND
# delivers the LERN-ZUGABE: it echoes the input AS-IS and asks the LLM to
# author ONLY the teaching annex as a separate generation, then string-
# concats input + " " + appendix. The input is never paraphrased.
#
# When the LLM call fails (no claude CLI, no API key, timeout, unparseable
# response), the function falls back to returning the input verbatim — the
# listener gets the original voice content, just without the teaching
# annex. Silence is not a failure mode.

_APPENDIX_SYSTEM_DE = (
    "Du bist ein Lehr-Anhang-Generator für Sprachausgabe (TTS). "
    "Du bekommst einen FERTIGEN Voice-Output-Text als Input. Dieser Text "
    "wird BEREITS vorgelesen — du sollst ihn weder echoen noch verändern.\n"
    "\n"
    "DEINE EINZIGE AUFGABE: Schreibe AUSSCHLIESSLICH eine kurze Lehr-"
    "Ergänzung (Lern-Zugabe), die anschließend an den Input vorgelesen "
    "wird. Sie MUSS mit einem dieser beiden Marker beginnen:\n"
    "  - \"Und zur Einordnung,\"\n"
    "  - \"Wissenswert dazu,\"\n"
    "\n"
    "INHALT: Führe das wichtigste zugrundeliegende Konzept aus dem Input "
    "in einem oder zwei Sätzen ein, und schließe mit einem Recap-Satz ab, "
    "der die neue Vokabel verankert. Insgesamt zwei bis drei Sätze, nicht "
    "mehr.\n"
    "\n"
    "REGELN — load-bearing:\n"
    "  - Antworte NUR mit der Lehr-Ergänzung — kein Echo, kein Vorspann.\n"
    "  - Beginne IMMER mit einem der beiden Marker oben.\n"
    "  - Nichts erfinden. Nur was der Input strukturell trägt.\n"
    "  - Spreche-sprache — keine Code-Tokens, keine Markdown-Tokens.\n"
    "  - Wenn der Input KEIN belastbares Konzept enthält (z.B. nur eine "
    "Begrüßung), antworte mit dem leeren String — keine erzwungene "
    "Belehrung."
)

_APPENDIX_SYSTEM_EN = (
    "You are a teaching-appendix generator for spoken output (TTS). "
    "You receive a FINISHED voice-output text as input. That text is "
    "ALREADY being read aloud — do not echo or modify it.\n"
    "\n"
    "YOUR ONLY TASK: write a short teaching annex that will be read "
    "AFTER the input. It MUST start with one of these markers:\n"
    "  - \"For context,\"\n"
    "  - \"Worth knowing,\"\n"
    "\n"
    "CONTENT: introduce the most important underlying concept from the "
    "input in one or two sentences, then close with a recap sentence "
    "that anchors the new vocabulary. Two to three sentences total.\n"
    "\n"
    "RULES — load-bearing:\n"
    "  - Reply with the annex ONLY — no echo, no preamble.\n"
    "  - Always start with one of the markers above.\n"
    "  - Invent nothing. Only what the input structurally implies.\n"
    "  - Spoken language only — no code tokens, no markdown.\n"
    "  - If the input carries no real concept (e.g. just a greeting), "
    "reply with the empty string — never force a lesson."
)


def _ollama_generate(system_prompt: str, user_input: str, timeout: int = _ANNEX_HERMES_TIMEOUT_S) -> str | None:
    """Shared low-level Hermes (Ollama /api/generate) call for the appendix
    and metapher backends — same base-url resolution and <think> stripping
    as _summarize_via_hermes, factored out so both annex generators can fall
    back to the zero-config default engine instead of having no fallback at
    all (their previous CLI-only shape meant a Hermes-only install, with no
    Claude CLI login ever, could never produce a LERN-ZUGABE/METAPHER
    annex). Returns None on any error (→ caller's existing silent-fail path,
    never worse than before)."""
    import json as _json
    import urllib.request as _ur

    base_url = ""
    for env_key in ("CORVIN_OLLAMA_BASE_URL", "OLLAMA_HOST", "CORVIN_HERMES_URL"):
        v = os.environ.get(env_key, "").strip()
        if v:
            base_url = v.rstrip("/")
            break
    if not base_url:
        base_url = "http://localhost:11434"

    payload = _json.dumps({
        "model": _resolve_hermes_model_for_summary(),
        "system": system_prompt,
        "prompt": user_input,
        "stream": False,
        # Disable qwen3-style reasoning so the annex (LERN-ZUGABE / METAPHER) is
        # emitted DIRECTLY instead of after a <think> block that eats the whole
        # 30s timeout — on a fresh install (cold Ollama) the annex silently
        # vanished (marker never produced in time → verbatim fallback). Ignored
        # by non-thinking models. (Verified: qwen3:8b dropped >30s→~10s and
        # produced the "Und zur Einordnung," marker.)
        "think": False,
        "options": {"temperature": 0.4},
    }).encode("utf-8")
    try:
        req = _ur.Request(
            f"{base_url}/api/generate", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with _ur.urlopen(req, timeout=timeout) as resp:
            if not (200 <= resp.getcode() < 300):
                return None
            data = _json.loads(resp.read().decode("utf-8"))
        out = str(data.get("response", "")).strip()
        out = re.sub(r"(?is)<think>.*?</think>", "", out).strip()
        return out or None
    except Exception:  # noqa: BLE001
        return None


def _appendix_via_cli(text: str, lang: str, model: str) -> str | None:
    """Run claude -p with the appendix-only system prompt."""
    if not shutil.which("claude") or not _claude_authenticated():
        return None
    sys_prompt = _APPENDIX_SYSTEM_EN if lang == "en" else _APPENDIX_SYSTEM_DE
    env = os.environ.copy()
    env["VOICE_HOOK_RECURSION"] = "1"
    try:
        out = subprocess.run(
            [
                "claude", "-p", text,
                "--append-system-prompt", sys_prompt,
                "--model", model,
                "--disallowedTools", "*",
            ],
            capture_output=True, text=True, env=env,
            timeout=_ANNEX_CLI_TIMEOUT_S, check=True,
        )
        return out.stdout.strip() or None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"[summarize] appendix CLI call failed: {exc}", file=sys.stderr)
        return None


def _appendix_via_hermes(text: str, lang: str) -> str | None:
    """Backend 2 for the LERN-ZUGABE annex — local Hermes (Ollama), tried
    when the CLI is unavailable/unauthenticated. See _ollama_generate."""
    sys_prompt = _APPENDIX_SYSTEM_EN if lang == "en" else _APPENDIX_SYSTEM_DE
    return _ollama_generate(sys_prompt, text)




# Curated markers we accept as evidence that the appendix is well-formed.
_APPENDIX_MARKERS = (
    "Und zur Einordnung,", "Wissenswert dazu,",
    "For context,", "Worth knowing,",
)


def _extract_appendix(raw: str) -> str:
    """Pluck a well-formed appendix from *raw*.

    The LLM is instructed to start with a marker; we strip everything
    before the FIRST marker we find. If no marker appears, the output
    is rejected (return "") and the caller falls back to verbatim
    input. Returns the appendix WITHOUT a leading space.
    """
    if not raw:
        return ""
    for marker in _APPENDIX_MARKERS:
        idx = raw.find(marker)
        if idx >= 0:
            return raw[idx:].strip()
    return ""


def generate_appendix(text: str, lang: str = "de",
                      model: str = "claude-haiku-4-5-20251001") -> str:
    """Return the teaching appendix for *text*, or "" on any failure.

    Uses the `claude` CLI (Max-subscription OAuth) with a short timeout (45 s)
    to keep voice latency
    bounded. Caller responsibilities:
      - Concat with the input (typically as ``input + " " + appendix``)
      - Return verbatim input if the appendix is empty
    """
    if not text or not text.strip():
        return ""
    raw = _appendix_via_cli(text, lang, model)
    if raw is None:
        raw = _appendix_via_hermes(text, lang)
    if raw is None:
        return ""
    return _extract_appendix(raw)


def summarize_with_appendix(text: str, lang: str = "de",
                            model: str = "claude-haiku-4-5") -> str:
    """Echo *text* verbatim plus a generated teaching appendix.

    Used by adapter.build_voice_summary in two paths that otherwise
    bypass --audience: the `<voice>`-override path and the
    short-text direct path. Faithfulness invariant: *text* itself is
    never paraphrased — only suffixed.
    """
    text = (text or "").strip()
    if not text:
        return ""
    appendix = generate_appendix(text, lang=lang, model=model)
    if not appendix:
        return text  # silent fail — listener still gets the original
    return f"{text} {appendix}"


# ─── Metapher-Zugabe (Layer-12 voice_audience_metaphors) ─────────────────────

_METAPHER_SYSTEM_DE = (
    "Du bist ein Metapher-Generator für Sprachausgabe (TTS). "
    "Du bekommst einen FERTIGEN Voice-Output-Text als Input. Dieser Text "
    "wird BEREITS vorgelesen — du sollst ihn weder echoen noch verändern.\n"
    "\n"
    "DEINE EINZIGE AUFGABE: Schreibe AUSSCHLIESSLICH ein bis zwei Sätze "
    "als Metapher oder Analogie, die das Kernthema des Inputs auf etwas "
    "aus dem Alltag übertragen. Diese Sätze werden anschließend an den "
    "Input vorgelesen.\n"
    "\n"
    "Die Sätze MÜSSEN mit einem dieser Marker beginnen:\n"
    "  - \"Als Bild gesprochen,\"\n"
    "  - \"Bildlich gesprochen,\"\n"
    "\n"
    "REGELN — load-bearing:\n"
    "  - Antworte NUR mit der Metapher — kein Echo, kein Vorspann.\n"
    "  - Beginne IMMER mit einem der beiden Marker oben.\n"
    "  - Maximal zwei Sätze — prägnant und konkret.\n"
    "  - Übertrage das Kernthema auf etwas Greifbares aus dem Alltag.\n"
    "  - Kein neues Wissen einführen, nur die Analogie.\n"
    "  - Spreche-sprache — keine Code-Tokens, keine Markdown-Tokens.\n"
    "  - Antworte NUR mit leerem String wenn der Input ausschließlich aus "
    "einer kurzen Begrüßung oder Bestätigung besteht (z.B. 'Hallo', 'Ja', "
    "'Ok', 'Danke') — kein inhaltlicher Kontext vorhanden. Für JEDE "
    "inhaltliche Aussage — auch kurze, nüchterne oder technische — "
    "erzeuge immer eine Metapher."
)

_METAPHER_SYSTEM_EN = (
    "You are a metaphor generator for spoken output (TTS). "
    "You receive a FINISHED voice-output text as input. That text is "
    "ALREADY being read aloud — do not echo or modify it.\n"
    "\n"
    "YOUR ONLY TASK: write one to two sentences as a metaphor or analogy "
    "that maps the core topic of the input onto something from everyday "
    "life. These sentences will be read aloud AFTER the input.\n"
    "\n"
    "The sentences MUST start with one of these markers:\n"
    "  - \"As a picture,\"\n"
    "  - \"Think of it like\"\n"
    "\n"
    "RULES — load-bearing:\n"
    "  - Reply with the metaphor ONLY — no echo, no preamble.\n"
    "  - Always start with one of the markers above.\n"
    "  - Two sentences maximum — concise and concrete.\n"
    "  - Map the core topic onto something tangible from everyday life.\n"
    "  - Introduce no new information — just the analogy.\n"
    "  - Spoken language only — no code tokens, no markdown.\n"
    "  - Reply with the empty string ONLY when the input consists solely "
    "of a short greeting or acknowledgement (e.g. 'Hi', 'Yes', 'OK', "
    "'Thanks') with no informational content. For ANY substantive "
    "statement — even short, dry, or technical — always produce a metaphor."
)

_METAPHER_MARKERS = (
    "Als Bild gesprochen,", "Bildlich gesprochen,",
    "As a picture,", "Think of it like",
)


def _metapher_via_cli(text: str, lang: str, model: str) -> str | None:
    """Run claude -p with the metapher-only system prompt."""
    if not shutil.which("claude") or not _claude_authenticated():
        return None
    sys_prompt = _METAPHER_SYSTEM_EN if lang == "en" else _METAPHER_SYSTEM_DE
    env = os.environ.copy()
    env["VOICE_HOOK_RECURSION"] = "1"
    try:
        out = subprocess.run(
            [
                "claude", "-p", text,
                "--append-system-prompt", sys_prompt,
                "--model", model,
                "--disallowedTools", "*",
            ],
            capture_output=True, text=True, env=env,
            timeout=_ANNEX_CLI_TIMEOUT_S, check=True,
        )
        return out.stdout.strip() or None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"[summarize] metapher CLI call failed: {exc}", file=sys.stderr)
        return None


def _extract_metapher(raw: str) -> str:
    """Pluck a well-formed metapher sentence from *raw*.

    Strips everything before the first recognised marker. Returns ""
    if no marker found (caller falls back to verbatim input).
    """
    if not raw:
        return ""
    for marker in _METAPHER_MARKERS:
        idx = raw.find(marker)
        if idx >= 0:
            return raw[idx:].strip()
    return ""


def _metapher_via_hermes(text: str, lang: str) -> str | None:
    """Backend 2 for the METAPHER annex — local Hermes (Ollama), tried when
    the CLI is unavailable/unauthenticated. See _ollama_generate."""
    sys_prompt = _METAPHER_SYSTEM_EN if lang == "en" else _METAPHER_SYSTEM_DE
    return _ollama_generate(sys_prompt, text)


def generate_metapher(text: str, lang: str = "de",
                      model: str = "claude-haiku-4-5-20251001") -> str:
    """Return 1-2 metaphor sentences for *text*, or "" on any failure."""
    if not text or not text.strip():
        return ""
    raw = _metapher_via_cli(text, lang, model)
    if raw is None:
        raw = _metapher_via_hermes(text, lang)
    if raw is None:
        return ""
    return _extract_metapher(raw)


def summarize_with_metapher(text: str, lang: str = "de",
                             model: str = "claude-haiku-4-5-20251001") -> str:
    """Echo *text* verbatim plus 1-2 generated metaphor sentences.

    Used by adapter.build_voice_summary when voice_audience_metaphors="on"
    and the regular --audience path is bypassed (voice-override and
    short-text direct paths). Faithfulness invariant: *text* itself is
    never paraphrased — only suffixed.
    """
    text = (text or "").strip()
    if not text:
        return ""
    metapher = generate_metapher(text, lang=lang, model=model)
    if not metapher:
        return text  # silent fail — listener still gets the original
    return f"{text} {metapher}"


def summarize(text: str, lang: str, max_chars: int, model: str, task: str = "", persona: str = "", audience: str = "", output_language: str = "") -> str:
    """Try CLI first (Max-subscription / OAuth), then SDK (API key), then
    structural compression. Each backend may return None to signal fallback.

    When `task` is non-empty, the LLM backends produce a two-part read-aloud
    (task paraphrase + answer summary). The structural fallback synthesizes
    the same shape by prefixing a clipped version of the task.

    When `persona` is non-empty AND known, a one-line tone addendum is added
    to the system prompt — modulates voice style, never overrides content
    rules. Unknown personas fall back to neutral tone (silent no-op).

    When `audience` is non-empty, a layer-12 listener-profile block is
    appended — steers WHICH analogies / jargon level the summarizer picks
    when translating cryptic content. Backward-compat: empty audience
    leaves the prompt byte-identical to the pre-layer-12 path.

    When `output_language` is a BCP-47 code (e.g. `zh-Hans`, `ja`, `ar`),
    a final OUTPUT LANGUAGE directive pins the LLM reply to that locale.
    For `de`/`en`/empty the prompt is byte-identical to the pre-i18n
    path. The structural-compression fallback ignores the directive (it
    has no LLM to obey it; for non-de/non-en locales the structural
    output stays in the source language — better than producing
    invalid text in a language we can't synthesize).
    """
    target = adaptive_target(text, max_chars)

    candidate: str | None = None

    _backend = os.environ.get("VOICE_SUMMARIZE_BACKEND", "auto")

    # Short-circuit: a reply that ALREADY fits the spoken budget needs no LLM
    # summary. Spawning `claude -p` / Hermes here is pure latency — the dominant
    # voice-reply delay (a cold `claude -p` first-spawn is ~tens of seconds) —
    # AND it risks the model DRIFTING: e.g. a 3-word "Erledigt, alles gut." was
    # being rewritten into a DIFFERENT sentence ("Prima, gerne! Falls noch mehr
    # anliegt …"), inventing content the source never had. Speak it verbatim
    # (the same faithful structural path Backend 3 uses) — instant and exact.
    # Only genuinely-too-long replies pay for a real LLM summary below. Explicit
    # backend pins (cli/hermes, used by tests) still exercise the LLM path.
    #
    # Gated on persona/audience both being unset: a user who explicitly
    # configured either wants that tone/steering applied to EVERY reply, not
    # just long ones — the pre-existing behavior before this short-circuit was
    # added. Skipping the LLM pass for those users silently dropped the
    # feature they opted into, so they still pay the LLM-latency cost here
    # (same as before this fix existed); everyone else gets the instant,
    # faithful verbatim path.
    if (
        _backend == "auto"
        and not persona.strip()
        and not audience.strip()
        and len(text.strip()) <= max_chars
    ):
        # Text already fits the budget → return it TRULY VERBATIM. NOT via
        # naive_truncate: that runs per-line first-clause compression which drops
        # the description sentence of each item in a short multi-sentence list
        # ("choices are sacred" violation, caught in review). Verbatim is exact
        # AND in-budget (len <= max_chars), so nothing needs shortening.
        body = text.strip()
        prefixed = _task_prefix(task, lang) + body if task.strip() else body
        # The task-prefix check above only verified `body` alone fits the
        # budget — re-check AFTER prefixing, since _task_prefix can add up to
        # ~140 chars. Only fall through to a real summary pass (which DOES
        # enforce the budget) if the prefixed text overruns it.
        if len(prefixed) <= max_chars:
            return prefixed

    # Backend 1: CLI — preferred for users with Claude Max who don't want
    # to manage a separate API key.
    if _backend in ("auto", "cli"):
        out = _summarize_via_cli(text, task, lang, target, model, persona, audience, output_language)
        if out:
            candidate = out

    # Backend 2: Hermes (local Ollama) — the DEFAULT zero-config engine. Without
    # this a Hermes-only install (no claude CLI / API key) had no LLM summarizer
    # and every long voice reply was naive_truncate'd mid-sentence. Tried after the
    # CLI so Claude-Max users are unaffected; before structural so the shipped
    # default gets a real summary.
    if candidate is None and _backend in ("auto", "hermes"):
        out = _summarize_via_hermes(text, task, lang, target, model, persona, audience, output_language)
        if out:
            candidate = out

    # Backend 3: structural compression. Never drops list items. When a task
    # is given, prefix it manually since the structural fallback can't
    # rephrase prose. The audience block has no effect here — it's an LLM-
    # only steering signal; structural compression keeps every list item
    # verbatim and has no LLM to obey style instructions.
    if candidate is None:
        body = naive_truncate(text, target)
        candidate = _task_prefix(task, lang) + body if task.strip() else body

    # Layer-11 dialectic faithfulness check (independent second-model
    # verification). The inline SELF-CHECK in the system prompt is the
    # always-on first line of defence and runs for every persona. This
    # CLI-mode judge is the OPTIONAL second line — a separate `claude -p`
    # round that judges the candidate against the source.
    #
    # Per-persona policy (pin-pointed from the persona-cycle E2E on
    # 2026-05-09 — see CLAUDE.md "voice_summary site"):
    #   * research / forge / browser — inline self-check leaves a mild
    #     residual drift (background-knowledge enrichment, op-action
    #     additions, markdown style). These three opt INTO the CLI
    #     judge by default — the second-model pass catches the drift
    #     the inline loop misses.
    #   * everyone else — inline self-check is sufficient; default-off
    #     to preserve the voice-reply latency budget. User can still
    #     flip the global `/dialectic-set voice_summary cli` to force
    #     the CLI judge for every persona.
    if _dialectic is not None:
        try:
            persona_mode = _PERSONA_VOICE_SUMMARY_MODE.get(
                (persona or "").lower())
            final, verdict, _why = _dialectic.judge_summary(
                source=text, candidate=candidate, lang=lang,
                persona=persona, mode=persona_mode,
            )
            if verdict == "corrected":
                return final
        except Exception:  # noqa: BLE001
            # Faithfulness check is observability + safety, never load-
            # bearing. Any error → ship the candidate as-is.
            pass

    return candidate


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", default="de", choices=["de", "en"])
    ap.add_argument("--max-chars", type=int, default=400)
    # Default resolves through helper_model (Layer-29.5 cost-split). Lazy
    # import keeps stop_hook fast in the no-helper-model path; if the
    # bridges/shared/ tree isn't on PYTHONPATH (standalone invocation),
    # fall back to the canonical Haiku-4.5 model id.
    try:
        sys.path.insert(0,
            str(Path(__file__).resolve().parent.parent.parent / "bridges" / "shared"))
        import helper_model as _helper_model  # type: ignore
        _default_model = (
            _helper_model.resolve_helper_model(_helper_model.SITE_VOICE_SUMMARY)
            or "claude-haiku-4-5-20251001"
        )
    except Exception:  # noqa: BLE001
        _default_model = "claude-haiku-4-5-20251001"
    ap.add_argument("--model", default=_default_model)
    ap.add_argument(
        "--task", default="",
        help="Original user prompt; if set, output includes a task paraphrase.",
    )
    ap.add_argument(
        "--persona", default="",
        help=(
            "Active cowork persona name (coder, browser, research, inbox, "
            "forge, skill-forge, homeassistant). Tints the speaking style "
            "without overriding faithfulness or completeness. Unknown "
            "names are a silent no-op."
        ),
    )
    ap.add_argument(
        "--audience", default="",
        help=(
            "Layer-12 listener-profile block (rendered by "
            "bridges/shared/profile.py::for_tts_audience). Steers HOW the "
            "summarizer translates cryptic content for the listener. "
            "Faithfulness and completeness in the base system prompt stay "
            "load-bearing — the audience block tunes tone, never content."
        ),
    )
    ap.add_argument(
        "--output-language", default="",
        help=(
            "BCP-47 locale to pin the spoken output to (e.g. zh-Hans, ja, "
            "ar, pt-BR). When empty or set to de/en, the prompt is "
            "byte-identical to the pre-i18n path (legacy behaviour). For "
            "any other locale, an OUTPUT LANGUAGE directive is appended "
            "after the SELF-CHECK block; the LLM produces the read-aloud "
            "in that language while keeping code identifiers / CLI flags "
            "in their canonical form."
        ),
    )
    ap.add_argument(
        "--appendix-mode", action="store_true",
        help=(
            "Echo stdin verbatim and append a generated teaching annex "
            "(LERN-ZUGABE) as a suffix — used by adapter.build_voice_"
            "summary when the regular --audience path is bypassed "
            "(voice-override and short-text direct paths). Input text "
            "is NEVER paraphrased — faithfulness invariant. Falls back "
            "to verbatim input when the appendix LLM call is "
            "unavailable or returns unparseable output."
        ),
    )
    ap.add_argument(
        "--metapher-mode", action="store_true",
        help=(
            "Echo stdin verbatim and append 1-2 generated metaphor/analogy "
            "sentences (METAPHER-ZUGABE) as a suffix — used by adapter."
            "build_voice_summary when voice_audience_metaphors='on' and the "
            "regular --audience path is bypassed (voice-override and "
            "short-text direct paths). Input text is NEVER paraphrased — "
            "faithfulness invariant. Falls back to verbatim input on failure."
        ),
    )
    args = ap.parse_args()

    text = sys.stdin.read()
    if not text.strip():
        return 0

    if args.appendix_mode:
        print(summarize_with_appendix(text, lang=args.lang, model=args.model))
        return 0

    if args.metapher_mode:
        print(summarize_with_metapher(text, lang=args.lang, model=args.model))
        return 0

    print(summarize(text, args.lang, args.max_chars, args.model, args.task,
                    args.persona, args.audience, args.output_language))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Tests for detect_voice_intent.detect()."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from detect_voice_intent import detect  # noqa: E402

CASES = [
    # ---- full ----
    ("lies mir das vollständig vor",                                   "full"),
    ("lies mir den ganzen Research-Text vor",                          "full"),
    ("Lies das komplett vor",                                          "full"),
    ("Bitte voll vorlesen",                                            "full"),
    ("lies das wörtlich vor",                                          "full"),
    ("lies das alles vor",                                             "full"),
    ("Ohne Kürzung vorlesen",                                          "full"),
    ("Bitte nicht zusammenfassen, sondern vollständig vorlesen",       "full"),
    ("Read the whole thing aloud",                                     "full"),
    ("Read it in full please",                                         "full"),
    ("read this verbatim",                                             "full"),
    ("Read everything completely",                                     "full"),
    ("no summary, please",                                             "full"),
    ("Don't summarize, just read it",                                  "full"),

    # ---- summary ----
    ("fass das zusammen",                                              "summary"),
    ("Fasse mir den Text zusammen",                                    "summary"),
    ("Gib mir die Kurzfassung",                                        "summary"),
    ("Erklär mir das in Kürze",                                        "summary"),
    ("In kurz, was kam raus?",                                         "summary"),
    ("Summarize this for me",                                          "summary"),
    ("Give me the short version",                                      "summary"),
    ("TL;DR please",                                                   "summary"),
    ("In short — what did you do?",                                    "summary"),

    # ---- no override ----
    ("Was ist 2 + 2?",                                                 ""),
    ("Bau mir ein Skript dafür",                                       ""),
    ("Hello, can you check the logs?",                                 ""),
    ("Lies das Lockfile",                                              ""),  # "lies" alone, no full-intent
    ("Sag mir kurz was du gemacht hast",                               ""),  # "kurz" without summary trigger
    ("",                                                               ""),

    # ---- precedence: full beats summary in mixed sentences ----
    ("Fass das zusammen — nein, lies es doch vollständig vor",         "full"),
]


def main() -> int:
    fails = []
    for text, want in CASES:
        got = detect(text)
        ok = got == want
        marker = "OK" if ok else "FAIL"
        print(f"  [{marker}] want={want!r:<10} got={got!r:<10} :: {text!r}")
        if not ok:
            fails.append((text, want, got))
    print()
    if fails:
        print(f"{len(fails)} of {len(CASES)} failed")
        return 1
    print(f"all {len(CASES)} ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())

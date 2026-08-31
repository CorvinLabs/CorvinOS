#!/usr/bin/env python3
"""Unit-Tests for router.py — alles via ROUTER_FAKE-Hook, kein LLM-Call.

Run: python3 operator/bridges/shared/test_router.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import router  # type: ignore

failures: list[str] = []


def expect(cond: bool, label: str) -> None:
    if cond:
        print(f"PASS: {label}")
    else:
        failures.append(label)
        print(f"FAIL: {label}")


PERSONAS = [
    {"name": "browser", "description": "Web", "zero_config": True},
    {"name": "research", "description": "Notes + Web", "zero_config": True},
    {"name": "assistant", "description": "Allrounder", "zero_config": True,
     "routing_exclude": True},
]


def with_fake(payload: str | None):
    if payload is None:
        os.environ.pop("ROUTER_FAKE", None)
        os.environ.pop("ROUTER_FAKE_RESULT", None)
    else:
        os.environ["ROUTER_FAKE"] = "1"
        os.environ["ROUTER_FAKE_RESULT"] = payload


def main() -> int:
    # ── 1. Klare Wahl, hohe Confidence → return ────────────────────────
    with_fake('{"persona":"browser","confidence":0.92,"why":"web task"}')
    r = router.route("open example.com", PERSONAS)
    expect(r is not None and r["persona"] == "browser",
           "klare Wahl wed backgegeben")
    expect(r is not None and abs(r["confidence"] - 0.92) < 0.01,
           "confidence wed durchgereicht")

    # ── 2. Zu niedrige Confidence → None ───────────────────────────────
    with_fake('{"persona":"browser","confidence":0.30,"why":"meh"}')
    r = router.route("hi", PERSONAS, min_confidence=0.5)
    expect(r is None,
           "low confidence rejected")

    # ── 3. Unbekannte Persona → None ───────────────────────────────────
    with_fake('{"persona":"nonexistent","confidence":0.99,"why":"x"}')
    r = router.route("?", PERSONAS)
    expect(r is None, "unbekannte Persona rejected")

    # ── 4. routing_exclude greift — Allrounder darf nicht gepickt ─────
    with_fake('{"persona":"assistant","confidence":0.99,"why":"all-purpose"}')
    r = router.route("?", PERSONAS)
    expect(r is None, "routing_exclude verhindert Pick")

    # ── 5. Leere Personas → None ───────────────────────────────────────
    with_fake('{"persona":"browser","confidence":0.99,"why":"x"}')
    r = router.route("?", [])
    expect(r is None, "leere Personas-Liste → None")

    # ── 6. Nur excluded Personas → None ────────────────────────────────
    r = router.route("?", [PERSONAS[2]])  # nur assistant (excluded)
    expect(r is None, "nur excluded → None")

    # ── 7. Leerer Text → None ──────────────────────────────────────────
    r = router.route("", PERSONAS)
    expect(r is None, "leerer Text → None")
    r = router.route("   ", PERSONAS)
    expect(r is None, "whitespace-only → None")

    # ── 8. Kaputtes JSON in FAKE_RESULT → None (graceful) ──────────────
    with_fake("not json")
    r = router.route("test", PERSONAS)
    expect(r is None, "kaputtes JSON → None")

    # ── 9. Confidence als String "0.8" → trotzdem akzeptiert ──────────
    with_fake('{"persona":"browser","confidence":"0.8","why":"x"}')
    r = router.route("?", PERSONAS)
    expect(r is not None and r["persona"] == "browser",
           "confidence als String wed konvertiert")

    # ── 10. Persona-Name fehlt → None ──────────────────────────────────
    with_fake('{"confidence":0.9,"why":"x"}')
    r = router.route("?", PERSONAS)
    expect(r is None, "fehlender persona-key → None")

    # cleanup
    with_fake(None)

    # ── 11. mode='off' → immer None, auch bei klarem Match ────────────
    r = router.route("öffne die URL example.com", PERSONAS, mode="off")
    expect(r is None, "mode=off → None")

    # ── 12. Heuristik browser: deutsches "öffne die URL" ──────────────
    r = router.route("öffne mal die URL example.com", PERSONAS,
                     mode="heuristic")
    expect(r is not None and r["persona"] == "browser",
           "heuristic: 'öffne die URL' → browser")
    expect(r is not None and r["confidence"] >= 0.5,
           "heuristic: confidence schlägt min_confidence")

    # ── 13. Heuristik research: "im internet recherchieren" ───────────
    r = router.route("Recherchiere mal im Internet zu Topic X",
                     PERSONAS, mode="heuristic")
    expect(r is not None and r["persona"] == "research",
           "heuristic: 'recherchier im internet' → research")

    # ── 14. Heuristik research: englisch "search the web" ─────────────
    r = router.route("search the web for python news",
                     PERSONAS, mode="heuristic")
    expect(r is not None and r["persona"] == "research",
           "heuristic: 'search the web' → research")

    # ── 15. Heuristik browser: englisch "navigate to" ─────────────────
    r = router.route("navigate to the page and click submit",
                     PERSONAS, mode="heuristic")
    expect(r is not None and r["persona"] == "browser",
           "heuristic: 'navigate to' → browser")

    # ── 16. Heuristik: Coding-Anfrage → kein Match → None ─────────────
    r = router.route("write a python function that sorts a list",
                     PERSONAS, mode="heuristic")
    expect(r is None, "heuristic: pure coding-task → None (Fallback)")

    # ── 17. Heuristik mode skipt LLM komplett — ROUTER_FAKE wird trotz-
    #       dem konsultiert wenn gesetzt. Test: ROUTER_FAKE liefert
    #       browser, mode='heuristic' soll trotzdem nur Heuristik
    #       prüfen. (FAKE wird in route() VOR mode-spezifischen Backends
    #       konsumiert, daher gibt's hier doch ein Match — explizit
    #       dokumentieren.)
    with_fake('{"persona":"browser","confidence":0.92,"why":"web"}')
    r = router.route("schreibe eine python funktion", PERSONAS,
                     mode="heuristic")
    expect(r is not None and r["persona"] == "browser",
           "ROUTER_FAKE wird auch in mode=heuristic respektiert (Test-Hook)")
    with_fake(None)

    # ── 18. CLI-Fallback ist standardmäßig deaktiviert ────────────────
    # Wir können den CLI-Pfad ohne echten Aufruf testen, indem wir
    # ROUTER_ALLOW_CLI explizit nicht setzen und mode=auto nutzen — der
    # Code soll dann die SDK probieren (ohne Key auch None) und den CLI-
    # Pfad NICHT erreichen. Bei intaktem Verhalten kommt None zurück
    # ohne 12-s-Hänger.
    os.environ.pop("ROUTER_ALLOW_CLI", None)
    os.environ.pop("ANTHROPIC_API_KEY", None)
    import time
    t0 = time.monotonic()
    r = router.route("write a poem about cats", PERSONAS, mode="auto")
    elapsed = time.monotonic() - t0
    expect(r is None, "mode=auto + kein API-Key + kein ALLOW_CLI → None")
    expect(elapsed < 2.0,
           f"mode=auto schlägt schnell fehl (kein 12-s-Hänger), war {elapsed:.2f}s")

    # ── 19. Heuristik-Pattern darf kein Wort-im-Wort-Match sein:
    #       "the open source library" enthält "open" — soll NICHT
    #       browser triggern, weil "open" allein nicht reicht.
    r = router.route("what is the open source library for X",
                     PERSONAS, mode="heuristic")
    expect(r is None,
           "heuristic: bloßes 'open' triggert nicht (kein false positive)")

    # ── 20. Heuristik respektiert routing_exclude ─────────────────────
    # Selbst wenn ein Pattern für 'assistant' definiert wäre, würde es
    # wegen routing_exclude nicht greifen. Aktuell hat assistant keine
    # Pattern, aber wir verifizieren, dass die Pool-Filterung greift.
    custom = [
        {"name": "assistant", "description": "all", "zero_config": True,
         "routing_exclude": True},
    ]
    r = router.route("öffne die URL example.com", custom, mode="heuristic")
    expect(r is None,
           "heuristic respektiert routing_exclude (nur assistant im pool)")

    if failures:
        print(f"\n{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nALL CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

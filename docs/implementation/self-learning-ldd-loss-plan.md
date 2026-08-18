# Implementierungsplan v3: Ehrliches Self-Learning (P1–P4)

**Status:** Proposal (v3, nach adversarialem Design-Review) · **Datum:** 2026-08-18 · **Autor:** Claude (Opus 4.8)
**Konzept:** [`docs/concepts/self-learning-ldd-loss-concept.md`](../concepts/self-learning-ldd-loss-concept.md) (v3)
**ADRs:** ADR-0373 (Signal: frei-negativ + gebaute Coding-Verifikation) · ADR-0374 (Konsument = Operator)
**Review-Ergebnis, eingearbeitet:** H1 (kein Pro-Turn-LDD-Loss) · H2 (Anker sparse) · M1–M6.

---

## Constraints (v3-korrigiert)
| # | Constraint | Ziel |
|---|---|---|
| C1 | Kein Nutzer-Rating | Operator-Override bleibt Ausnahme |
| C2 | Ehrliches Signal | positives Signal NUR aus gebauter Verifikation (Code); sonst nur Abwertung |
| C3 | Kein Auto-Verhalten | nichts ändert Verhalten automatisch; Operator disponiert (löst M1/M3/M4/M5) |
| C4 | Operator-only-Promotion absolut | `__signal__` promotet NIE (löst M3) |
| C5 | Task-Attribution | Verifikations-Signal kreditiert die Aufgabe, nicht Turn N+2 (löst M6) |
| C6 | Content-free | nur Exit-Codes/Metriken/Fingerprints |
| C7 | Ship-dark | jede Phase Flag-off = heute |
| C8 | Ehrliche Grenze | Nicht-Coding-Turns lernen nur Abwertung; klar kommuniziert (H2) |

---

## Phase 1 — Frei-negatives Signal verdrahten *(billig, richtige Ebene)*
Ersetzt das pauschale `True` an `chat_runtime.py:6401`. **Kein Uniform-Blame** (Review F1 — `rc` ist der
*Worker*-Exit-Code, entsteht nach der Kontext-Assemblierung):
- Harter Fehler (`rc≠0` / Healing / Artefakt-Fehler) → **Turn-Health-Record** (Turn-Ebene), NICHT auf alle gelaufenen Stages verteilt.
- Per-Stage-Abwerter **nur mit direktem Link:** geforgtes Tool lief mit Fehler → `toolforge`; Artefakt installierte nicht → erzeugende Stage. Sonst kein Stage-Grade.
- **Sonst: gar nichts** (kein `True`). Flag `ldd_loss_learning` (default off).
- **Test (E2E):** Fehler-Turn → Turn-Health negativ + (bei Tool-Fehler) genau `toolforge` abgewertet, **nicht** alle Stages; sauberer Turn → **kein** Grade. Durch die `stream_turn`-Grenze.

## Phase 2 — Coding-Verifikation als echter Anker *(opt-in; echte Isolation, nicht env-scrub)*
`core/learning/turn_verification.py`:
- Erkennt, ob der Turn Tests/Build produziert hat (Workdir-Diff, `is_test_path`).
- **Isolation (Review F2):** das Reproduction-Gate ist **kein Sandbox** (sein Header: „full sandbox/uid-drop out of scope, nur env-scrub"). Turn-produzierten Code auszuführen braucht **echte Isolation** (Container/Namespace/uid-drop) und ist **single-tenant only**, bis die Isolation steht (Konsole ist multi-tenant, L18–21). Ohne echte Isolation wird P2 nicht ausgeliefert.
- **Runnability (Review F2b):** ein Test, der nicht laufen *kann* (fehlende Deps/Fixtures), ist **`inconclusive`, NICHT rot** — sonst wird gute Arbeit systematisch als Fehlschlag gelabelt. Nur ein *laufender, roter* Test ist ein Negativ; nur ein *laufender, grüner* ein Positiv.
- Positiver `__signal__`-Grade, **task-attribuiert** (C5/M6). Flag `coding_verification` (default off, kostet Compute).
- **Test:** grüner produzierter Test → positiver task-Grade; roter → Abwerter; nicht-lauffähiger → `inconclusive` (kein Grade); Nicht-Coding-Turn → nichts. In echter Isolation.

## Phase 3 — Anzeige + Operator-Empfehlung aus STAGE-LOKALEN Signalen *(kein Auto-Verhalten)*
- Weg-A-Baum (ADR-0372) zeigt verdiente/abgewertete Confidence + Evidence-Split (schon da).
- Empfehlung **nur aus direkten Stage-lokalen Signalen** (Tool lief mit Fehler; injizierte Quelle nie referenziert) — **NICHT** aus rohen „Stage in N Fehler-Turns"-Zählungen (Base-Rate-Confound, Review F1; hält §4 konsistent). Operator nimmt per Klick an (schreibt `operator`-Grade) oder ignoriert.
- **Kein** Auto-Gate. Promotion bleibt operator-only (C4).
- **Test:** Empfehlung erscheint nur bei einem stage-lokalen Signal (nicht bei einem bloßen Häufigkeits-Count einer Always-on-Stage); Annehmen schreibt `operator`-Grade; nichts ändert sich ohne Klick.

## Phase 4 — Kalibrierung, ehrlich begrenzt
- `core/learning/signal_calibration.py`: Gewicht eines Signals = Funktion seiner rollierenden Korrelation mit dem **Verifikations-Anker + Operator-Override**.
- **Ehrlich (H2):** der Anker existiert nur auf Coding-Turns → Nicht-Coding-Signale bleiben Gewicht 0, protokolliert. Das wird nicht wegdefiniert; es ist die reale Decke.
- Verhaltens-Signal (v1): nur hier, nur wenn es den Anker vorhersagt, nur negativ, nie Optimierungsziel (strukturell: es gibt keinen Auto-Optimierer, C3/M5).

---

## Was v3 NICHT baut (vom Review gestrichen)
- ❌ LDD-Loss-Bridge (Anker existiert nicht pro Turn, H1).
- ❌ Auto-Gating von Opt-in-Stages / attention_budget-Konsument (M1/M2/M4).
- ❌ `__ldd__`-promotet-nach-Kalibrierung (M3).
- ❌ Statistische Per-Stage-Attribution auf Einzel-Instanz (an G5-Volumen verschoben).

## Test-Strategie + Rollback
Jede Phase Flag-off = heutiges Verhalten. P1 stellt das pauschale `True` NICHT wieder her (das war der Bug) — Flag-off = kein Auto-Grade.

## Erfolgs-Kriterien
| Anf. | Metrik | Ziel |
|---|---|---|
| C2 | positive Grades nur aus Verifikation | 100 % |
| C3 | Auto-Verhaltensänderungen | 0 |
| C4 | `__signal__` in `_PROMOTING_GRADERS` | nein |
| C5 | Verifikations-Signal task-attribuiert | ja |

## Bekannte Lücken
- **KG1 (H2):** Self-Learning ist auf verifizierbare (Coding-)Turns begrenzt. Ehrlich kommuniziert.
- **KG2:** Coding-Verifikation kostet Compute → opt-in; nicht für jeden Turn.
- **KG3:** Der 90/10-Punkt: für einen Einzelnutzer ist Weg-A + P1-Abwertung bereits der Großteil des Werts. P2 lohnt nur, wenn viel Engineering-Arbeit über die Console läuft.

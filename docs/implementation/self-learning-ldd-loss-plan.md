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

## Phase 1 — Frei-negatives Signal verdrahten *(billig, sofort ehrlicher)*
Ersetzt das pauschale `True` an `chat_runtime.py:6401`:
- `rc≠0` / Healing/Anomaly gefeuert / Artefakt-Install-Fehler → `grade_stage(..., grader="__signal__", score=0.0, notes="hard_failure:<kind>")` für die gelaufenen Stages (Abwerter).
- **Sonst: gar nichts schreiben** (kein `True`). Flag `ldd_loss_learning` (default off).
- **Test (E2E):** Fehler-Turn → Abwerter-Grade sichtbar in `build_earned_tree`; sauberer Turn → **kein** Grade (nicht 1.0). Durch die `stream_turn`-Grenze.

## Phase 2 — Coding-Verifikation als echter Anker *(opt-in, das Neue)*
`core/learning/turn_verification.py`:
- Erkennt, ob der Turn Tests/Build produziert hat (Artefakte im Workdir).
- Läuft sie **sandboxed** (Reproduction-Gate-Muster, `aco/reproduction.py` als Vorbild) → objektives pass/fail.
- Positiver `__signal__`-Grade, **task-attribuiert** (Aufgabe = Turn-Kette bis grün; C5/M6): das Signal kreditiert die genutzten Stages/Tools der Aufgabe, nicht den Zufalls-Turn.
- Flag `coding_verification` (default off, kostet Compute).
- **Test:** ein Coding-Turn mit grünen Tests → positiver task-attribuierter Grade; roter Test → Abwerter; Nicht-Coding-Turn → kein Verifikations-Grade.

## Phase 3 — Anzeige + Operator-Empfehlung *(der Konsument, kein Auto-Verhalten)*
- Weg-A-Baum (ADR-0372) zeigt abgewertete/verdiente Confidence + Evidence-Split (schon da).
- Neu: wo ein Muster stark ist, eine **Empfehlung** („Stage X war in N harten Fehler-Turns — abwerten?"), die der Operator per Klick annimmt (schreibt einen `operator`-Grade) oder ignoriert.
- **Kein** Auto-Gate. Promotion bleibt operator-only (C4).
- **Test:** Empfehlung erscheint bei klarem Muster; Annehmen schreibt einen `operator`-Grade; nichts ändert sich ohne Klick.

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

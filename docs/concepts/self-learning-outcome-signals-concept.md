# Konzept: Self-Learning ohne Nutzer-Bewertung — echte, automatische Erfolgssignale

**Status:** Proposal · **Datum:** 2026-08-18 · **Autor:** Claude (Opus 4.8) für shumway
**Scope:** CEL-Outcome-Loop (G4) · `record_turn_outcome` · ADR-0314 Learning-Infra · TreeOfThoughts-Confidence (Weg A)
**Verwandt:** ADR-0269 (Outcome-Feedback), ADR-0285 (Stage-Grades), ADR-0314–0320 (Learning-Infra), ADR-0372 (Weg A), ADR-0179/0180 (Reproduction/Healing)
**Leitsatz:** *Der Nutzer bewertet nie etwas. Das System zieht seine Signale aus dem, was ohnehin passiert.*

---

## 1. Das eigentliche Problem

Der Outcome-Loop (G4) schreibt heute für **jeden abgeschlossenen Turn** pauschal `success=True`
(`chat_runtime.py:6401` → `record_turn_outcome(tenant_id, stage_ids, True)`). Damit trendet die
„verdiente Confidence" jeder CEL-Stage Richtung 100 % — **egal wie gut der Turn war**. Sie ist ein
*Turn-Zähler*, keine Qualitätsnote.

Zwei Befunde aus dem Code-Scan machen das lösbar mit wenig Neubau:

1. **Das Signal wird weggeworfen, nicht ist es abwesend.** Genau in derselben Funktion, in der das
   pauschale `True` steht, liegen bereits: der **Worker-Exit-Code** `rc` (`chat_runtime.py:6361`),
   die **Fehler-/Cancel-Events** (`:6349`, `:6363-6366`), das **Artefakt-Install-Ergebnis**
   (`:6409`), die **Token-Usage** (`:6324`). Sie werden für die Bewertung ignoriert.
2. **Die Infrastruktur ist gebaut, aber schläft.** Der komplette ADR-0314-Stack —
   Confidence-Intervalle (`core/learning/confidence.py`, ADR-0315), Outcome-Feedback
   (`outcome_feedback.py`, ADR-0317), Decision-History (ADR-0316), Metriken (ADR-0320) — **existiert,
   hat aber keinen Live-Produzenten**. `OutcomeRecorder.record_outcome()` konsumiert heute nur eine
   *manuelle* Bewertung und wird nur von Tests aufgerufen. Der fast-fertige `ChatLearningWrapper`
   (baut echte `ExecutionMetrics(success=not error, latency, cost)`) hat **null Aufrufer**.

**Fazit:** Es geht nicht um „ein Learning-System bauen", sondern um **verdrahten + ein
Signal-Modell**, das aus vorhandenen Rohsignalen eine ehrliche, driftsichere Qualitätsnote macht.

---

## 2. Nicht-Ziele

1. **Keine Nutzer-Bewertungs-UI als Mechanismus.** Operator-Override (👎😐👍, Weg A) bleibt die
   *Ausnahme-Korrektur*, nie die primäre Datenquelle.
2. **Kein neues Learning-Framework.** Die schlafende ADR-0314-Infra wird wiederverwendet, nicht
   dupliziert.
3. **Kein Verhalten-Ändern durch unkalibrierte Auto-Signale.** Automatische Grades bleiben
   **non-promoting**, bis sie kalibriert sind — nur Operator-Grades promoten eine Stage
   (`_PROMOTING_GRADERS`, `grades.py:42`); diese Sicherheit bleibt unangetastet.
4. **Kein Speichern von Transkripten/Prompt-Text.** Alles content-free (Compliance-Baseline).

---

## 3. Drei Signal-Ebenen (billig → reich)

### Ebene 1 — Was jeden Turn schon anfällt *(nur verdrahten)*
Das pauschale `True` wird durch einen berechneten Ausgang ersetzt, aus Signalen, die alle bei
`chat_runtime.py:6361-6409` in Scope sind:

| Signal | Quelle | Bedeutung |
|---|---|---|
| `rc == 0` + kein `error`-Event | `:6361`, `:6363-6366` | sauber durchgelaufen (schwaches Positiv) |
| `rc != 0` / Fehler / `CancelledError` | `:6349`, `:6363` | **Negativ** |
| Healing/Anomaly gefeuert | `aco/htrace.py`, `anomaly_detector.py` | **Negativ**, content-free, existiert |
| Panel/Tool-Install fehlgeschlagen | `:6409` | **Negativ** (Artefakt kaputt) |
| Context-Overflow-Retry gefeuert (Bridge) | `adapter.py:5261-5275` | **Negativ** (nur Bridge-Pfad heute) |

**Ehrliche Grenze:** `rc=0` heißt „Worker nicht abgestürzt", nicht „gute Antwort". Ebene 1 fängt vor
allem **harte Fehler**. Notwendig, aber schwach — sie tötet den Turn-Zähler, macht die Note aber noch
nicht zur echten Qualität.

### Ebene 2 — Implizites Verhalten *(bauen — der Kern von „Self-Learning")*
Die **nächste Nutzer-Aktion IST die Bewertung**, ohne Bewertungs-UI. Heute **komplett abwesend**
(kein Sentiment-/Repair-/Follow-up-Detektor existiert):

- **Conversational Repair (nächste Nachricht):** sofortiges Umformulieren derselben Frage,
  „nein / falsch / nochmal / das meinte ich nicht" → **starkes Negativ**; Themenwechsel /
  „danke / passt" / auf der Antwort aufbauen → **schwaches Positiv**.
- **Folge-Verhalten:** wurde der vorgeschlagene Befehl ausgeführt? Bei Code: **Änderung behalten
  (commit) vs. verworfen (revert)** — das stärkste implizite Signal.
- **Engagement:** langer Abstand + kein Nachhaken (Nutzer hat gehandelt) vs. Session-Abbruch direkt
  nach einer substanziellen Antwort.

Braucht einen kleinen Klassifikator (lokales Modell oder Keywords+Embedding). **Content-Rule:** der
Text darf *berechnet*, nur das abgeleitete Signal *gespeichert* werden.

### Ebene 3 — Objektiver Ausgang bei Agenten-Turns *(Goldstandard, teils vorhanden)*
Bei Code/Tools ist die Wahrheit objektiv und maschinell prüfbar:

- **Build/Tests grün?** Das **Reproduction-Gate** (`aco/reproduction.py`, ADR-0179) produziert genau
  dieses binäre proven/not-proven-Signal — läuft aber heute nur im **Maintainer-Self-Healing-Loop**
  (`maintainer_cli.py:143`), nicht pro Console-Turn. → an Console-Coding-Turns hängen.
- **Forge-Tool ohne Fehler gelaufen?** Panel danach **wiederbenutzt statt gelöscht**?
- **TDE-Loss-Tracker** (`loss_profile_tracker.py`) lernt schon aus Delegations-Ausgängen — aber nur
  bei aktivem TDE.

---

## 4. Zwei harte Kernprobleme (ehrlich benannt)

### 4a. Attribution — welche Stage war schuld/verdient?
Heute **uniform**: `record_turn_outcome` schreibt allen gelaufenen Stages dieselbe Turn-Note
(`grades.py:114-122`). Das ist Rauschen — ein Turn lief durch 8 Stages; bei Fehler memory gleich stark
zu bestrafen wie toolforge ist falsch. Stufen, billig → teuer:

1. **Stage-lokale Signale** (heute abwesend, zu bauen): wurde die geholte Memory/Skill in der Antwort
   *tatsächlich referenziert*? lief das geforgte Tool? Das sind die *ehrlichen* Per-Stage-Signale.
2. **Statistische Attribution über Volumen:** eine Stage, die oft in fehlgeschlagenen Turns steckt,
   verliert Confidence (Korrelation, keine Kausation) — billig, braucht Verkehr.
3. *(Vermeiden vorerst: teure Counterfactual-/Ablations-Läufe.)*

### 4b. Anti-Drift — warum es nicht doch gegen 100 % läuft
Selbst mit echten Signalen driftet „kein Fehler = Erfolg = 1.0" nach oben. Fix:
**negativ-vorgespannt + evidenz-gated.**
- Erkannter Fehler → **starker** Abzug.
- Abwesenheit von Fehler → **kleiner** Aufschlag, und erst nach genug Volumen vertraut
  (**Confidence-Intervalle — ADR-0315 existiert, schläft nur**).
- Ergebnis: Confidence steigt langsam bei anhaltend fehlerfrei, fällt schnell bei echtem Fehler →
  echte Qualitätsnote, kein Zähler.

---

## 5. Der strukturelle Umbau

1. **Verzögerte Auflösung (deferred outcome).** Nicht bei Turn-Ende bewerten — der Ausgang ist da
   noch nicht bekannt (die nächste Nachricht / das Build-Ergebnis / das Healing-Event kommen später).
   Turn-Id + Stage-Ids als **pending** persistieren; ein **Resolver** vergibt die Note, wenn das
   Signal eintrifft (nächste Nachricht, Artefakt-Verifikation, oder Timeout → schwacher Default).
2. **Signal-Fusion → kalibrierte Wahrscheinlichkeit**, nicht binär: gewichtete Kombination der
   Ebenen-1/2/3-Signale zu einem Score mit Konfidenz-Intervall.
3. **Neuer Grader `__signal__`** (getrennt von `__loop__`), **non-promoting bis kalibriert** — ändert
   nie Verhalten, bevor er vertrauenswürdig ist.
4. **ADR-0314-Substrat wiederverwenden:** `event_schema` (hat schon `reliability_score`/
   `relevance_score`), `confidence.py`, `metrics.py`, `outcome_feedback.py` als Bausteine — statt neu.

**Datenfluss (Ziel):**
```
Turn läuft → CEL-Stages laufen → [pending outcome: turn_id, stage_ids, ts, ebene1-signale]
                                        │
   nächste Nachricht / Build / Healing / commit-vs-revert  ──►  Resolver
                                        │
        Signal-Fusion (negativ-gated, Konfidenz-Intervall)  ──►  grade_stage(grader="__signal__", score=p)
                                        │
                        ce_stage_grades.json  ──►  build_earned_tree  ──►  TreeOfThoughts (Weg A)
```

---

## 6. Compliance-Leitplanke
Durchgängig **content-free** (Compliance-Baseline, ADR-0179/0180): die nächste Nutzer-Nachricht darf
*berechnet* werden (Repair-Detektor), aber nur das **abgeleitete Signal** (Score/Boolean/Fingerprint)
wird persistiert — nie Prompt-/Transkript-Text. Healing-Fingerprints sind schon content-free
(`htrace.py:166`, Allowlist). Fail-closed: ein Signal, das PII-Shape trägt, wird verworfen, nicht
gespeichert.

---

## 7. Was existiert vs. was zu bauen ist (aus dem Code-Scan)

| Baustein | Status | Aktion |
|---|---|---|
| Ebene-1-Rohsignale (rc, Fehler, Healing, Artefakt) | **existieren, verworfen** | verdrahten |
| ADR-0314 Learning-Infra (Events, Confidence-Intervalle, Metriken) | **existiert, schläft** | wiederverwenden |
| `record_turn_outcome` / Grade-Store / Weg-A-Anzeige | **existiert, verdrahtet** | Signalquelle austauschen |
| Reproduction-Gate (Build/Test grün) | **existiert, nur Maintainer-Loop** | an Console-Turns hängen |
| TDE-Loss-Tracker | existiert, nur bei TDE | später einbeziehen |
| Conversational-Repair / Sentiment / Follow-up | **abwesend** | bauen (Ebene 2) |
| Stage-lokale Attribution | **abwesend** | bauen (4a) |
| Deferred-Resolver + Signal-Fusion + `__signal__` | **abwesend** | bauen (Kern) |

---

## 8. Phasen (jede eigenständig lieferbar, hinter dem `outcome_feedback_loop`-Flag)

- **P1 — Ebene 1 verdrahten.** `True` → berechneter Ausgang aus `rc`/Fehler/Healing/Artefakt. Kleiner
  Wiring-Fix an `chat_runtime.py:6401`; tötet den Turn-Zähler. *(Ehrlich: nur harte Fehler.)*
- **P2 — Deferred-Resolver + Negativ-Bias + Konfidenz-Intervalle (ADR-0315).** Pending-Store,
  Resolver, Signal-Fusion, `__signal__`-Grader. Das Fundament für „echte, nicht driftende Note".
- **P3 — Ebene 2: Conversational-Repair-Detektor.** Nächste-Nachricht-Klassifikator +
  commit-vs-revert für Code. Der eigentliche „lernt aus Verhalten"-Sprung.
- **P4 — Ebene 3: Reproduction-Gate an Console-Turns + stage-lokale Signale + statistische
  Attribution.** Der reichste, objektivste Ausgang.

Reihenfolge nach Wert/Aufwand: **P1 sofort** (billig, tötet den Zähler), **P2 als Fundament**, **P3
der Kern-Sprung**, **P4 der Goldstandard**.

---

## 9. Alternativen erwogen

- **Explizites 👍/👎 als Primär-Signal.** Verworfen — genau die Belastung, die der Nutzer nicht will;
  bleibt nur als seltenes Override (Weg A).
- **LLM-Judge bewertet jede Antwort selbst.** Teuer pro Turn, zirkulär (das Modell bewertet sich
  selbst), und nicht content-free-freundlich. Höchstens als ein *schwaches* Fusions-Signal, nicht als
  Basis.
- **Counterfactual-Attribution (Stage weglassen, Ergebnis vergleichen).** Korrekt, aber
  vervielfacht die Kosten pro Turn — später, wenn überhaupt.
- **Alles bei Turn-Ende bewerten (kein Deferred).** Verworfen — der Ausgang ist bei Turn-Ende noch
  nicht bekannt; das ist genau der heutige Fehler.

---

## 10. Bekannte Grenzen / Risiken

- Implizite Signale sind **verrauscht** (Themenwechsel ≠ immer Erfolg; Schweigen ≠ immer Zufriedenheit)
  → Konfidenz-Intervalle + Volumen-Schwelle sind Pflicht, nicht Kür.
- **Cold-Start:** 0.5-Prior bis genug Signal — die Anzeige muss „noch nicht genug Evidenz" ehrlich
  zeigen (Weg A tut das via Evidence-Split schon).
- **Kausation vs. Korrelation** bei statistischer Attribution — nie als promotendes Signal behandeln,
  bis stage-lokale Signale existieren.

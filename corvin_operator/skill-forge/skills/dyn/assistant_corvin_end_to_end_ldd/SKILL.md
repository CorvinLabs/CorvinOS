---
name: assistant_corvin_end_to_end_ldd
description: Orchestriert mock-freie E2E-Läufe von Corvin auf echten Daten und leitet daraus ein belastbares LDD-Loss-Signal ab.
---

# Corvin E2E-Lauf mit LDD-Loss-Signal

Ziel: reproduzierbares Loss-Signal statt binärem pass/fail — mit expliziter Entscheidungsregel, wann ein Delta berichtet wird.

## 0. Modus, Herkunft, Analyse-Einheiten

**Modus über genau eine Frage:** *Führt dieser Lauf das System erneut aus?*
- **Ja ⇒ Modus A.** Sandbox zwingend, Teardown zwingend, Retry/Idempotenz/DOM bewertbar.
- **Nein ⇒ Modus B** (eingefrorene Logs). Sandbox entfällt; Terme mit Re-Execution-Bedarf sind **`unbewertbar`**, nicht 0.

**Herkunft ist eine unabhängige Facette:** `synthetisch` | `korpus`. Bei `korpus` gilt die PII-Regel (Pseudonymisierung, Aufbewahrung, Zitatberechtigung). Mischbetrieb: getrennt aggregieren, Modus und Herkunft als Labels führen.

**Vertikal:** Lauf → Suite → Historie. **Horizontal — Partition:** Intent, Kanal, Sprache, Kundentyp, Tool-Pfad, Modellversion; vorab benennen, als Labels an jeder Einheit.

## 1. Blocker
Repo-Pfad + Startkommandos; bestehendes LDD-Loss-Format; je nach Modus Sandbox-Ziel, je nach Herkunft Korpus-Zugriff + Facettenschema. Fehlt eines: stoppen.

## 2. `E2E-PLAN.md`
Trace über alle Systemgrenzen; pro Hop ein Signal und die Abgrenzung korrekt / Flake / echter Fehler. Stabile IDs. **Gewichte hängen an Partitionen** (Häufigkeit × Schadenshöhe).

**MDE deklarieren, bevor gesampelt wird.** Ein Satz: „Wir wollen Loss-Deltas ab `mde` zuverlässig erkennen." Default `mde = 0.05`. Daraus folgt das Mindest-n je Partition: `n_min = ceil(16 · s² / mde²)`, mit `s` = geschätzte Streuung *zwischen* Einheiten der Partition (Pilotlauf oder Vorlauf; ohne Schätzung `s = 0.3` annehmen ⇒ `n_min ≈ 58`). `n_min` und `mde` gehören in `E2E-PLAN.md`; das Sample-Budget ist ihre Summe über alle Partitionen, nicht umgekehrt.

## 3. Sandbox + Teardown (nur A)
Snapshot + Restore, Sandbox-Keys, crash-fester Teardown (`trap`), Idempotenz gegen Reste aus Lauf n.

## 4. Erhebung
Preflights (Anti-Mock, Sandbox) nur in A. Geschichtetes Sampling über die Partitionen bis `n_min`; dünne Partitionen übergewichtet ziehen. Deterministische Anker n = 1, stochastische n ≥ 3.

**Zwei Streuungen, getrennt führen:** `stddev_within` (Wiederholungen *einer* Einheit — Hygiene, Flake-Detektor) und `stddev_between` (Streuung über die Einheiten *einer* Partition — die einzige Größe, die in den Vergleich eingeht). Bei `model_judged`-Kategorien mindestens 20 % Doppelbewertung; die Rater-Varianz wird in `stddev_between` addiert, nicht separat berichtet.

## 5. `loss-catalog.vN.json`
```json
{"version":"v3","categories":[
 {"id":"tool_call_wrong_arg","weight":0.30,"type":"deterministic","max":1},
 {"id":"tone_off","weight":0.10,"type":"model_judged","max":1}]}
```
Pflicht: `version`, je Kategorie `id`, `weight`, `type`, `max`. Summe `model_judged` ≤ 30 %. Jeder Fund mit Log-Zitat. Reviewer vergibt Kategorien, nie Gewichte.

## 6. `suite.json`
```json
{"catalog_version":"v3","mode":"A","provenance":"korpus","mde":0.05,
 "suite_score":0.18,"completion_rate":0.94,
 "partitions":[{"labels":{"intent":"refund"},"weight":0.4,"n":62,"n_min":58,
   "loss":0.22,"stddev_between":0.28,"se":0.036,"stddev_within":0.05,
   "coverage":0.8,"status":"scored","by_category":{"tool_call_wrong_arg":0.15}}]}
```
Loss je Einheit auf [0,1]. `se = stddev_between/√n` ist Pflichtfeld. Partitionstabelle ist Pflicht — der Skalar allein ist ungültig. `n < n_min` ⇒ `status:"unterbelegt"`: wird berichtet, aber nie in `TOP-REGRESSIONS.md` gelistet. Abbruchquote > 10 % ⇒ Score nicht berichten, Harness fixen.

## 7. Baseline + `TOP-REGRESSIONS.md` (Pflicht)
Rohscores verschiedener Katalogversionen sind inkommensurabel: **Re-Scoring** archivierter Runs oder **Frozen Reference Set**.

**Vergleichsoperation je Partition × Kategorie:**
1. `delta = loss_neu − loss_basis`; `se_delta = √(se_neu² + se_basis²)`.
2. Multiplizität: `k` = Anzahl verglichener Zellen; kritischer Wert `z = 2 + ln(k)/2` (k=10 ⇒ 3.15). Intervall: `[delta − z·se_delta, delta + z·se_delta]`.
3. **Berichtsregel:** Zeile nur, wenn `untere Grenze > mde`. Sonst kein Befund — auch bei großem Punktschätzer.
4. **Sortierung nach unterer Intervallgrenze absteigend**, nie nach `delta`.

Spalten: `partition | kategorie | delta | ci_low | ci_high | n | run_id | log_zitat`. Zeilen ohne Zitat sind ungültig. Leere Datei ist ein gültiges Ergebnis und heißt: keine belegbare Regression.

## 8. Ausbaustufen & Loop
v1 deterministisch → v2 UI (Upstream-Ausfall ⇒ `unbewertbar`) → v3 Adversarial, gedeckelt.
`LOSS-HISTORY.md`-Zeile: `datum | catalog_version | mode | provenance | suite_score | completion | coverage | mde | sample_budget | n_zellen_unterbelegt | befunde_über_schwelle | top_regression_ci_low`. Harness-Debugging getrennt vom System-Debugging.

---

**Keywords:** e2e, ldd, loss-signal, corvin, playwright, integration-test, adversarial-review, no-mocks, sandbox

**Dependencies:** bash, playwright, jq, git, psql

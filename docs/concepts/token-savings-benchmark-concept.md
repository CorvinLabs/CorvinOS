# Konzept: Evidenzbasiertes Token-Savings-Benchmark (echtes A/B, reproduzierbar)

**Status:** Proposal · **Datum:** 2026-08-18 · **Autor:** Claude (Opus 4.8) für shumway
**Motiv:** „Token-Sparen" ist Verkaufsargument — aber die aktuelle Baseline ist **erfunden**
(`token_baseline.py:37`: `baseline_tokens = 1800 × multiplier`, Kommentar: *„Real baseline would run a
stateless engine"* — tut es nicht). Dieses Konzept ersetzt die Schätzung durch **gemessene** A/B-Daten,
die der Nutzer selbst reproduzieren kann.
**Methode:** dialektisch (These/Antithese/Synthese) · Domain-Driven Design · LDD (Token = Loss).

---

## 1. Ubiquitous Language (DDD — alle meinen exakt dasselbe)

- **Task:** eine *lösen-bis-fertig*-Einheit (ein Problem, evtl. über mehrere Turns), NICHT ein einzelner Turn.
- **Arm A (Baseline / „Native"):** dieselbe Task mit **CEL aus** (`vibe_engineering=false`) — echte Ausführung.
- **Arm B (Vibe):** dieselbe Task mit **CEL an** (`vibe_engineering=true`) — echte Ausführung.
- **Tokens:** `input_tokens + output_tokens`, **real** aus der Worker-Usage (`stream_turn` result-Event),
  getrennt erfasst, für beide Arme identisch gemessen.
- **Savings:** `(A_tokens − B_tokens) / A_tokens` **pro Task** — **nur gültig, wenn Qualität(B) ≥ Qualität(A)**
  (Non-Inferiority-Gate, s. §3). Eine Token-Reduktion mit Qualitätsverlust ist **kein** Sparen.
- **Run:** eine Ausführung eines Arms einer Task. **Trial:** `n` Runs eines Arms (wegen Nicht-Determinismus).

---

## 2. Dialektik — warum ein naives A/B genauso lügt

**These:** Jede Benchmark-Task einmal nativ (A), einmal vibe (B) laufen lassen, Tokens messen, Differenz = Savings.

**Antithese (die Fallen, die ein naives A/B zu einer neuen Lüge machen):**
1. **Nicht-Determinismus.** Dieselbe Task zweimal → unterschiedliche Token-Zahlen (Sampling). Ein einzelnes
   A-vs-B ist Rauschen, kein Messwert.
2. **Qualität nicht kontrolliert (der Killer).** Arm A ohne Kontext kann eine *schlechtere* Antwort mit
   *weniger* Tokens liefern — das sähe wie „Vibe spart nicht" aus, ODER Vibe könnte mit mehr Tokens eine
   bessere Antwort geben. **Tokens ohne Qualitäts-Kontrolle sind bedeutungslos** — man kann jeden Wert durch
   schlechtere Antworten „gewinnen".
3. **CEL erhöht Input-Tokens.** CEL *injiziert* Kontext → Arm B hat **größere Prompts** → **pro Turn kann B
   teurer sein.** Der Nutzen (falls vorhanden) liegt auf **Task-Ebene** (weniger Turns bis fertig, weniger
   Nacharbeit). Pro Turn gemessen könnte CEL *verlieren*.
4. **Task-Mix-Bias.** Savings hängen massiv vom Task-Typ ab. Eine einzelne „X %"-Zahl versteckt riesige Varianz.
5. **Cache/Memory-Confound.** CEL-Cache macht den *zweiten* Lauf billiger → A/B-Reihenfolge verzerrt.
6. **Statistik.** Token-Verteilungen sind schief (long tail) → ein t-Test (Normalverteilungs-Annahme) ist falsch.
7. **Reproduzierbarkeit vs. Nicht-Determinismus.** Der Nutzer kann es nicht *bit-genau* reproduzieren (LLM),
   nur *statistisch* (gleiche Verteilung innerhalb CI). Das muss ehrlich benannt sein.
8. **Das Benchmark kann das Verkaufsargument WIDERLEGEN.** Wenn CEL auf Task-Ebene *nicht* spart, zeigt ein
   ehrliches Benchmark das. Genau **deshalb** existierte die erfundene Baseline: sie zeigt *immer* Savings.

**Synthese — das valide Design:**
- **Einheit = Task** (lösen-bis-fertig), gemessen als Summe input+output über alle Turns bis „done".
- **Zwei echte, lauffähige Arme:** A = `vibe_engineering` aus, B = an. Alles andere identisch (Modell, Task,
  Seed-kontrollierbares fix).
- **Qualitäts-Gate (Non-Inferiority):** jede Antwort wird gegen einen **objektiven Check** bewertet
  (Coding-Task: produzierter Test grün? — das *ist* das Reproduction-Gate-Signal; QA-Task: Antwort enthält
  die erwartete Fakten-Menge). **Savings zählen nur, wo Qualität(B) ≥ Qualität(A).** Sonst wird der Fall als
  „Qualitätsregression" separat ausgewiesen, nicht als Ersparnis verkauft.
- **`n` Wiederholungen pro Arm** (Default klein, nutzer-skalierbar) gegen Nicht-Determinismus.
- **Bootstrap-95%-CI** auf der Savings-Verteilung (nicht-parametrisch — passt zu schiefen Token-Daten) +
  **Mann-Whitney-U** für „ist der Unterschied signifikant, nicht zufällig?". **Nie eine nackte Zahl** — immer
  `X % ± Y % (95 % CI, n=N)`.
- **Pro-Task-Typ** aufgeschlüsselt + **cold vs. warm** Cache getrennt ausgewiesen.
- **Reales Token-Messen** aus der Worker-Usage, für beide Arme identisch.

---

## 3. Das Qualitäts-Gate (ohne das ist alles wertlos)

Pro Task ein **objektiver, automatischer** Check (keine menschliche Bewertung):
- **Coding-Task:** ein mitgelieferter Test/Assertion läuft gegen das Ergebnis → pass/fail. (Deckt sich mit dem
  Self-Learning-Anker, ADR-0373: *dasselbe* Reproduction-Signal.)
- **Faktenbasierte QA-Task:** die erwarteten Schlüssel-Fakten (Referenz-Set) müssen in der Antwort vorkommen →
  Score 0..1.
- **Regel:** ein Savings-Datenpunkt wird **nur gezählt**, wenn `quality(B) ≥ quality(A)`. Andernfalls landet er
  im Report als *„weniger Tokens, aber schlechter"* — transparent, nicht versteckt.

Damit kann das Benchmark **nicht** durch schlechtere Antworten „gewinnen".

---

## 4. Bounded Contexts (DDD — klare Verantwortungen, minimale Schnittstellen)

```
benchmark/token-savings/
├── Measurement Context   (run_benchmark.py) → führt A- und B-Arme real aus, erfasst RohRuns
│      emits: RunResult{task_id, arm, tokens_in, tokens_out, quality, cold_warm, ts}   (JSONL, roh)
├── Analysis Context      (stats.py)          → Bootstrap-CI, Mann-Whitney-U, Qualitäts-Gate, per-Typ
│      consumes RunResult[] → emits SavingsReport{per_type: {mean, ci_low, ci_high, p, n, quality_ok}}
└── Reporting Context     (report.py + README) → menschenlesbarer Report + Dashboard-Speisung
```
- Jeder Context besitzt seine Daten; Schnittstelle = die zwei Datensätze (`RunResult`, `SavingsReport`).
- Der **rohe** `RunResult`-JSONL ist die Beweiskette — nichts wird aggregiert-und-weggeworfen; der Nutzer
  kann die Rohdaten selbst nachrechnen.

---

## 5. Reproduzierbarkeit (der Nutzer fährt es selbst)

- **Versionierte Task-Suite** (`tasks/*.json`, eingecheckt): feste Task-Inputs + der objektive Check je Task.
  Der Nutzer läuft *dieselben* Tasks → dieselbe Verteilung (innerhalb CI).
- **Determinismus wo möglich:** gleiches Modell (aus der Tenant-Config gelesen, protokolliert), gleiche
  CEL-Config, `n` und Seed als CLI-Parameter, geloggt.
- **Ehrliche Grenze:** LLM-Nicht-Determinismus → **statistische**, nicht bit-genaue Reproduktion. Der Report
  nennt Modell-ID, `n`, Task-Suite-Version, CIs — alles, was zum Nachvollziehen nötig ist.
- **Kosten:** jeder Run ist ein echter LLM-Call (kostet Tokens). Default-Suite klein + `n` klein; der Nutzer
  skaliert bewusst. Das Benchmark ist **opt-in**, nie automatisch.

---

## 6. Ehrliches Reporting (was das Dashboard zeigen darf)
- **Nur der zuletzt *gemessene* Wert** mit CI: „**23 % ± 6 % gespart** (95 % CI, n=20, Suite v1, Modell …,
  qualitäts-gated)" — oder **„noch nicht gebenchmarkt"**. **Nie** die erfundene `1800×mult`-Zahl.
- Wenn das Benchmark **keine** signifikante Ersparnis findet (Mann-Whitney p ≥ 0.05 oder CI überschneidet 0):
  Dashboard sagt **„kein signifikanter Unterschied gemessen"** — nicht „0 %" als ob es Ersparnis wäre.
- Qualitätsregressions-Fälle werden **separat** gezeigt, nie in die Savings-Zahl gemischt.

---

## 7. LDD-Rahmen
Token = **Loss**. Das A/B misst `Δloss = A_tokens − B_tokens` pro Task, **gated** durch ein
Qualitäts-Signal (dasselbe Reproduction-Gate wie im Self-Learning-Konzept). Das ist eine echte
Loss-Messung mit Nicht-Inferioritäts-Bedingung — kein geschätzter Regler.

---

## 8. Alternativen erwogen
- **Baseline weiter schätzen, nur „geschätzt" labeln.** Ehrlicher als heute, aber verschenkt die Chance auf
  ein echtes Verkaufsargument — und ein Kunde will die Messung, nicht das Label.
- **Nur Tokens messen, ohne Qualitäts-Gate.** Verworfen — der Killer (Antithese #2): manipulierbar durch
  schlechtere Antworten.
- **Pro-Turn statt pro-Task.** Verworfen — misst CELs Nutzen an der falschen Stelle (Antithese #3).
- **t-Test.** Verworfen — Normalverteilungs-Annahme falsch für Token-Daten; Bootstrap + Mann-Whitney.

## 9. Ehrliche Grenzen
- **Input-Token-Erfassung muss verifiziert sein (kritisch).** Ein Smoke-Lauf zeigte den Console-Worker mit
  `input_tokens=0` (nur Output). CELs *Haupt*-Kosten ist der **größere Input-Prompt** — wird nur Output
  gemessen, ist CELs Kosten unsichtbar und die Savings **überschätzt**. Der Runner warnt laut
  (`input_captured:false`) und der Report darf dann **keine** Sparzahl behaupten. Diese Lücke betrifft auch
  das *bestehende* Dashboard (`chat_runtime.py:3727` defaultet Input auf 0). Fix: echte Input-Erfassung
  oder den assemblierten Prompt direkt tokenisieren.
- Das Benchmark **könnte zeigen, dass CEL nicht (überall) spart.** Feature, kein Bug: nur dann ist die
  verbleibende Sparzahl glaubwürdig.
- Statistisch, nicht bit-genau reproduzierbar (LLM). Der Report macht das explizit.
- Kostet echte Tokens beim Laufen → opt-in, bewusst skaliert.

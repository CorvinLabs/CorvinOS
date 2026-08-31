# Mess-Fix-Plan: Token korrekt erfassen (LDD-reviewt → BENCHMARK umgesetzt)

**Status:** Benchmark-Teil UMGESETZT + verifiziert · Dashboard-Teil neu-diagnostiziert (Follow-up) · **Datum:** 2026-08-18

## Review-Ergebnis + Umsetzungsstand (nach adversarialem Review, vor/während Bau)
- ✅ **Additivität bestätigt korrekt** (kein Doppelzählen). Das Repo hat den Kanon schon inline
  (`operator/orchestration/tde/worker_ipc.py:311`, `tde_engine.py:435`). → **Ein geteilter Helper**
  `core/learning/token_accounting.py` gebaut + getestet (5/5), statt einer 3. Inline-Summe.
- ✅ **Benchmark-Runner umgesetzt:** erfasst jetzt alle vier Klassen; Roh-JSONL trägt sie; Guard keyt auf
  summierten Input (nicht mehr `input_tokens=2>0`-Falschpass). **Echter Lauf verifiziert.**
- ✅ **Verifizierter Befund (die Dialektik hatte recht):** korrekt gemessen braucht CEL auf trivialen
  Single-Turn-Tasks **mehr** Tokens (A=62.896, B=63.263 → **−0,6 %**). Das Tool sagt korrekt
  „no saving — do not claim". Der 38k-`cache_creation` ist der System-Prompt (beide Arme); CEL fügt ~367 hinzu.
- 🔴 **Dashboard-Diagnose war FALSCH (Review-Finding, korrigiert):** der **native** Pfad zeichnet **gar
  nichts** auf (`record_turn_metrics` sitzt nur im Hermes-Zweig, `chat_runtime.py:3723`); die Store-API
  (`token_measurement_hook.py:227`) nimmt **keine** Cache-Felder; die Subsystem-Zahlen (50/100/25) sind
  erfunden. → Der Dashboard-Fix ist **kein Einzeiler**, sondern: (a) native Turns überhaupt aufzeichnen,
  (b) Cache-Felder durch `record_turn_metrics → DB-Schema` fädeln, (c) die Fake-Subsystem-Zahlen ersetzen.
  **Separates, größeres Follow-up** — hier nur ehrlich dokumentiert, nicht als „ein Feld" verkauft.
- 🟡 **suite-v1 misst CELs Worst Case:** 100 % Single-Turn, frische Session/Rep → zahlt immer
  `cache_creation`, amortisiert nie. Das cold/warm-Feld war konfundiert → **entfernt.** Amortisierung
  (CELs Best Case) braucht Multi-Turn-Tasks (Follow-up).
- 🟡 **Offen (Finding 4):** ist das terminale `usage` über Tool-Runden kumulativ? Für suite-v1 (kein Tool)
  egal; **vor** einem Tool-nutzenden Suite/Dashboard-Fix zu verifizieren.

---

**Status:** Proposal (Review vor Implementierung) · **Datum:** 2026-08-18
**Auslöser:** Ein echtes Turn-Usage zeigt `input_tokens=2` aber `cache_read=24433` + `cache_creation=38060`.
Die Console (`chat_runtime.py:3727`) und das Benchmark lesen nur `input_tokens` → **~99,997 % des Inputs
werden ignoriert.** Jede bestehende Token-Zahl ist dadurch falsch.

---

## 1. Was „richtig messen" bedeutet (die vier Komponenten)

Ein Anthropic-Usage hat **vier** Token-Klassen, nicht zwei:
| Feld | Was | Preis-Klasse |
|---|---|---|
| `input_tokens` | frischer, ungecachter Input | 1× |
| `cache_creation_input_tokens` | Input, der neu in den Cache geschrieben wird | ~1,25× |
| `cache_read_input_tokens` | Input aus dem Cache gelesen | ~0,1× |
| `output_tokens` | generierter Output | ~5× (modellabhängig) |

**Roher Input = `input_tokens + cache_creation + cache_read`.** Alles andere unterzählt.

---

## 2. Dialektik — die Zahl, die „richtig messen" liefern wird

**These:** Alle Input-Komponenten summieren + Output = Total-Tokens; darauf Savings messen. Fix = 3 Felder addieren.

**Antithese (der unbequeme Teil):**
1. **Roh gezählt wird CEL wahrscheinlich MEHR Tokens brauchen, nicht weniger.** CEL *injiziert* Kontext
   (hier: 38k cache_creation!). Native (CEL aus) hat einen kleinen Prompt. Auf **roher Token-Zahl** verliert
   CEL fast sicher. → „Richtig messen" wird das Verkaufsargument „weniger Tokens" vermutlich **widerlegen.**
2. **Kosten ≠ Zahl wegen Caching.** cache_read ist ~0,1×, cache_creation ~1,25×. Turn 1 zahlt teures
   cache_creation, Folge-Turns billiges cache_read. Über eine **Multi-Turn-Task** kann CEL *günstiger* sein,
   obwohl es *mehr* rohe Tokens verarbeitet. Das echte Argument ist wohl **Kosten pro Task**, nicht Roh-Zahl.
3. **Baseline cached auch.** Native Claude Code nutzt ebenfalls Prompt-Caching (System-Prompt). Beide Arme
   müssen cache-aware + apples-to-apples verglichen werden.
4. **Preise sind modellabhängig + wären eine neue Fiktion, wenn hardcodiert.** → Rohe Komponenten messen und
   Kosten NUR mit einer echten Preis-Quelle (Config/Preisliste) berechnen, sonst nur Roh-Zahlen zeigen.
5. **Cold vs. warm.** cache_creation (teuer) fällt beim ERSTEN Turn an; eine lange Session amortisiert es.
   Ein Single-Turn-Task überzeichnet cache_creation → Task-Ebene + cold/warm trennen.

**Synthese — was tatsächlich zu bauen ist:**
- **Alle vier Komponenten getrennt erfassen**, nie vorzeitig zu einer Zahl kollabieren.
- **Zwei ehrliche Metriken berichten, nicht eine:**
  - **Roh-Token-Zahl** (Summe aller Input-Klassen + Output) — die wörtliche „verarbeitete Tokens". Erwartung:
    CEL ist HÖHER. Ehrlich so sagen.
  - **Kosten** — nur mit echten Preis-Multiplikatoren pro Klasse (aus Config/Preisliste, nie erfunden); sonst
    die rohen Komponenten ausweisen und den Operator seine Preise anlegen lassen.
- **Das ehrliche Headline ist vermutlich NICHT „weniger Tokens"**, sondern „geringere Kosten durch Caching /
  weniger Turns" — oder gar keine Ersparnis. Das Benchmark misst, welches.

---

## 3. Implementierungs-Schritte (nach Review)

**S1 — Benchmark-Runner (`run_benchmark.py`, `_run_one`):** alle vier Komponenten aus dem Usage lesen und pro
Run speichern: `fresh_input, cache_creation, cache_read, output`. `tokens_total_raw = fresh+creation+read+output`.
Die Roh-JSONL bekommt alle Komponenten (Beweiskette).

**S2 — Report:** zwei Blöcke — **Roh-Token-Savings** (mit CI, ehrlich auch wenn negativ) **und** eine
**Komponenten-Tabelle** (fresh/creation/read/output je Arm). Kosten NUR wenn eine Preis-Quelle vorliegt;
sonst „Kosten: rohe Komponenten unten, Preise selbst anlegen". Warnung entfällt (Input jetzt erfasst).

**S3 — Dashboard-Bug benennen/fixen:** `chat_runtime.py:3727` (+ der token_metrics-Pfad) liest nur
`input_tokens`. Derselbe Fix: alle Komponenten summieren. **Separat committen**, klar als „bestehende Zahlen
waren falsch" markiert. (Scope-Entscheidung im Review: nur benennen oder gleich fixen?)

**S4 — Verifikation:** ein echter A/B-Lauf, der zeigt: (a) Input jetzt > 0 und plausibel (~60k), (b) die
Komponenten-Tabelle stimmt mit dem rohen Usage überein, (c) der Report macht die Roh-vs-Kosten-Unterscheidung
explizit.

---

## 4. Was der Review klären muss (offene Punkte)
- **O1:** Ist die Roh-Token-Zahl überhaupt das, was der Nutzer verkaufen will — oder Kosten/Turns? (Antithese #1/#2)
- **O2:** Dashboard-Fix jetzt mitziehen oder separat? (Blast-Radius: es ändert *alle* historischen Zahlen.)
- **O3:** Preis-Multiplikatoren — woher (Config? Modell-Katalog?) ohne neue Fiktion?
- **O4:** Ist `cache_creation` fair der Vibe-Seite anzulasten, wenn der Cache über Turns/Sessions geteilt wird?

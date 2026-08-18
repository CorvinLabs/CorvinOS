# Konzept v3: Ehrliches Self-Learning — was ein Einzelnutzer-AI-OS wirklich messen kann

**Status:** Proposal (v3, nach adversarialem Design-Review) · **Datum:** 2026-08-18 · **Autor:** Claude (Opus 4.8) für shumway
**Supersedes:** v1 (implizite Signale) · v2 (LDD-Loss als Anker) — **beide vom Review widerlegt, siehe §0.**
**Verwandt:** ADR-0269/0284/0285 (Grades/Promotion), ADR-0179 (Reproduction-Gate), ADR-0372 (Weg A), ADR-0373/0374 (v3-korrigiert)
**Leitsatz:** *Ehrlich messen, was wirklich messbar ist. Nichts auto-ändert Verhalten. Der Operator disponiert.*

---

## 0. Was der adversariale Review widerlegt hat (und warum v3 kleiner + ehrlicher ist)

Zwei frühere Fassungen scheiterten an einem Design-Review gegen den echten Code:

- **v2s Anker ist eine Fiktion (Finding H1).** „LDD misst pro Turn Loss, das lesen wir aus" ist **falsch**:
  `.ldd/trace.log` ist **LLM-von-Hand-geschrieben** (`ldd_trace append --loss-norm` ist ein *Input*, keine
  Messung), granular auf **Coding-Task-Iterationen** (nicht Console-Turns), `gate_on_activity: true`, und
  **kein CorvinOS-Code liest es**. Das Reproduction-Gate (`aco/reproduction.py`) läuft nur im
  Maintainer-Self-Heal, nie pro Turn. Der TDE-Loss-Tracker ist TDE-intern (Default aus).
  → **Es gibt heute kein automatisches, objektives, positives Pro-Turn-Qualitätssignal in der Console.**
- **v1s Verhaltens-Signal ist manipulierbar + unvalidierbar** (Recommender-Falle) — bleibt verworfen als
  Primärsignal.

**Ehrliche Konsequenz (v3):** Man kann für einen Einzelnutzer *nicht* umsonst ein positives Qualitätssignal
aus dem Nichts ziehen. Was real + frei ist, ist **negativ** (Fehler-Erkennung). Ein echtes *positives* Signal
muss **gebaut** werden und ist auf **verifizierbare Arbeit** (Code) begrenzt. Und: **nichts davon darf
Verhalten automatisch ändern** — das löst gleich vier MEDIUM-Findings auf einen Schlag.

---

## 1. Die drei ehrlichen Signal-Klassen

| Klasse | Existiert? | Wert | Einsatz in v3 |
|---|---|---|---|
| **Frei + negativ** — `rc≠0`, Healing gefeuert, Artefakt-Install-Fehler | **ja, pro Turn** (`chat_runtime.py:6361/6409`, `aco/htrace.py`) | „harter Fehler passierte" | **Abwerter** + Anzeige. Der einzige *freie* Pro-Turn-Signalstrom. |
| **Gebaut + positiv** — Post-Turn-Verifikation eines Coding-Turns (produzierte Tests/Build laufen lassen) | **nein — zu bauen**, begrenzt auf Code | „die Arbeit hat objektiv funktioniert" | **Anker**, aber nur für verifizierbare Turns. Neues Infra (Reproduction-Gate-Muster auf Console-Coding-Turns erweitert). |
| **Mensch** — Operator-Override (👎😐👍, Weg A) | ja | die Ground-Truth | **EINZIGER Promoter** (bleibt absolut). Kalibrier-Anker. |

**Was es NICHT gibt und v3 nicht vortäuscht:** ein freies positives Signal für Nicht-Coding-Turns. Reine
Konversation liefert höchstens die schwache CoT-Selbsteinschätzung — v3 gewichtet sie mit **0**, bis ein
echter Anker sie kalibriert. Self-Learning ist am stärksten für Engineering-Arbeit, praktisch abwesend für
Small-Talk. Das ist ehrlich so und wird nicht überverkauft.

---

## 2. Die harte Invariante (löst H1-Folge, M3, M5)

> **Kein automatisches Signal ändert je Verhalten. Es informiert nur die Anzeige und *schlägt dem Operator
> vor*. Verhalten ändert allein der Operator.**

Damit lösen sich mehrere Review-Findings *strukturell*, nicht per Behauptung:
- **M3 (Operator-only-Promotion aufgeweicht):** entfällt — `__signal__`-Grades promoten **nie**, unter keiner
  Bedingung. `_PROMOTING_GRADERS = {"operator"}` bleibt unangetastet. „Kalibriert → promotet" wird gestrichen.
- **M5 (Manipulations-Sperre nur behauptet):** wird strukturell — wenn **nichts** die Confidence
  automatisch optimiert (kein Auto-Gate), kann kein Signal Optimierungsziel sein. Die Sperre ist die
  Abwesenheit eines Auto-Optimierers, nicht ein Versprechen.
- **M1/M2/M4 (Konsument existiert nicht/passt nicht/Cold-Start):** entfallen — der „Konsument" ist der
  **Operator**, dem verdiente/abgewertete Confidence als **Empfehlung** angezeigt wird („Stage X war in 5
  fehlerhaften Turns — abwerten?"). Kein Auto-Gate → kein Henne-Ei, kein θ-Problem, kein
  attention_budget-Umbau.

Auto-Gating von Verhalten (z. B. Opt-in-Stages überspringen) ist **explizit außerhalb v3-Scope** — es käme
frühestens, wenn ein kalibrierter Anker über G5-Volumen *und* ein sauberer Cold-Start-Seed existieren, als
eigene, separat gereviewte Entscheidung.

---

## 3. Was v3 konkret baut (klein, ehrlich)

1. **Frei-negativ verdrahten (P1).** Das pauschale `record_turn_outcome(..., True)` wird ersetzt: bei
   `rc≠0` / Healing / Artefakt-Fehler ein **Abwerter**-Grade (`grader="__signal__"`, non-promoting); sonst
   **nichts** (kein pauschales True mehr). Sofort ehrlicher als heute (Turn-Zähler weg).
2. **Coding-Verifikation als echter Anker (P2, opt-in, das eigentliche Neue).** Für einen Turn, der
   Code/Tests produziert hat: nach dem Turn die produzierten Tests/den Build **tatsächlich laufen lassen**
   (sandboxed, wie das Reproduction-Gate) → objektives pass/fail → positiver `__signal__`-Grade, **task-**
   (nicht turn-)attribuiert (löst M6: der grüne Test gehört der ganzen Coding-Aufgabe, nicht einem Turn).
3. **Anzeige + Empfehlung, kein Auto-Verhalten (P3).** Weg-A-Baum zeigt die abgewertete/verdiente Confidence
   + Evidence-Split; wo ein Muster klar ist, eine **Operator-Empfehlung** (er entscheidet).
4. **Kalibrierung nur wo ein Anker existiert (P4).** Verrauschte Signale bekommen Gewicht nur, wenn sie den
   *Coding-Verifikations-Anker* + Operator-Override vorhersagen. Da Anker nur auf Coding-Turns existiert, wird
   ehrlich benannt: außerhalb Coding bleibt alles Gewicht 0 (H2 akzeptiert, nicht wegdefiniert).

---

## 4. Attribution (M6-korrigiert)
- **Task-Level, nicht Turn-Level**, für das Verifikations-Signal: eine Coding-Aufgabe = mehrere Turns bis der
  Test grün ist; das Signal kreditiert die **Aufgabe** und die darin genutzten Stages/Tools, nicht den
  Zufalls-Turn N+2.
- **Stage-lokal, nur wo ein direkter Link existiert:** das geforgte Tool lief fehlerfrei; die injizierte
  Quelle wurde in der (verifiziert-erfolgreichen) Antwort referenziert. Sonst keine Per-Stage-Attribution.
- Statistische Attribution: gestrichen als Einzel-Instanz-Feature; nur als G5-Volumen-Ausblick genannt.

---

## 5. Compliance
Content-free: frei-negative Signale sind Metriken/Fingerprints (`htrace.py`, Allowlist); die Coding-
Verifikation liest Test-Exit-Codes, keinen Nutzer-Text. Fail-closed.

---

## 6. Ehrliche Grenzen (v3 verkauft nichts über)
- **Positives Signal nur für Code.** Nicht-Coding-Turns lernen praktisch nicht (nur Abwertung bei hartem
  Fehler). Das ist die reale Decke für einen Einzelnutzer.
- **Verifikation kostet.** Tests nach jedem Coding-Turn laufen zu lassen ist nicht gratis — deshalb opt-in +
  nur wenn der Turn Tests/Build produzierte.
- **Der 90/10-Punkt bleibt stehen:** für einen Einzelnutzer ist Weg-A (Operator-Override + ehrliche Anzeige)
  bereits der Großteil des Werts. v3 rechtfertigt sich nur durch (a) die *freie* Abwertung (P1, billig) und
  (b) die *opt-in* Coding-Verifikation (P2, echtes Positiv-Signal für die Turns, wo es objektiv geht). Alles
  Grandiose (LDD-Loss-Anker, Auto-Attention-Budget) hat der Review als nicht-existente Infrastruktur entlarvt.

---

## 7. Alternativen erwogen (nach Review)
- **v2 (LDD-Loss-Anker).** Verworfen — Anker existiert nicht pro Turn (H1).
- **Auto-Gating von Stages über Confidence.** Verworfen für v3 — Cold-Start-Henne-Ei (M4), Konsument-
  Mismatch (M1/M2), Sicherheits-Aufweichung (M3), Manipulations-Loop (M5). Später, separat, wenn überhaupt.
- **Gar nichts bauen (nur Weg A).** Ernsthaft erwogen und teils angenommen: v3 ist bewusst auf die zwei
  Dinge geschrumpft, die real + billig bzw. real + opt-in sind. Der Rest ist gestrichen.

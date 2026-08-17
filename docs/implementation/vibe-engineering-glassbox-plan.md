# Implementierungsplan: Glass Box Vibe Engineering (G1–G5)

**Status:** Proposal · **Datum:** 2026-08-17 · **Autor:** Claude (Opus 4.8) für shumway
**Methode:** LDD Architect-Mode L3, `creativity=standard` (inventive nicht acknowledged; harte Legacy-Constraints)
**Konzept-Grundlage:** [`docs/concepts/vibe-engineering-glassbox-concept.md`](../concepts/vibe-engineering-glassbox-concept.md)
**LDD-Review:** dialektischer Pass (Phase 4) + adversarialer Zweitblick gegen den echten Code.
**Review-Ergebnis:** *„Strategisch solide, faktisch nicht wie geschrieben baubar — erst revidieren."* 3 HIGH-Defekte
gefunden und **in diesem Dokument bereits korrigiert** (v2): G4 rief `record_turn_outcome` mit falscher Signatur
(F1); G1-Origin-Mapping ist aus `/prompt` nicht fein ableitbar (F2); Assembly-Persistenz ist Bridge-only, Glass
Box wäre auf Console-Turns leer (F3). Plus MEDIUM (PII-Backstop überverkauft, F4) und Naming-Fixes. Alle
Korrekturen sind inline als „Review-korrigiert (Fn)" markiert. **Verdikt nach Revision: baubar.**

**Implementierungsstand (2026-08-17):**
- **G1 — GEBAUT & E2E-verifiziert** (ADR-0368). Backend: `persist_assembly` im Console-Pfad (F3-Fix), verifiziert
  durch echten `stream_turn`-Turn (final_prompt 11 439 Zeichen). Frontend: `GlassBoxPrompt` — Basis-Prompt vs.
  CEL-Block-Split + Sektions-Legende, Playwright-verifiziert. Kein neuer Flag (lebt in der `vibe_engineering`-Page).
- **G5 — KERN GEBAUT & getestet, Transport ships-dark** (ADR-0369). Merge-Engine `core/cross_device/tenant_sync.py`
  (Grade-Array-Union, JSONL-Union, LWW, PII-Backstop) — 4/4 Tests. Route `POST /sync` mit Auth+CSRF+Flag-Gate,
  HTTP-verifiziert (Flag-off → „disabled"). Flag `cross_device_sync` default-off. Frontend Port-Probing entfernt.
  **Offen (nächstes Inkrement, hinter default-off Flag):** live `git clone/pull/push` + GPG-Wiring gegen ein echtes
  Remote — braucht Operator-PAT im Vault + `sync_remote`-Config.
- **G2/G3/G4 — geplant, noch nicht gebaut.** (G2 Inspector-Entfernung + Overview; G3 Learning Ledger + Grade-UI;
  G4 Outcome-Wiring.) Nebenbei erledigt: der verwaiste `learning.tsx`-Build-Blocker (doppelter Default-Export).

---

## Phase 1 — Constraint-Tabelle

Jede Anforderung aus dem Konzept + jede bindende Repo-Regel, die den Plan formt.

| # | Anforderung / Constraint | Ziel (messbar) | Quelle |
|---|---|---|---|
| C1 | „Was geht in die Worker-Engine" sichtbar | `final_prompt` als erstklassige Ansicht, ≤ 3 Klicks von Turn-Liste | User; `vibe_engineering.py:207` |
| C2 | Herkunft der Prompt-Teile (Review-korrigiert) | **CEL-Block** vs. Basis-Prompt getrennt + grobe Sektions-Liste mit Rücklink; feine Per-Stage-Spans nur mit Backend-Arbeit (s. G1) | User („auditierbar"); Review F2 |
| C13 | Assembly-Persistenz gilt heute nur für Bridge-Turns | `persist_assembly` wird auch aus dem Console-Chat-Pfad aufgerufen, sonst zeigt Glass Box `found:false` für Console-Turns | Review F3 (`adapter.py:3373` vs `chat_runtime.py:4686`) |
| C14 | End-Nutzer-PII (Dritt-Daten) ≠ Operator-PII | Memory/Skill-Bodies können aus End-Nutzer-Konversationen abgeleitete Dritt-PII enthalten — Sync behandelt das als Egress-Ereignis (GDPR Art. 6/32) | Review F4; Compliance-Baseline |
| C3 | Redundanz Context Pipeline ↔ Vibe Inspector auflösen | genau **eine** Trace-Oberfläche; `/traces` einmal gelesen | User; Scan P1 |
| C4 | Learning verständlich | 3 Systeme unter **einem** „Learning Ledger"; Stage-Grade-UI existiert | User; Scan P3/P4 |
| C5 | Lern-Kreislauf schließen | `record_turn_outcome` hat ≥ 1 Production-Caller | Scan P4; ADR-0269 Ph-4b |
| C6 | Cross-Device-Sync real statt Stub | `POST /sync` führt echten git-merge aus + Rückgabe-Ergebnis | User; Scan P5 |
| C7 | Sync-Sicherheit (GDPR) | opt-in default-off, tenant-isoliert, PAT im Vault, kein hardcoded Repo | Compliance-Baseline; Scan P5 |
| C8 | Ship-Dark | jedes neue Feature hinter default-**false** Flag, in Settings→Features toggle-bar | CLAUDE.md Feature-Flags |
| C9 | ADR-Sync | jede Struktur-/Endpoint-/Protokoll-Änderung trägt eigenes ADR (0264-Frontmatter) | CLAUDE.md Code/Docs-Sync |
| C10 | Audit-Integrität unangetastet | Glass Box liest nur, schwächt Hash-Chain nicht, kein PII in Labels | Compliance-Baseline |
| C11 | E2E-Wiring-Proof | jeder neue Endpoint/Route/UI hat ≥ 1 realen Call-Site + 1 E2E durch die echte Transport-Grenze | CLAUDE.md; `e2e-wiring-proof` |
| C12 | Begriff CEL, nicht CIES | UI + Docs sagen durchgängig „CEL (Context Engineering Layer)" | Scan §5 |

**Explizit benannte Unsicherheiten (nicht still gefüllt):**
- **U1 (aufgelöst nach Review):** GPG beim Tenant-Sync ist **Pflicht** (mandatory) — der PII-Backstop ist best-effort, also trägt die Verschlüsselung die Garantie. Operator kann im G5-ADR final bestätigen.
- **U2:** Merge-Strategie bei echtem Skill-/Grade-Konflikt (Grades = Array-Union verlustfrei; Skills/Memory = LWW mit Kollisions-Report + UI-Auflösung) — Plan schlägt Default vor, Operator kann kippen.
- **U3:** `record_turn_outcome` wird aus dem Chat-Turn-Abschluss gespeist (`success = kein Fehler`); Task-Erfolg aus TDE/ACS ist die benannte reichere Alternative.

---

## Phase 2 — Non-Goals (Scope-Grenzen)

1. **Kein neues Audit-System.** Die Glass Box *rendert* nur die bereits hash-chain-verankerten `/prompt`/`/explain`/`/forged`-Daten. Keine neue Persistenz, kein neuer Event-Typ.
2. **Kein Backend-Merge der drei Learning-Systeme.** CEL-Grades, TreeOfThoughts-Nodes und ULO bleiben getrennte Stores/Backends; nur die **UI** führt sie an einem Ort zusammen.
3. **Kein neuer Sync-Server / keine Cloud-Infra.** Tenant-Sync nutzt Git als Transport gegen ein bestehendes Remote. Kein Corvin-Cloud-Relay in diesem Scope.
4. **Keine Änderung an den CEL-Stages selbst.** Reihenfolge, Contract (`ContextStage`), die 8 Stages bleiben unverändert; wir bauen Observability *darüber*, nicht hinein.
5. **Kein Redesign von „Your Talent".** Bleibt als separates Ergebnis-Dashboard; wir verlinken es nur in den Trace.
6. **Keine Echtzeit-Streaming-Ansicht.** Der Trace bleibt Pull/Refresh (15 s), kein WebSocket-Push in diesem Scope.

---

## Phase 3 — Drei Kandidaten auf der load-bearing Achse

Die riskanteste, offenste Entscheidung des gesamten Plans ist **G5 (Cross-Device Tenant-Sync)** — G1–G4 sind durch das Konzept weitgehend determiniert (UI-Arbeit über existierenden Endpoints). Die load-bearing Achse ist daher **der Sync-Transport & das Merge-Modell**. Drei Kandidaten:

### Kandidat A — Git-basierter Zustand-Sync (Konzept-Vorschlag)
Der Tenant-Lernzustand (skills, grades, learning-events, memory, panels) ist ein lokales Git-Repo; `POST /sync` macht `pull → typ-spezifischer merge → push` gegen ein privates Remote (GitHub o. Ä.).
**Gewinnt:** History/Diff/Rollback geschenkt · offline-first · keine neue Infra · passt zu „läuft-lokal".
**Verliert:** Git-Konflikt-Handling für Nicht-JSONL-Dateien ist Aufwand · PAT-Verwaltung · kein Live-Push (nur bei Sync-Trigger).

### Kandidat B — A2A-Peer-Aggregation ausbauen (existierendes `multi_instance_sync.py`)
Statt Git: die bereits gebaute A2A-JSON-RPC-Peer-Schicht erweitern, sodass Instanzen ihren Lernzustand direkt Peer-to-Peer austauschen.
**Gewinnt:** Transport existiert schon (signiert, session/CSRF-gated) · kein GitHub nötig · live.
**Verliert:** beide Peers müssen gleichzeitig online + gepaart sein (kein async über Zeit) · keine History/Rollback · Pairing ist Konsolen-verwaltet (Reibung) · skaliert schlecht über > 2 Geräte.

### Kandidat C — Cloud-Relay-Hub (Corvin-Logs/Cloud als Sync-Broker)
Eine zentrale Cloud-Komponente hält den kanonischen Tenant-Zustand; Instanzen pushen/pullen gegen sie.
**Gewinnt:** echtes Multi-Device async · zentrale Konfliktauflösung · kein Peer-Online-Zwang.
**Verliert:** **neue Server-Infra** (widerspricht Non-Goal 3) · GDPR-Blast-Radius (Lernzustand verlässt die Maschine zu *uns*) · Betriebskosten · widerspricht der „deine Maschine"-Haltung am stärksten.

---

## Phase 4 — Scoring + dialektische Auswahl

Bewertung gegen die 6-Dimensionen-Rubrik (1 = schwach, 5 = stark):

| Dimension | A (Git) | B (A2A-Peer) | C (Cloud-Relay) |
|---|---|---|---|
| Requirements-Coverage (C6/C7 real async sync) | 5 | 3 | 4 |
| Boundary-Clarity (klarer Contract) | 4 | 4 | 3 |
| Evolution-Paths (mehr Geräte, später) | 5 | 2 | 4 |
| Dependency-Explicitness | 4 | 4 | 2 |
| Test-Strategy (E2E fahrbar ohne 2. Live-Host) | 5 | 2 | 3 |
| Rollback-Plan (Zustand wiederherstellbar) | 5 | 2 | 3 |
| GDPR/Compliance (Non-Goal 3 + Blast-Radius) | 5 | 4 | 1 |
| **Summe (max 35)** | **33** | **21** | **20** |

**Gewinner: Kandidat A (Git-Sync), 33/35 — dezisiv (Δ ≥ 12 zum Zweiten).**

**Dialektik auf A:**
- **These:** Git-Sync gewinnt, weil es History/Rollback/Offline geschenkt liefert, keine neue Infra braucht und den GDPR-Blast-Radius minimal hält (Zustand bleibt beim Operator, Remote ist sein eigenes privates Repo).
- **Antithese (feindlicher Reviewer):** „Git ist ein *Datei*-Transport, kein Zustands-Merge. Zwei Instanzen, die parallel dieselbe `grades.json` ändern, produzieren einen Git-Merge-Konflikt, den ein Endnutzer nie auflösen will. Und ein `git push` von potenziell PII-haltigem Lernzustand zu GitHub ist genau die Art unbeabsichtigter Egress, gegen die die Compliance-Baseline steht — ‚privates Repo' ist keine technische Garantie."
- **Synthese (geschärft, Antithese eingebaut):**
  1. **Typ-spezifischer Merge statt Git-Textmerge.** Der Sync fasst NICHT `git merge` auf Arbeitsdateien an. Er liest beide Seiten *strukturiert* und merged nach Datentyp: Learning-Events (JSONL) = Union+Sort; **Grades = Union der `grades[]`-Arrays** (verlustfrei — `ce_stage_grades.json` hält pro Stage die volle Grade-Liste; `n_grades`/`mean_score` werden aus dem vereinigten Array *neu berechnet*, nicht addiert); Skills/Memory = Last-Write-Wins per `mtime` mit Kollisions-Report. Git ist nur **Transport + History**, nicht der Merge-Algorithmus. Damit verschwindet der „Endnutzer löst Git-Konflikt"-Angriff.
  2. **Egress ist opt-in & so-gut-wie-möglich abgesichert — ehrlich benannt.** Default-off Feature-Flag (C8); vor dem ersten Push ein expliziter Consent-Schritt; **GPG-Verschlüsselung ist Pflicht** (U1 → aufgelöst zu *mandatory*, nicht optional), entschlüsselbar nur mit Operator-Key. Der `_assert_no_raw_pii`-Backstop scannt die Payload — **aber ehrlich: er ist NICHT fail-closed-äquivalent zum Telemetrie-`_assert_safe`.** Telemetrie validiert eine *geschlossene Enum-Allowlist* (strukturell dicht); Lernzustand ist freier Text (Memory, Skill-Bodies, Grade-`notes` bis 200 Zeichen) — ein Shape-Scanner darüber ist best-effort, keine strukturelle Garantie. Der Backstop reduziert Risiko, ersetzt aber nicht die GPG-Pflicht + Consent. **Dritt-PII (C14):** aus End-Nutzer-Konversationen abgeleiteter Inhalt ist keine „Operator-eigenen-Daten"-Sache — der G5-ADR muss Operator-PII von Dritt-PII trennen und den Egress als GDPR Art. 6/32-Ereignis behandeln, nicht als bloßes „eigene Maschine → eigenes Repo".
  3. **A2A bleibt, aber in seiner Rolle.** Kandidat B wird nicht weggeworfen — `multi_instance_sync.py` bleibt für *Live-Peer-Metriken* (was es heute halb tut). Klare Trennung: **Git = Zustand über Zeit, A2A = Metriken live.** Das entfernt die konzeptionelle Dopplung (Scan P5), ohne bestehende Arbeit zu verlieren.

---

## Phase 5 — Deliverable: der phasierte Implementierungsplan

Fünf eigenständig lieferbare Phasen. Reihenfolge nach Nutzer-Nutzen (G1 zuerst — beantwortet die dringendste Frage; G5 zuletzt — größter Bau, eigenes Sicherheits-ADR).

### G1 — Glass-Box Prompt-Reveal  *(ADR: „Glass-Box Context Reveal", UI-Kontrakt + Assembly-Persistenz)*
**Ziel:** `final_prompt` von vergrabenem Modal-Tab zur erstklassigen Ansicht. **Review-korrigiert (F2/F3):** `final_prompt` ist EIN konkatenierter String; `sections` sind Retrieval-*Kanäle* (memory/adrs/skills/approach/blockers/synthesis/tools), NICHT die 8 CEL-Stages, und es gibt **keine Offsets/Spans** vom String zurück zur Sektion. `stages[].sources` liegt auf `/traces`, nicht auf `/prompt`. Origin-Annotation ist daher **zweistufig gescoped**:
- **v1 (aus vorhandenen Daten, kein Blocker):** die sauber lokalisierbare Grenze anzeigen — **CEL-Block vs. Basis-System-Prompt** (`final_prompt = sys_prompt + "\n\n" + cel_text`, `adapter.py:3369`) — plus die grobe `sections`-Liste als Legende. Rücklink pro Sektion via Cross-Referenz auf den `/traces`-`cel.decision`-Record desselben Turns (nicht `/prompt`).
- **v2 (Backend-Arbeit, im ADR benannt):** feine Per-Stage-Spans erfordern, dass `persist_assembly` beim Rendern die Sektions-Offsets im `final_prompt` mit-persistiert (oder ein echtes `GET /prompt/{turn}?annotated=1`). **Also NICHT „kein Backend nötig"** — v2 ist Backend-Scope.
- **Blocker-Fix (C13/F3):** `persist_assembly` wird heute NUR aus dem Bridge-Pfad aufgerufen (`adapter.py:3373`); der Console-Chat (`chat_runtime.py:4686`) ruft es nie → Glass Box zeigt `found:false` für Console-Turns, obwohl sie in `/traces` gelistet sind. **G1 muss `persist_assembly` in den Console-Chat-Pfad einhängen** (fire-and-forget, Muster wie `_install_generated_panels`), sonst ist die Ansicht leer für genau die Oberfläche, auf der sie lebt.
- **Frontend** `pages/vibe-engineering.tsx`: `TurnGlassBox` (aus `PromptInspectorModal` extrahiert + erstklassig).
- **Tests:** E2E (Playwright) — realer **Console**-Chat-Turn → Turn-Liste → „Glass Box" → assert `final_prompt`-Text erscheint (nicht `found:false`) + CEL-Block visuell abgesetzt + Sektions-Legende + Rücklink klickbar (C11).

### G2 — Vibe Inspector entfernen, Overview einführen  *(kein ADR — Refactor + Löschung)*
- **Löschen:** `public/external-panels/vibe-inspector/index.html`; Registry-Eintrag `vibe-inspector` (`registry.tsx:46-57`); Nav-Item (`layout.tsx`); tote `group:"observability"`-Metadata.
- **Neu** `pages/vibe-overview.tsx`: Fluss-Diagramm (Turn → 8 CEL-Stages → Assembly → Engine → Outcome → Learning) + die Aggregat-Kacheln aus dem alten Inspector (aus `/traces` berechnet) + „So liest du einen Trace"-Erklärkasten. Nutzt das Design-System (kein hand-gerolltes Dark-Mode).
- **Tests:** E2E — `/app/vibe-inspector` liefert 404/Redirect; `/app/vibe-overview` rendert Aggregate + Diagramm. Bestehende Inspector-E2E-Tests entfernen/ersetzen.

### G3 — Learning Ledger + Stage-Grade-UI  *(ADR: „Learning Ledger + Operator Stage-Grading", neuer Endpoint-Caller)*
- **Neu** `pages/learning-ledger.tsx`, drei Abschnitte:
  1. **Stage-Vertrauen (CEL-Grades):** liest neuen `GET /vibe-engineering/grades` (Reader über `grades.py`-Store); Grade-Button → neuer `POST /vibe-engineering/grades/{stage}` (CSRF) ruft die **echte** Funktion `grade_stage(tenant_id, stage_id, score, notes="", grader="operator")` (`grades.py:66` — Review-korrigiert, `record_operator_grade` existiert nicht). `grader="operator"` ist der einzige promotende Grader (`_PROMOTING_GRADERS`).
  2. **Muster (TreeOfThoughts):** hängt das **verwaiste** `components/LearningDashboard.tsx` hier ein (importiert von `pages/learning.tsx:7`, aber die Kette ist nicht geroutet → tot). Endpoints `GET/POST /v1/console/learning/{nodes,grade,note}` (`routes/learning.py:57/88/116`) existieren.
  3. **Ziele (ULO):** `learning-objectives.tsx` bekommt seinen Nav-Platz hier.
- **Backend** `routes/vibe_engineering.py`: `GET /grades` + `POST /grades/{stage}` (dünn über `grades.py grade_stage`).
- **Flywheel-Verlauf:** kleine Zeitreihe „geforgt → Grade → Confidence" aus den vorhandenen `cel.decision`-Records.
- **Tests:** E2E — Grade abgeben → `GET /grades` reflektiert `n_grades+1`; verwaistes Dashboard ist jetzt erreichbar (Reachability-Proof: realer Nav-Call-Site).

### G4 — Lern-Kreislauf schließen  *(ADR: „Outcome-Feedback Wiring", ADR-0269 Ph-4b)*
- **Review-korrigiert (F1):** die echte Signatur ist `record_turn_outcome(tenant_id, stage_ids, success: bool)` (`grades.py:110`) — sie attribuiert das Ergebnis an **die Stages, die in diesem Turn liefen**. Der Plan-Erstentwurf `record_turn_outcome(turn_id, signal)` war falsch geformt und hätte nichts zu attribuieren gehabt.
- **Verdrahten:** der Caller muss die **gelaufene Stage-Liste** aus dem CEL-Pipeline-Run des Turns durchreichen. **Plan-Default (U3):** `success = Turn ohne Fehler` (billigster immer-vorhandener Proxy); optionales User-👍/👎 verfeinert später. Alternative benannt: Task-Erfolg aus TDE/ACS.
- **Backend** `chat_runtime.py`: nach dem CEL-Run (wo die `stage_ids` bekannt sind, nahe `emit_decision_record`/`persist_trace` `:4686`) ein `record_turn_outcome(rec.tenant_id, stage_ids, success)`-Hook (fire-and-forget, nie raise).
- **Ehrliche Grenze (F1 2. Ordnung):** dieser Loop schreibt `grader="__loop__"`, was **advisory & explizit NICHT-promotend** ist (`_PROMOTING_GRADERS={"operator"}`). G4 erfüllt C5 *literal* (≥ 1 realer Caller), stärkt aber NICHT den Promotions-Flywheel von G3 — nur Operator-Grades (G3) tun das. Der ADR muss das so benennen; die „geforgt→Grade→Confidence"-Narrative darf das nicht überverkaufen.
- **Tests:** E2E — ein realer **Console**-Chat-Turn → `grades.py`-Store zeigt einen advisory Outcome-Record mit den korrekten `stage_ids` (durch die echte `stream_turn`-Grenze, C11).

### G5 — Tenant Sync (Git, verschlüsselt, opt-in)  *(ADR: „Tenant Sync Protocol + Security Default", Protokoll + fail-closed Default)*
- **Ablösen** des Prototyps `routes/multi_instance.py`:
  - Hardcoded Pfad `~/projects/Tenant-Shumway/` + Repo `veegee82/...` → Config `spec.cross_device.sync_remote` (tenant-scoped) + PAT aus Secret-Vault.
  - `POST /sync` (heute Stub) → echter `TenantSync.run()`: `clone/pull → typ-spezifischer Merge (§Phase 4 Synthese) → optional GPG-encrypt → push`, gibt Merge-Report zurück.
  - Auth-Dependency (`require_session`) auf alle Endpoints (heute ungeschützt); Tenant-Isolation (kein globales „shumway-corvin").
- **Neu** `core/cross_device/tenant_sync.py`: der Merge-Kern + `_assert_no_raw_pii`-Backstop (fail-closed, Muster wie Telemetrie-`_assert_safe`).
- **Feature-Flag** `cross_device_sync` default-**false**, Settings→Features (C8).
- **Frontend** `pages/multi-instance.tsx`: localhost-Port-Probing raus (echte relative API); Merge-Report + Konflikt-Auflösungs-UI; ehrliche Trennung „Git-Zustand ↔ A2A-Live-Metrik".
- **Tests:** E2E — zwei lokale Tenant-Checkouts, divergente `ce_stage_grades.json` + `learning-events.jsonl` → `POST /sync` → assert: Events unioniert, **Grade-Arrays unioniert** (`n_grades`/`mean_score` neu berechnet, nicht addiert), Payload GPG-verschlüsselt, Backstop droppt ein vergiftetes PII-Fixture. Git-Remote = lokales Bare-Repo (kein echtes GitHub im Test).

---

## Integrations-Kontrakte (jede externe Grenze benannt)

| Grenze | Kontrakt |
|---|---|
| Glass Box → CEL-Daten | `GET /vibe-engineering/prompt/{turn}` (bestehend; v2 optional `?annotated=1`) + Cross-Ref `/traces` für Sektions-Rücklink; **`persist_assembly` neu im Console-Pfad** |
| Ledger → CEL-Grades | `GET/POST /vibe-engineering/grades[/{stage}]` (neu, CSRF) → `grade_stage(tenant_id, stage_id, score, notes, grader="operator")` |
| Ledger → ToT | `GET/POST /v1/console/learning/{nodes,grade,note}` (bestehend) |
| Outcome-Wiring | `record_turn_outcome(tenant_id, stage_ids, success)` aus `chat_runtime` (fire-and-forget, advisory/non-promoting) |
| Tenant-Sync → Remote | Git über PAT (Vault); Payload GPG-verschlüsselt; `_assert_no_raw_pii` fail-closed |
| Alle neuen Flags | `spec.features.{cross_device_sync, …}` default-false, Settings→Features |

## Test-Strategie + Rollback

- **Test:** pro Phase ≥ 1 Playwright-E2E **durch die echte Transport-Grenze** (C11); Backend-Unit-Tests für Merge-Algorithmus (G5) und Grade-Aggregation (G3). Flag-off-Pfad getestet (altes Verhalten bleibt).
- **Rollback:** jede Phase hinter Flag ⇒ Flag-off = Vorzustand. G2 (Löschung) ist der einzige nicht-flag-bare Schritt → eigener Commit, revert = Wiederherstellung. Tenant-Sync-Rollback = Git-History (jeder Sync ist ein Commit).

## Messbare Erfolgs-Kriterien (1 Metrik pro Anforderung)

| Anf. | Metrik | Ziel |
|---|---|---|
| C1 | Klicks Turn-Liste → sichtbarer `final_prompt` | ≤ 3 |
| C3 | Anzahl Trace-Oberflächen / `/traces`-Reads pro View | 1 |
| C4 | Learning-Systeme mit UI-Zugang | 3/3 |
| C5 | Production-Caller von `record_turn_outcome` | ≥ 1 |
| C6 | `POST /sync` führt echten Merge aus | ja (Merge-Report ≠ Stub) |
| C7 | PII-Shapes in Push-Payload (Backstop-Test) | 0 |
| C11 | Neue Entry-Points mit realem Call-Site + E2E | 100 % |

## Bekannte Lücken (Known Gaps) — nach LDD-Review aktualisiert

- **KG1 (U1 → aufgelöst):** GPG-Verschlüsselung ist jetzt **Pflicht** (nicht optional). Der Backstop ist best-effort, nicht fail-closed — GPG + Consent tragen die Garantie, nicht der Scanner (Review F4).
- **KG2 (Review F2):** Feines Per-Stage-Origin-Mapping (G1) ist aus den `/prompt`-Daten NICHT ableitbar — v1 zeigt nur CEL-Block-Grenze + Sektions-Legende; feine Spans sind v2-Backend-Scope (`persist_assembly`-Offsets). C2 wurde entsprechend auf das v1-Ziel abgesenkt.
- **KG3 (Review F1):** G4-Outcome-Grades sind `__loop__` = advisory & non-promoting — C5 ist literal erfüllt, aber der Promotions-Flywheel läuft nur über Operator-Grades (G3). Ein promotendes Auto-Signal wäre eine eigene Design-Entscheidung (Folge-Arbeit).
- **KG4 (Review F3):** Assembly-Persistenz ist heute Bridge-only; G1 muss sie in den Console-Pfad einhängen, sonst ist die Glass Box auf Console-Turns leer.
- **KG5 (Review F4):** Dritt-PII (End-Nutzer-abgeleitet) in Memory/Skills ist eine echte GDPR-Egress-Exposition beim Sync — der G5-ADR muss Operator-PII von Dritt-PII trennen und darf den Push nicht als bloßes „eigene-Maschine"-Ereignis framen.

---

## Scaffold — failing tests (loss_0 für den Inner Loop)

Ein bewusst fehlschlagender E2E-Test pro Phase, der das erwartete Verhalten benennt (Dateien noch nicht angelegt — das ist der erste Gradient):

- `tests/e2e/test_glassbox_reveal.py` — Turn-Liste → Glass Box zeigt `final_prompt` + Stage-Gutter (G1)
- `tests/e2e/test_vibe_overview.py` — `/app/vibe-inspector`→404, `/app/vibe-overview` rendert Aggregate (G2)
- `tests/e2e/test_learning_ledger.py` — Operator-Grade → `GET /grades` n_grades+1 (G3)
- `tests/e2e/test_outcome_wiring.py` — realer Chat-Turn → Outcome-Record im Store (G4)
- `tests/e2e/test_tenant_sync_merge.py` — divergente Checkouts → Union-Merge + 0 PII (G5)

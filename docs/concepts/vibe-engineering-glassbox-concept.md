# Konzept: „Glass Box Vibe Engineering" — von der opaken Pipeline zum nachvollziehbaren System

**Status:** Proposal · **Datum:** 2026-08-17 · **Autor:** Claude (Opus 4.8), im Auftrag von shumway
**Scope:** Console-Bereich „Vibe Engineering" + CEL (Context Engineering Layer) + Learning-Systeme + Cross-Device-Sync
**Kein ADR (noch):** Dies ist das *Design-Konzept*, das die Überarbeitung strukturiert. Einzelne umgesetzte
Entscheidungen (Panel-Konsolidierung, Sync-Protokoll) werden je ein eigenes ADR, sobald sie gebaut werden.

---

## 1. Warum überhaupt — das eigentliche Problem

Der Operator hat vier Panels unter „Vibe Engineering" (Your Talent · Context Pipeline · Vibe Inspector ·
Cross-Device Learning) und kann trotzdem drei einfache Fragen **nicht** in wenigen Klicks beantworten:

1. **Was genau ging als Context in die Worker-Engine — und warum?**
2. **Was hat das System aus diesem Turn gelernt?**
3. **Wie hängen CEL, „Brain" und die Learning-Systeme zusammen?**

Der Grund ist nicht fehlende Funktion, sondern fehlende **Nachvollziehbarkeit und Kohärenz**. Der Scan des
Ist-Zustands (2026-08-17) förderte fünf konkrete Struktur-Probleme zutage:

| # | Befund | Beleg |
|---|---|---|
| P1 | **Vibe Inspector ist redundant** — eine dünne read-only-Teilmenge der Context Pipeline. Beide lesen `GET /vibe-engineering/traces`; der Inspector zeigt nur zusätzlich Aggregat-Zähler + Inline-Timings. | `external-panels/vibe-inspector/index.html` vs `pages/vibe-engineering.tsx` |
| P2 | **Die wichtigste Ansicht ist vergraben.** „Was ging in die Worker-Engine" existiert schon (`GET /prompt/{turn_id}` → `final_prompt`, „Finaler Prompt → Worker-Engine"), aber nur als Tab in einem Per-Turn-Modal, nicht als erstklassige Ansicht. | `vibe_engineering.py:207`, `vibe-engineering.tsx:314` |
| P3 | **Drei unverbundene Learning-Systeme.** (a) CEL-Stage-Grades (`grades.py`) — **keine UI**. (b) TreeOfThoughts-Nodes (`learning.py` + `learning.tsx`) — **verwaist**, nicht geroutet. (c) ULO-Objectives (`ulo.py` + `learning-objectives.tsx`) — geroutet, aber **nicht in der Nav**. | s. §5 |
| P4 | **Der Lern-Kreislauf ist nicht geschlossen.** `record_turn_outcome` hat keinen Production-Caller (ADR-0269 Phase-4b unverdrahtet). Grades entstehen nur durch explizites Operator-Grading — für das es **keine UI** gibt. | `grades.py:22-26` |
| P5 | **Cross-Device-Sync ist Prototyp.** Hardcoded lokaler Pfad + Repo, keine Auth, keine Tenant-Isolation, `POST /sync` ist ein Stub, das Frontend port-probed localhost. Dazu ein *zweiter*, konzeptionell überlappender A2A-Pfad. | `routes/multi_instance.py`, `api/multi_instance_sync.py` |

**Begriffsklärung vorweg:** „CIES" ist kein System — im Code gibt es nur **CEL (Context Engineering Layer)**.
Die Suche nach „CIES" traf ausschließlich Substrings („poliCIES", „dependenCIES"). Im Konzept und in der UI
verwenden wir durchgängig **CEL**.

---

## 2. Leitprinzip: Glass Box statt Black Box

> **Der Operator soll in ≤ 3 Klicks von „ein Turn ist passiert" zu „ich sehe den exakten Prompt, seine
> Herkunft Stück für Stück, und was das System daraus gelernt hat" kommen.**

Alles Weitere leitet sich daraus ab: Redundanz raus, die auditierbare Kern-Ansicht nach vorne, die
Learning-Systeme zu **einem** verständlichen Ort zusammenführen, und der Sync von „Demo" zu „echt".

---

## 3. Neue Informationsarchitektur — von 4 überlappenden Panels zu 4 klaren Rollen

Statt vier Panels mit überlappendem Inhalt bekommt jedes eine **eindeutige Rolle** entlang des Lebenszyklus
eines Turns: *verstehen → nachvollziehen → konfigurieren → lernen*.

```
Vibe Engineering
├── 1. Overview          "Wie funktioniert das?"   ← mentales Modell + Aggregate (absorbiert Vibe Inspector)
├── 2. Context Trace     "Was ging in die Engine?"  ← heutige Context Pipeline + Glass-Box-Prompt-Reveal
├── 3. Pipeline Editor   "Wie stelle ich es ein?"   ← heutiger Editor (unverändert)
└── 4. Learning Ledger   "Was hat es gelernt?"      ← konsolidiert die 3 Learning-Systeme + Grade-UI
```

- **Your Talent** bleibt separat als „Ergebnis"-Dashboard (Score-Verlauf), verlinkt aber in den Trace.
- **Vibe Inspector wird entfernt.** Sein einziger Mehrwert (Aggregat-Kacheln: Turns, Sessions, Ø-Score,
  Degraded + Inline-Stage-Timings) wandert prominent in **Overview**. Damit fällt ein komplettes iframe-Panel
  und eine doppelte `/traces`-Abfrage weg. *(Alternative erwogen — s. §8.)*
- Die tote `group:"observability"`-Metadata in `registry.tsx` wird bereinigt.

### 3.1 Overview (neu)
Eine Landing-Ansicht, die das **mentale Modell** vermittelt — genau die Frage 3 des Operators:
- Ein **Fluss-Diagramm**: `Turn → 8 CEL-Stages → Prompt-Assembly → Worker-Engine → Outcome → Learning`.
  Jede Stage ein Knoten mit Kurzbeschreibung, Effekt-Typ (pure / egress / forge) und Live-Status.
- Die **Aggregate** aus dem alten Inspector (Turns, Sessions, Ø-Score, Degraded).
- Ein „So liest du einen Trace"-Erklärkasten (onboarding).

### 3.2 Context Trace (= heutige Context Pipeline, aufgewertet)
Die Per-Turn-Liste bleibt, aber der **„Finaler Prompt → Worker-Engine"** steigt vom vergrabenen Modal-Tab zur
**erstklassigen Glass-Box-Ansicht** auf (§4).

---

## 4. Die Glass Box — vollständige Auditierbarkeit dessen, was in die Engine geht

Das ist der Kern-Wunsch: *„man soll sehen was in die worker engine als context übergeben wird."* Die Daten
existieren bereits (`final_prompt` via `/prompt/{turn_id}`) — sie müssen nur **sichtbar und erklärt** werden.

**„Prompt Reveal" — ein Schichten-View pro Turn:**
- Der **exakte finale Prompt**, der die Engine erreicht hat — 1:1, kein Reword.
- **Farbcodiert nach Herkunft:** jeder Block trägt sein Stage-Label (memory / graph / skill / synthesis / …),
  sodass man sieht *welche Stage welchen Teil beigesteuert hat*.
- **Rückverlinkung:** ein Memory-Block verlinkt auf den Memory-Eintrag, ein Graph-Block auf den Knoten, ein
  Skill-Block auf den Skill-Body. Von „das steht im Prompt" zu „das kam von hier" in einem Klick.
- **Audit-Anker:** der Hash-Chain-Record (schon via `/explain/{brief_sha256}`) + der GDPR-Erasure-Status
  bleiben sichtbar — Auditierbarkeit heißt auch: *nachweisbar unverändert*.
- **Forge-Transparenz:** die in diesem Turn geforgten Tools/Skills (Code + Body, via `/forged/{turn}`) direkt
  daneben — man sieht nicht nur den Prompt, sondern auch was das System sich *dafür gebaut* hat.

**Warum das reicht und nicht mehr:** die Auditierbarkeit ist bereits hash-chain-verankert und
GDPR-löschbar. Wir bauen **kein** neues Audit-System — wir heben das existierende aus dem Modal an die
Oberfläche und annotieren es mit Herkunft. Das respektiert die Compliance-Baseline (kein PII in Labels, keine
Schwächung der Chain).

---

## 5. Learning verständlich — die drei Systeme zu einem „Learning Ledger" führen

Heute liegen drei Lern-Mechanismen unverbunden herum (P3). Das Konzept führt sie in **einer** Ansicht mit drei
klar benannten Abschnitten zusammen — *nicht* durch Code-Merge der Backends, sondern durch eine gemeinsame UI,
die jedes System an seiner richtigen Stelle zeigt:

1. **Stage-Vertrauen (CEL-Grades).** Endlich eine **Grade-UI** (schließt P4): der Operator kann eine Stage
   bewerten, sieht `n_grades` / `mean_score` und ob eine Opt-in-Stage default-fähig wird (Schwelle 0.5). Das
   ist der fehlende Production-Caller für Operator-Grading.
2. **Muster (TreeOfThoughts).** Das verwaiste `learning.tsx` wird hier eingehängt: gelernte Muster,
   Confidence, Operator-Notizen (`/learning/nodes`, `/learning/grade`, `/learning/note`).
3. **Ziele (ULO).** Die User-Learning-Objectives (`learning-objectives.tsx`) bekommen ihren Nav-Platz hier.

Dazu, als roter Faden, der **Flywheel-Verlauf**: was wurde geforgt → welche Grade bekam es → wie entwickelte
sich die Confidence über die Zeit. Das macht den „Lern-Kreislauf" endlich *sichtbar*.

**Kreislauf schließen (P4):** getrennt vom UI-Teil sollte `record_turn_outcome` einen echten Caller bekommen
(ADR-0269 Phase-4b) — z. B. gespeist aus Voice-/Chat-Feedback oder Task-Erfolg. Das ist ein eigenes
Arbeitspaket mit eigenem ADR; das Ledger macht das Ergebnis nur sichtbar.

---

## 6. Cross-Device-Learning — „Tenant Sync" neu gedacht

Der Auftrag war explizit *„denk dir was aus"*. Der Ist-Zustand (P5) ist ein Prototyp; hier die Ziel-Idee.

**Grundidee:** Der *lernbare Zustand* eines Tenants — Skills, CEL-Stage-Grades, Learning-Events, Memory,
generierte Panels — wird als **versioniertes Git-Repo** geführt und gegen ein privates Remote (GitHub o. Ä.)
synchronisiert. Mehrere Corvin-Instanzen (Laptop, Server, zweiter Rechner) teilen dasselbe Gelernte →
Cross-Device-Learning wird **real** statt Demo.

**Vier Design-Säulen:**

1. **Echter Sync statt Stub.** In-Process (oder als echter Scheduled-Job) `git pull → merge → push` des
   Tenant-Zustands. `POST /sync` führt tatsächlich etwas aus und liefert ein Merge-Ergebnis zurück.
2. **Konfliktfreies Mergen nach Datentyp:**
   - *Learning-Events / Audit* (append-only JSONL) → mergen sich konfliktfrei per Union + Sortierung.
   - *Skills / Grades* (Key-Value) → Last-Write-Wins mit Timestamp, bzw. Grades summieren (`n_grades`
     addieren, `mean_score` gewichtet) — ein CRDT-artiger Merge, kein „einer gewinnt alles".
   - *Panels / Memory-Files* → Pfad-basierter 3-Way-Merge; bei echtem Konflikt beide Versionen behalten +
     im UI zur Auflösung anzeigen.
3. **Sicherheit & Compliance zuerst (GDPR!):**
   - **Opt-in per Feature-Flag**, default **off** (Ship-Dark-Regel). Ein frisches Install synct nie ungefragt.
   - **Optionale GPG-Verschlüsselung** des Tenant-Inhalts *vor* dem Push — der Learning-Zustand kann
     Nutzerdaten enthalten; ein privates Remote ist nicht dasselbe wie „PII darf raus".
   - **Auth via GitHub-PAT im Secret-Vault**, nicht hardcoded. Der Repo-Pfad ist Config, nicht `veegee82/...`.
   - **Tenant-isoliert:** ein Repo/Branch pro `tenant_id`, kein globales „shumway-corvin".
4. **Zwei Pfade sauber trennen** statt duplizieren:
   - **Tenant Sync (Git)** = *Zustand* teilen (das hier).
   - **A2A-Peer-Aggregation** (`multi_instance_sync.py`) = *Live-Metriken* zwischen gepaarten Instanzen.
   Beide bleiben, aber mit klarer Rollen-Trennung und einer gemeinsamen „Cross-Device"-Ansicht, die beide
   ehrlich beschriftet (was ist Git-Sync-Zustand, was ist Live-Peer-Metrik).

**Warum Git als Transport:** es ist ohnehin da, gibt uns History/Diff/Rollback des Lern-Zustands geschenkt,
funktioniert offline-first (lokal committen, später pushen) und braucht keine neue Server-Infrastruktur —
passt zu Corvins „läuft auf deiner Maschine"-Haltung.

---

## 7. Umsetzung in Phasen (jede Phase eigenständig lieferbar, Ship-Dark wo nötig)

| Phase | Inhalt | ADR? |
|---|---|---|
| **G1** | Glass-Box-Prompt-Reveal: `final_prompt` als erstklassige Ansicht + Herkunfts-Farbcodierung | ja (UI-Kontrakt) |
| **G2** | Vibe Inspector entfernen; Aggregate → neues Overview; tote `observability`-Metadata bereinigen | nein (Refactor + Löschung) |
| **G3** | Learning Ledger: Grade-UI (P4) + verwaistes `learning.tsx` einhängen + ULO in Nav | ja (neuer Endpoint-Caller) |
| **G4** | `record_turn_outcome` verdrahten — Lern-Kreislauf schließen (ADR-0269 Phase-4b) | ja |
| **G5** | Tenant Sync: echter Git-Merge + Auth + Verschlüsselung + Feature-Flag; A2A-Pfad abgrenzen | ja (Protokoll + Sicherheits-Default) |

Reihenfolge nach Nutzer-Nutzen: **G1 zuerst** (beantwortet die dringendste Frage), **G5 zuletzt** (größter
Bau, klarster Bedarf an eigenem Sicherheits-ADR).

---

## 8. Alternativen erwogen (Dialektik)

- **Vibe Inspector behalten statt entfernen?** Pro: das iframe-Panel ist der erste „externe Panel"-Referenzfall
  (ADR-0362/0363 P5) — Löschen nimmt ein lebendes Beispiel weg. Contra: es dupliziert `/traces` und verwirrt
  („warum zwei Seiten für dasselbe?"). **Entscheidung:** entfernen, aber die *PanelHost-iframe-Referenz* woanders
  am Leben halten (z. B. das AI-generierte Panel aus ADR-0366 ist bereits der bessere lebende Beweis).
- **Learning-Backends zu einem mergen?** Verworfen — die drei Systeme haben verschiedene Datenmodelle
  (Stage-Grades ≠ ToT-Muster ≠ ULO-Ziele). Ein UI-Zusammenschluss (ein Ort, drei Abschnitte) liefert die
  Verständlichkeit, ohne riskante Backend-Migration.
- **Sync über eigenen Server statt Git?** Verworfen — widerspricht der „läuft-lokal"-Haltung, braucht neue
  Infra, verliert die geschenkte History/Diff/Rollback von Git.

---

## 9. Offene Fragen an den Operator

1. **Reihenfolge/Priorität:** G1 (Prompt-Reveal) zuerst — oder ist die Cross-Device-Sync (G5) dringender?
2. **Verschlüsselung Tenant-Sync:** GPG-Pflicht oder optional? (Betrifft, ob Learning-Zustand als
   PII-verdächtig behandelt wird.)
3. **Vibe Inspector:** endgültig entfernen (empfohlen) oder als „Kompakt-Dashboard" umbauen?

---

## 10. Bezug zur Compliance-Baseline

Nichts in diesem Konzept schwächt eine strukturelle Compliance-Garantie: die Glass Box zeigt nur bereits
hash-chain-verankerte, GDPR-löschbare Daten; der Tenant-Sync ist opt-in/default-off (Ship-Dark), tenant-isoliert
und verschlüsselbar; keine Compliance-Mechanik bekommt einen Kill-Switch. Jede Phase mit Endpoint-/Protokoll-
oder Sicherheits-Default trägt ihr eigenes ADR (ADR-0264-Frontmatter).

# Data Protection Impact Assessment (DPIA) — Corvin Template

> **Bilingual:** German + English sections side-by-side where they
> are structurally distinct; common technical references in English.
> This is a **template**; the operator customises and submits to the
> DPO for signature.

**Deployment / Verarbeitung:** _____________________________________
**Verantwortlicher / Controller:** _________________________________
**Auftragsverarbeiter / Processor (optional):** ___________________
**Datum der Erstprüfung / Date of initial assessment:** ___________
**Revision:** _____________________________________________________

---

## 1. Beschreibung der Verarbeitung / Processing description

### DE

Corvin ist ein Multi-Tenant-Agent-Framework für interaktive
KI-Assistenten in Messaging-Bridges (Telegram, Discord, WhatsApp,
Signal). Die Verarbeitung umfasst:

- **Eingabe-Daten:** Nachrichten von autorisierten Benutzern, vom
  Bridge-Daemon entgegengenommen und an einen Worker-Engine
  weitergegeben.
- **Verarbeitungs-Logik:** Engine-Auswahl gemäß `tenant.corvin.yaml`,
  Routing nach Compliance-Zone, ggf. Tool-Aufrufe via Forge / Skill-
  Forge / Cowork.
- **Ausgabe-Daten:** Textantwort des Agents an den User; Audit-Events
  in eine hash-chained `audit.jsonl`-Datei.

### EN

Corvin is a multi-tenant agent framework for interactive AI
assistants in messaging bridges (Telegram, Discord, WhatsApp, Signal).
Processing covers:

- **Input data:** Messages from authorised users, received by the
  bridge daemon and forwarded to a worker engine.
- **Processing logic:** Engine selection per `tenant.corvin.yaml`,
  compliance-zone routing, optional tool calls via Forge / Skill-Forge
  / Cowork.
- **Output data:** Agent reply text to the user; audit events
  appended to a hash-chained `audit.jsonl` file.

---

## 2. Zwecke und Rechtsgrundlage / Purposes and legal basis

| Zweck / Purpose | Rechtsgrundlage / Legal basis (GDPR) |
|---|---|
| Interaktive AI-Assistenz für autorisierte Benutzer | Art. 6 (1) (b) Vertragserfüllung **oder** Art. 6 (1) (f) berechtigtes Interesse |
| Audit-Log für Compliance (Art. 30, Art. 32) | Art. 6 (1) (c) gesetzliche Verpflichtung |
| Bot-Disclosure (EU AI Act Art. 50) | Art. 6 (1) (c) gesetzliche Verpflichtung |
| Erasure-Trail (Art. 17) | Art. 6 (1) (c) gesetzliche Verpflichtung |

---

## 3. Datenkategorien / Data categories

| Kategorie | Beispiele | Sensitivität (L34) | Aufbewahrung |
|---|---|---|---|
| Nachrichteninhalte | User-Prompts, Agent-Antworten | INTERNAL bis CONFIDENTIAL | Session-TTL (7 d default, L8); bis `/forget` (L28) |
| Pseudonymous subject_id | `user_42`, hash-of-uid | INTERNAL | Bis Art.-17-Löschung (L36) |
| Identity Mapping (subject_id → real identity) | operator-eigener Store | CONFIDENTIAL | Bis Art.-17-Löschung |
| Audit-Events | Metadaten zu jedem Engine-Spawn, Tool-Aufruf, Policy-Decision | INTERNAL (Allow-List enforced) | 7 Jahre (L37) |
| Voice-Transcripts (falls L23 aktiv) | STT-Text | CONFIDENTIAL | Nicht im Audit-Chain (Metadata only) |
| Forge-Tools (User-Scope) | Schemata, Code-Snippets | CONFIDENTIAL | Bis User-Erasure (L7) |

---

## 4. Identifizierte Risiken / Identified risks

### 4.1 Datenleck zu US-Cloud-Providern

- **Risiko (DE):** Standard-Engine `claude_code` ruft `api.anthropic.com`
  (USA). Schrems II macht SCC fragwürdig.
- **Risk (EN):** Default engine `claude_code` calls `api.anthropic.com`
  (US). Schrems II makes SCC questionable.
- **Wahrscheinlichkeit:** Hoch ohne EU_PRODUCTION-Preset.
- **Schadenshöhe:** Hoch (Bußgelder bis 4 % Jahresumsatz).
- **Gegenmaßnahmen:**
  - **L34** matrix `[local]` only für CONFIDENTIAL / SECRET (strukturell).
  - **L35** egress lockdown mit `forbid_engines` + `egress.forbidden_hosts`.
  - **EU_PRODUCTION-Preset** (`tenant.corvin.eu-production-ollama.yaml`).
  - **Operator-Perimeter-Firewall** (iptables / Cloud-SG).
- **Restrisiko:** Niedrig (drei strukturelle Verteidigungsschichten).

### 4.2 PII in Audit-Chain

- **Risiko (DE):** Free-Text-Felder im Audit könnten PII enthalten.
- **Risk (EN):** Free-text fields in audit could carry PII.
- **Wahrscheinlichkeit:** Mittel ohne strukturelle Allow-Lists.
- **Schadenshöhe:** Hoch (Audit-Chain ist 7 Jahre aufbewahrt).
- **Gegenmaßnahmen:**
  - Per-Modul Audit-Allow-Liste (L16, L23, L34, L35, L36, L37 et al.).
  - Regression-Tests für jede Allow-Liste (`_validate_audit_details`).
  - `subject_id`-Regex (L36) lehnt PII-Shapes ab.
- **Restrisiko:** Sehr niedrig (strukturell ausgeschlossen, getestet).

### 4.3 Audit-Manipulation

- **Risiko:** Bösartiger Akteur ändert Audit-Einträge.
- **Gegenmaßnahmen:**
  - **L16** hash-chained `audit.jsonl` mit `prev_hash` + `hash`.
  - **L37** chmod 444 + Verschlüsselung für rotierte Segmente.
  - Daily `voice-audit verify` Timer.
  - chmod 0600 auf live `audit.jsonl`.
- **Restrisiko:** Sehr niedrig.

### 4.4 Unautorisierter Zugriff über kompromittierte Bridge

- **Gegenmaßnahmen:**
  - **L19** Disclosure + Consent-Gate (L16 Phase 4).
  - **L18** Rollen-Modell (owner / admin / member / observer).
  - **L20** Quota pro User-Rolle.
  - **L21** Proposal-Stack (Two-step approve).
- **Restrisiko:** Mittel — operative Maßnahmen (2FA, SSH-Key-
  Hardening) sind operator-Verantwortung.

### 4.5 Schädliche Tool-Generierung (Forge)

- **Gegenmaßnahmen:**
  - **L6** Forge mit Sandbox (bwrap) + Policy-Allow-List.
  - **L10** Path-Gate fail-closed.
  - **L29.1/29.2** Delegation Hardening (Hermetic tempdir, Env-Allowlist).
- **Restrisiko:** Niedrig (bei korrekter Policy-Konfiguration).

### 4.6 Recht auf Vergessenwerden vs. Audit-Chain

- **Risiko (DE):** GDPR Art. 17 fordert Löschung; L16/L37 wollen
  unveränderlichen Audit.
- **Resolution:** Pseudonymous `subject_id` in der Chain; L36 löscht
  Identity-Mapping + Content-Stores. EDPB-Guidance erkennt
  Pseudonymisierung als ausreichende Art.-17-Maßnahme bei Konflikt
  mit Art. 30 / 32 Pflichten an.
- **Restrisiko:** Niedrig (rechtlich begründet, technisch implementiert).

---

## 5. Technische und organisatorische Maßnahmen (TOM)

Verweis auf die Layer-spezifischen Implementierungen:

| TOM | Layer | Datei |
|---|---|---|
| Hash-chained tamper-evident Audit | L16 | `operator/forge/forge/security_events.py` |
| Per-User-Consent (Art. 6 / 7) | L16 Phase 4 | `operator/bridges/shared/consent.py` |
| Bot-Disclosure (EU AI Act Art. 50) | L19 | `operator/bridges/shared/disclosure.py` |
| Path-Gate Fail-Closed | L10 | `operator/voice/hooks/path_gate.py` |
| Engine-Identity-Gate | compliance-zone | `operator/bridges/shared/engine_policy.py` |
| Data Classification | L34 | `operator/bridges/shared/data_classification.py` |
| Network Egress Lockdown | L35 | `operator/bridges/shared/egress_gate.py` |
| GDPR Art. 17 Erasure | L36 | `operator/bridges/shared/erasure_orchestrator.py` |
| Audit-at-Rest + Retention | L37 | `operator/bridges/shared/audit_sealer.py` |
| Secret-Vault → bwrap-Env | L16 v3 | `operator/voice/scripts/secret_vault.py` |

---

## 6. Sicherheitstests / Security testing

### 6.1 Automatisierte Tests

| Test | Coverage |
|---|---|
| `test_data_classification.py` | L34 matrix, allow-list, classifier (33 tests) |
| `test_egress_gate.py` | L35 policy + presets (33 tests) |
| `test_audit_sealer.py` | L37 rotation + sealing + retention (28 tests) |
| `test_erasure_orchestrator.py` | L36 cross-layer erasure (30 tests) |
| Per-Layer Audit-Allow-List Regressions | every L34/L35/L36/L37 module |

### 6.2 Externer Penetration-Test

→ siehe `docs/compliance/PENTEST-SCOPE.md`. Mindestens jährlich.

### 6.3 Daily Audit-Chain-Verify

`voice-audit verify` Timer läuft täglich. Exit-Code ≠ 0 löst
CRITICAL `audit.integrity_violation` aus.

---

## 7. Datenübermittlung in Drittländer / International transfers

| Empfänger | Land | Gewählter Mechanismus |
|---|---|---|
| Keine in EU_PRODUCTION-Mode | — | L35 verhindert Outbound |
| Optional: Mistral (Frankreich) | EU | Kein Drittland; DPA mit Mistral SAS |
| Optional: Eigene Ollama-Instanz | On-Prem | Kein Drittland |

**Im EU_PRODUCTION-Modus findet keine Datenübermittlung in Drittländer
statt.** Verifiziert via L35 `egress.blocked` Audit-Events bei
Testanfragen zu US-Endpoints.

---

## 8. Bewertung / Assessment

### 8.1 Notwendigkeit und Verhältnismäßigkeit

- [ ] Verarbeitung ist für den deklarierten Zweck notwendig.
- [ ] Es gibt keine weniger eingriffsintensive Alternative.
- [ ] Datenminimierung umgesetzt (Audit-Allow-Lists, L34 matrix).

### 8.2 Restrisiko

- Gesamt-Restrisiko: **Niedrig** bei vollständiger Anwendung des
  EU_PRODUCTION-Presets + operator-Perimeter-Firewall.

### 8.3 Empfehlung

- [ ] Verarbeitung ist zulässig unter den genannten TOM.
- [ ] Operator hat alle Punkte aus `DSB-CHECKLIST.md` abgehakt.
- [ ] Externer Pentest hat keine Critical / High Findings hinterlassen.

---

## 9. Sign-off

| Rolle | Name | Unterschrift | Datum |
|---|---|---|---|
| Datenschutzbeauftragter (DSB) | | | |
| IT-Sicherheitsbeauftragter | | | |
| Legal Counsel | | | |
| Geschäftsleitung | | | |

---

## 10. Revisionshistorie

| Datum | Revision | Änderung | Autor |
|---|---|---|---|
| | initial | DPIA für Corvin Erstdeployment | |

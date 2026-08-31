# DSB-Checkliste — Corvin Pre-Go-Live (Template)

> **Note (English):** This document is the German-language checklist
> handed to the Datenschutzbeauftragter (DPO) before an Corvin
> deployment enters production under the EU_PRODUCTION preset. It is
> a **template**; the operator fills in deployment-specific values
> and submits to the DPO for review and signature.

**Deployment:** _____________________________________
**Verantwortlich (Operator):** _____________________
**DSB:** ___________________________________________
**Datum der Prüfung:** _____________________________

---

## 1. Technische Kontrollen (Layer-Stack)

Alle nachweislichen technischen Kontrollen, die Corvin strukturell
bietet. Jede Zeile referenziert die Quelldatei + den Audit-Event-Typ,
der die Funktionsfähigkeit belegt.

### 1.1 Bot-Offenlegung (EU AI Act Art. 50)

- [ ] Bot-Disclosure-Karte wird einmalig pro (Kanal, Chat, uid) gezeigt
  - Implementiert: **Layer 19** (`operator/bridges/shared/disclosure.py`)
  - Audit: `disclosure.shown`
  - Speicherort: `<corvin_home>/tenants/<tid>/global/disclosure/`
- [ ] Opt-Out-Befehle `/leave` / `/pass` funktionieren strukturell
  - Test: `python3 operator/bridges/shared/test_disclosure.py`

### 1.2 Consent-Gate (GDPR Art. 6, Art. 7)

- [ ] Per-User-Consent ist deny-by-default
  - Implementiert: **Layer 16 Phase 4** (`operator/bridges/shared/consent.py`)
  - Audit: `consent.granted`, `consent.revoked`, `consent.consumed`
- [ ] TTL für jeden Consent-Eintrag dokumentiert
- [ ] Re-Validierung bei Consume verifiziert

### 1.3 Hash-Chained Audit-Log (GDPR Art. 30, Art. 32)

- [ ] `audit.jsonl` mit `prev_hash` + `hash` pro Eintrag
  - Implementiert: **Layer 16** (`operator/forge/forge/security_events.py`)
  - Daily-Verify: `voice-audit verify` läuft als systemd-Timer
- [ ] Audit-File chmod 0600
- [ ] Rotierte Segmente verschlüsselt (siehe 1.7)

### 1.4 Data Classification (orthogonale Sensitivitätsachse)

- [ ] 4-Stufen-Matrix konfiguriert: PUBLIC / INTERNAL / CONFIDENTIAL / SECRET
  - Implementiert: **Layer 34** (`operator/bridges/shared/data_classification.py`)
  - Audit: `data_flow.approved`, `data_flow.blocked`
  - Konfig: `tenant.corvin.yaml::spec.data_classification`
- [ ] EU_PRODUCTION-Preset matrix tightened auf `[local]` only

### 1.5 Network Egress Lockdown (Three-Layer Defence)

- [ ] `spec.egress.enabled: true`
  - Implementiert: **Layer 35** (`operator/bridges/shared/egress_gate.py`)
  - Audit: `egress.approved`, `egress.blocked`, `egress.preset_loaded`
- [ ] `forbidden_hosts` enthält mindestens: `api.anthropic.com`,
  `api.openai.com`, `api.mistral.ai`,
  `generativelanguage.googleapis.com`
- [ ] `default_action: deny` mit explizit gelisteten `allowed_hosts`
- [ ] **Perimeter-Firewall** (iptables / Docker network / Cloud-SG)
  setzt L35 operativ um — operator-Verantwortung

### 1.6 Engine-Identity-Gate (compliance-zone)

- [ ] `spec.data_residency.allowed_engines` enthält **nur** EU-safe
  Engine-IDs (z.B. `opencode_ollama`)
- [ ] `spec.data_residency.forbid_engines` explizit listed `claude_code`,
  `codex_cli`, generic `opencode`

### 1.7 Audit-at-Rest + 7-Jahre-Retention

- [ ] `spec.audit.encryption_at_rest.enabled: true`
  - Implementiert: **Layer 37** (`operator/bridges/shared/audit_sealer.py`)
  - Audit: `audit.segment_sealed`, `audit.unseal_requested`
- [ ] Sealer-Binary (`age` oder `gpg`) installiert + recipient hinterlegt
- [ ] `spec.audit.retention_years: 7`
- [ ] Rotation-Timer (`max_size_mb` / `max_age_days`) konfiguriert
- [ ] Backup der gesealten Segmente: täglich, separater Aufbewahrungsort

### 1.8 GDPR Art. 17 Erasure-Pfad

- [ ] `corvin-erasure <subject_id>` Pfad existiert
  - Implementiert: **Layer 36** (`operator/bridges/shared/erasure_orchestrator.py`)
  - Audit: `erasure.requested` / `applied` / `skipped` / `failed` / `completed`
- [ ] Per-Layer-Handler registriert für L7, L24, L28, L33,
  Identity-Mapping
- [ ] subject_id-Shape (Pseudonymous-Regex) dokumentiert

### 1.9 Sandbox / Path-Gate

- [ ] `operator/voice/hooks/path_gate.py` aktiv
  - Implementiert: **Layer 10**
  - Audit: `path_gate.denied`
- [ ] Boot-Self-Test bestätigt path-gate-self-test

### 1.10 Voice-Transcript Privacy

- [ ] L23 STT emittiert ausschließlich Metadaten in den Audit-Chain
  - Implementiert: **Layer 23**
  - Audit: `voice.transcribed` (allow-list strict)
- [ ] Regression-Test `test_audit_event_only_carries_metadata` läuft grün

---

## 2. Governance-Kontrollen

### 2.1 Vertragliche Grundlagen

- [ ] Apache-2.0-Lizenz dokumentiert
- [ ] CLA §3 Relicense-Right (BSL/FSL/kommerziell erreichbar)
- [ ] DPA / Auftragsverarbeitungsvertrag mit dem Corvin-Maintainer
  unterzeichnet (falls externes Hosting)
- [ ] DPA mit jedem Cloud-Provider unterzeichnet

### 2.2 Datenklassifikation

- [ ] Verzeichnis von Verarbeitungstätigkeiten (Art. 30 GDPR) geführt
- [ ] Aufbewahrungsfristen pro Datenkategorie definiert:
  - Audit-Logs: 7 Jahre (strukturell, L37)
  - Session-Daten: 7 Tage (Layer 8 Daily Sweep)
  - Recall-DB: Bis `/forget` oder Subject-Löschung (L28)

### 2.3 Access Control

- [ ] Nur autorisierte Operatoren haben Read-Access auf `<corvin_home>`
- [ ] SSH-Keys + Zwei-Faktor für Operator-Console
- [ ] Audit-File mode 0600 (nicht world-readable)

### 2.4 Right-to-Subject-Access

- [ ] Prozess für Auskunftsanfragen (Art. 15 GDPR) dokumentiert
- [ ] Auskunfts-Bericht-Generator (corvin-compliance-reports Plugin)
  installiert

### 2.5 Right-to-Erasure (Art. 17 GDPR)

- [ ] Prozess für Löschanfragen dokumentiert
- [ ] `corvin-erasure` CLI dem DSB-Team bekannt
- [ ] Identity-Mapping-Store dem DSB-Team bekannt (operator-spezifisch)

### 2.6 Breach Notification

- [ ] Incident-Response-Plan für Datenlecks geschrieben
- [ ] 72-h-Meldepflicht (Art. 33 GDPR) im Plan adressiert
- [ ] CRITICAL-Audit-Events lösen Alert aus (SIEM-Integration)

---

## 3. Compliance-Prüfungen

### 3.1 Penetration Test

- [ ] Externer Penetration-Test durch qualifizierte Firma
  - Scope-Dokument: `docs/compliance/PENTEST-SCOPE.md`
  - Pentest-Datum: _____________________
  - Befund-Anzahl (Critical / High / Medium / Low): __ / __ / __ / __
  - Alle Critical / High behoben: ☐ ja ☐ nein (offen: _____)

### 3.2 DPIA (Data Protection Impact Assessment)

- [ ] DPIA durchgeführt + signiert
  - Template: `docs/compliance/DPIA-TEMPLATE.md`
  - Signiert von DSB: _____________________
  - Datum: _____________________

### 3.3 EU AI Act Art. 14 Audit

- [ ] Engine-Policy-Allowlist verifiziert
- [ ] Compliance-Zone-Routing verifiziert
- [ ] Engine-Canary-Test bestanden

### 3.4 Schrems II / Cross-Border

- [ ] Keine US-Cloud-Provider im Verarbeitungspfad (verifiziert via L35
  `egress.blocked` audit-events bei Test-Aufrufen zu US-Endpoints)
- [ ] Bei EU-Cloud-Provider: SCC + zusätzliche Maßnahmen dokumentiert
- [ ] Bei On-Prem: Netzwerk-Diagramm zeigt keine Outbound-Verbindungen
  über die Tenant-Perimeter hinaus

---

## 4. Operative Kontrollen

### 4.1 Monitoring

- [ ] Daily `voice-audit verify` Timer aktiv + grün
- [ ] CRITICAL-Audit-Events lösen Alert in SIEM aus
- [ ] Prometheus / Grafana Dashboard für L34/L35/L36/L37-Events

### 4.2 Backups

- [ ] Audit-Logs täglich gebackupt
- [ ] Gesealte Audit-Segmente in separatem Aufbewahrungsort
- [ ] Backup-Restore-Test mindestens jährlich

### 4.3 Disaster Recovery

- [ ] RTO / RPO definiert
- [ ] DR-Plan beinhaltet Audit-Chain-Wiederherstellung
- [ ] Cross-Segment-Chain-Verify nach Restore funktioniert

### 4.4 Personell

- [ ] DSB benannt + Kontaktdaten dokumentiert
- [ ] IT-Sicherheitsbeauftragter benannt
- [ ] Wer darf `corvin-erasure` ausführen: _____________________
- [ ] Wer hat Sealer-Schlüssel: _____________________

---

## 5. Dokumentation

- [ ] Datenschutzerklärung publiziert (Template:
  `docs/compliance/PRIVACY-NOTICE-TEMPLATE.md`)
- [ ] Security Policy für Corvin geschrieben
- [ ] Audit-Log-Review-Schedule definiert (wöchentlich / monatlich)
- [ ] Compliance-Bericht-Schedule definiert (monatlich)

---

## 6. Regelmäßige Überprüfung

- **Täglich:** `voice-audit verify` Timer (automatisch)
- **Wöchentlich:** Audit-Logs auf CRITICAL-Events sichten (Operator)
- **Monatlich:** Compliance-Bericht generieren + DSB vorlegen
- **Quartalsweise:** Internal Security Audit
- **Jährlich:** Externer Penetration-Test + DPIA-Review
- **Auf Anfrage:** `corvin-erasure` für Art. 17 / `corvin-export`
  für Art. 15

---

## 7. Sign-off

| Rolle | Name | Unterschrift | Datum |
|---|---|---|---|
| Datenschutzbeauftragter (DSB) | | | |
| IT-Sicherheitsbeauftragter | | | |
| Legal Counsel | | | |
| CISO / Geschäftsleitung | | | |

---

## Hinweise für die Befüllung

* "☐ ja ☐ nein" Felder: Operator füllt aus, DSB prüft.
* Verweise auf `docs/...` Pfade beziehen sich auf das Corvin-Repository
  zum Zeitpunkt der Prüfung. Datei-SHA / Commit-Hash sollte im Sign-off
  vermerkt werden, damit die geprüfte Version reproduzierbar ist.
* "Per-Layer-Handler registriert" (§ 1.8) bedeutet: bei der Implementierung
  von L7/L24/L28/L33-Erasure-Handlern hat der Operator dokumentiert, dass
  jeder Layer einen ErasureHandler registriert hat. Stand M4: nur
  `builtin_stub_chain()` aktiv.

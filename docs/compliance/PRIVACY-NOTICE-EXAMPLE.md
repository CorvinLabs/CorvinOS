> **This is an example for a self-hosted Corvin deployment.**
> Replace all values with your actual deployment details before publishing.
> The template is at [PRIVACY-NOTICE-TEMPLATE.md](PRIVACY-NOTICE-TEMPLATE.md).
> This document requires legal review and sign-off before it becomes legally binding.

---

# Datenschutzerklärung — Corvin

# Privacy Notice — Corvin

> **Bilingual / Zweisprachig.** German first (required for DE deployments),
> English afterward.

---

## DE — Datenschutzerklärung

### 1. Verantwortlicher

Acme GmbH
Musterstraße 1, 10115 Berlin, Germany
privacy@acme.example.com
+49 30 12345678

**Datenschutzbeauftragter:**
Jane Doe
dpo@acme.example.com

### 2. Verarbeitete Daten

Im Rahmen der Nutzung von Corvin unter https://corvin.acme.example.com
verarbeiten wir folgende personenbezogene Daten:

- **Nachrichteninhalte:** Texte, die Sie über autorisierte
  Messaging-Bridges (Telegram, Discord, WhatsApp, Signal) an die
  KI-Assistenz senden, sowie die zugehörigen Antworten der Assistenz.
- **Pseudonymisierter Identifikator (`subject_id`):** Eine opake
  Zeichenfolge, die intern Ihre Nachrichten Ihrer Identität zuordnet.
  Wird NICHT in den Audit-Logs in Klartext mit einer realen Identität
  verknüpft.
- **Audit-Daten:** Zeitstempel, Engine-ID, Klassifikations-Stufe,
  Compliance-Zone und weitere Metadaten zu jeder Interaktion. **Keine**
  Inhalte Ihrer Nachrichten landen in den Audit-Daten.
- **Optional:** Sprach-zu-Text-Transkripte (Voice-Notes), wenn Sie
  Sprachnachrichten senden. Transkripte werden ausschließlich für die
  Antwort verwendet und NICHT in den Audit-Daten gespeichert.

### 3. Rechtsgrundlage

- **Art. 6 Abs. 1 lit. b DSGVO** — Erfüllung des Nutzungsvertrags
- **Art. 6 Abs. 1 lit. c DSGVO** — Gesetzliche Verpflichtungen
  (Audit nach Art. 30 / 32 DSGVO, Bot-Disclosure nach EU AI Act Art. 50)
- **Art. 6 Abs. 1 lit. f DSGVO** — Berechtigtes Interesse an
  Sicherheit und Betrugsprävention (kann widersprochen werden)

### 4. KI-Disclosure (EU AI Act Art. 50)

Die Antworten, die Sie über diese Bridge erhalten, werden von einer
**künstlichen Intelligenz (KI)** generiert, nicht von einem Menschen.
Sie haben dies bei der ersten Nutzung über die Disclosure-Karte zur
Kenntnis genommen.

Sie können die KI-Interaktion jederzeit verlassen mit:
- `/leave` — Sie werden aus dem Chat entfernt.
- `/pass` — Bestimmte Nachrichten werden ignoriert.

### 5. Speicherdauer

| Datenkategorie | Speicherdauer |
|---|---|
| Nachrichteninhalte (Recall-DB) | Bis zur Löschanfrage via `/forget` oder zur Sitzungsbereinigung (Standard: 7 Tage) |
| Pseudonymous-ID Mapping | Bis zur Löschanfrage via `corvin-erasure` |
| Audit-Logs | 7 Jahre (gesetzlich nach Art. 30 / 32 DSGVO) |
| Voice-Transcripts | Nicht persistent (nur für die Antwort) |

### 6. Datenübermittlung

In unserem EU-Compliance-Deployment findet **keine** Datenübermittlung
in Drittländer statt. Alle Verarbeitung erfolgt entweder:

- on-premises auf Servern in Deutschland,
- oder bei einem EU-ansässigen Cloud-Provider mit DPA.

Es findet **keine** Datenübermittlung an Anthropic, OpenAI, Google
oder andere US-Provider statt. Diese Einschränkung ist strukturell
durch die Layer-Architektur (L34 Data Classification + L35 Network
Egress Lockdown) umgesetzt und durch tägliche Audit-Verifizierung
abgesichert.

### 7. Ihre Rechte (DSGVO Art. 15 – 22)

| Recht | Wie Sie es ausüben |
|---|---|
| **Auskunft (Art. 15)** | Anfrage an dpo@acme.example.com. Wir liefern einen vollständigen Bericht über die zu Ihnen gespeicherten Daten. |
| **Berichtigung (Art. 16)** | Anfrage an privacy@acme.example.com. |
| **Löschung (Art. 17)** | Anfrage an privacy@acme.example.com oder direkt `/forget` für Recall-Daten. Für die vollständige Löschung über alle Schichten (L7, L24, L28, L33 sowie Identity-Mapping) führt unser Operator `corvin-erasure` aus. |
| **Einschränkung (Art. 18)** | Anfrage an privacy@acme.example.com. |
| **Datenportabilität (Art. 20)** | Anfrage an privacy@acme.example.com. Wir liefern Ihre Daten in JSON-Format. |
| **Widerspruch (Art. 21)** | Anfrage an privacy@acme.example.com. |

### 8. Beschwerde

Bei Verstößen gegen den Datenschutz können Sie sich an die zuständige
Aufsichtsbehörde wenden:

Berliner Beauftragte für Datenschutz und Informationsfreiheit
Friedrichstr. 219, 10969 Berlin
https://www.datenschutz-berlin.de

### 9. Sicherheit

Wir setzen technische und organisatorische Maßnahmen entsprechend
Art. 32 DSGVO ein, unter anderem:

- Hash-chained tamper-evident Audit-Logs.
- Verschlüsselung im Ruhezustand für rotierte Audit-Segmente (AES-256
  via `age` oder `gpg`).
- TLS 1.3 für alle Netzwerkverbindungen.
- Sandbox-Isolation (bwrap) für ausgeführten Code.
- Pseudonymisierung der Benutzer-IDs im Audit-Trail.

### 10. Stand

Diese Datenschutzerklärung wurde am 2026-05-21 zuletzt aktualisiert.

---

## EN — Privacy Notice

### 1. Controller

Acme GmbH
Musterstraße 1, 10115 Berlin, Germany
privacy@acme.example.com
+49 30 12345678

**Data Protection Officer:**
Jane Doe
dpo@acme.example.com

### 2. Data processed

We process the following personal data when you use Corvin at
https://corvin.acme.example.com:

- **Message content:** Text you send via authorised messaging
  bridges (Telegram, Discord, WhatsApp, Signal) to the AI assistant,
  and the assistant's replies.
- **Pseudonymous identifier (`subject_id`):** An opaque string that
  internally links your messages to your account. It is NOT linked
  to a real-world identity in clear text in the audit logs.
- **Audit data:** Timestamps, engine ID, classification grade,
  compliance zone, and other metadata for each interaction. **No**
  content of your messages lands in the audit data.
- **Optional:** Speech-to-text transcripts (voice notes) when you
  send voice messages. Transcripts are used solely for the reply
  and are NOT stored in audit data.

### 3. Legal basis

- **Art. 6 (1) (b) GDPR** — performance of the usage contract
- **Art. 6 (1) (c) GDPR** — compliance with legal obligations
  (audit per Art. 30 / 32 GDPR, bot disclosure per EU AI Act Art. 50)
- **Art. 6 (1) (f) GDPR** — legitimate interest in security and
  fraud prevention (subject to objection)

### 4. AI disclosure (EU AI Act Art. 50)

Replies you receive via this bridge are generated by **artificial
intelligence (AI)**, not a human. You acknowledged this via the
disclosure card on first use.

You can leave the AI interaction at any time via:
- `/leave` — you are removed from the chat.
- `/pass` — selected messages are ignored.

### 5. Retention

| Data category | Retention period |
|---|---|
| Message content (recall DB) | Until erasure request via `/forget` or session cleanup (default 7 days) |
| Pseudonymous-ID mapping | Until erasure request via `corvin-erasure` |
| Audit logs | 7 years (statutory per Art. 30 / 32 GDPR) |
| Voice transcripts | Not persisted (used only for the reply) |

### 6. International transfers

Under our EU compliance deployment there is **no** transfer of data
to third countries. All processing happens either:

- on-premises on servers in Germany, or
- with an EU-located cloud provider under a DPA.

There is **no** transfer of data to Anthropic, OpenAI, Google, or
other US providers. This restriction is structurally enforced by
the layer architecture (L34 Data Classification + L35 Network
Egress Lockdown) and verified by daily audit-chain check.

### 7. Your rights (GDPR Art. 15–22)

| Right | How to exercise |
|---|---|
| **Access (Art. 15)** | Email dpo@acme.example.com. We produce a full report of your data. |
| **Rectification (Art. 16)** | Email privacy@acme.example.com. |
| **Erasure (Art. 17)** | Email privacy@acme.example.com or `/forget` for recall data. For full cross-layer erasure (L7, L24, L28, L33, identity mapping) our operator runs `corvin-erasure`. |
| **Restriction (Art. 18)** | Email privacy@acme.example.com. |
| **Portability (Art. 20)** | Email privacy@acme.example.com. We deliver your data in JSON. |
| **Objection (Art. 21)** | Email privacy@acme.example.com. |

### 8. Complaint

You may lodge a complaint with the competent supervisory authority:

Berliner Beauftragte für Datenschutz und Informationsfreiheit
Friedrichstr. 219, 10969 Berlin
https://www.datenschutz-berlin.de

### 9. Security

We apply technical and organisational measures per Art. 32 GDPR
including:

- Hash-chained tamper-evident audit logs.
- Encryption at rest for rotated audit segments (AES-256 via
  `age` or `gpg`).
- TLS 1.3 for all network connections.
- Sandbox isolation (bwrap) for executed code.
- Pseudonymisation of user IDs in the audit trail.

### 10. Status

This privacy notice was last updated on 2026-05-21.

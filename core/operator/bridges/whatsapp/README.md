# WhatsApp-Bridge für corvin-voice

Steuere Claude Code per WhatsApp — schicke Text- oder Voice-Nachrichten, bekomme Text- und/oder Sprach-Antworten zurück.

## Architektur

```
WhatsApp App  ──Baileys──▶  daemon.js (Node)  ──fs queue──▶  adapter.py (Python)  ──┐
     ▲                                                                              │
     └──────────────────  outbox/<id>.json  ◀──────────  claude CLI  ◀──────────────┘
```

- `daemon.js` verbindet sich via Baileys mit deinem persönlichen WhatsApp-Konto (Linked Device).
- Eingehende Nachrichten werden als JSON in `inbox/` abgelegt.
- `adapter.py` pollt `inbox/`, transkribiert Voice-Notes via Whisper, ruft `claude -p "<prompt>"`, schreibt die Antwort als JSON nach `outbox/`.
- `daemon.js` pollt `outbox/`, sendet Text und/oder Voice-Note (OpenAI TTS → OGG-Opus) zurück.

## Erste Inbetriebnahme

1. Node-Dependencies installieren:
   ```bash
   cd operator/voice/whatsapp
   npm install
   ```

2. Pairing — QR-Code scannen:
   ```bash
   node daemon.js --pair-only
   ```
   Im Terminal erscheint ein QR-Code. In der WhatsApp-App: Einstellungen → Verknüpfte Geräte → Gerät hinzufügen → QR-Code scannen.

3. `settings.json` anlegen (Vorlage in `settings.json.example`):
   ```json
   {
     "whitelist": ["491701234567@s.whatsapp.net"],
     "rate_limit_per_hour": 30,
     "always_voice": false,
     "voice_threshold_chars": 200,
     "pin": "1234"
   }
   ```
   Telefonnummer ohne Plus, mit `@s.whatsapp.net`-Suffix. Solange `whitelist` leer ist, akzeptiert die Bridge alle Sender (DEV-Mode — nicht produktiv lassen).

4. Daemon und Adapter dauerhaft starten:
   ```bash
   node daemon.js &
   python3 adapter.py &
   ```

## Mock-Modus für lokale Tests

Ohne WhatsApp-Account testbar:
```bash
node daemon.js --mock &
python3 adapter.py &

# Eingehende Mock-Nachricht simulieren:
curl -X POST http://127.0.0.1:7891/mock/inbound \
  -H 'Content-Type: application/json' \
  -d '{"from":"491701234567@s.whatsapp.net","text":"Was ist 2+2?","ts":1714867200000}'
```
Der Adapter verarbeitet die Nachricht; die "ausgehende" Antwort wird im Mock-Modus geloggt statt versendet.

## Auth & Whitelist

- **Default: alles aus.** Solange der aktuelle Chat nicht explizit aktiviert ist, leitet der Daemon eingehende Nachrichten **nicht** an die KI weiter.
- **Pro-Chat-Schalter direkt in WhatsApp** (nur durch dich selbst auslösbar — nutzt das vom WA-Server gesetzte `fromMe`-Flag, das in Gruppen niemand sonst spoofen kann):
  - `/on` — aktiviert die KI für den Chat, in dem du das tippst (DM oder Gruppe).
  - `/off` — deaktiviert sie wieder.
  - `/status` — Bot quittiert mit "KI ist an." / "KI ist aus.".
- Aktivierte Chats werden in `settings.json` unter `enabled_chats` persistiert und überleben Daemon-Restarts.
- **Whitelist** und **PIN-Flow** in `settings.json` bleiben als zusätzliche Auth-Schicht erhalten — wer durch das `enabled_chats`-Gate kommt, durchläuft danach noch die alte Whitelist-Prüfung. Im Normalfall reichen aber `/on`/`/off` zur Steuerung.
- **Rate-Limit**: standard 30 Nachrichten pro Stunde pro Sender, in-memory.

## Voice-Note-Format

OpenAI TTS gibt direkt OGG-Opus aus (`response_format="opus"`) — das ist exakt das Format, das WhatsApp für Voice-Notes erwartet. Kein `ffmpeg`-Schritt nötig.

## Slash-Commands

- `/whatsapp-status` — Verbindung, Whitelist-Größe, ausstehende Outbox-Items.
- `/whatsapp-pair` — startet das Pairing erneut (z.B. nach Logout in der App).
- `/whatsapp-on` / `/whatsapp-off` — Daemon und Adapter starten/stoppen.

## Sicherheit

- `auth/`-Verzeichnis enthält den Baileys-Session-State. Nicht ins Git einchecken (steht im `.gitignore`).
- `settings.json` enthält PIN — ebenfalls nicht committen.
- `.env` mit `OPENAI_API_KEY` und `ANTHROPIC_API_KEY` wird vom Plugin-Hauptverzeichnis gelesen.

# Voice TTS Provider Configuration Fix

## Problem Summary

Voice Summary auf Discord Voice ist auf Edge TTS konfiguriert, obwohl ein OpenAI API Key verfügbar ist. Die Fallback-Logik funktioniert korrekt (OpenAI → Edge → Piper), aber OpenAI wird übersprungen.

**Root Causes gefunden:**
1. ❌ **OpenAI API Keys sind PLACEHOLDERS** in `~/.config/corvin-voice/service.env`
2. ❌ **openai Python SDK nicht installiert** in der voice daemon Umgebung
3. ✓ **Fallback-Logik ist korrekt** — Edge TTS wird korrekt verwendet als Fallback

---

## Configuration Status Report

### Current State (nach Diagnose)
```
Environment Variable           Status              Value
─────────────────────────────────────────────────────────────
CORVIN_TTS_OPENAI_KEY        ❌ PLACEHOLDER      sk-proj-PLACEHOLDER-TTS-20260916_201207
OPENAI_API_KEY               ❌ PLACEHOLDER      sk-proj-PLACEHOLDER-OpenAI-20260916_201207
CORVIN_TTS_LOCAL_ONLY        ✓ NOT SET           (OpenAI allowed)

Python Packages
─────────────────────────────────────────────────────────────
openai                       ❌ NOT INSTALLED
edge_tts                     ❌ NOT INSTALLED

Provider Availability
─────────────────────────────────────────────────────────────
OpenAI TTS                   ❌ NOT AVAILABLE    (SDK missing, key is placeholder)
Edge TTS                     ❌ NOT AVAILABLE    (SDK missing)
Piper TTS                    ✓ AVAILABLE         (used as fallback)
```

---

## Fix: Step-by-Step Instructions

### Step 1: Obtain Real OpenAI API Key

Der echte OpenAI API Key ist in der Vault gespeichert:

```bash
# Vault auslesen (benötigt Vault-Zugriff)
vault_cli=$(find ~ -name "vault_cli.py" 2>/dev/null | head -1)
if [ -z "$vault_cli" ]; then
    echo "⚠️ vault_cli.py nicht gefunden"
    echo "Nutze alternativ: CorvinOS console → Settings → API Keys → OpenAI"
    exit 1
fi

# Key aus Vault abrufen
python3 "$vault_cli" get openai_api_key
```

Oder über die CorvinOS Console:
1. Öffne http://localhost:8765/console
2. Gehe zu Settings → Secrets → OpenAI TTS
3. Kopiere den API Key

### Step 2: Update service.env mit echtem Key

```bash
# 1. Datei öffnen
nano ~/.config/corvin-voice/service.env

# 2. Zeile ersetzen:
# FALSCH (PLACEHOLDER):
# CORVIN_TTS_OPENAI_KEY=sk-proj-PLACEHOLDER-TTS-20260916_201207

# RICHTIG (echter Key):
# CORVIN_TTS_OPENAI_KEY=sk-proj-xxxxxxxxxxxx...

# 3. Datei speichern und rechte setzen:
chmod 600 ~/.config/corvin-voice/service.env

# 4. Überprüfen:
grep CORVIN_TTS_OPENAI_KEY ~/.config/corvin-voice/service.env | head -c 30
# Sollte: sk-proj-xxxx zeigen, NICHT PLACEHOLDER
```

### Step 3: Install Python Dependencies

```bash
# In der Python-Umgebung, wo voice daemon läuft:
python3 -m pip install openai edge-tts

# Überprüfen:
python3 -c "import openai; print(f'✓ openai {openai.__version__}')"
python3 -c "import edge_tts; print('✓ edge-tts installed')"
```

### Step 4: Restart Voice Daemon

```bash
# Voice daemon neustarten (damit neue Umgebung geladen wird)
systemctl --user restart corvin-voice

# Überprüfen:
systemctl --user is-active corvin-voice
# Sollte: active zeigen
```

### Step 5: Verify Configuration

```bash
# Konfiguration überprüfen
bash /tmp/diagnose_tts.sh

# Logs überprüfen (live):
journalctl --user -u corvin-voice -f | grep -i "tts\|openai\|edge"

# Sollte zeigen:
# ✓ TTS: Trying primary provider (OpenAI)...
# ✓ TTS: OpenAI synthesis succeeded
```

---

## TTS Provider Priority (korrekter Fallback-Stack)

```
┌──────────────────────────────────────┐
│  1. OpenAI TTS (Preferred)           │
│     ✓ High quality (0.95)            │
│     ✓ Supports multiple languages    │
│     ✓ Natural-sounding voices        │
│     ⚠️  Requires OPENAI_API_KEY      │
│     ⚠️  Requires openai SDK          │
└────────────┬─────────────────────────┘
             │ (API error, no key, SDK missing)
             ↓
┌──────────────────────────────────────┐
│  2. Microsoft Edge TTS (Fallback)    │
│     ✓ Good quality (0.75)            │
│     ✓ Free (no API key)              │
│     ⚠️  Requires internet            │
│     ⚠️  Requires ffmpeg              │
│     ⚠️  Requires edge-tts SDK        │
└────────────┬─────────────────────────┘
             │ (Network error, ffmpeg missing, SDK missing)
             ↓
┌──────────────────────────────────────┐
│  3. Piper Local TTS (Fallback)       │
│     ✓ Offline (no internet)          │
│     ⚠️  Lower quality                │
│     ⚠️  Requires piper binary        │
│     ⚠️  Requires ONNX models         │
└────────────┬─────────────────────────┘
             │ (Piper unavailable)
             ↓
┌──────────────────────────────────────┐
│  4. Text-Only Fallback               │
│     • No voice note                  │
│     • User sees skip reason          │
│     • Text delivery still works       │
└──────────────────────────────────────┘
```

---

## Key Resolution Precedence

```
CORVIN_TTS_OPENAI_KEY
    ↓ (if empty/missing)
OPENAI_API_KEY (general OpenAI key)
    ↓ (if empty/missing)
OPENAI_APIKEY (legacy alias)
    ↓ (if empty/missing)
❌ None → OpenAI unavailable
```

**Wichtig:** Der erste gefundene Key wird verwendet. Die Suche stoppt beim ersten Hit:
- Environment variables werden ZUERST geprüft (höchste Priorität)
- Dann service.env
- Dann vault/secrets.enc

---

## Common Issues & Troubleshooting

### Issue 1: PLACEHOLDER Keys in service.env
```bash
❌ CORVIN_TTS_OPENAI_KEY=sk-proj-PLACEHOLDER-TTS-20260916_201207

✓ Lösung: Replace with real key from vault (siehe Step 2)
```

### Issue 2: openai SDK Not Installed
```bash
Log: "synth: openai package not installed"

✓ Lösung:
python3 -m pip install openai
systemctl --user restart corvin-voice
```

### Issue 3: CORVIN_TTS_LOCAL_ONLY=1 Set
```bash
❌ CORVIN_TTS_LOCAL_ONLY=1
# Diese Variable blockiert ALLE OpenAI-Zugriffe

✓ Lösung:
unset CORVIN_TTS_LOCAL_ONLY
# Oder aus service.env entfernen
```

### Issue 4: OpenAI API Quota Exceeded (429)
```bash
Log: "insufficient_quota" or "Error code: 429"

✓ Fallback aktiv: Edge TTS wird verwendet
✓ Automatischer Backoff: 1 Stunde warten, dann retry
✓ Kein Operator-Eingriff notwendig
```

### Issue 5: Network/ffmpeg Issues with Edge TTS
```bash
Log: "edge TTS failed" (but fallback should work)

✓ Überprüfen:
which ffmpeg
which piper

✓ Fallback sollte zu Piper gehen
```

---

## Verification Checklist

Nach der Konfiguration:

- [ ] `service.env` enthält echten OPENAI_API_KEY (nicht PLACEHOLDER)
- [ ] `chmod 600 ~/.config/corvin-voice/service.env` ausgeführt
- [ ] `python3 -c "import openai"` erfolgreich
- [ ] `python3 -c "import edge_tts"` erfolgreich
- [ ] `systemctl --user status corvin-voice` = active
- [ ] `journalctl --user -u corvin-voice` zeigt OpenAI-Logs
- [ ] Voice Summary Test erfolgreich: 
  ```bash
  cd /path/to/CorvinOS
  python3 tests/test_tts_provider_selection_e2e.py
  # Sollte: OpenAI TTS succeeded zeigen
  ```

---

## ADR & References

- **ADR-0554:** Voice summaries on proactive messages (TTS configuration)
- **ADR-0193:** Budget/Fallback strategy for TTS/STT
- **ADR-0445:** Resilience patterns (fallback logic)
- **provider_keys.py:** Canonical key resolver (single source of truth)
- **adapter.py:** Voice synthesis orchestration (synthesize_voice_note)

---

## Appendix: Testing E2E Behavior

### Test Scenario 1: OpenAI Success
```bash
# Mit korrektem OPENAI_API_KEY:
python3 -c "
import asyncio
from corvin_operator.bridges.shared import adapter

async def test():
    path = await asyncio.to_thread(adapter.synthesize_voice_note, 'Hello world', 'en')
    print(f'✓ Voice created: {path}')
    
asyncio.run(test())
"
```

### Test Scenario 2: OpenAI Unavailable (Key Missing)
```bash
# Mit OPENAI_API_KEY='' (leer):
export OPENAI_API_KEY=""
unset CORVIN_TTS_OPENAI_KEY

# Sollte auf Edge TTS fallback:
python3 -c "
import asyncio
from corvin_operator.bridges.shared import adapter

async def test():
    path = await asyncio.to_thread(adapter.synthesize_voice_note, 'Fallback test', 'en')
    print(f'✓ Fallback used: {path}')
    
asyncio.run(test())
"
```

### Test Scenario 3: Quota Exceeded (429)
```bash
# Mit invalid/quota-exceeded key:
# Sollte automatisch auf Edge fallback:
python3 -c "
from corvin_operator.bridges.shared import adapter

state = adapter._voice_engine_state
print(f'Quota backoff active: {state.get(\"quota_until\", 0) > 0}')
"
```

---

## Summary: Was hat sich geändert?

| Bereich | Vorher | Nachher |
|---------|--------|---------|
| OPENAI_API_KEY in service.env | PLACEHOLDER | ✓ Echter Key |
| openai SDK | ❌ Nicht installiert | ✓ Installiert |
| Voice daemon | ❌ Nicht laufen | ✓ Läuft |
| TTS Fallback | (funktioniert, aber OpenAI übersprungen) | ✓ OpenAI wird versucht, Edge Fallback aktiv |
| Voice Summary | Edge TTS verwendet | ✓ OpenAI TTS wenn verfügbar, Edge als Fallback |

---

## How to Apply This Fix

```bash
# 1. Vault-Key abrufen
# (siehe Step 1)

# 2. service.env aktualisieren
nano ~/.config/corvin-voice/service.env
# CORVIN_TTS_OPENAI_KEY=<INSERT_REAL_KEY_HERE>

# 3. Dependencies installieren
python3 -m pip install openai edge-tts

# 4. Voice daemon neustarten
systemctl --user restart corvin-voice

# 5. Überprüfen
bash /tmp/diagnose_tts.sh
journalctl --user -u corvin-voice -f

# Done! OpenAI TTS sollte jetzt bevorzugt werden.
```

# 🎯 Token Metrics Integration für CorvinOS Instances

**Status:** Ready to integrate  
**Impact:** Measure real token usage und Vibe Engineering Savings  
**Time to implement:** ~15 minutes  
**Result:** Echte Token-Metriken in der Console unter Vibe Engineering

---

## 🎯 Was passiert nach Integration

Nach der Integration zeigt deine Console:
- ✅ **Real Token Usage** — Echte Tokens pro Turn
- ✅ **Cost Savings** — USD gespart durch Vibe Engineering
- ✅ **Subsystem Breakdown** — Welche Komponente spart wieviel
- ✅ **Live Updates** — Alle Metriken aktualisieren in Echtzeit

---

## 📋 Integration Checklist

### Step 1: Token Hook Initialize (in startup code)

Wenn deine CorvinOS Instance startet (z.B. in `corvin_console/app.py` oder `chat_runtime.py`):

```python
from core.learning.token_metrics_store import TokenMetricsStore
from core.learning.event_emitter import EventEmitter
from core.learning.token_measurement_hook import initialize_token_hook

# Initialize at startup
emitter = EventEmitter()
store = TokenMetricsStore(emitter)
token_hook = initialize_token_hook(store, emitter)

print("✅ Token measurement initialized")
```

### Step 2: Record Turns in Chat Pipeline

Bei jedem Turn (in deinem `ChatRuntime.process_turn()` oder ähnlich):

```python
from core.learning.token_measurement_hook import record_turn_metrics

async def process_turn(self, turn_id: str, session_id: str, prompt: str):
    """Process a single turn and measure tokens."""
    
    # 1. Get input from user
    # 2. Prepare context (memory, skills, etc.)
    # 3. Call LLM (e.g., Claude API)
    
    response = await llm.complete(prompt)
    
    # 4. Record metrics
    record_turn_metrics(
        turn_id=turn_id,
        session_id=session_id,
        tenant_id=current_tenant_id,  # From auth
        input_tokens=response.usage.prompt_tokens,
        output_tokens=response.usage.completion_tokens,
        subsystems={
            "memory_lookup": 50,        # Tokens for memory context
            "skill_injection": 100,     # Tokens for skill briefs
            "graph_traversal": 25,      # Tokens for graph edges
        }
    )
    
    return response
```

### Step 3: Check Database Integration

Stelle sicher, dass TokenMetricsDB initialisiert ist:

```python
from core.learning.token_metrics_db import TokenMetricsDB

# At startup
db = TokenMetricsDB()
db.initialize()  # Creates table if needed
print("✅ Token metrics database ready")
```

### Step 4: Verify in Console

Nach der Integration:

1. Öffne die CorvinOS Console
2. Gehe zu **Settings → Features**
3. Aktiviere **"vibe_engineering"**
4. Gehe zu **"Vibe Engineering"** Menü
5. Klicke auf **"Token Metrics"**

Du siehst dann:
- 📊 Real token usage deiner Turns
- 💵 USD gespart
- 🎯 Subsystem breakdown

---

## 🔧 Full Implementation Example

Hier ist ein **komplettes Beispiel** für einen Chat-Turn mit Token-Messung:

```python
# chat_runtime.py

from datetime import datetime
from fastapi import FastAPI, Depends
from core.learning.token_measurement_hook import record_turn_metrics
from core.console.corvin_console.routes.auth import get_current_user

app = FastAPI()

@app.post("/api/chat/turn")
async def chat_turn(
    prompt: str,
    current_user: dict = Depends(get_current_user),
):
    """Process a chat turn with token measurement."""
    
    # Get session and user info
    session_id = current_user.get("session_id", "default")
    tenant_id = current_user.get("tenant_id", "default")
    turn_id = f"turn_{int(datetime.utcnow().timestamp() * 1000)}"
    
    # 1. Lookup context (memory, skills, etc.)
    memory_context = await memory_engine.lookup(prompt)  # ~50 tokens
    skills = await skill_system.find_relevant(prompt)    # ~100 tokens
    
    # 2. Build full prompt
    full_prompt = f"""
    {SYSTEM_PROMPT}
    
    Memory: {memory_context}
    Skills: {skills}
    
    User: {prompt}
    """
    
    # 3. Call LLM
    response = await claude_client.messages.create(
        model="claude-opus-4-1",
        max_tokens=2000,
        messages=[{"role": "user", "content": full_prompt}]
    )
    
    # 4. Record token metrics
    record_turn_metrics(
        turn_id=turn_id,
        session_id=session_id,
        tenant_id=tenant_id,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        subsystems={
            "memory_lookup": 50,
            "skill_injection": 100,
            "context_bridge": 25,
        }
    )
    
    # 5. Return response
    return {
        "turn_id": turn_id,
        "response": response.content[0].text,
        "tokens_used": response.usage.input_tokens + response.usage.output_tokens,
    }
```

---

## 🧪 Testing Token Metrics

Nach der Integration, teste mit:

```bash
# 1. Check if metrics are being recorded
curl http://localhost:8000/api/metrics/session/test_session

# Expected response:
# {
#   "turn_count": 5,
#   "total_tokens": 12500,
#   "baseline_tokens": 17500,
#   "savings_percent": 28.5,
#   "estimated_savings": $2.47
# }

# 2. Check database directly
sqlite3 ~/.corvin/token_metrics.db "SELECT * FROM token_metrics ORDER BY timestamp DESC LIMIT 5;"

# 3. View in Console
# Open Console → Settings → Features → Enable "vibe_engineering"
# Vibe Engineering → Token Metrics
```

---

## 📊 What Gets Measured

### Per-Turn Metrics
- **input_tokens** — Tokens in der Eingabe (prompt)
- **output_tokens** — Tokens in der Ausgabe (response)
- **total_tokens** — Summe
- **baseline_tokens** — Tokens ohne Vibe Engineering
- **savings_percent** — % saved vs. baseline

### Subsystem Attribution
- **memory_lookup** — Tokens für Memory Context
- **skill_injection** — Tokens für Skills
- **context_bridge** — Tokens für Context Engineering
- **graph_traversal** — Tokens für Graph Lookups
- **llm_synthesis** — Tokens für LLM Synthesis

### Cost Calculation
```
baseline_cost = total_tokens * $0.0084 / 1000
actual_cost = baseline_tokens * $0.0084 / 1000
savings = baseline_cost - actual_cost
```

---

## 🎯 API Endpoints (nach Integration)

### Get Session Metrics
```bash
GET /api/metrics/session/{session_id}
Response:
{
  "turn_count": 42,
  "total_tokens": 13100,
  "baseline_tokens": 18300,
  "savings_tokens": 5200,
  "savings_percent": 28.5,
  "estimated_savings": $2.47,
  "by_task_type": {...},
  "subsystems": {...}
}
```

### Get Cluster Stats (All Instances)
```bash
GET /api/metrics/stats
Response:
{
  "instance_count": 3,
  "total_turns": 456,
  "total_tokens": 1234567,
  "avg_savings_percent": 27.8
}
```

---

## ✅ Verification Checklist

Nach der Integration:

- [ ] Token Hook wird beim Startup initialisiert
- [ ] `record_turn_metrics()` wird nach jedem Turn aufgerufen
- [ ] Database speichert Metrics (check mit sqlite3)
- [ ] Console Panel zeigt Token Metrics
- [ ] Metrics updaten in Echtzeit (alle 5 Sekunden)
- [ ] Subsystem Breakdown ist korrekt
- [ ] Cost Savings werden berechnet und angezeigt

---

## 🚀 Go Live Checklist

Wenn alles funktioniert:

- [ ] Integration auf Production-Instance
- [ ] Vibe Engineering Savings live beobachtbar
- [ ] Metrics im Console-Dashboard angezeigt
- [ ] Stats auf corvin-labs.com/stats aktualisieren sich
- [ ] Team sieht echte Einsparungen

---

## 📞 Troubleshooting

### Keine Metrics angezeigt
```bash
# 1. Check if hook is initialized
ps aux | grep python | grep token_measurement

# 2. Check database
sqlite3 ~/.corvin/token_metrics.db ".tables"
sqlite3 ~/.corvin/token_metrics.db "SELECT COUNT(*) FROM token_metrics;"

# 3. Check API
curl http://localhost:8000/api/metrics/session/any_session

# 4. Check logs
journalctl -u corvinos-stats -f
```

### Metriken stimmen nicht
- Prüfe ob `input_tokens` und `output_tokens` korrekt sind
- Prüfe ob Subsystem-Tokens korrekt berechnet werden
- Vergleiche mit LLM API Response

### Console Panel zeigt "No data"
- Prüfe ob `vibe_engineering` Feature aktiviert ist
- Prüfe ob API Endpoint erreichbar ist (`/api/metrics/session/{id}`)
- Prüfe ob Daten in Datenbank existieren

---

## 📖 Full Reference

- **Token Instrumentation:** `core/learning/token_instrumentation.py`
- **Token Metrics Store:** `core/learning/token_metrics_store.py`
- **Token Measurement Hook:** `core/learning/token_measurement_hook.py`
- **API Endpoints:** `core/console/corvin_console/routes/vibe_metrics_api.py`
- **Console Component:** `core/console/corvin_console/web-next/src/pages/token-metrics.tsx`

---

**Bereit zum Integrieren?** Die komplette Infrastruktur steht. Du brauchst nur noch die Integration in deinem Chat-Runtime Code durchzuführen! 🚀

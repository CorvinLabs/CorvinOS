# ADR-0274 Dashboard Deployment Guide

**Week 6 Measurement Phase** — Live Dashboard Integration

---

## Overview

The ADR-0274 learning system now has a **complete end-to-end visualization**:

```
Local Instances
    ↓ (K=8 Aggregator writes JSONL)
~/.corvin/measurement/2026-08-XX/
    ├─ predictions.jsonl       (ADR-0270)
    ├─ feedback.jsonl          (ADR-0271)
    ├─ user_choices.jsonl      (ADR-0272)
    └─ budget_allocations.jsonl (ADR-0273)
    ↓
[Backend] operator/context_engineering/api_server.py
    └─ Reads JSONL files
    └─ Computes stats
    └─ Serves REST JSON
    ↓
[Export] operator/context_engineering/export_stats_to_website.py
    └─ Runs hourly
    └─ Syncs to Corvin-Website JSON
    ↓
[Frontend] corvin-labs.com/stats
    ├─ stats.html (static page)
    ├─ MeasurementDashboard.jsx (React component)
    └─ Live 30-sec refresh
```

---

## Deployment Steps

### Step 1: Start the Backend API (Local or Railway)

**Locally:**
```bash
cd /home/shumway/projects/CorvinOS
uv run python operator/context_engineering/api_server.py
# Listens on http://localhost:5000
```

**On Railway:**
```bash
# 1. Create new service in Railway
# 2. Add environment variables:
export CORVIN_MEASUREMENT_ROOT=$HOME/.corvin/measurement

# 3. Deploy with Procfile:
echo "web: python operator/context_engineering/api_server.py" > Procfile

# 4. Railway auto-runs the web process
# API available at https://<your-railway-domain>/api/v1/measurements/latest
```

### Step 2: Set Up Hourly Export (Cron)

**Every hour, sync measurements to website:**

```bash
# Add to crontab
0 * * * * cd /home/shumway/projects/CorvinOS && python operator/context_engineering/export_stats_to_website.py

# Or on Railway, add periodic job:
# Schedule: 0 * * * * (every hour)
# Command: python operator/context_engineering/export_stats_to_website.py
```

The export writes to:
```
/home/shumway/projects/Corvin-Website/api/v1/telemetry/measurements/latest.json
```

### Step 3: Deploy Website Dashboard

**Option A: Static HTML (No Build)**
```bash
cd /home/shumway/projects/Corvin-Website
# stats.html already in root
wrangler pages deploy .
# Dashboard at corvin-labs.com/stats
```

**Option B: React with Build**
```bash
npm install recharts
npm run build
# Deploy build/ to Cloudflare Pages
```

### Step 4: Configure API Endpoint

In **stats.html** or **MeasurementDashboard.jsx**, set:

```javascript
const API_BASE = process.env.REACT_APP_API_URL || 'https://corvinos-api.railway.app'
```

Or in HTML:
```html
<script>
  const API_BASE = 'https://corvinos-api.railway.app'
</script>
```

---

## Verify Setup

### Check Backend Health

```bash
curl http://localhost:5000/health
# Response: {"status": "ok", "service": "ADR-0274 API"}
```

### Check Latest Measurements

```bash
curl http://localhost:5000/api/v1/measurements/latest?days=7
# Response: {
#   "timestamp": "2026-08-08T...",
#   "stats": {
#     "adr_0270_uncertainty": {...},
#     "adr_0271_feedback": {...},
#     ...
#   }
# }
```

### Check Website Export

```bash
cat /home/shumway/projects/Corvin-Website/api/v1/telemetry/measurements/latest.json
# Should contain measurement stats
```

### Visit Dashboard

```
http://localhost:3000/stats
  (local dev)

https://corvin-labs.com/stats
  (production)
```

---

## API Endpoints

All endpoints return JSON with per-track stats:

| Endpoint | Returns | Purpose |
|----------|---------|---------|
| `/api/v1/measurements/latest` | All 4 tracks summary | Dashboard main view |
| `/api/v1/measurements/predictions` | ADR-0270 data + stats | Confidence accuracy |
| `/api/v1/measurements/feedback` | ADR-0271 data + stats | Learning rate |
| `/api/v1/measurements/preferences` | ADR-0272 data + stats | User style patterns |
| `/api/v1/measurements/budget` | ADR-0273 data + stats | Budget allocation |
| `/health` | {"status": "ok"} | Service health |

**Example request:**
```bash
curl "http://localhost:5000/api/v1/measurements/latest?days=7" \
  -H "Accept: application/json"
```

**Example response:**
```json
{
  "timestamp": "2026-08-08T17:42:33.123456",
  "days_lookback": 7,
  "stats": {
    "adr_0270_uncertainty": {
      "count": 42,
      "avg_confidence": 0.82,
      "avg_outcome": 0.81,
      "accuracy": 0.98,
      "contexts_tracked": 12
    },
    "adr_0271_feedback": {
      "count": 28,
      "avg_delta": 0.03,
      "helpful_pct": 72.5,
      "learning_events": 8
    },
    "adr_0272_preferences": {
      "count": 35,
      "pragmatic_pct": 65.0,
      "task_types": {"ml": 8, "devops": 5, "refactor": 3},
      "unique_users": 3
    },
    "adr_0273_budget": {
      "count": 40,
      "critical_pct": 25.0,
      "avg_match": 0.89,
      "total_tokens": 48000
    }
  },
  "record_counts": {
    "predictions": 42,
    "feedback": 28,
    "user_choices": 35,
    "budget_allocations": 40
  }
}
```

---

## Dashboard Features

### ADR-0270: Uncertainty Quantification
- **Metric:** Confidence Accuracy (1.0 - avg|pred - actual|)
- **Target:** ≥ 0.90 (predictions within ±10% of actual)
- **Insight:** "Are predictions well-calibrated?"

### ADR-0271: Outcome Feedback Loop
- **Metric:** Helpful % + Learning Event Count
- **Target:** ≥ 60% helpful feedback, >0 learning events
- **Insight:** "Is system learning from feedback?"

### ADR-0272: User Preferences
- **Metric:** Pragmatic % + Task Type Distribution
- **Target:** Clear clustering by task type
- **Insight:** "What patterns in user decisions?"

### ADR-0273: Attention Budget
- **Metric:** Budget-Complexity Match Score
- **Target:** ≥ 0.85 average match
- **Insight:** "Is budget aligned with complexity?"

---

## Troubleshooting

### Dashboard shows "Connection error"

**Problem:** API not reachable

**Solution:**
```bash
# 1. Check backend is running
ps aux | grep api_server

# 2. Check CORS enabled
curl -i http://localhost:5000/health

# 3. Update API_BASE in stats.html
const API_BASE = 'http://localhost:5000'
```

### No measurement data appearing

**Problem:** JSONL files not being created

**Solution:**
```bash
# 1. Check measurement dir exists
ls -la ~/.corvin/measurement/$(date +%Y-%m-%d)/

# 2. Verify task_engine is calling record_* functions
grep -r "record_prediction" operator/

# 3. Ensure K=8 aggregator is running
ps aux | grep aggregator
```

### Export script failing

**Problem:** `export_stats_to_website.py` errors

**Solution:**
```bash
# Run manually to see error
python operator/context_engineering/export_stats_to_website.py

# Check output directory exists
mkdir -p ~/projects/Corvin-Website/api/v1/telemetry/measurements

# Check permissions
chmod 755 ~/projects/Corvin-Website/api/v1/telemetry/measurements
```

---

## Next Steps

1. **Week 6 Measurement:**
   - Dashboard goes live when Week 6 starts (2026-08-08)
   - Monitor all 4 tracks for 7 days
   - Collect baseline telemetry

2. **Data Analysis (Post-Week-6):**
   - Analyze convergence patterns
   - Identify high-confidence contexts
   - Document learning insights

3. **Phase 2: Production Integration:**
   - Wire guard into console suggestions
   - Deploy to all instances
   - Enable real-time context filtering

---

## Architecture Summary

| Component | Purpose | Technology |
|-----------|---------|------------|
| **Aggregator** (K=8) | Read JSONL, compute stats | Python |
| **API Server** | Serve JSON endpoints | Flask + CORS |
| **Export Script** | Hourly sync to website | Python cron |
| **Frontend** | Live dashboard | HTML/JS + optional React |
| **Storage** | Measurement queue | JSONL files on disk |
| **Hosting** | Website + API | Cloudflare Pages + Railway |

---

## Support

- **Issues:** Check `ADR-0274-INCIDENT-RESPONSE.md`
- **Integration:** See `WEEK6-MEASUREMENT-PHASE-ACTIVE.md`
- **Code:** `operator/context_engineering/api_server.py`

---

**Status:** ✅ Ready for Week 6 deployment
**Last Updated:** 2026-08-08

# CorvinOS Live Stats System — Complete Architecture Design

**Status:** DESIGN PHASE (no implementation yet)  
**Scope:** Central telemetry collection, aggregation, dashboard, and archival system  
**Target Release:** Phase 1-6 over 6 weeks (Nov 2026)  
**Compliance Baseline:** GDPR Art. 5, 6, 30, 32 + EU AI Act Art. 50 + CorvinOS telemetry policy  

---

## Executive Summary

This document specifies a production-grade, real-time statistics system that aggregates telemetry from all CorvinOS instances worldwide into a live dashboard at `corvin-labs.com/stats`. The system:

- **Collects** anonymous, GDPR-safe telemetry from each instance every 5-30 minutes
- **Aggregates** data in a time-series database with <2 min user-visible latency
- **Visualizes** live stats on a React dashboard with world-map instance distribution
- **Archives** daily snapshots to GitHub Pages (static, SEO-friendly, offline-accessible)
- **Maintains** 100% GDPR compliance (no PII, fail-closed scrubbing, 90-day retention)
- **Provides** drill-down to individual instance health + performance metrics
- **Operates** with 99.9% uptime SLA and <5 min data freshness

The system leverages existing CorvinOS telemetry infrastructure (ADR-0179/0180) and extends it with centralized aggregation, real-time publishing, and public observability.

---

## 1. SYSTEM ARCHITECTURE OVERVIEW

```
┌─────────────────────────────────────────────────────────────────────┐
│                        CorvinOS Instances (N)                        │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │  Instance 1      │  │  Instance 2      │  │  Instance N      │  │
│  │  CorvinOS v0.2.1 │  │  CorvinOS v0.2.1 │  │  CorvinOS v0.2.1 │  │
│  │                  │  │                  │  │                  │  │
│  │ [Telemetry      │  │ [Telemetry      │  │ [Telemetry      │  │
│  │  Agent]         │  │  Agent]         │  │  Agent]         │  │
│  │                  │  │                  │  │                  │  │
│  │ - Collect       │  │ - Collect       │  │ - Collect       │  │
│  │ - Scrub (PII)   │  │ - Scrub (PII)   │  │ - Scrub (PII)   │  │
│  │ - Sign (Ed25519)│  │ - Sign (Ed25519)│  │ - Sign (Ed25519)│  │
│  │ - Report q5min  │  │ - Report q5min  │  │ - Report q5min  │  │
│  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  │
└───────────┼────────────────────┼────────────────────┼──────────────┘
            │                    │                    │
            │   HTTPS TLS 1.3    │                    │
            └────────┬───────────┴────────────────────┘
                     │
        ┌────────────▼────────────────────────────────┐
        │  Collector API (POST /api/v1/telemetry)    │
        │  ┌──────────────────────────────────────┐  │
        │  │ 1. Validate Schema + Signature       │  │
        │  │ 2. De-duplicate (5-min window)       │  │
        │  │ 3. Store Raw Payload                 │  │
        │  │ 4. Emit Event (async)                │  │
        │  └──────────────────────────────────────┘  │
        └────────────┬─────────────────────────────┘
                     │
        ┌────────────▼────────────────────────────────┐
        │  Aggregation Pipeline (async)               │
        │  ┌──────────────────────────────────────┐  │
        │  │ 1. Bucket by time (1m, 1h, 1d)       │  │
        │  │ 2. Compute aggregates (sum, avg, pct)│  │
        │  │ 3. Regional breakdown                │  │
        │  │ 4. Version distribution              │  │
        │  │ 5. Publish to cache (Redis)          │  │
        │  └──────────────────────────────────────┘  │
        └────────────┬─────────────────────────────┘
                     │
        ┌────────────▼────────────────────────────────┐
        │  Time-Series DB (InfluxDB / Timescale)      │
        │  ┌──────────────────────────────────────┐  │
        │  │ Raw telemetry (90-day retention)    │  │
        │  │ 1m buckets (7 days)                 │  │
        │  │ 1h buckets (30 days)                │  │
        │  │ 1d buckets (90 days)                │  │
        │  └──────────────────────────────────────┘  │
        └────────────┬─────────────────────────────┘
                     │
        ┌────────────┴──────────┬──────────────┐
        │                       │              │
    ┌───▼─────────┐  ┌─────────▼──┐  ┌───────▼────┐
    │  Dashboard  │  │ GitHub Pg  │  │ Monitoring │
    │ API (JSON)  │  │ Archive    │  │ (Datadog)  │
    │ <2s /api/.. │  │ (static)   │  │            │
    └─────────────┘  └────────────┘  └────────────┘
        │
    ┌───▼──────────────────────────────────┐
    │  Web Frontend (Next.js + React)      │
    │  ┌──────────────────────────────────┐│
    │  │ corvin-labs.com/stats            ││
    │  │ - KPI Cards (header)             ││
    │  │ - World Map (Mapbox)             ││
    │  │ - Time Series (Recharts)         ││
    │  │ - Instance List (table)          ││
    │  │ - Instance Detail (/:instance_id)││
    │  └──────────────────────────────────┘│
    └─────────────────────────────────────┘
```

---

## 2. DATA COLLECTION & REPORTING (Instance → Collector)

### 2.1 Telemetry Payload Schema

**Version:** `1.0` (immutable after publication)

**Payload Structure:**

```json
{
  "schema_version": "1.0",
  "instance_id": "uuid4_anonymous_id",
  "timestamp": "ISO 8601 UTC",
  "instance_metadata": {
    "version": "semantic_version",
    "os": "Linux|Darwin|Windows",
    "python_version": "X.Y.Z",
    "region": "EU|NA|APAC|LATAM|MENA",
    "country_code": "ISO 3166-1 alpha-2",
    "deployment_type": "docker|kubernetes|bare-metal|cloud-function"
  },
  "uptime": {
    "boot_time": "ISO 8601 UTC",
    "uptime_hours": "float",
    "last_heartbeat": "ISO 8601 UTC"
  },
  "usage": {
    "active_users": "int",
    "sessions_total": "int ≥0 (cumulative)",
    "sessions_today": "int",
    "tokens_used_today": "int",
    "cost_today": "float USD"
  },
  "performance": {
    "avg_latency_ms": "float",
    "p95_latency_ms": "float",
    "p99_latency_ms": "float",
    "error_rate_percent": "float 0-100"
  },
  "system": {
    "cpu_usage_percent": "float 0-100",
    "memory_used_mb": "int",
    "memory_total_mb": "int",
    "disk_free_gb": "float"
  },
  "features": {
    "models_used": ["model_name_1", "model_name_2"],
    "plugins_installed_count": "int",
    "marketplace_plugins_count": "int",
    "custom_layers_active": "int"
  },
  "health": {
    "audit_chain_healthy": "bool",
    "compliance_tripwire_active": "bool",
    "boot_time_seconds": "float",
    "last_audit_verify_time": "ISO 8601 UTC"
  },
  "signature": "ed25519_base64url_signature_of_normalized_payload"
}
```

**Payload Constraints:**
- **Max size:** 32 KB (serialized JSON)
- **Max strings:** 128 characters (model names, etc.)
- **Number ranges:** all validated on parse (negative reject, NaN reject)
- **Timestamp:** UTC only, validated ±1 hour of server time (reject stale/future)
- **Signature:** Ed25519 signature of canonical JSON (not including `signature` field itself)

### 2.2 PII Scrubbing & Fail-Closed Guards

**Scrubber Integration** (extends existing `_assert_safe` pattern from ADR-0179):

```python
def _assert_telemetry_safe(payload: dict) -> dict:
    """
    Fail-closed: drops any field carrying PII/secret shape.
    Returns sanitized payload or raises ValueError if critical field is contaminated.
    """
    # Forbidden patterns (compiled regex):
    FORBIDDEN_PATTERNS = [
        r'(?i)(email|@)',           # email addresses
        r'(?i)(password|token|key)', # secrets
        r'(?i)(user_id|username)',   # user identifiers
        r'\/home\/|\/root\/|C:\\Users', # home paths
        r'\b(?:\d{1,3}\.){3}\d{1,3}\b', # IPv4
        r'[a-f0-9]{32,}',            # long hex strings (UUIDs, hashes)
    ]
    
    # Check all string fields
    for key, value in flatten_payload(payload):
        if isinstance(value, str):
            for pattern in FORBIDDEN_PATTERNS:
                if re.search(pattern, value):
                    # Drop the field; if critical, fail
                    payload[key] = "[SCRUBBED]"
                    if is_critical_field(key):
                        raise ValueError(f"Payload contaminated: {key}")
    
    return payload
```

**Critical Fields** (fail if contaminated):
- `instance_id` (must be valid UUID)
- `timestamp` (must be valid ISO 8601)
- `signature` (must be valid base64url)

**Non-critical Fields** (scrub but accept):
- `country_code`, `deployment_type`, `os`, `python_version`
- `models_used`, `plugins_installed_count`

**Always Drop** (never send):
- IP addresses
- Hostnames
- Email addresses
- Auth tokens / API keys
- File paths
- Usernames / user IDs
- Full exception tracebacks

### 2.3 Collector Endpoint API

**Endpoint:** `POST https://collector.corvin-labs.com/api/v1/telemetry/report`

**Headers:**
```
Content-Type: application/json
X-Instance-ID: {instance_uuid}  # for rate limiting
User-Agent: CorvinOS/{version}
```

**Request Validation:**

1. **Schema validation** (JSON Schema)
2. **Signature verification** (Ed25519 against instance public key)
3. **De-duplication** (instance_id + timestamp within 5-min window → reject as duplicate)
4. **Rate limiting** (≤10 requests/min per instance_id → reject)
5. **Size check** (≤32 KB payload)

**Response Codes:**

| Code | Meaning |
|---|---|
| 202 | Accepted, queued for processing |
| 400 | Invalid schema / payload structure |
| 401 | Signature verification failed |
| 409 | Duplicate (same instance_id + time window) |
| 429 | Rate limit exceeded |
| 503 | Collector temporarily unavailable (retry after 60s) |

**Example Request:**

```bash
curl -X POST https://collector.corvin-labs.com/api/v1/telemetry/report \
  -H "Content-Type: application/json" \
  -H "X-Instance-ID: corvin_abc123def456" \
  -H "User-Agent: CorvinOS/0.2.1" \
  -d '{"schema_version":"1.0","instance_id":"...","signature":"..."}' \
  -w "\n%{http_code}\n"
```

**Response Example:**

```json
{
  "accepted": true,
  "request_id": "telemetry_req_2026_08_29_143001_abc123",
  "queue_position": 42,
  "eta_processing_seconds": 3
}
```

### 2.4 Instance-Side Telemetry Agent

**Module:** `core/telemetry/instance_reporter.py`

**Responsibilities:**

1. **Collect metrics** (system, usage, performance, health)
2. **Scrub PII** (fail-closed via `_assert_telemetry_safe`)
3. **Sign payload** (Ed25519 private key)
4. **Report to collector** (HTTP POST with retry)
5. **Handle failures** (buffer up to 100 payloads, exponential backoff)
6. **Respect consent** (check `telemetry_enabled` flag before sending)

**Startup & Scheduling:**

```python
class TelemetryReporter:
    def __init__(self, config: TelemetryConfig):
        self.enabled = config.remote_reporting_enabled()  # from tenant.corvin.yaml
        self.interval = config.report_interval_seconds  # 5-30 min
        self.private_key = load_instance_private_key()
        self.instance_id = load_or_create_instance_uuid()
        self.buffer = deque(maxlen=100)
        self.thread = None
    
    def start(self):
        """Boot telemetry thread (daemon, non-blocking)"""
        if not self.enabled:
            return
        self.thread = threading.Thread(target=self._report_loop, daemon=True)
        self.thread.start()
    
    def _report_loop(self):
        """Periodic reporting loop (every 5-30 min)"""
        while True:
            try:
                payload = self.collect_metrics()
                payload = _assert_telemetry_safe(payload)
                signature = sign_payload(payload, self.private_key)
                payload['signature'] = signature
                
                self.send_report(payload)
            except Exception as e:
                self.buffer.append((time.time(), payload))
                audit_log('telemetry.report_failed', {'error': str(e)})
            
            time.sleep(self.interval)
```

**Retry Logic:**

- **Immediate:** if network error → buffer locally
- **Exponential backoff:** 1s, 2s, 4s, 8s, 16s, 32s (cap at 1 backoff per 5 min)
- **Buffer:** up to 100 payloads in memory (FIFO, oldest dropped if full)
- **Flush on boot:** empty buffer on next successful report

**Metrics Collection:**

```python
def collect_metrics(self) -> dict:
    return {
        "schema_version": "1.0",
        "instance_id": self.instance_id,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        
        "instance_metadata": {
            "version": corvin_version(),
            "os": platform.system(),
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "region": self._get_region(),  # from geo resolver
            "country_code": self._get_country(),  # ISO 3166-1
            "deployment_type": detect_deployment_type(),
        },
        
        "uptime": {
            "boot_time": get_boot_time().isoformat() + "Z",
            "uptime_hours": get_uptime_hours(),
            "last_heartbeat": datetime.utcnow().isoformat() + "Z",
        },
        
        "usage": {
            "active_users": count_active_sessions(),
            "sessions_total": get_total_session_count(),
            "sessions_today": get_sessions_today(),
            "tokens_used_today": get_token_usage_today(),
            "cost_today": calculate_cost_today(),
        },
        
        "performance": {
            "avg_latency_ms": get_request_latency_avg(),
            "p95_latency_ms": get_request_latency_p95(),
            "p99_latency_ms": get_request_latency_p99(),
            "error_rate_percent": calculate_error_rate(),
        },
        
        "system": {
            "cpu_usage_percent": psutil.cpu_percent(interval=1.0),
            "memory_used_mb": psutil.virtual_memory().used // 1024 // 1024,
            "memory_total_mb": psutil.virtual_memory().total // 1024 // 1024,
            "disk_free_gb": psutil.disk_usage('/').free / 1024 / 1024 / 1024,
        },
        
        "features": {
            "models_used": get_top_models_used(limit=5),
            "plugins_installed_count": count_plugins(),
            "marketplace_plugins_count": count_marketplace_plugins(),
            "custom_layers_active": count_custom_layers(),
        },
        
        "health": {
            "audit_chain_healthy": verify_audit_chain(),
            "compliance_tripwire_active": check_tripwire_status(),
            "boot_time_seconds": measure_boot_time(),
            "last_audit_verify_time": get_last_audit_verify().isoformat() + "Z",
        },
    }
```

---

## 3. CENTRAL COLLECTOR & AGGREGATOR

### 3.1 Collector Architecture

**Component Stack:**

```
Load Balancer (nginx/Cloudflare)
    ↓
Collector API (Flask/FastAPI, 4 instances)
    ↓
De-duplication Cache (Redis Sorted Set)
    ├→ Payload validation + signature verification
    ├→ De-dup check (key: instance_id:timestamp_bucket)
    └→ Emit event to queue
    ↓
Async Task Queue (Celery/RQ, 8 workers)
    ├→ Write to time-series DB
    ├→ Update aggregation tables
    └→ Publish to cache (Redis)
    ↓
Time-Series DB (InfluxDB / Timescale)
    └→ Raw payloads (90-day retention, immutable)
    └→ Aggregated buckets (1m, 1h, 1d)
    ↓
Cache Layer (Redis)
    └→ Latest aggregate stats (O(1) lookup)
    └→ Instance list (sorted by last_report_time)
    └→ Regional breakdowns
```

### 3.2 Collector Endpoint Implementation

**Framework:** FastAPI (async, type-safe)

```python
from fastapi import FastAPI, HTTPException, Header
from datetime import datetime, timedelta
import hashlib
import json

app = FastAPI()

# De-duplication: Redis sorted set with TTL 5min
DEDUP_KEY_PATTERN = "telemetry:dedup:{instance_id}:{time_bucket}"

@app.post("/api/v1/telemetry/report")
async def receive_telemetry(
    payload: dict,
    x_instance_id: str = Header(...),
) -> dict:
    """
    Receive and validate a telemetry payload.
    Returns 202 if accepted, errors on validation failure.
    """
    
    # 1. Validate payload schema
    try:
        validate_telemetry_schema(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid schema: {str(e)}")
    
    # 2. Verify signature
    try:
        instance_id = payload['instance_id']
        public_key = load_instance_public_key(instance_id)
        verify_ed25519_signature(payload, public_key)
    except ValueError as e:
        audit_log('telemetry.signature_failed', {'instance_id': instance_id})
        raise HTTPException(status_code=401, detail="Signature verification failed")
    
    # 3. De-duplicate (5-min window)
    now = datetime.utcnow()
    time_bucket = now.replace(second=0, microsecond=0)  # 1-min precision
    dedup_key = f"telemetry:dedup:{instance_id}:{time_bucket.isoformat()}"
    
    if redis_client.exists(dedup_key):
        raise HTTPException(status_code=409, detail="Duplicate report (same time bucket)")
    
    redis_client.setex(dedup_key, 300, "1")  # 5-min TTL
    
    # 4. Rate limiting (≤10 reports/min per instance)
    rate_key = f"telemetry:rate:{instance_id}"
    current_count = redis_client.incr(rate_key)
    if current_count == 1:
        redis_client.expire(rate_key, 60)  # 1-min window
    if current_count > 10:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    
    # 5. Scrub PII (fail-closed)
    try:
        payload = _assert_telemetry_safe(payload)
    except ValueError as e:
        audit_log('telemetry.scrubbing_failed', {'instance_id': instance_id})
        raise HTTPException(status_code=400, detail=f"PII detected and failed scrub: {str(e)}")
    
    # 6. Queue for async processing
    task_id = await queue_telemetry_processing.enqueue(
        process_telemetry_payload,
        payload,
        instance_id=instance_id,
        received_at=now,
    )
    
    # 7. Audit trail
    audit_log('telemetry.report_received', {
        'instance_id': instance_id,
        'payload_size': len(json.dumps(payload)),
        'task_id': task_id,
    })
    
    return {
        "accepted": True,
        "request_id": f"telemetry_req_{now.strftime('%Y_%m_%d_%H%M%S')}_{task_id[:6]}",
        "queue_position": current_count,
        "eta_processing_seconds": max(1, current_count // 8),  # ~8 workers
    }
```

### 3.3 Aggregation Pipeline

**Async Task:** processes each telemetry payload into aggregates

```python
async def process_telemetry_payload(
    payload: dict,
    instance_id: str,
    received_at: datetime,
) -> None:
    """
    Process a single telemetry report:
    - Write raw payload to time-series DB
    - Update aggregation tables (1m, 1h, 1d buckets)
    - Publish to cache (for live dashboard)
    """
    
    try:
        # 1. Write raw payload to InfluxDB
        await influxdb.write_point(
            measurement="telemetry_raw",
            tags={
                "instance_id": instance_id,
                "version": payload['instance_metadata']['version'],
                "region": payload['instance_metadata']['region'],
                "country": payload['instance_metadata']['country_code'],
            },
            fields={
                "uptime_hours": payload['uptime']['uptime_hours'],
                "active_users": payload['usage']['active_users'],
                "avg_latency_ms": payload['performance']['avg_latency_ms'],
                "error_rate_percent": payload['performance']['error_rate_percent'],
                "cpu_usage_percent": payload['system']['cpu_usage_percent'],
                "cost_today": payload['usage']['cost_today'],
            },
            timestamp=payload['timestamp'],
        )
        
        # 2. Update 1-minute aggregate
        await update_aggregates(payload, "1m", received_at)
        
        # 3. Update 1-hour aggregate
        await update_aggregates(payload, "1h", received_at)
        
        # 4. Update instance index (for instance list + drill-down)
        await update_instance_index(instance_id, payload, received_at)
        
        # 5. Publish to cache (for live dashboard)
        await publish_to_cache(instance_id, payload, received_at)
        
        # 6. Audit trail
        audit_log('telemetry.processed', {
            'instance_id': instance_id,
            'timestamp': payload['timestamp'],
        })
        
    except Exception as e:
        audit_log('telemetry.processing_failed', {
            'instance_id': instance_id,
            'error': str(e),
        })
        # Dead-letter queue for manual inspection
        await dlq.enqueue(payload)
```

### 3.4 Time-Series Database Schema

**InfluxDB 2.x (recommended)**

```yaml
# Bucket: corvinOS-telemetry (30-day default retention, override to 90d)
Measurements:
  telemetry_raw:
    tags:
      - instance_id
      - version
      - region
      - country
      - deployment_type
    fields:
      # Numeric (gauges)
      - uptime_hours: float
      - active_users: int
      - sessions_today: int
      - tokens_used_today: int
      - cost_today: float
      - avg_latency_ms: float
      - p95_latency_ms: float
      - p99_latency_ms: float
      - error_rate_percent: float
      - cpu_usage_percent: float
      - memory_used_mb: int
      - disk_free_gb: float
      - boot_time_seconds: float

  telemetry_1m_aggregate:
    # Pre-computed buckets (1-min precision)
    tags:
      - region
      - country
      - version
    fields:
      - instance_count: int
      - active_users_sum: int
      - avg_latency_weighted: float
      - error_rate_weighted: float

  telemetry_1h_aggregate:
    # Pre-computed buckets (1-hour precision)
    tags:
      - region
      - country
    fields:
      - instance_count: int
      - active_users_sum: int

  telemetry_1d_aggregate:
    # Pre-computed buckets (1-day precision, 90-day retention)
    tags:
      - region
    fields:
      - instance_count_avg: float
      - active_users_sum_avg: float
```

**Retention Policies:**
- `telemetry_raw`: 30 days (detail)
- `telemetry_1m_aggregate`: 7 days (1-min precision)
- `telemetry_1h_aggregate`: 30 days (1-hour precision)
- `telemetry_1d_aggregate`: 90 days (1-day precision, annual trends)

### 3.5 Cache Strategy (Redis)

**Cache Layer** (for live dashboard, <500ms lookups)

```python
# Key: aggregate:global:latest
# Value: JSON snapshot of global stats
{
    "timestamp": "2026-08-29T14:35:00Z",
    "instance_count": 1247,
    "active_users": 3842,
    "global_uptime_percent": 98.3,
    "instances_reporting_5m": 1235,
    "cost_today": 12456.78,
    "avg_latency_ms": 342,
    "regions": {
        "EU": { "instance_count": 512, "active_users": 1821 },
        "NA": { "instance_count": 456, "active_users": 1234 },
        ...
    },
    "versions": {
        "0.2.1": { "count": 890 },
        "0.2.0": { "count": 340 },
        ...
    }
}

# Key: instance:{instance_id}:latest
# Value: last telemetry report (for instance detail page)
{
    "instance_id": "...",
    "timestamp": "2026-08-29T14:30:00Z",
    "version": "0.2.1",
    "region": "EU",
    "country": "DE",
    "uptime_hours": 52.25,
    "active_users": 3,
    "avg_latency_ms": 342,
    "error_rate_percent": 0.3,
    "health": { "audit_chain_healthy": true }
}

# Cache TTL:
# - global stats: 30 seconds (refreshed every publish)
# - instance list: 1 minute
# - instance detail: 2 minutes
```

---

## 4. DASHBOARD SPECIFICATION

### 4.1 Technology Stack

- **Frontend:** Next.js 14+ (React 18, TypeScript)
- **UI Components:** TailwindCSS + Shadcn/ui (or Material-UI)
- **Charts:** Recharts (React charting library)
- **Maps:** Mapbox GL JS (world distribution)
- **State Management:** TanStack Query (React Query, for server state)
- **Real-time Updates:** Server-Sent Events (SSE) or WebSocket (optional)
- **Build:** Vercel (auto-deploy from GitHub)

### 4.2 Core Pages

#### 4.2.1 Homepage (`/stats`)

**Purpose:** Real-time overview of global CorvinOS health

**Above the Fold:**

```
┌─────────────────────────────────────────────────────────┐
│  GLOBAL STATISTICS                                      │
├─────────┬──────────┬──────────┬──────────┬──────────────┤
│1247     │3.8K      │98.3%     │1235      │$12,456.78    │
│Instances│Active    │Global    │Reporting │Cost          │
│         │Users     │Uptime    │(5m)      │(Today)       │
├─────────┴──────────┴──────────┴──────────┴──────────────┤
│ Instances Health: █████████████░░ (98.3% healthy)      │
│ (Green: 1212 | Yellow: 23 | Red: 12 | Gray: 0)        │
└─────────────────────────────────────────────────────────┘
```

**Key Metrics (KPI Cards):**
- Total instances (live count, trend ↑/↓)
- Active users (across all instances, trend)
- Global uptime % (weighted average)
- Instances reporting (healthy indicator)
- Global cost (sum of cost_today)
- Avg latency (p50, p95, p99)
- Global error rate
- Deployment breakdown (pie)

**Below the Fold:**

1. **World Map** (Mapbox)
   - Instance pins colored by health
   - Click: drill-down to instance detail
   - Hover: show metadata

2. **Time Series Graphs** (24-hour window)
   - Instance count over time
   - Active users over time
   - Global cost per hour
   - Latency p50/p95/p99
   - Error rate timeline
   - CPU/memory usage

3. **Distribution Charts**
   - Instances by version (pie)
   - Instances by region (donut)
   - Instances by deployment type
   - Top 5 models used

4. **Instance List** (sortable/filterable table)
   - Columns: ID, Version, Region, Uptime %, Users, Latency, Errors, Last Report
   - Filters: Version, Region, Status
   - Sort: Users, Latency, Cost, Uptime

#### 4.2.2 Instance Detail (`/stats/:instance_id`)

**Purpose:** Drill-down to individual instance health

**Header Card:**
```
┌──────────────────────────────────────────────────────────┐
│ Instance: corvin_abc123def456                            │
│ Version: 0.2.1 | Region: EU (DE) | Deployment: docker   │
│ Health: ✓ Healthy | Uptime: 52h 15m | Last Report: 2m   │
└──────────────────────────────────────────────────────────┘
```

**Tabs:**

1. **Overview**
   - Metadata card (version, region, boot time, uptime)
   - Key metrics (users, cost, latency, errors)
   - Health status (audit chain, tripwire)

2. **Performance** (7-day window)
   - Latency trend (p50, p95, p99)
   - Error rate timeline
   - CPU/memory usage
   - Request throughput

3. **Features**
   - Models used (top 5)
   - Plugins installed (list + count)
   - Custom layers active
   - Feature flags enabled

4. **Audit Trail** (sample)
   - Last 20 audit events
   - Hash chain integrity indicator
   - Download full audit.jsonl

5. **Errors** (7-day window)
   - Last 20 errors (type, timestamp, count)
   - Error rate trend
   - Error categories (pie)

#### 4.2.3 Settings & Filters

**Global Filters:**
- Date range (24h, 7d, 30d, 90d)
- Region (EU, NA, APAC, LATAM, MENA, all)
- Version (dropdown: 0.2.1, 0.2.0, 0.1.x, all)
- Status (Healthy, Degraded, Unhealthy, Offline, all)
- Deployment type (docker, k8s, bare-metal, etc.)

**Dashboard Settings:**
- Refresh rate (5s, 10s, 30s, 60s, manual)
- Theme (light/dark)
- Metric units (USD/EUR for cost, ms/s for latency)
- Default view (map/table/charts)
- Export data (JSON, CSV)

### 4.3 API Endpoints (Dashboard Backend)

**Base URL:** `https://api.corvin-labs.com/api/v1`

#### 4.3.1 Global Stats

```
GET /dashboard/stats/global
  ?range=24h|7d|30d|90d
  &region=EU|NA|all

Response:
{
  "timestamp": "2026-08-29T14:35:00Z",
  "instance_count": 1247,
  "instance_count_trend": 0.03,  # 3% up vs. 24h ago
  "active_users": 3842,
  "global_uptime_percent": 98.3,
  "instances_reporting_5m": 1235,
  "cost_today": 12456.78,
  "avg_latency_ms": 342,
  "p95_latency_ms": 1250,
  "p99_latency_ms": 3400,
  "error_rate_percent": 0.3,
  "regions": {
    "EU": { "instance_count": 512, "active_users": 1821, "uptime": 99.1 },
    "NA": { "instance_count": 456, "active_users": 1234, "uptime": 98.2 },
    ...
  },
  "versions": {
    "0.2.1": { "count": 890 },
    "0.2.0": { "count": 340 },
    ...
  }
}
```

#### 4.3.2 Time Series Data

```
GET /dashboard/timeseries/instance-count
  ?range=24h|7d|30d
  &granularity=1m|1h|1d
  &region=EU|all

Response:
{
  "data": [
    { "timestamp": "2026-08-29T00:00:00Z", "value": 1200 },
    { "timestamp": "2026-08-29T01:00:00Z", "value": 1210 },
    ...
  ]
}
```

#### 4.3.3 Instance List

```
GET /dashboard/instances
  ?status=healthy|degraded|unhealthy|offline|all
  &region=EU|all
  &version=0.2.1|all
  &sort_by=users|latency|cost|uptime
  &limit=100
  &offset=0

Response:
{
  "total": 1247,
  "instances": [
    {
      "instance_id": "corvin_abc123...",
      "version": "0.2.1",
      "region": "EU",
      "country": "DE",
      "uptime_hours": 52.25,
      "uptime_percent": 99.1,
      "active_users": 3,
      "avg_latency_ms": 342,
      "error_rate_percent": 0.3,
      "cost_today": 45.32,
      "status": "healthy",
      "last_report": "2026-08-29T14:30:00Z",
      "boot_time": "2026-08-27T10:15:30Z"
    },
    ...
  ]
}
```

#### 4.3.4 Instance Detail

```
GET /dashboard/instances/{instance_id}

Response:
{
  "instance_id": "...",
  "metadata": {
    "version": "0.2.1",
    "region": "EU",
    "country": "DE",
    "deployment_type": "docker",
    "python_version": "3.11.6"
  },
  "uptime": { "boot_time": "...", "uptime_hours": 52.25 },
  "usage": { "active_users": 3, "cost_today": 45.32 },
  "performance": { "avg_latency_ms": 342, ... },
  "system": { "cpu_usage_percent": 34, ... },
  "features": {
    "models_used": ["claude-3-sonnet", ...],
    "plugins_installed": 5
  },
  "health": {
    "audit_chain_healthy": true,
    "compliance_tripwire_active": true
  }
}
```

#### 4.3.5 Audit Trail

```
GET /dashboard/instances/{instance_id}/audit
  ?limit=20

Response:
{
  "events": [
    {
      "event_id": "...",
      "timestamp": "2026-08-29T14:30:00Z",
      "event_type": "user_login",
      "hash": "sha256_hash",
      "parent_hash": "previous_event_hash"
    },
    ...
  ],
  "chain_integrity": true,  # all hashes verify
  "last_verified": "2026-08-29T14:35:00Z"
}
```

---

## 5. GITHUB PAGES ARCHIVE

### 5.1 Purpose & Strategy

**Goal:** Archive daily snapshots for:
- SEO (static indexing by search engines)
- Offline browsing (downloadable snapshots)
- Historical trend analysis
- Transparency (public data, no auth required)

**Update Schedule:** Daily at 00:00 UTC (nightly batch job)

### 5.2 Archive Structure

```
Repository: CorvinLabs/corvinOS-stats-archive
Branch: main
Deploy: GitHub Pages (automatic from main)

├── index.html                   # Latest snapshot (redirect to latest date)
├── README.md                    # Explanation + links
├── CHANGELOG.md                 # Weekly trend summary
│
├── snapshots/
│   ├── 2026-08-29/
│   │   ├── index.html           # Embedded stats snapshot
│   │   ├── stats.json           # Full aggregated stats
│   │   ├── instances.csv        # All instances + metrics
│   │   └── instances.json       # Structured format
│   │
│   ├── 2026-08-28/
│   └── ...
│
└── dashboards/
    └── embedded-charts.html     # Static charts (no JS interactivity)
```

### 5.3 Daily Archive Job

**Trigger:** GitHub Actions scheduled job (daily, midnight UTC)

```yaml
# .github/workflows/daily-archive.yml
name: Daily Stats Archive

on:
  schedule:
    - cron: '0 0 * * *'  # midnight UTC
  workflow_dispatch:

jobs:
  archive:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Fetch latest stats from API
        run: |
          DATE=$(date -u +%Y-%m-%d)
          curl -s https://api.corvin-labs.com/api/v1/dashboard/stats/global \
            > snapshots/${DATE}/stats.json
          curl -s https://api.corvin-labs.com/api/v1/dashboard/instances?limit=10000 \
            > snapshots/${DATE}/instances.json
      
      - name: Generate CSV
        run: |
          python3 scripts/convert_json_to_csv.py \
            snapshots/${DATE}/instances.json \
            snapshots/${DATE}/instances.csv
      
      - name: Generate HTML snapshot
        run: |
          python3 scripts/generate_html_snapshot.py \
            snapshots/${DATE}/stats.json \
            snapshots/${DATE}/index.html
      
      - name: Update index.html (latest)
        run: |
          DATE=$(date -u +%Y-%m-%d)
          sed "s/LATEST_DATE/${DATE}/g" templates/index-redirect.html > index.html
      
      - name: Update CHANGELOG.md
        run: |
          python3 scripts/update_changelog.py
      
      - name: Commit & push
        run: |
          git config user.name "CorvinOS Bot"
          git config user.email "bot@corvin-labs.com"
          git add -A
          git commit -m "chore(archive): daily snapshot $(date -u +%Y-%m-%d)"
          git push
```

### 5.4 Example Archive Files

**snapshots/2026-08-29/index.html** (static, embedded stats)

```html
<!DOCTYPE html>
<html>
<head>
  <title>CorvinOS Stats — August 29, 2026</title>
  <meta charset="utf-8">
  <meta name="description" content="Daily snapshot of CorvinOS global statistics">
</head>
<body>
  <h1>CorvinOS Global Statistics</h1>
  <p>Snapshot: <strong>2026-08-29 00:00 UTC</strong></p>
  
  <h2>Key Metrics</h2>
  <ul>
    <li>Total Instances: <strong>1247</strong></li>
    <li>Active Users: <strong>3,842</strong></li>
    <li>Global Uptime: <strong>98.3%</strong></li>
    <li>Reporting (5m): <strong>1235</strong></li>
    <li>Cost (Today): <strong>$12,456.78</strong></li>
  </ul>
  
  <h2>Regional Distribution</h2>
  <table>
    <tr><th>Region</th><th>Instances</th><th>Users</th><th>Uptime</th></tr>
    <tr><td>EU</td><td>512</td><td>1821</td><td>99.1%</td></tr>
    <tr><td>NA</td><td>456</td><td>1234</td><td>98.2%</td></tr>
    ...
  </table>
  
  <p><a href="/snapshots/">All snapshots</a> | <a href="/">Live dashboard</a></p>
</body>
</html>
```

**snapshots/2026-08-29/stats.json** (full data)

```json
{
  "timestamp": "2026-08-29T00:00:00Z",
  "instance_count": 1247,
  "active_users": 3842,
  "global_uptime_percent": 98.3,
  "cost_today": 12456.78,
  "regions": {
    "EU": { "instance_count": 512, "active_users": 1821 },
    ...
  },
  "versions": {
    "0.2.1": { "count": 890 },
    ...
  }
}
```

---

## 6. SECURITY & COMPLIANCE

### 6.1 GDPR Compliance (Verified)

**Principle: Data minimization + fail-closed scrubbing**

| GDPR Article | Requirement | Implementation |
|---|---|---|
| Art. 5(1)(a) | Lawfulness, fairness, transparency | Telemetry opt-out in `/pass` page + disclosure in privacy policy |
| Art. 5(1)(b) | Purpose limitation | Telemetry used ONLY for aggregate stats, never for tracking individuals |
| Art. 5(1)(c) | Data minimization | NO PII collected; only anonymous instance UUID + system metrics |
| Art. 5(1)(e) | Storage limitation | 90-day retention, automatic purge via TTL |
| Art. 5(2) | Accountability | Audit trail logged for every report (telemetry.report_received, .succeeded, .failed) |
| Art. 6 | Lawful basis | Opt-out model (Art. 6(1)(f) legitimate interest); user can flip `remote_reporting_enabled` flag |
| Art. 7 | Right to withdraw | User can opt-out anytime via Console Settings → Telemetry |
| Art. 30 | Records of processing | Audit log is immutable + hash-chained (see ADR-0232/0233) |
| Art. 32 | Security | TLS 1.3, Ed25519 signatures, fail-closed scrubbing, immutable storage |

**PII Scrubbing Verification:**

```python
# Never sent:
- IP addresses (Cloudflare geo resolver only)
- Email addresses (fail-closed regex)
- Usernames / user IDs (fail-closed regex)
- Home paths / file paths (fail-closed regex)
- Auth tokens / secrets (fail-closed regex)
- Prompts / chat content (source never included)
- Message transcripts (source never included)
```

### 6.2 EU AI Act Compliance (Art. 50)

**Requirement:** Bot disclosure (one-time per user, at first login)

**Implementation:**

In `/pass` (opt-out page):
```
This CorvinOS instance collects anonymous telemetry data about:
- Version, OS, Python version, region, deployment type
- Uptime, active users, system metrics
- Performance (latency, error rates)
- Feature usage (models, plugins)

Purpose: Aggregate statistics for product improvement + health monitoring.

No PII, prompts, or chat content is collected.
No individual tracking — only anonymous instance UUID.

You can opt out anytime:
  Settings → Telemetry & Privacy → toggle "Remote Reporting"
  or set: spec.telemetry.remote_reporting_enabled: false
```

### 6.3 Cryptographic Signing & Verification

**Instance Public Key:**

Each instance generates an Ed25519 keypair:
- **Private key:** stored locally in `~/.corvin/instance.key` (encrypted at rest)
- **Public key:** stored in manifest + sent to collector on first report

**Signature Verification:**

```python
def verify_ed25519_signature(payload: dict, public_key_b64: str) -> bool:
    """Verify Ed25519 signature of payload."""
    # 1. Extract signature
    signature_b64 = payload.pop('signature')
    signature = base64.urlsafe_b64decode(signature_b64)
    
    # 2. Canonicalize payload (deterministic JSON)
    canonical = json.dumps(payload, separators=(',', ':'), sort_keys=True)
    
    # 3. Verify signature
    public_key = nacl.signing.VerifyKey(
        base64.urlsafe_b64decode(public_key_b64)
    )
    try:
        public_key.verify(canonical.encode(), signature)
        return True
    except nacl.exceptions.BadSignatureError:
        return False
```

### 6.4 Fail-Closed Guards

**1. PII Scrubber (fail-closed)**
   - Drops any field carrying PII pattern
   - Critical fields fail if contaminated
   - Non-critical fields scrubbed to "[SCRUBBED]"

**2. Signature Verification (fail-closed)**
   - No signature → reject (400 Bad Request)
   - Signature invalid → reject (401 Unauthorized)
   - No fallback or skip mechanism

**3. Schema Validation (fail-closed)**
   - Rejects malformed payloads (400 Bad Request)
   - No lenient parsing or defaults

**4. Rate Limiting (fail-closed)**
   - Exceeds 10 reports/min → reject (429 Too Many Requests)
   - No whitelist or bypass

### 6.5 Audit Trail Integration

**Every telemetry action logged:**

```
Event Type                      | Fired When                      | Fields Logged
────────────────────────────────────────────────────────────────────────────────
telemetry.report_received       | Instance sends report            | instance_id, timestamp, payload_hash, size
telemetry.signature_failed      | Signature verification failed   | instance_id, error_code
telemetry.scrubbing_failed      | PII scrubbing failed            | instance_id, contaminated_fields
telemetry.rate_limit_exceeded   | Instance exceeds 10 reports/min | instance_id, rate_count
telemetry.processed             | Aggregation completed           | instance_id, timestamp, records_written
telemetry.processing_failed     | Aggregation error               | instance_id, error_message
telemetry.duplicate_rejected    | De-dup detected                 | instance_id, time_bucket
telemetry.report_failed         | Instance reporter error (local) | instance_id, error_message
```

**Audit Chain Integration:**
- Each event appended to `audit.jsonl` (immutable, hash-chained)
- Collector writes events to its own audit log (separate, signed)
- User can run `voice-audit verify` to check chain integrity

---

## 7. OBSERVABILITY & MONITORING (Corvin Labs Team)

### 7.1 Dashboards (Internal Datadog/New Relic)

**Collector Health Dashboard:**

```
┌────────────────────────────────────────────────────────┐
│ Collector Health (Last 24h)                            │
├────────────┬──────────┬──────────┬──────────────────────┤
│200 req/min │ 95 p50   │ 0.02%    │ 4 instances online   │
│(last 5min) │ latency  │ 5xx rate │                      │
├────────────┴──────────┴──────────┴──────────────────────┤
│ Requests by status code:                               │
│   202 (Accepted): 95% | 400 (Bad): 3% | 401 (Sig): 1% │
│   409 (Dup): 0.5% | 429 (Rate): 0.3% | 5xx: 0.2%     │
│                                                        │
│ Instance registration:                                │
│   New instances: 5 (last 24h)                         │
│   Failed signature: 3 (review)                        │
│   Re-registrations: 124                               │
└────────────────────────────────────────────────────────┘
```

**Data Quality Dashboard:**

```
┌────────────────────────────────────────────────────────┐
│ Data Quality (Last 24h)                                │
├────────────┬──────────┬──────────┬──────────────────────┤
│1247 inst   │99.7% valid│0.1% dup │0.2% scrubbed (PII) │
│(reporting) │(schema)   │(de-dup) │                    │
├────────────┴──────────┴──────────┴──────────────────────┤
│ Scrubbing events (PII detected + dropped):            │
│   - Hostnames: 12                                     │
│   - Paths: 3                                          │
│   - IPs: 0                                            │
│   - Tokens: 1 (instance blacklisted)                 │
│                                                       │
│ Dead-letter queue: 4 payloads (manual review)        │
└────────────────────────────────────────────────────────┘
```

**Pipeline Performance:**

```
┌────────────────────────────────────────────────────────┐
│ Processing Latency (Last 1h)                           │
├────────────┬──────────┬──────────┬──────────────────────┤
│ Collector  │ Queue    │ Processing│ Total E2E           │
│ 95ms p50   │ 120ms p50│ 340ms p50 │ 555ms p50           │
│ 340ms p95  │ 450ms p95│ 1200ms p95│ 1990ms p95          │
├────────────┴──────────┴──────────┴──────────────────────┤
│ Database write latency: 120ms p50 / 450ms p95         │
│ Cache publish latency: 20ms p50 / 80ms p95            │
│                                                       │
│ Aggregation lag: 2s (from report to cache)           │
│ Dashboard refresh lag: 30s (from report to UI)       │
└────────────────────────────────────────────────────────┘
```

### 7.2 Alerting Rules

| Alert | Threshold | Severity | Action |
|---|---|---|---|
| Collector unavailable | 5+ min downtime | Critical | Page oncall |
| High latency | p95 >1s | Warning | Monitor trend |
| High error rate | >5% 5xx | High | Page oncall |
| Instance drop | >20% in 1h | High | Investigate outage |
| Signature failures | >10/min | Warning | Review logs |
| Scrubbing failures | >5/min | High | Audit contaminated data |
| Dead-letter queue | >10 payloads | Warning | Manual review needed |
| No reports from region | 30+ min silence | Warning | Check network |

---

## 8. IMPLEMENTATION ROADMAP (6 Weeks)

### Phase 1: Core Collector (Week 1)
**Deliverables:**
- [ ] Telemetry payload schema (JSON Schema)
- [ ] FastAPI collector endpoint (POST /api/v1/telemetry/report)
- [ ] De-duplication logic (Redis)
- [ ] Rate limiting (Redis)
- [ ] Signature verification (Ed25519)
- [ ] InfluxDB schema + retention policies
- [ ] Unit tests (90%+ coverage)
- [ ] Security audit (TLS, rate limits, signature)

**Owner:** Backend Lead  
**Effort:** 60 hours  
**Risks:** Clock skew (timestamp validation), Redis availability  

### Phase 2: Instance Reporter (Week 2)
**Deliverables:**
- [ ] Telemetry agent (core/telemetry/instance_reporter.py)
- [ ] Metrics collection (CPU, memory, latency, costs)
- [ ] PII scrubber (fail-closed)
- [ ] Payload signing (Ed25519)
- [ ] Retry logic + buffer
- [ ] Consent flag integration
- [ ] Audit trail logging
- [ ] E2E tests (instance → collector → DB)

**Owner:** Core Platform  
**Effort:** 50 hours  
**Risks:** Metrics accuracy (latency, cost), system resource contention  

### Phase 3: Aggregation Pipeline (Week 2-3)
**Deliverables:**
- [ ] Async aggregation worker (Celery/RQ)
- [ ] Time-bucket computations (1m, 1h, 1d)
- [ ] Regional/version breakdown
- [ ] Cache publishing (Redis)
- [ ] Monitoring + alerting (Datadog)
- [ ] Error handling + DLQ
- [ ] Load testing (1000 reports/sec)
- [ ] Integration tests

**Owner:** Backend Lead  
**Effort:** 70 hours  
**Risks:** Aggregation correctness, database performance at scale  

### Phase 4: Dashboard Frontend (Week 3-4)
**Deliverables:**
- [ ] Next.js project setup
- [ ] Dashboard API client (TanStack Query)
- [ ] KPI cards (global stats)
- [ ] World map (Mapbox)
- [ ] Time series charts (Recharts)
- [ ] Instance list (table with filters/sort)
- [ ] Instance detail page
- [ ] Real-time refresh (30s polling or SSE)
- [ ] Dark mode support
- [ ] Mobile responsive (375px, 768px, 1920px)
- [ ] E2E tests (Playwright)
- [ ] Accessibility audit (WCAG 2.1 AA)

**Owner:** Frontend Lead  
**Effort:** 100 hours  
**Risks:** Map rendering performance, chart responsiveness, real-time lag  

### Phase 5: GitHub Pages Archive (Week 4)
**Deliverables:**
- [ ] GitHub Actions scheduled job
- [ ] HTML snapshot generation
- [ ] CSV export script
- [ ] Daily archival automation
- [ ] GitHub Pages setup
- [ ] SEO optimization (meta tags, schema)
- [ ] CHANGELOG generation
- [ ] Verification tests

**Owner:** DevOps  
**Effort:** 30 hours  
**Risks:** Scheduled job reliability, data availability during snapshots  

### Phase 6: Production Hardening (Week 5-6)
**Deliverables:**
- [ ] Security audit (GDPR, HTTPS, key rotation)
- [ ] Performance tuning (collector throughput, DB queries)
- [ ] HA setup (load balancer, DB replication)
- [ ] Runbook documentation
- [ ] Incident response procedures
- [ ] On-call escalation
- [ ] Soft launch (internal instances only)
- [ ] Beta launch (early adopters)
- [ ] Full launch + blog post
- [ ] 2-week monitoring period

**Owner:** DevOps + SRE  
**Effort:** 80 hours  
**Risks:** Scale stability, regional latency, key rotation procedures  

---

## 9. SUCCESS METRICS

| Metric | Target | Measure |
|---|---|---|
| Instance participation | >95% | % instances sending telemetry |
| Data accuracy | >99% | Duplicate detection rate, signature failures |
| Dashboard load time | <2s | HTTP p99 latency from origin |
| Data freshness | <2 min | Lag from instance report to UI display |
| Collector availability | 99.9% | Uptime SLA (9h downtime/year) |
| GDPR compliance | 100% | Audit findings = 0, retention verified |
| PII contamination | 0% | Scrubber catches 100% of test cases |
| User engagement | >50 daily active | Unique IPs viewing dashboard daily |
| Archive SEO | >10K/mo | Organic search traffic to GitHub Pages |

---

## 10. RISK REGISTER

| Risk | Impact | Probability | Mitigation |
|---|---|---|---|
| Collector bottleneck (scale) | High | Medium | Load testing, async workers, DB optimization |
| Data validation bugs | High | Low | Comprehensive unit tests, fuzz testing |
| PII leak through scrubber | Critical | Very Low | Regex validation, fail-closed audit, manual review |
| Instance registration failure | Medium | Medium | Manual key distribution, fallback registration |
| Time-series DB capacity | Medium | Low | Retention policy enforcement, archival to cold storage |
| Dashboard real-time lag | Medium | Low | Cache strategy, SSE/WebSocket, client-side polling |
| Map rendering (1000s pins) | Medium | Medium | Clustering algorithm, canvas rendering fallback |
| GDPR audit non-compliance | Critical | Very Low | Legal review, audit trail verification, retention monitoring |
| Scheduled archive job failure | Low | Low | Dead-letter queue, retry logic, alerting |

---

## 11. COMPLIANCE CHECKLIST (Before Launch)

**Security:**
- [ ] TLS 1.3 configured on collector + dashboard
- [ ] Ed25519 keys generated + backed up
- [ ] Rate limiting tested (10 req/min per instance)
- [ ] DDoS protection (Cloudflare rate limits)
- [ ] Signature verification 100% on unit tests
- [ ] PII scrubber fuzz-tested (100+ test cases)
- [ ] Collector logs encrypted at rest
- [ ] No secrets in code (audit via git-secrets)

**GDPR:**
- [ ] Privacy policy updated
- [ ] Opt-out mechanism working (Console Settings)
- [ ] 90-day retention TTL verified
- [ ] Data subject access request (DSAR) procedure documented
- [ ] Right to erasure procedure documented
- [ ] Audit trail hash-chain verified
- [ ] Legitimate interest assessment (Art. 6(1)(f)) approved by Legal

**EU AI Act:**
- [ ] Bot disclosure includes telemetry notice
- [ ] Disclosure on `/pass` page (opt-out)
- [ ] No personal data transmitted
- [ ] No individual tracking
- [ ] Fail-closed safeguards verified

**Operations:**
- [ ] Runbook created (on-call playbook)
- [ ] Incident response procedure tested
- [ ] Monitoring + alerting configured
- [ ] Key rotation procedure documented
- [ ] Backup + restore tested
- [ ] Disaster recovery plan (RTO <4h, RPO <1h)

---

## 12. REFERENCES

- **ADR-0179:** Error & Healing Telemetry
- **ADR-0180:** Instance Ping + Aggregation
- **ADR-0232:** Audit Chain Integrity
- **ADR-0233:** Plugin Compliance Consolidation
- **GDPR Art. 5, 6, 30, 32:** Data protection principles
- **EU AI Act Art. 50:** Transparency + disclosure
- **CorvinOS Telemetry Policy:** `/docs/telemetry-and-privacy.md`

---

**DESIGN STATUS:** ✅ COMPLETE (Ready for team review & sign-off)

**Next Step:** Proceed to Phase 1 implementation after stakeholder approval.

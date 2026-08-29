# Collector API Specification (OpenAPI 3.1)

**Version:** 1.0  
**Base URL:** `https://collector.corvin-labs.com/api/v1`  
**Content-Type:** `application/json`  

---

## API Overview

The Collector API receives anonymized telemetry reports from CorvinOS instances worldwide. It validates payloads, verifies cryptographic signatures, de-duplicates reports, and queues them for aggregation.

### Authentication

**No bearer token required.** Authentication is via Ed25519 signature embedded in each telemetry payload.

### Rate Limiting

- **Per-instance limit:** 10 requests/minute
- **Headers returned:** `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`
- **Exceeding limit:** Returns 429 Too Many Requests with `Retry-After` header

### Error Handling

All errors return JSON with `error` object:

```json
{
  "error": {
    "code": "INVALID_SIGNATURE",
    "message": "Ed25519 signature verification failed",
    "details": {
      "instance_id": "corvin_abc123",
      "timestamp": "2026-08-29T14:30:00Z"
    }
  }
}
```

---

## Endpoints

### 1. POST /telemetry/report

**Purpose:** Receive a telemetry report from an instance

#### Request

**Headers:**
```
Content-Type: application/json
X-Instance-ID: {instance_uuid}
User-Agent: CorvinOS/{version}
```

**Body:**

```json
{
  "schema_version": "1.0",
  "instance_id": "corvin_abc123def456",
  "timestamp": "2026-08-29T14:30:00Z",
  "instance_metadata": {
    "version": "0.2.1",
    "os": "Linux",
    "python_version": "3.11.6",
    "region": "EU",
    "country_code": "DE",
    "deployment_type": "docker"
  },
  "uptime": {
    "boot_time": "2026-08-27T10:15:30Z",
    "uptime_hours": 52.25,
    "last_heartbeat": "2026-08-29T14:29:55Z"
  },
  "usage": {
    "active_users": 3,
    "sessions_total": 247,
    "sessions_today": 15,
    "tokens_used_today": 125000,
    "cost_today": 45.32
  },
  "performance": {
    "avg_latency_ms": 342,
    "p95_latency_ms": 1250,
    "p99_latency_ms": 3400,
    "error_rate_percent": 0.3
  },
  "system": {
    "cpu_usage_percent": 34,
    "memory_used_mb": 2048,
    "memory_total_mb": 8192,
    "disk_free_gb": 45
  },
  "features": {
    "models_used": ["claude-3-sonnet", "claude-3-haiku"],
    "plugins_installed_count": 5,
    "marketplace_plugins_count": 2,
    "custom_layers_active": 0
  },
  "health": {
    "audit_chain_healthy": true,
    "compliance_tripwire_active": true,
    "boot_time_seconds": 8,
    "last_audit_verify_time": "2026-08-29T14:00:00Z"
  },
  "signature": "ed25519_base64url_encoded_signature"
}
```

#### Response

**Status:** 202 Accepted

```json
{
  "accepted": true,
  "request_id": "telemetry_req_2026_08_29_143001_abc123",
  "queue_position": 42,
  "eta_processing_seconds": 3
}
```

#### Error Responses

**400 Bad Request** — Invalid schema

```json
{
  "error": {
    "code": "INVALID_SCHEMA",
    "message": "Missing required field: instance_id",
    "details": {
      "field": "instance_id",
      "expected_type": "string"
    }
  }
}
```

**401 Unauthorized** — Signature verification failed

```json
{
  "error": {
    "code": "INVALID_SIGNATURE",
    "message": "Ed25519 signature verification failed",
    "details": {
      "instance_id": "corvin_abc123",
      "reason": "signature_mismatch"
    }
  }
}
```

**409 Conflict** — Duplicate report (same instance_id + time bucket)

```json
{
  "error": {
    "code": "DUPLICATE_REPORT",
    "message": "Report already received for this time bucket",
    "details": {
      "instance_id": "corvin_abc123",
      "time_bucket": "2026-08-29T14:30:00Z"
    }
  }
}
```

**429 Too Many Requests** — Rate limit exceeded

```json
{
  "error": {
    "code": "RATE_LIMIT_EXCEEDED",
    "message": "Maximum 10 requests per minute per instance",
    "details": {
      "instance_id": "corvin_abc123",
      "limit": 10,
      "window_seconds": 60
    }
  }
}
```

**503 Service Unavailable** — Collector temporarily down (retry after 60s)

```json
{
  "error": {
    "code": "SERVICE_UNAVAILABLE",
    "message": "Collector is temporarily unavailable",
    "details": {
      "retry_after_seconds": 60,
      "status_page": "https://status.corvin-labs.com"
    }
  }
}
```

### Request Validation Rules

| Field | Type | Min | Max | Notes |
|---|---|---|---|---|
| `schema_version` | string | - | - | Must be "1.0" |
| `instance_id` | string (UUID) | - | 36 chars | Valid UUID4 format |
| `timestamp` | string (ISO 8601) | - | - | Within ±1 hour of server time |
| `instance_metadata.version` | string | 1 | 20 | Semantic version (e.g., "0.2.1") |
| `instance_metadata.os` | enum | - | - | "Linux", "Darwin", "Windows" |
| `instance_metadata.python_version` | string | 5 | 10 | e.g., "3.11.6" |
| `instance_metadata.region` | enum | - | - | "EU", "NA", "APAC", "LATAM", "MENA" |
| `instance_metadata.country_code` | string | 2 | 2 | ISO 3166-1 alpha-2 (e.g., "DE") |
| `instance_metadata.deployment_type` | string | 1 | 20 | e.g., "docker", "kubernetes" |
| `uptime.uptime_hours` | float | 0 | 8760 | Hours since boot (≤1 year) |
| `usage.active_users` | int | 0 | 1000000 | Number of active sessions |
| `usage.sessions_total` | int | 0 | ∞ | Cumulative session count |
| `usage.tokens_used_today` | int | 0 | ∞ | Tokens consumed today |
| `usage.cost_today` | float | 0 | 1000000 | USD cost today |
| `performance.avg_latency_ms` | float | 0 | 60000 | Milliseconds |
| `performance.error_rate_percent` | float | 0 | 100 | Percentage (0-100) |
| `system.cpu_usage_percent` | float | 0 | 100 | Percentage (0-100) |
| `system.memory_used_mb` | int | 0 | 1048576 | Megabytes (≤1TB) |
| `system.disk_free_gb` | float | 0 | 1048576 | Gigabytes (≤1PB) |
| `features.models_used` | array[string] | 0 | 10 | Model names |
| `health.boot_time_seconds` | float | 0 | 300 | Seconds to boot |
| `signature` | string (base64url) | 86 | 88 | Ed25519 signature (88 chars base64url) |

### Payload Size & Limits

- **Max payload size:** 32 KB (gzip acceptable)
- **Max string length:** 128 characters (any field)
- **Max array length:** 10 items (models_used, etc.)
- **Number precision:** floats rounded to 2 decimal places

---

## 2. GET /health

**Purpose:** Health check for monitoring

#### Request

```
GET /health
```

#### Response

**Status:** 200 OK

```json
{
  "status": "healthy",
  "timestamp": "2026-08-29T14:35:00Z",
  "uptime_seconds": 86400,
  "version": "1.0.0",
  "dependencies": {
    "redis": "healthy",
    "influxdb": "healthy",
    "signature_service": "healthy"
  },
  "metrics": {
    "requests_per_minute": 1200,
    "queue_depth": 42,
    "error_rate_percent": 0.1
  }
}
```

---

## 3. GET /metrics (Prometheus)

**Purpose:** Prometheus-compatible metrics endpoint

#### Request

```
GET /metrics
```

#### Response

**Status:** 200 OK  
**Content-Type:** `text/plain; version=0.0.4`

```
# HELP telemetry_reports_total Total telemetry reports received
# TYPE telemetry_reports_total counter
telemetry_reports_total{status="accepted"} 1247
telemetry_reports_total{status="invalid_schema"} 3
telemetry_reports_total{status="invalid_signature"} 1
telemetry_reports_total{status="duplicate"} 12
telemetry_reports_total{status="rate_limit_exceeded"} 2

# HELP telemetry_processing_latency_seconds Processing latency
# TYPE telemetry_processing_latency_seconds histogram
telemetry_processing_latency_seconds_bucket{le="0.1"} 1100
telemetry_processing_latency_seconds_bucket{le="0.5"} 1240
telemetry_processing_latency_seconds_bucket{le="1.0"} 1246

# HELP collector_queue_depth Current queue depth
# TYPE collector_queue_depth gauge
collector_queue_depth 42

# HELP collector_uptime_seconds Collector uptime
# TYPE collector_uptime_seconds gauge
collector_uptime_seconds 86400
```

---

## Data Validation Examples

### Valid Payload

```json
{
  "schema_version": "1.0",
  "instance_id": "550e8400-e29b-41d4-a716-446655440000",
  "timestamp": "2026-08-29T14:30:00Z",
  "instance_metadata": {
    "version": "0.2.1",
    "os": "Linux",
    "python_version": "3.11.6",
    "region": "EU",
    "country_code": "DE",
    "deployment_type": "docker"
  },
  "uptime": {
    "boot_time": "2026-08-27T10:15:30Z",
    "uptime_hours": 52.25,
    "last_heartbeat": "2026-08-29T14:29:55Z"
  },
  "usage": {
    "active_users": 3,
    "sessions_total": 247,
    "sessions_today": 15,
    "tokens_used_today": 125000,
    "cost_today": 45.32
  },
  "performance": {
    "avg_latency_ms": 342.5,
    "p95_latency_ms": 1250.0,
    "p99_latency_ms": 3400.0,
    "error_rate_percent": 0.3
  },
  "system": {
    "cpu_usage_percent": 34.2,
    "memory_used_mb": 2048,
    "memory_total_mb": 8192,
    "disk_free_gb": 45.5
  },
  "features": {
    "models_used": ["claude-3-sonnet", "claude-3-haiku"],
    "plugins_installed_count": 5,
    "marketplace_plugins_count": 2,
    "custom_layers_active": 0
  },
  "health": {
    "audit_chain_healthy": true,
    "compliance_tripwire_active": true,
    "boot_time_seconds": 8.2,
    "last_audit_verify_time": "2026-08-29T14:00:00Z"
  },
  "signature": "CgTqDz4xUXpY8kL2mNvQfhJ9sP5aR3bT6wXzY8jKlMqN9oP1qRsT3uVwXyZ0aBc="
}
```

### Invalid Payload (Missing required field)

```json
{
  "schema_version": "1.0",
  "instance_id": "550e8400-e29b-41d4-a716-446655440000",
  "timestamp": "2026-08-29T14:30:00Z",
  // Missing 'instance_metadata' ❌
  "uptime": { ... },
  "usage": { ... },
  "performance": { ... },
  "system": { ... },
  "features": { ... },
  "health": { ... },
  "signature": "..."
}
```

**Error:** 400 Bad Request

### Invalid Payload (Contaminated PII)

```json
{
  "schema_version": "1.0",
  "instance_id": "550e8400-e29b-41d4-a716-446655440000",
  "timestamp": "2026-08-29T14:30:00Z",
  "instance_metadata": {
    "version": "0.2.1",
    "os": "Linux",
    "python_version": "3.11.6",
    "region": "EU",
    "country_code": "DE",
    "deployment_type": "docker"
    // ❌ Contains hostname (screened by scrubber)
  },
  // ... other fields
  "signature": "..."
}
```

**Error:** 400 Bad Request (PII scrubbing failed) OR scrubbed to "[SCRUBBED]" if non-critical

---

## Rate Limiting Examples

### Request 1-10 (within limit)

```
POST /telemetry/report
X-Instance-ID: corvin_abc123
...

202 Accepted
X-RateLimit-Limit: 10
X-RateLimit-Remaining: 9
X-RateLimit-Reset: 2026-08-29T14:31:00Z
```

### Request 11 (exceeds limit)

```
POST /telemetry/report
X-Instance-ID: corvin_abc123
...

429 Too Many Requests
X-RateLimit-Limit: 10
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 2026-08-29T14:31:00Z
Retry-After: 20
```

---

## Signature Verification Algorithm

### Payload Signing (Instance Side)

```python
import json
import base64
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

# 1. Load private key
private_key = ed25519.Ed25519PrivateKey.from_private_bytes(private_key_bytes)

# 2. Canonicalize payload (exclude 'signature' field)
payload_copy = payload.copy()
payload_copy.pop('signature', None)
canonical = json.dumps(payload_copy, separators=(',', ':'), sort_keys=True)

# 3. Sign canonical bytes
signature_bytes = private_key.sign(canonical.encode('utf-8'))

# 4. Encode as base64url
signature_b64url = base64.urlsafe_b64encode(signature_bytes).decode('ascii').rstrip('=')

# 5. Attach to payload
payload['signature'] = signature_b64url
```

### Signature Verification (Collector Side)

```python
import json
import base64
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.exceptions import InvalidSignature

# 1. Extract signature
signature_b64url = payload.pop('signature')

# 2. Decode from base64url (add padding)
signature_bytes = base64.urlsafe_b64decode(
    signature_b64url + '=' * (4 - len(signature_b64url) % 4)
)

# 3. Canonicalize remaining payload
canonical = json.dumps(payload, separators=(',', ':'), sort_keys=True)

# 4. Load public key + verify
public_key = ed25519.Ed25519PublicKey.from_public_bytes(public_key_bytes)
try:
    public_key.verify(signature_bytes, canonical.encode('utf-8'))
    return True
except InvalidSignature:
    return False
```

---

## Deployment Notes

### Load Balancing

Deploy 4+ collector instances behind a load balancer (nginx/HAProxy/ALB):

```
Client → Cloudflare (DDoS) → Load Balancer → Collector 1
                                           → Collector 2
                                           → Collector 3
                                           → Collector 4
```

### TLS Configuration

- **Minimum:** TLS 1.3
- **Ciphers:** Modern only (no legacy)
- **HSTS:** `Strict-Transport-Security: max-age=31536000; includeSubDomains`
- **Certificate:** Let's Encrypt (auto-renew)

### Monitoring & Alerting

Set up alerts for:
- **5xx error rate** >1%
- **Response latency p95** >1 second
- **Queue depth** >1000 items
- **Signature verification failures** >10/min
- **De-dup cache miss rate** (indicates memory pressure)

### Database Connection

- **InfluxDB:** Connection pool size ≥16
- **Redis:** Connection pool size ≥32
- **Timeouts:** Read 5s, Write 10s, Connect 2s

---

**API Status:** ✅ Ready for implementation

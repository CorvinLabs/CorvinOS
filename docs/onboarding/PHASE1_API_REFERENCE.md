# Phase 1: CorvinOS API Reference

**Version:** 1.0  
**Base URL:** `http://localhost:8765` or `https://api.corvinlabs.io` (production)  
**Authentication:** Bearer token (Anthropic API key)  
**Response Format:** JSON  
**Status:** Production-ready

---

## Table of Contents

1. [Authentication](#authentication)
2. [Core Endpoints](#core-endpoints)
3. [Skill Management](#skill-management)
4. [Learning & Feedback](#learning--feedback)
5. [Audit & Compliance](#audit--compliance)
6. [Console Integration](#console-integration)
7. [Error Handling](#error-handling)
8. [Rate Limiting](#rate-limiting)

---

## Authentication

All API requests require an `Authorization` header with a valid Anthropic API key.

### Bearer Token

```bash
curl -X GET http://localhost:8765/v1/health \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json"
```

### Header Format

```
Authorization: Bearer <ANTHROPIC_API_KEY>
```

### Environment Variable

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
curl -X GET http://localhost:8765/v1/health \
  -H "Authorization: Bearer $ANTHROPIC_API_KEY"
```

---

## Core Endpoints

### 1. Health Check

**GET** `/v1/health`

Check if the CorvinOS API is running.

**Request:**
```bash
curl -X GET http://localhost:8765/v1/health \
  -H "Authorization: Bearer $ANTHROPIC_API_KEY"
```

**Response (200 OK):**
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "uptime_seconds": 3600,
  "components": {
    "audit_chain": "ok",
    "plugin_registry": "ok",
    "learning_processor": "ok",
    "api_gateway": "ok"
  }
}
```

---

### 2. Get Capabilities

**GET** `/v1/console/capabilities/manifest`

Get installed skills, plugins, and system capabilities.

**Request:**
```bash
curl -X GET http://localhost:8765/v1/console/capabilities/manifest \
  -H "Authorization: Bearer $ANTHROPIC_API_KEY" \
  -H "Content-Type: application/json"
```

**Response (200 OK):**
```json
{
  "skills": [
    {
      "id": "assistant.quick_fix",
      "name": "Quick Fix",
      "version": "1.2.0",
      "status": "active",
      "confidence": 0.87,
      "category": "optimization"
    },
    {
      "id": "assistant.cost_optimizer",
      "name": "Cost Optimizer",
      "version": "2.0.1",
      "status": "active",
      "confidence": 0.92
    }
  ],
  "plugins": [
    {
      "id": "marketplace.video-producer",
      "name": "Video Producer",
      "version": "1.0.0",
      "boot_layer": "bundled",
      "enabled": true
    }
  ],
  "features": {
    "learning_loop": true,
    "cost_tracking": true,
    "audit_chain": true,
    "multi_tenant": true
  }
}
```

---

## Skill Management

### 1. List Skills

**GET** `/v1/skills`

List all installed skills.

**Request:**
```bash
curl -X GET http://localhost:8765/v1/skills \
  -H "Authorization: Bearer $ANTHROPIC_API_KEY"
```

**Query Parameters:**
- `status` — Filter by status: `active`, `inactive`, `disabled`
- `category` — Filter by category: `optimization`, `automation`, `analysis`
- `source` — Filter by source: `builtin`, `marketplace`, `community`

**Response (200 OK):**
```json
{
  "skills": [
    {
      "id": "assistant.quick_fix",
      "name": "Quick Fix",
      "version": "1.2.0",
      "status": "active",
      "category": "optimization",
      "confidence": 0.87,
      "runs_total": 23,
      "runs_successful": 18,
      "last_used": "2026-09-22T16:30:00Z"
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 50
}
```

### 2. Execute Skill

**POST** `/v1/skills/{skill_id}/execute`

Execute a specific skill with input.

**Request:**
```bash
curl -X POST http://localhost:8765/v1/skills/assistant.quick_fix/execute \
  -H "Authorization: Bearer $ANTHROPIC_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "input": "Analyze this error: NameError: name x is not defined",
    "context": {
      "file": "app.py",
      "line": 42
    },
    "tenant_id": "_default"
  }'
```

**Response (200 OK):**
```json
{
  "skill_id": "assistant.quick_fix",
  "status": "completed",
  "output": {
    "analysis": "Variable 'x' is used before being defined on line 42.",
    "suggested_fix": "Define x = 0 before using it.",
    "confidence": 0.95
  },
  "execution_time_ms": 245,
  "audit_event_id": "evt_abc123"
}
```

### 3. Get Skill Status

**GET** `/v1/skills/{skill_id}`

Get detailed status of a specific skill.

**Request:**
```bash
curl -X GET http://localhost:8765/v1/skills/assistant.quick_fix \
  -H "Authorization: Bearer $ANTHROPIC_API_KEY"
```

**Response (200 OK):**
```json
{
  "id": "assistant.quick_fix",
  "name": "Quick Fix",
  "version": "1.2.0",
  "status": "active",
  "enabled": true,
  "learning": {
    "enabled": true,
    "confidence": 0.87,
    "feedback_count": 18,
    "last_optimized": "2026-09-22T12:00:00Z"
  },
  "metrics": {
    "total_runs": 23,
    "successful_runs": 18,
    "failed_runs": 5,
    "average_latency_ms": 245,
    "success_rate": 0.78
  }
}
```

---

## Learning & Feedback

### 1. Emit Feedback

**POST** `/v1/learning/feedback`

Send feedback about a skill execution.

**Request:**
```bash
curl -X POST http://localhost:8765/v1/learning/feedback \
  -H "Authorization: Bearer $ANTHROPIC_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "skill_id": "assistant.quick_fix",
    "feedback_type": "outcome",
    "signal": "correct",
    "confidence_score": 0.95,
    "execution_id": "evt_abc123",
    "tenant_id": "_default",
    "metadata": {
      "user_comment": "Fix worked perfectly"
    }
  }'
```

**Feedback Types:**
- `outcome` — Was the result correct? (`correct`, `incorrect`, `partial`)
- `preference` — What style do you prefer? (`llm_recommended`, `deterministic`, `neither`)
- `confidence` — How confident is the skill? (0.0-1.0)
- `metric` — Custom metric (latency, cost, etc.)

**Response (201 Created):**
```json
{
  "feedback_id": "fdbk_xyz789",
  "skill_id": "assistant.quick_fix",
  "status": "received",
  "audit_event_id": "evt_abc124"
}
```

### 2. View Learning Status

**GET** `/v1/learning/status`

Get the current learning loop status.

**Request:**
```bash
curl -X GET http://localhost:8765/v1/learning/status \
  -H "Authorization: Bearer $ANTHROPIC_API_KEY"
```

**Response (200 OK):**
```json
{
  "learning_enabled": true,
  "processor_status": "running",
  "event_queue_size": 3,
  "events_processed_today": 145,
  "last_optimization": "2026-09-22T15:30:00Z",
  "optimized_skills": [
    {
      "skill_id": "assistant.quick_fix",
      "optimization_delta": {
        "confidence_before": 0.85,
        "confidence_after": 0.87
      }
    }
  ]
}
```

---

## Audit & Compliance

### 1. Get Audit Trail

**GET** `/v1/audit/trail`

Retrieve audit events (hash-chained, immutable).

**Request:**
```bash
curl -X GET "http://localhost:8765/v1/audit/trail?limit=50&since=2026-09-22T00:00:00Z" \
  -H "Authorization: Bearer $ANTHROPIC_API_KEY"
```

**Query Parameters:**
- `limit` — Number of events to return (default: 50, max: 1000)
- `since` — Start timestamp (ISO 8601)
- `until` — End timestamp (ISO 8601)
- `event_type` — Filter by type: `skill_executed`, `plugin_loaded`, `feedback_received`, etc.
- `tenant_id` — Filter by tenant (required for multi-tenant setups)

**Response (200 OK):**
```json
{
  "events": [
    {
      "event_id": "evt_abc123",
      "timestamp": "2026-09-22T16:30:45.123Z",
      "event_type": "skill_executed",
      "tenant_id": "_default",
      "skill_id": "assistant.quick_fix",
      "input_hash": "sha256:abc123...",
      "output_hash": "sha256:def456...",
      "latency_ms": 245,
      "hash": "sha256:xyz789...",
      "prev_hash": "sha256:prev123...",
      "lom": "assistant.Forge::execute_skill:L237"
    }
  ],
  "total": 1245,
  "page": 1,
  "page_size": 50
}
```

### 2. Verify Audit Chain

**POST** `/v1/audit/verify`

Verify the integrity of the audit chain.

**Request:**
```bash
curl -X POST http://localhost:8765/v1/audit/verify \
  -H "Authorization: Bearer $ANTHROPIC_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "_default"
  }'
```

**Response (200 OK):**
```json
{
  "status": "verified",
  "tenant_id": "_default",
  "chain_height": 1245,
  "last_hash": "sha256:xyz789...",
  "verification_result": true,
  "gap_count": 0,
  "verification_time_ms": 1234
}
```

---

## Console Integration

### 1. Get Console Settings

**GET** `/v1/console/settings`

Retrieve current console configuration.

**Request:**
```bash
curl -X GET http://localhost:8765/v1/console/settings \
  -H "Authorization: Bearer $ANTHROPIC_API_KEY"
```

**Response (200 OK):**
```json
{
  "tenant_id": "_default",
  "engine": "anthropic",
  "model": "claude-opus-5",
  "max_tokens": 8000,
  "temperature": 0.7,
  "learning_enabled": true,
  "cost_tracking_enabled": true,
  "telemetry_level": "basic"
}
```

### 2. Update Console Settings

**PUT** `/v1/console/settings`

Update console configuration.

**Request:**
```bash
curl -X PUT http://localhost:8765/v1/console/settings \
  -H "Authorization: Bearer $ANTHROPIC_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-5",
    "max_tokens": 4000,
    "learning_enabled": true
  }'
```

**Response (200 OK):**
```json
{
  "status": "updated",
  "settings": {
    "engine": "anthropic",
    "model": "claude-sonnet-5",
    "max_tokens": 4000,
    "learning_enabled": true
  },
  "audit_event_id": "evt_abc125"
}
```

---

## Error Handling

### Error Response Format

All error responses follow this format:

```json
{
  "error": {
    "type": "error_type",
    "message": "Human-readable error message",
    "code": "ERROR_CODE",
    "details": {
      "field": "additional context"
    }
  },
  "request_id": "req_abc123"
}
```

### Common Error Codes

| Code | HTTP Status | Meaning | Solution |
|---|---|---|---|
| `INVALID_API_KEY` | 401 | API key is missing or invalid | Check `ANTHROPIC_API_KEY` env var |
| `TENANT_NOT_FOUND` | 404 | Tenant does not exist | Create tenant first with `corvin bootstrap` |
| `SKILL_NOT_FOUND` | 404 | Skill is not installed | Install skill: `corvin skill install <id>` |
| `RATE_LIMIT_EXCEEDED` | 429 | Too many requests | Wait before retrying (exponential backoff) |
| `INTERNAL_SERVER_ERROR` | 500 | Server error | Check logs: `journalctl --user -u corvin-webui` |
| `AUDIT_CHAIN_BROKEN` | 503 | Audit chain verification failed | Run `corvin audit repair --tenant=_default` |

### Example Error Response

```bash
curl -X POST http://localhost:8765/v1/skills/nonexistent/execute \
  -H "Authorization: Bearer $ANTHROPIC_API_KEY"
```

**Response (404 Not Found):**
```json
{
  "error": {
    "type": "SkillNotFound",
    "message": "Skill 'nonexistent' is not installed",
    "code": "SKILL_NOT_FOUND",
    "details": {
      "skill_id": "nonexistent",
      "available_skills": ["assistant.quick_fix", "assistant.cost_optimizer"]
    }
  },
  "request_id": "req_xyz789"
}
```

---

## Rate Limiting

### Headers

All API responses include rate limit headers:

```
X-RateLimit-Limit: 1000          # Requests per hour
X-RateLimit-Remaining: 945       # Remaining requests
X-RateLimit-Reset: 1663866000   # Unix timestamp when limit resets
```

### Behavior

- **Default:** 1000 requests per hour per API key
- **Exceeded:** Returns 429 status code
- **Retry:** Use exponential backoff (start at 2 seconds, double each retry)

### Retry Example

```bash
#!/bin/bash

retry_count=0
max_retries=3

while [ $retry_count -lt $max_retries ]; do
  response=$(curl -s -w "\n%{http_code}" -X GET \
    http://localhost:8765/v1/skills \
    -H "Authorization: Bearer $ANTHROPIC_API_KEY")
  
  http_code=$(echo "$response" | tail -n1)
  body=$(echo "$response" | head -n-1)
  
  if [ "$http_code" -eq 429 ]; then
    wait_time=$((2 ** retry_count))
    echo "Rate limited. Waiting ${wait_time}s..."
    sleep $wait_time
    retry_count=$((retry_count + 1))
  else
    echo "$body"
    break
  fi
done
```

---

## Examples

### Example 1: Complete Workflow

```bash
#!/bin/bash

API_KEY="$ANTHROPIC_API_KEY"
BASE_URL="http://localhost:8765"

# 1. Check health
curl -X GET "$BASE_URL/v1/health" \
  -H "Authorization: Bearer $API_KEY" | jq .

# 2. List skills
curl -X GET "$BASE_URL/v1/skills" \
  -H "Authorization: Bearer $API_KEY" | jq .

# 3. Execute a skill
SKILL_RESPONSE=$(curl -s -X POST "$BASE_URL/v1/skills/assistant.quick_fix/execute" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "input": "Fix this: x = y + z (y undefined)",
    "tenant_id": "_default"
  }')

EXECUTION_ID=$(echo "$SKILL_RESPONSE" | jq -r '.audit_event_id')

# 4. Send feedback
curl -X POST "$BASE_URL/v1/learning/feedback" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"skill_id\": \"assistant.quick_fix\",
    \"feedback_type\": \"outcome\",
    \"signal\": \"correct\",
    \"execution_id\": \"$EXECUTION_ID\",
    \"tenant_id\": \"_default\"
  }" | jq .

# 5. View audit trail
curl -X GET "$BASE_URL/v1/audit/trail?limit=10" \
  -H "Authorization: Bearer $API_KEY" | jq .
```

### Example 2: Python Client

```python
import requests
import os
from datetime import datetime, timedelta

class CorvinOSClient:
    def __init__(self, api_key, base_url="http://localhost:8765"):
        self.api_key = api_key
        self.base_url = base_url
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
    
    def execute_skill(self, skill_id, input_text, tenant_id="_default"):
        """Execute a skill and return the result."""
        url = f"{self.base_url}/v1/skills/{skill_id}/execute"
        payload = {
            "input": input_text,
            "tenant_id": tenant_id
        }
        response = requests.post(url, json=payload, headers=self.headers)
        return response.json()
    
    def send_feedback(self, skill_id, signal, execution_id, tenant_id="_default"):
        """Send feedback about a skill execution."""
        url = f"{self.base_url}/v1/learning/feedback"
        payload = {
            "skill_id": skill_id,
            "feedback_type": "outcome",
            "signal": signal,
            "execution_id": execution_id,
            "tenant_id": tenant_id
        }
        response = requests.post(url, json=payload, headers=self.headers)
        return response.json()
    
    def get_audit_trail(self, tenant_id="_default", days=1):
        """Get audit trail for the past N days."""
        since = (datetime.utcnow() - timedelta(days=days)).isoformat() + "Z"
        url = f"{self.base_url}/v1/audit/trail"
        params = {"tenant_id": tenant_id, "since": since, "limit": 100}
        response = requests.get(url, params=params, headers=self.headers)
        return response.json()

# Usage
client = CorvinOSClient(os.environ["ANTHROPIC_API_KEY"])
result = client.execute_skill("assistant.quick_fix", "Fix: x = undefined_var")
print(result)
```

---

## Support & Further Reading

- **Setup Guide:** `PHASE1_SETUP_GUIDE.md`
- **Troubleshooting:** `PHASE1_TROUBLESHOOTING.md`
- **Incident Response:** `INCIDENT_RESPONSE_RUNBOOK.md`
- **API Status:** https://status.corvinlabs.io

---

**Last Updated:** 2026-09-22  
**API Version:** 1.0.0

# RAG Provider Manifest Examples

This directory contains production-ready example manifests for integrating RAG providers with CorvinOS.

## Quick Start

### 1. Choose Your Provider Type

| Manifest | Type | Use Case | Status |
|----------|------|----------|--------|
| `elasticsearch-production.yaml` | Keyword Search | Enterprise search + faceting | ✅ Ready |
| `vector-db-semantic.yaml` | Semantic Search | ML-powered similarity matching | ✅ Ready |
| `google-drive-integration.yaml` | Cloud Docs + OCR | Team collaboration + documents | ✅ Ready |
| `custom-http-api-template.yaml` | Custom API | Your proprietary backend | ✅ Template |

### 2. Register a Provider

```bash
# Copy an example to your registry
cp elasticsearch-production.yaml \
   ~/.corvin/tenants/_default/global/rag/my-elasticsearch.yaml

# Or use the template for a custom API
cp custom-http-api-template.yaml \
   ~/.corvin/tenants/_default/global/rag/my-api.yaml
# Then edit it and replace [YOUR_*] placeholders

# Register the provider
corvin-rag register ~/.corvin/tenants/_default/global/rag/my-elasticsearch.yaml

# Verify it works
corvin-rag health my-elasticsearch
```

### 3. Monitor in Console

Open http://localhost:8000/app/rag and see:
- **Providers tab** — Real-time health status
- **Query Tester tab** — Execute live queries
- **Statistics tab** — Performance dashboard

---

## Manifest Structure

Every provider manifest has these sections:

### **Retrieval Configuration**
```yaml
retrieval:
  endpoint: https://api.example.com/search
  method: POST
  timeout_ms: 5000
  auth:
    type: bearer-token
    token_env_var: MY_API_TOKEN
```

- `endpoint` — Your API's URL
- `method` — GET or POST
- `timeout_ms` — Request timeout
- `auth` — Authentication method (bearer-token, api-key, basic, oauth2)

### **Response Format**
```yaml
response_format:
  content_path: results[].content
  score_path: results[].score
  metadata_path: results[]
  source_url_path: results[].url
```

Use **JSONPath** expressions to extract results from your API response.

### **Classification & Zone**
```yaml
dataClassification: INTERNAL          # PUBLIC, INTERNAL, CONFIDENTIAL, SECRET
complianceZone: EU                    # Where your data lives
```

**GDPR Compliance:** Declares data tier and geographic zone.

### **Capabilities**
```yaml
capabilities:
  - keyword-search
  - semantic-search
  - filtering-by-metadata
  - time-range-queries
```

List what your API supports.

### **Resilience**
```yaml
resilience:
  circuit_breaker:
    failure_threshold: 5              # Fail after 5 errors
    timeout_seconds: 60               # Stop trying for 60s
  retry_strategy: exponential
  max_retries: 3
```

Fail-safe mechanism when your API is down.

### **Quotas**
```yaml
quotas:
  requests_per_second: 100
  concurrent_requests: 10
  daily_limit: 1000000
  monthly_limit: 20000000
```

Rate limiting to protect your API.

### **Erasure Handler (GDPR Art. 17)**
```yaml
erasureHandler:
  type: http-delete
  endpoint: https://api.example.com/erase
  query_template: |
    {
      "user_id": "{subject_id}",
      "reason": "GDPR Art. 17"
    }
```

When operators run `corvin-erasure <user_id>`, this endpoint is called.

---

## Which Manifest Should I Use?

### **Enterprise Keyword Search → Elasticsearch**

```bash
cp elasticsearch-production.yaml ~/.corvin/tenants/_default/global/rag/es.yaml
export ES_API_TOKEN="your-token"
corvin-rag register ~/.corvin/tenants/_default/global/rag/es.yaml
```

**Features:**
- Keyword search + boolean operators
- Faceting + filtering
- Time-range queries
- High throughput (100 RPS)

---

### **Semantic Similarity → Vector DB**

```bash
cp vector-db-semantic.yaml ~/.corvin/tenants/_default/global/rag/vdb.yaml
export VECTOR_DB_API_KEY="your-key"
corvin-rag register ~/.corvin/tenants/_default/global/rag/vdb.yaml
```

**Features:**
- Vector embeddings (text-embedding-3-large)
- Cosine similarity matching
- Semantic search (find similar concepts)
- ML-powered results

---

### **Team Documentation → Google Drive**

```bash
cp google-drive-integration.yaml ~/.corvin/tenants/_default/global/rag/gdrive.yaml
export GOOGLE_DRIVE_CREDENTIALS_JSON='{"type":"service_account",...}'
corvin-rag register ~/.corvin/tenants/_default/global/rag/gdrive.yaml
```

**Features:**
- Full-text search on Docs, Sheets, Slides
- OCR for PDFs (EN, DE, FR)
- Sharing-aware access control
- Collaborative editing support

---

### **Custom API → Template**

```bash
# Copy the template
cp custom-http-api-template.yaml ~/.corvin/tenants/_default/global/rag/my-api.yaml

# Edit it and replace [YOUR_*] placeholders:
# - [YOUR_ENDPOINT] → https://api.yourcompany.com/search
# - [YOUR_TOKEN_ENV] → MY_API_TOKEN (env var name)
# - [YOUR_CONTENT_PATH] → results[].text (JSONPath to content)
# - [YOUR_SCORE_PATH] → results[].confidence (JSONPath to score)

# Register it
corvin-rag register ~/.corvin/tenants/_default/global/rag/my-api.yaml
```

**Why use the template?**
- Extensive inline documentation
- 4 authentication options
- Validation checks built-in
- GDPR compliance pre-configured

---

## Testing Your Manifest

### **Step 1: Validate Syntax**
```bash
corvin-rag validate ~/.corvin/tenants/_default/global/rag/my-provider.yaml
# Output: ✅ Valid manifest (or detailed error)
```

### **Step 2: Register**
```bash
corvin-rag register ~/.corvin/tenants/_default/global/rag/my-provider.yaml
# Output: ✅ Provider registered as 'my-provider'
```

### **Step 3: Check Health**
```bash
corvin-rag health my-provider
# Output: Healthy / Unhealthy (with latency + error details)
```

### **Step 4: Try a Query**
```bash
corvin-rag query "test query" --provider my-provider
# Output: JSON results
```

### **Step 5: Monitor in Console**
Open http://localhost:8000/app/rag
- Provider status (real-time)
- Query latency
- Error rates

---

## Customization Guide

### **Change the API Endpoint**

In your manifest:
```yaml
retrieval:
  endpoint: https://your-new-url.com/api/search
```

### **Change Authentication**

Bearer token (JWT):
```yaml
auth:
  type: bearer-token
  token_env_var: MY_JWT_TOKEN
```

API Key:
```yaml
auth:
  type: api-key
  token_env_var: MY_API_KEY
```

Basic auth:
```yaml
auth:
  type: basic
  token_env_var: MY_BASIC_AUTH  # Format: username:password (base64)
```

### **Change Response Parsing**

If your API returns results differently:
```yaml
response_format:
  content_path: data.results[].title
  score_path: data.results[].relevance_score
  source_url_path: data.results[].link
```

Use JSONPath syntax. Test with:
```bash
corvin-rag validate my-manifest.yaml
```

### **Adjust Rate Limiting**

If your API supports higher throughput:
```yaml
quotas:
  requests_per_second: 500    # Increase if API allows
  concurrent_requests: 50
```

Or lower limits if your API is rate-constrained:
```yaml
quotas:
  requests_per_second: 10
  concurrent_requests: 2
```

### **Change Health Check Frequency**

More frequent checks (catch failures faster):
```yaml
healthCheck:
  interval_seconds: 10
```

Less frequent checks (save quota):
```yaml
healthCheck:
  interval_seconds: 300  # 5 minutes
```

---

## Production Deployment Checklist

Before registering a manifest:

- [ ] Manifest is valid YAML
- [ ] API endpoint is reachable (test with curl)
- [ ] Authentication token is set (export env var)
- [ ] JSONPath extraction works (test with curl + jq)
- [ ] Rate limits match your API's capacity
- [ ] Health check endpoint responds
- [ ] Erasure handler endpoint configured
- [ ] Compliance zone matches your data residency

After registration:

- [ ] `corvin-rag health <provider>` returns "Healthy"
- [ ] Console shows provider in `/app/rag`
- [ ] Query Tester executes a sample query
- [ ] Statistics tab shows query counts
- [ ] Audit logs show `rag.query_executed` events

---

## Troubleshooting

### **"Provider not found" or "Health check failed"**

1. Check environment variables:
   ```bash
   echo $MY_API_TOKEN    # Make sure token is set
   ```

2. Test API endpoint directly:
   ```bash
   curl -H "Authorization: Bearer $MY_API_TOKEN" \
        https://api.example.com/health
   ```

3. Check manifest syntax:
   ```bash
   corvin-rag validate my-manifest.yaml
   ```

### **"Timeout" errors**

1. Increase `retrieval.timeout_ms`:
   ```yaml
   retrieval:
     timeout_ms: 10000  # 10s instead of 5s
   ```

2. Check API latency:
   ```bash
   time curl https://api.example.com/health
   ```

3. Adjust circuit breaker thresholds:
   ```yaml
   resilience:
     circuit_breaker:
       timeout_seconds: 120  # Longer recovery period
   ```

### **"Circuit breaker open" (provider marked unhealthy)**

The provider failed 5 times. Reasons:
1. API is down — check API status
2. Authentication failed — verify token
3. Rate limited — reduce `quotas.requests_per_second`
4. Network issue — check firewall rules

The circuit breaker will automatically retry after the timeout.

---

## GDPR Compliance Features

All manifests include:

1. **Data Minimisation (Art. 5)**
   - Manifest declares classification level
   - Metadata-only audit logging
   - No prompt/response text in logs

2. **Right to Deletion (Art. 17)**
   - Erasure handler endpoint configured
   - When `corvin-erasure <user_id>` runs, provider is notified
   - Immutable trail file records request

3. **Records of Processing (Art. 30)**
   - L16 audit chain logs all queries
   - No sensitive data in audit logs
   - Hash-chained tamper-evident events

4. **Security (Art. 32)**
   - L34 data classification gate (fail-closed)
   - Circuit breaker for resilience
   - Timeout management
   - Retry strategies

---

## Advanced: Integrating Your Own API

### **Example: Connect a Custom Search Engine**

1. Create a manifest:
```bash
cp custom-http-api-template.yaml my-search.yaml
```

2. Edit `my-search.yaml`:
```yaml
metadata:
  name: my-custom-search
  description: Our proprietary search API

spec:
  retrieval:
    endpoint: https://search.mycompany.com/api/search
    method: POST
    timeout_ms: 8000
    auth:
      type: api-key
      token_env_var: MY_SEARCH_API_KEY

    query_format:
      type: custom-http
      sample: |
        {
          "q": "{query}",
          "limit": {limit},
          "filters": {"date_from": "2025-01-01"}
        }

  response_format:
    content_path: results[].body
    score_path: results[].relevance
    metadata_path: results[]
    source_url_path: results[].url

  dataClassification: INTERNAL
  complianceZone: EU

  capabilities:
    - keyword-search
    - filtering-by-metadata
    - custom-ranking
```

3. Test the endpoint:
```bash
export MY_SEARCH_API_KEY="your-key"
curl -X POST https://search.mycompany.com/api/search \
  -H "X-API-Key: $MY_SEARCH_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"q":"test","limit":5}'
```

4. Update JSONPath if needed:
```yaml
# If your response looks like: {"data":{"items":[...]}}
response_format:
  content_path: data.items[].content
  score_path: data.items[].score
```

5. Register and test:
```bash
corvin-rag register ~/.corvin/tenants/_default/global/rag/my-search.yaml
corvin-rag health my-search
```

---

## Reference

- **ADR-0089:** RAG Integration System (architecture)
- **PHASE_3_COMPLETE.md:** Query engine + orchestrator
- **PHASE_6_COMPLETE.md:** Full onboarding guide
- **Custom API Template:** See inline documentation

---

**Happy integrating! 🚀**

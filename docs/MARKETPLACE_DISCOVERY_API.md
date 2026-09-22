# Marketplace Discovery API (Phase 1 Session 1)

Complete reference for the Marketplace Discovery endpoints enabling advanced search, filtering, and browsing of the skill marketplace.

## Base URL

```
https://<host>/v1/console/api/v1/marketplace
```

All requests require a valid session token in the `Authorization` header.

## Endpoints

### Search Skills

**Request:**
```http
GET /search?q=<query>&category=<cat>&tier=<tier>&tag=<tag>&license=<lic>&min_rating=<rating>&sort_by=<sort>&limit=<limit>&offset=<offset>
```

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `q` | string | No | Free-text search (name, description, tags, author) |
| `category` | string | No | Filter by category (e.g., `learning`, `optimization`, `media`) |
| `tier` | string | No | Filter by tier (`buildin`, `contributor`) |
| `tag` | string | No | Filter by tag (e.g., `ml`, `optimization`) |
| `license` | string | No | Filter by license (e.g., `Apache-2.0`) |
| `min_rating` | number | No | Minimum rating (0-5). Default: 0 |
| `sort_by` | string | No | Sort order: `relevance`, `downloads`, `rating`, `recency`, `name`. Default: `relevance` |
| `limit` | integer | No | Results per page (1-1000). Default: 100 |
| `offset` | integer | No | Pagination offset. Default: 0 |

**Response:**
```json
{
  "results": [
    {
      "id": "plugin:buildin-model-selector",
      "name": "Model Selector",
      "version": "1.0.0",
      "author": "Corvin Labs",
      "description": "AI model routing and selection",
      "category": "learning",
      "tier": "buildin",
      "tags": ["ml", "routing"],
      "installable": true,
      "installed": false,
      "enabled": false,
      "rating": 4.5,
      "download_count": 5000
    }
  ],
  "total": 42,
  "query": {
    "q": "model",
    "category": null,
    "tier": null,
    "tag": null,
    "license": null,
    "min_rating": 0,
    "sort_by": "relevance",
    "limit": 100,
    "offset": 0
  },
  "facets": {
    "categories": {
      "learning": 12,
      "optimization": 8,
      "media": 15
    },
    "tiers": {
      "buildin": 30,
      "contributor": 12
    },
    "tags": {
      "ml": 18,
      "routing": 5,
      "optimization": 10
    }
  }
}
```

**HTTP Status Codes:**
- `200 OK` - Search successful
- `401 Unauthorized` - Missing or invalid session
- `400 Bad Request` - Invalid query parameters

**Examples:**

Search for "cost":
```bash
curl -X GET "https://localhost/v1/console/api/v1/marketplace/search?q=cost" \
  -H "Authorization: Bearer <token>"
```

Filter by category and minimum rating:
```bash
curl -X GET "https://localhost/v1/console/api/v1/marketplace/search?category=learning&min_rating=4.0" \
  -H "Authorization: Bearer <token>"
```

Sort by most downloaded:
```bash
curl -X GET "https://localhost/v1/console/api/v1/marketplace/search?sort_by=downloads&limit=20" \
  -H "Authorization: Bearer <token>"
```

---

### Get Skill Details

**Request:**
```http
GET /plugins/{plugin_id}
```

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `plugin_id` | string | Yes | Plugin ID (e.g., `plugin:buildin-model-selector`) |

**Response:**
```json
{
  "id": "plugin:buildin-model-selector",
  "name": "Model Selector",
  "version": "1.0.0",
  "author": "Corvin Labs",
  "description": "AI model routing and selection",
  "long_description": "A comprehensive skill for routing requests to optimal AI models...",
  "category": "learning",
  "tier": "buildin",
  "license": "Apache-2.0",
  "tags": ["ml", "routing", "optimization"],
  "dependencies": ["numpy", "scikit-learn"],
  "requires_version": "1.2.0",
  "boot_layer": "core",
  "sla_level": "SLA-A",
  "readme_url": "https://github.com/CorvinLabs/Corvin-Marketplace/plugins/...",
  "source_url": "https://github.com/CorvinLabs/Corvin-Marketplace",
  "documentation_url": "https://docs.corvinlabs.io/model-selector",
  "support_url": "https://github.com/CorvinLabs/Corvin-Marketplace/issues",
  "metrics": {
    "downloads": 5000,
    "installs": 1200,
    "rating": 4.5,
    "review_count": 42,
    "success_rate": 0.98,
    "avg_latency_ms": 145.2,
    "last_updated": "2026-09-20T15:30:00Z"
  },
  "versions": [
    {
      "version": "1.0.0",
      "release_date": "2026-09-20",
      "release_notes": "Initial release",
      "downloads": 5000,
      "required_version": "1.2.0"
    },
    {
      "version": "0.9.0",
      "release_date": "2026-09-15",
      "release_notes": "Beta release",
      "downloads": 800
    }
  ],
  "reviews": [
    {
      "author": "john_doe",
      "rating": 5,
      "date": "2026-09-19",
      "text": "Excellent skill, very effective!"
    }
  ]
}
```

**HTTP Status Codes:**
- `200 OK` - Details retrieved
- `401 Unauthorized` - Missing or invalid session
- `404 Not Found` - Plugin not found

**Example:**
```bash
curl -X GET "https://localhost/v1/console/api/v1/marketplace/plugins/plugin:buildin-model-selector" \
  -H "Authorization: Bearer <token>"
```

---

### List Collections

**Request:**
```http
GET /collections
```

**Response:**
```json
{
  "collections": [
    {
      "id": "collection:optimization-expert",
      "name": "Optimization Expert",
      "description": "Skills for AI model optimization, cost reduction, and performance tuning",
      "icon": "⚡",
      "skills": [
        "plugin:buildin-learning-model-selector",
        "plugin:buildin-optimization-cost-analyzer"
      ],
      "target_use_case": "Optimize AI model routing and reduce inference costs",
      "difficulty": "advanced",
      "estimated_setup_time_minutes": 30
    },
    {
      "id": "collection:data-analyst",
      "name": "Data Analyst",
      "description": "Skills for data processing, visualization, and insights",
      "icon": "📊",
      "skills": [
        "plugin:buildin-datahub-creator",
        "plugin:buildin-data-processor"
      ],
      "target_use_case": "Analyze data and generate insights",
      "difficulty": "intermediate",
      "estimated_setup_time_minutes": 20
    }
  ]
}
```

**HTTP Status Codes:**
- `200 OK` - Collections retrieved
- `401 Unauthorized` - Missing or invalid session

**Example:**
```bash
curl -X GET "https://localhost/v1/console/api/v1/marketplace/collections" \
  -H "Authorization: Bearer <token>"
```

---

### List Categories

**Request:**
```http
GET /categories
```

**Response:**
```json
{
  "categories": {
    "learning": 12,
    "optimization": 8,
    "media": 15,
    "security_compliance": 5,
    "integration": 10,
    "data_processing": 6,
    "observability": 4
  }
}
```

**HTTP Status Codes:**
- `200 OK` - Categories retrieved
- `401 Unauthorized` - Missing or invalid session

**Example:**
```bash
curl -X GET "https://localhost/v1/console/api/v1/marketplace/categories" \
  -H "Authorization: Bearer <token>"
```

---

### List Tags

**Request:**
```http
GET /tags
```

**Response:**
```json
{
  "tags": {
    "ml": 18,
    "routing": 5,
    "optimization": 10,
    "cost": 7,
    "learning": 12,
    "automation": 14,
    "security": 8
  }
}
```

**HTTP Status Codes:**
- `200 OK` - Tags retrieved
- `401 Unauthorized` - Missing or invalid session

**Example:**
```bash
curl -X GET "https://localhost/v1/console/api/v1/marketplace/tags" \
  -H "Authorization: Bearer <token>"
```

---

## Filtering & Sorting

### Supported Sort Orders

| Sort By | Description |
|---------|-------------|
| `relevance` | Match relevance (default) |
| `downloads` | Most downloaded first |
| `rating` | Highest rated first |
| `recency` | Recently updated first |
| `name` | Alphabetical order |

### Supported Categories

The following categories are currently available (retrieved via `/categories`):
- `learning` — AI model selection, routing, decision-making
- `optimization` — Cost, performance, efficiency optimization
- `media` — Video, audio, image processing
- `security_compliance` — Security, compliance, audit
- `integration` — External service integrations
- `data_processing` — Data pipeline, ETL, transformation
- `observability` — Monitoring, logging, telemetry

### Filtering Behavior

- **Empty filters return all:** If no filters are specified, all skills are returned
- **Multiple filters AND together:** Specifying both `category=learning` and `tier=buildin` returns only skills matching both criteria
- **Rating filter is inclusive:** `min_rating=4.0` includes all skills with rating >= 4.0
- **Search + filters combine:** Free-text search results are further filtered by other criteria

---

## Audit & Telemetry

All marketplace discovery operations are logged to the audit trail:

| Event | Emitted When | Payload |
|-------|--------------|---------|
| `marketplace.search` | User performs a search | query, filters, result count |
| `marketplace.view_details` | User views skill details | plugin_id |
| `marketplace.view_collections` | User views collections | collections_count |
| `marketplace.view_categories` | User browses categories | categories_count |
| `marketplace.view_tags` | User browses tags | tags_count |

All events include:
- `tenant_id` — Tenant isolation
- Timestamp — Immutable record
- Hash chain — Linked to previous event

---

## Error Handling

### Common Errors

| Status | Error | Solution |
|--------|-------|----------|
| 401 | `Unauthorized` | Add valid `Authorization` header with session token |
| 400 | `Bad Request` | Check query parameter types and values |
| 404 | `Not Found` | Verify plugin_id exists (try search endpoint first) |
| 500 | `Internal Server Error` | Check server logs; marketplace index may be unavailable |

### Error Response Format

```json
{
  "detail": "Plugin not found"
}
```

---

## Rate Limiting

No rate limits are currently enforced on discovery endpoints, but this may change in future versions.

---

## Versioning

API Version: `1.0` (matches ADR-0892, Phase 1 Session 1)

All endpoints are stable and production-ready.

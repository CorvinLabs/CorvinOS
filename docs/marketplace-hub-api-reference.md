
# Marketplace Hub API Reference (ADR-0678, Phase 1)

## Overview
Unified discovery API for 5 artifact types: Plugins, Skills, Tools, Connectors, Layers.

**Prefix:** `/v1/console/marketplace/hub`

**Features:**
- Fuzzy search across 5 categories
- Faceted filtering (tier, domain, origin, rating)
- Pagination (1-100 per page)
- 5-minute TTL caching
- Audit trail integration
- Tenant-scoped queries (GDPR Art. 5, 6)

---

## Endpoints

### 1. GET `/hub/index` — Full Index

**Description:** Get marketplace hub index with all 5 categories (paginated).

**Parameters:**
| Name | Type | Default | Range | Description |
|---|---|---|---|---|
| `skip` | int | 0 | ≥ 0 | Items to skip per category |
| `limit` | int | 20 | 1-100 | Max items per category |
| `force_refresh` | bool | false | - | Bypass 5-min cache |

**Response (200 OK):**
```json
{
  "skills": [
    {
      "id": "skill:learning-optimizer",
      "category": "skills",
      "name": "Learning Optimizer",
      "description": "...",
      "version": "2.0.0",
      "author": "Shumway",
      "rating": 4.8,
      "rating_count": 42,
      "tags": ["learning", "optimization"],
      "domain": "decision-making",
      "tier": "contributor",
      "origin": "vetted",
      "install_count": 1205,
      "created_at": "2026-09-01T12:00:00Z",
      "updated_at": "2026-09-16T18:30:00Z",
      "is_trending": true,
      "is_new": false,
      "badge": "verified"
    }
  ],
  "plugins": [],
  "tools": [],
  "connectors": [],
  "layers": [],
  "timestamp": "2026-09-17T23:30:00Z",
  "total_count": 312
}
```

---

### 2. GET `/hub/search` — Search Across Categories

**Description:** Fuzzy search across all 5 categories with filtering.

**Parameters:**
| Name | Type | Default | Description |
|---|---|---|---|
| `q` | string | "" | Search query (fuzzy matched) |
| `categories` | string | all | Comma-separated: `skills,plugins,tools,connectors,layers` |
| `tier` | string | - | Filter by tier (e.g. `compliance`, `core`, `builtin`) |
| `domain` | string | - | Filter by domain (e.g. `memory`, `security`) |
| `origin` | string | - | Filter by origin (`builtin`, `vetted`, `community`) |
| `min_rating` | float | - | Min rating (0.0-5.0) |
| `page` | int | 1 | Page number (1-indexed) |
| `per_page` | int | 20 | Results per page (1-100) |

**Example Request:**
```bash
curl "http://localhost:8765/v1/console/marketplace/hub/search?q=router&categories=skills,plugins&tier=vetted&page=1&per_page=10"
```

**Response (200 OK):**
```json
{
  "items": [
    {
      "id": "skill:os-delegation-router",
      "category": "skills",
      "name": "OS Delegation Router",
      "rating": 4.9
    }
  ],
  "total": 42,
  "page": 1,
  "per_page": 10,
  "query": "router",
  "filters": {
    "tier": "vetted"
  },
  "facets": {
    "category": {
      "skills": 28,
      "plugins": 14
    }
  }
}
```

---

### 3. GET `/hub/trending` — Trending Items

**Description:** Get trending items scored by recency, rating, and install count.

**Parameters:**
| Name | Type | Default | Range |
|---|---|---|---|
| `limit` | int | 10 | 1-50 |

**Response (200 OK):**
```json
{
  "items": [
    {
      "id": "plugin:vibe-engineering",
      "name": "Vibe Engineering Dashboard",
      "is_trending": true
    }
  ],
  "total": 15,
  "label": "Trending Now"
}
```

---

### 4. GET `/hub/newest` — Newest Items

**Description:** Get newest items sorted by `created_at` (descending).

**Parameters:**
| Name | Type | Default | Range |
|---|---|---|---|
| `limit` | int | 10 | 1-50 |

---

### 5. GET `/hub/{category}/{item_id}` — Item Detail

**Description:** Get detailed view of a single item.

**Parameters:**
| Name | Type | Description |
|---|---|---|
| `category` | string (path) | `skills`, `plugins`, `tools`, `connectors`, or `layers` |
| `item_id` | string (path) | Item ID |

---

### 6. POST `/hub/drill-down` — Navigate to Subsystem UI

**Description:** Construct URL to navigate from marketplace to subsystem panel.

**Request Body:**
```json
{
  "subsystem_type": "skills",
  "item_id": "os-delegation-router",
  "target_page": "detail"
}
```

---

## Performance Characteristics

| Operation | Time | Cached |
|---|---|---|
| Full index | ~50ms | Yes (5 min TTL) |
| Fuzzy search | ~75ms | Yes (5 min TTL) |
| Trending | ~30ms | Yes (5 min TTL) |
| Newest | ~30ms | Yes (5 min TTL) |
| Detail view | ~20ms | Yes |

**All endpoints respond in <500ms** ✅

---

## Compliance & Audit

- ✅ All operations audited (GDPR Art. 30, 32)
- ✅ Tenant-scoped queries (Art. 5, 6)
- ✅ Hash-chained audit trail
- ✅ No PII in responses

---

## Related Documentation

- [Marketplace Hub Design (ADR-0678)](../decisions/ADR-0678-unified-marketplace-with-navigation-hub.md)
- [Marketplace Index v3 Schema](marketplace-index-v3-schema.md)

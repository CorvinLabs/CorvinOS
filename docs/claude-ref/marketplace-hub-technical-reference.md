# Marketplace Hub — Technical Reference (ADR-0678)

**Status:** Phase B Week 1 — ACCEPTED  
**Date:** 2026-09-18  
**All 5 LDD Gates:** ✅ COMPLETE

---

## Overview

The Marketplace Hub is a unified **discovery + navigation layer** for 5 artifact types:
- **Plugins** — Boot-layer plugins with lifecycle management
- **Skills** — Learnable OS-level skills with feedback loops
- **Tools** — MCP protocol tools (stateless)
- **Connectors** — Credential-based integrations
- **Layers** — Non-installable compliance-critical layers (read-only)

The Hub itself is **read-only discovery** — all mutations (install/enable/disable) happen in type-specific panels.

---

## Architecture

### Service Layer

**File:** `core/skills/marketplace_hub.py`

**Class:** `MarketplaceHub(corvin_home: str)`

Provides unified index + search across all 5 types:

```python
hub = MarketplaceHub(corvin_home)

# Load full index (cached, 5-minute TTL)
index = hub.get_index(force_refresh=False)
# → HubIndex(skills=[], plugins=[], tools=[], connectors=[], layers=[])

# Search with fuzzy matching + filters
result = hub.search(
    query="plugin",
    categories=["plugins"],
    filters={"tier": "builtin", "rating_min": 4.0},
    page=1,
    per_page=20
)
# → SearchResult(items=[...], total=42, facets={...})

# Trending items (scored by recency + rating + installs)
trending = hub.trending(limit=10)

# Newest items (sorted by created_at)
newest = hub.newest(limit=10)

# Get detail of single item
item = hub.get_detail(item_id="plugin:memory-retriever", category="plugins")
```

### Data Models

**DiscoveryItem** — One marketplace artifact

```python
@dataclass
class DiscoveryItem:
    id: str                    # e.g., "plugin:buildin-memory-semantic_context"
    category: str              # "plugins", "skills", "tools", "connectors", "layers"
    name: str
    description: str
    version: str
    author: str
    rating: float              # 0.0–5.0
    rating_count: int
    tags: List[str] = []
    domain: str = ""           # e.g., "memory", "security", "learning"
    tier: str = ""             # e.g., "builtin", "vetted", "community"
    origin: str = ""           # "builtin", "vetted", "community"
    install_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    is_trending: bool = False
    is_new: bool = False
    badge: str = ""            # "verified", "licensed", "popular", "new", "trending"
```

**HubIndex** — Full index across all 5 types

```python
@dataclass
class HubIndex:
    skills: List[DiscoveryItem]
    plugins: List[DiscoveryItem]
    tools: List[DiscoveryItem]
    connectors: List[DiscoveryItem]
    layers: List[DiscoveryItem]
    timestamp: str
    total_count: int
```

**SearchResult** — Paginated search results with metadata

```python
@dataclass
class SearchResult:
    items: List[DiscoveryItem]
    total: int
    page: int
    per_page: int
    query: str
    filters: Dict[str, Any]
    facets: Dict[str, Dict[str, int]]  # category → {value → count}
```

### API Routes

**File:** `core/console/corvin_console/routes/marketplace_hub_routes.py`

All routes are under `/v1/marketplace/` prefix:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/hub/index?skip=0&limit=20&force_refresh=false` | Full index with pagination |
| `GET` | `/hub/search?q=plugin&categories=plugins&tier=builtin&page=1&per_page=20` | Fuzzy search with filters |
| `GET` | `/hub/trending?limit=10` | Trending items across all types |
| `GET` | `/hub/newest?limit=10` | Newest items (by created_at) |
| `GET` | `/hub/{category}/{item_id}` | Detail view of single item |
| `POST` | `/hub/drill-down` | Navigate from Hub to type-specific panel |

#### Search Query Parameters

```
q              — Search query (fuzzy matched against name, description, tags)
categories     — Comma-separated types: "plugins,skills,tools,connectors,layers"
tier           — Filter by tier (builtin, vetted, community, compliance, core, installed)
domain         — Filter by domain (memory, security, learning, etc.)
origin         — Filter by origin (builtin, vetted, community)
min_rating     — Minimum rating (0–5)
page           — Page number (1-indexed, default: 1)
per_page       — Results per page (1–100, default: 20)
```

#### Search Response

```json
{
  "items": [
    {
      "id": "plugin:buildin-memory-semantic_context",
      "category": "plugins",
      "name": "Semantic Context Retriever",
      "description": "...",
      "version": "1.0.0",
      "author": "Corvin Labs",
      "rating": 4.8,
      "rating_count": 142,
      "tags": ["memory", "semantic", "context"],
      "domain": "memory",
      "tier": "buildin",
      "origin": "builtin",
      "install_count": 847,
      "created_at": "2026-01-15T00:00:00Z",
      "updated_at": "2026-09-17T00:00:00Z",
      "is_trending": true,
      "is_new": false,
      "badge": "verified"
    }
  ],
  "total": 42,
  "page": 1,
  "per_page": 20,
  "query": "plugin",
  "filters": {"tier": "builtin"},
  "facets": {
    "category": {"plugins": 32, "skills": 10, "tools": 0, "connectors": 0, "layers": 0},
    "tier": {"builtin": 25, "vetted": 12, "community": 5},
    "origin": {"builtin": 25, "vetted": 12, "community": 5}
  }
}
```

### Frontend Component

**File:** `core/console/corvin_console/web-next/src/panels/marketplace.tsx`

**Component:** `MarketplacePanel`

Features:
- 5 discovery cards (Plugins | Skills | Tools | Connectors | Layers)
- Unified search + filter bar (client-side)
- Real-time count updates per type
- Links to type-specific management panels
- Install progress tracking (Phase 2)
- Custom repository support

```tsx
export const MarketplacePanel: React.FC = () => {
  // Browse all 5 types
  // Search across all types
  // Filter by category, tier, domain, origin, rating
  // Click → navigate to type-specific panel or detail view
}
```

### Tenant Structure

**Location:** `~/.corvin/tenants/<tenant_id>/marketplace/`

```
marketplace/
├── config.yaml              # Hub settings (featured types, cache TTL)
├── index_v3.json           # Cached marketplace index (24h refresh)
├── search_cache.json       # Pre-computed search index (optional)
└── history.jsonl           # Search/browse history (audit trail)
```

**config.yaml Example:**

```yaml
spec:
  schema_version: '1.0'
  hub_enabled: true
  featured_types:
    - plugins
    - skills
  disabled_types: []
  index_refresh_interval_hours: 24
  cache_ttl_seconds: 3600
```

---

## Performance SLAs

| Operation | SLA | Typical | Notes |
|-----------|-----|---------|-------|
| `get_index()` | <500ms | 150ms | Cached locally, 5-min TTL |
| `search()` | <500ms | 120ms | Fuzzy scoring + filtering |
| `trending()` | <300ms | 80ms | Pre-computed scores |
| `newest()` | <300ms | 75ms | Pre-sorted by created_at |
| `get_detail()` | <200ms | 50ms | Direct lookup |

Cache is refreshed:
- **On Hub load** (if cache age > 5 minutes)
- **Force refresh** (query param `force_refresh=true`)
- **Daily automatic sync** (24-hour cron, checks remote index v3)

---

## Search Algorithm

### Fuzzy Scoring

Query matched against:
1. **Name** — 50% weight
2. **Description** — 30% weight
3. **Tags** — 20% weight

Scoring:
```python
def fuzzy_score(query: str, target: str) -> float:
    if query == target:
        return 1.0
    if query in target:
        return 0.8
    # Character matching: count consecutive chars found
    # Result: (matches/query_len + matches/target_len) / 2
```

Results sorted by score (descending).

### Filtering Pipeline

1. **Fuzzy score** (if query present, skip items with score < 0.1)
2. **Category filter** (if specified)
3. **Tier filter** (if specified)
4. **Domain filter** (if specified)
5. **Origin filter** (if specified)
6. **Rating minimum** (if specified)
7. **Pagination** (apply offset/limit)

Filters are **AND** logic — all must match.

### Facets

Facets are count-by-value aggregates computed after filtering:

```json
"facets": {
  "category": {
    "plugins": 32,
    "skills": 10,
    "tools": 5,
    "connectors": 3,
    "layers": 2
  },
  "tier": {
    "builtin": 25,
    "vetted": 15,
    "community": 12
  },
  "origin": {
    "builtin": 25,
    "vetted": 15,
    "community": 12
  }
}
```

---

## Caching Strategy

### Cache Levels

| Level | Location | TTL | Refresh |
|-------|----------|-----|---------|
| **L1: Memory** | `self.index` (process) | Session | Manual or force_refresh |
| **L2: Disk** | `~/.corvin/tenants/<tid>/marketplace/index_v3.json` | 5 min | Load from L1 if stale |
| **L3: Remote** | Corvin-Marketplace repo (CDN) | 24h | CI/CD nightly aggregation |

### Cache Invalidation

Cache is considered **stale** when:
- Age > 5 minutes (TTL)
- Hub first-load (check remote for updates)
- `force_refresh=true` query param

Stale cache is **refreshed** by:
1. Re-aggregating from 5 subsystem registries
2. Hash-chaining with audit trail (GDPR Art. 32)
3. Saving to disk with `_timestamp` for age tracking

---

## Audit Trail Integration

Every Hub action is audited (read-only discovery):

```python
# Event emitted for each search/trending/detail access
audit_event = {
    "tenant_id": "<tenant>",
    "event_type": "marketplace_hub_search",
    "query": "plugin",
    "categories": ["plugins"],
    "results_count": 42,
    "filters_applied": {"tier": "builtin"},
    "timestamp": "2026-09-18T12:34:56Z",
    "hash": "sha256(...)",
    "prev_hash": "sha256(...)",  # Hash-chained
    "lom": "corvin_console.routes.marketplace_hub_routes:search:L163",
}
audit_backend.write_event(audit_event)
```

---

## Compliance

### GDPR (Articles 5, 6, 30, 32)

- ✅ **Art. 5 (Lawfulness):** Hub is discovery-only; no personal data processed
- ✅ **Art. 6 (Consent):** No consent required (non-personal discovery)
- ✅ **Art. 30 (Accountability):** All Hub actions audit-logged
- ✅ **Art. 32 (Integrity):** Hash-chained audit trail + cache integrity verification

### EU AI Act (Article 50)

- ✅ **Art. 50 (Transparency):** Hub is user-controlled discovery, not AI-driven
- ✅ **Bot disclosure:** Not required (no AI decision-making in Hub)

### Data Classification (L34)

Hub search results are **LOW confidentiality** (public marketplace metadata):
- No user secrets
- No credentials
- No sensitive configuration

Filter: `ALLOW` for all authenticated tenants.

---

## Integration Points

### Type-Specific Panels (Phase 2–3)

| Type | Panel | Integration | Status |
|------|-------|------------ |--------|
| **Plugins** | `/plugin-center` | ✅ Hub links to panel | Live (ADR-0511) |
| **Skills** | `/skills` | 🟡 Links to panel (install via type-specific flow) | Phase 2 (ADR-0532) |
| **Tools** | `/mcp-tools` | 🟡 Links to panel (register via MCP) | Phase 2 (RFC) |
| **Connectors** | `/connectors` | 🟡 Links to panel (auth flow) | Phase 2 (TBD) |
| **Layers** | Layer metadata (read-only) | ✅ Display info, no install | Live (ADR-0243) |

### Marketplace Index v3

**File:** `Corvin-Marketplace/index/marketplace_index_v3.json`

**Generation:** CI/CD aggregates from 5 sources:
- `index/plugins.json` (Plugin registry)
- `index/skills.json` (Skill Forge)
- `index/tools.json` (MCP registry)
- `index/connectors.json` (Connector system)
- `index/layers.json` (Layer metadata)

**Deployment:**
1. Generate Index v3 (nightly CI/CD)
2. Upload to CDN / GitHub releases
3. CorvinOS downloads on Hub first-load (if cache age > 5 min)
4. Store locally in `~/.corvin/tenants/<tid>/marketplace/index_v3.json`

---

## Error Handling

### Graceful Degradation

| Failure | Fallback |
|---------|----------|
| Index load fails | Return cached index (even if stale) or empty list |
| Search crashes | Return empty results + error logged |
| Filter value invalid | Coerce to correct type or skip filter |
| Pagination out of range | Return empty list (not an error) |
| Item detail not found | Return 404 with message |

All errors are **audit-logged** and **fail-closed** (no data leakage).

---

## Testing

### Test Coverage

- **Unit tests:** 20+ (fuzzy scoring, filtering, pagination)
- **E2E tests:** 40+ (endpoint existence, search accuracy, response time SLAs)
- **Adversarial tests:** 26 (injection, performance, isolation, caching, edge cases)
- **Integration tests:** 5+ (cross-type ranking, cache sync, audit trail)

**Test file:** `tests/e2e/test_track_d_marketplace_hub_adversarial.py`

Run all tests:
```bash
python3 tests/e2e/test_track_d_marketplace_hub_adversarial.py
```

---

## Future Enhancements (Phase 2–3)

1. **Unified install modal** — 1-click install from Hub (launches type-specific wizard)
2. **Index v3 enrichment** — Add ratings, reviews, popularity scores
3. **Trending algorithm** — ML-based recommendation (future)
4. **Cross-type search ranking** — Smart ranking across all 5 types
5. **Marketplace Item Protocol (MIP)** — Standardize common fields across types
6. **Licensing integration** — Show license tier + restrictions in Hub

---

## References

- **ADR-0678:** Unified Marketplace with Navigation Hub + Phased Integration
- **ADR-0511:** Marketplace Plugin-First Architecture
- **ADR-0532–0535:** Skills 2.0 as Control Plane
- **ADR-0243:** Plugin Boot-Layer Taxonomy
- **CONCEPT-0038:** Marketplace Integration Hub Pattern
- **GDPR:** Articles 5, 6, 30, 32
- **EU AI Act:** Article 50

---

## Support & Troubleshooting

### No results in search

1. Check query is not empty or too specific
2. Verify categories specified exist (plugins, skills, tools, connectors, layers)
3. Check filters are not too restrictive
4. Force cache refresh: `?force_refresh=true`

### Slow search response

1. Check performance SLAs above
2. Verify network latency (remote index fetch)
3. Check if cache is expired and triggering refresh
4. Reduce per_page if pagination is slow

### Stale results in Hub

1. Results cached locally (5-min TTL)
2. Force refresh: `?force_refresh=true` in search/index
3. Or wait for daily auto-refresh (nightly CI/CD)

### Type-specific panel missing link

1. Hub is read-only; links to type-specific panels
2. If panel doesn't exist yet (Skills, Tools, Connectors), Hub still shows counts but links are disabled
3. Create the panel and register route in NAV_GROUPS (console layout)

---

**Last Updated:** 2026-09-18  
**Status:** Phase B Week 1 Complete  
**All 5 LDD Gates:** ✅ PASS

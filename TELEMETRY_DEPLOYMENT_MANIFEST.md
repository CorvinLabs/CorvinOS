# CorvinOS Public Telemetry Deployment Manifest

**Status:** K=3-K=5 Specification  
**Goal:** Live stats + GitHub Pages + World Map @ corvin-labs.com/stats

---

## K=3: Live Web Dashboard (corvin-labs.com/stats)

### Deployment Architecture

```
┌─────────────────────────────────┐
│  All CorvinOS Instances         │
│  (local + remote via A2A)       │
│  ├─ /api/metrics/stats          │
│  └─ /api/metrics/session/{id}   │
└────────────┬────────────────────┘
             │ (Instance Discovery via A2A)
             ↓
┌─────────────────────────────────┐
│  Instance Registry              │
│  (~/.corvin/instances.json)     │
│  ├─ instance_id                 │
│  ├─ hostname                    │
│  ├─ location (lat,lon)          │
│  ├─ turn_count                  │
│  └─ total_tokens                │
└────────────┬────────────────────┘
             │
             ↓
┌─────────────────────────────────┐
│  Aggregation API                │
│  GET /api/metrics/stats         │
│  ├─ cluster stats (real-time)   │
│  ├─ all instances               │
│  └─ historical trends           │
└────────────┬────────────────────┘
             │
    ┌────────┴────────┐
    ↓                 ↓
┌─────────────┐   ┌──────────────┐
│ GitHub Pages│   │corvin-labs   │
│  /stats.html│   │/stats (live) │
│ (static)    │   │(real-time)   │
└─────────────┘   └──────────────┘
```

### Implementation Steps (K=3)

1. **Deploy Reverse Proxy** (nginx/CloudFlare)
   - `corvin-labs.com/stats` → Internal CorvinOS API
   - CORS: Allow public access
   - Rate limit: 100 req/min per IP

2. **Wire Instance Discovery**
   - On startup: CorvinOS registers itself in InstanceRegistry
   - A2A Protocol: Query remote instances for their stats
   - Update frequency: Every 5 minutes

3. **Create React Dashboard Component**
   ```typescript
   // core/console/frontend/src/pages/PublicStats.tsx
   export const PublicStats: React.FC = () => {
     const [stats, setStats] = useState<ClusterStats | null>(null)
     const [instances, setInstances] = useState<Instance[]>([])
     
     useEffect(() => {
       const poll = setInterval(async () => {
         const res = await fetch('/api/metrics/stats')
         const data = await res.json()
         setStats(data.summary)
         setInstances(data.cluster.instances || [])
       }, 5000)
       return () => clearInterval(poll)
     }, [])
     
     return (
       <div className="public-stats">
         <h1>CorvinOS Telemetry</h1>
         <StatsGrid summary={stats} />
         <WorldMap instances={instances} />
         <InstancesTable instances={instances} />
         <TrendCharts instances={instances} />
       </div>
     )
   }
   ```

4. **Register Route**
   - Path: `/stats` (public, no auth required)
   - Component: PublicStats
   - Caching: 5-second client-side polling

---

## K=4: World Map Visualization

### Requirements

- **Library:** Leaflet (already in docs/stats.html)
- **Geo-Location:** GeoIP lookup for instance locations
- **Markers:** Color-coded by instance health (online/offline, savings %)
- **Clustering:** Group instances by region

### Implementation

```typescript
// core/learning/geo_locator.py (new)
class GeoLocator:
    """Geo-locate instances by IP address."""
    
    async def locate(self, hostname: str) -> tuple[float, float]:
        """Get latitude/longitude for hostname.
        
        Uses MaxMind GeoIP2 or IP2Location API.
        Falls back to "unknown" location if lookup fails.
        """
        # Lookup in GeoIP2 DB
        # Cache in InstanceRegistry
        # Return (lat, lon) or ("unknown", 0)
```

### Instance Marker Styling

```javascript
// Green circle: healthy instance (savings > 20%)
// Blue circle: moderate instance (savings 10-20%)
// Yellow circle: baseline instance (savings < 10%)
// Red circle: offline (not seen in 24h)

L.circleMarker([lat, lon], {
  radius: 8 + (savings_percent / 10),  // Size by savings
  fillColor: getColor(savings_percent),  // Color by perf
  color: '#79c0ff',
  weight: 2,
  opacity: 1,
  fillOpacity: 0.8,
}).bindPopup(`
  <strong>${instance.hostname}</strong><br>
  Location: ${instance.location}<br>
  Turns: ${instance.turn_count}<br>
  Tokens: ${instance.total_tokens.toLocaleString()}<br>
  Savings: ${instance.savings_percent.toFixed(1)}%<br>
  <small>Last seen: ${instance.last_seen}</small>
`)
```

---

## K=5: E2E Integration Test

### Test Scenarios

```python
# tests/e2e/test_public_telemetry.py

async def test_instance_registration():
    """Test that instances self-register."""
    registry = get_instance_registry()
    
    # Register test instance
    registry.register_instance(
        instance_id="test-001",
        hostname="test.local",
        version="0.10.51",
        location="52.5200,13.4050",  # Berlin
        api_url="http://test.local:8000/api/metrics"
    )
    
    # Verify registration
    instances = registry.get_instances()
    assert any(i.instance_id == "test-001" for i in instances)

async def test_cluster_aggregation():
    """Test that cluster stats aggregate correctly."""
    registry = get_instance_registry()
    
    # Register 3 test instances
    for i in range(3):
        registry.register_instance(
            instance_id=f"test-{i}",
            hostname=f"test-{i}.local",
            version="0.10.51",
            location=f"{50+i},{10+i}",
        )
        registry.update_metrics(
            f"test-{i}",
            turn_count=100 * (i+1),
            total_tokens=1000 * (i+1),
            avg_tokens_per_turn=10,
            savings_percent=20.0 + i,
        )
    
    stats = registry.aggregate_stats()
    
    assert stats["instance_count"] == 3
    assert stats["total_turns"] == 600  # 100 + 200 + 300
    assert stats["total_tokens"] == 6000  # 1000 + 2000 + 3000

async def test_api_stats_endpoint():
    """Test GET /api/metrics/stats returns cluster data."""
    client = AsyncClient(app=app, base_url="http://test")
    response = await client.get("/api/metrics/stats")
    
    assert response.status_code == 200
    data = response.json()
    assert "cluster" in data
    assert "summary" in data
    assert "timestamp" in data
    assert data["summary"]["instance_count"] >= 0

async def test_public_stats_page_loads():
    """Test that docs/stats.html is valid HTML and loads."""
    with open("docs/stats.html") as f:
        html = f.read()
    
    assert "<!DOCTYPE html>" in html
    assert "CorvinOS Telemetry" in html
    assert "leaflet" in html.lower()  # Map library
    assert "chart.js" in html.lower()  # Charts library

async def test_instance_discovery_via_a2a():
    """Test A2A protocol discovers remote instances."""
    # This would require real A2A setup; for now, mock:
    
    mock_remote = {
        "instance_id": "remote-001",
        "hostname": "remote.example.com",
        "location": "37.7749,-122.4194",  # San Francisco
        "turn_count": 500,
        "total_tokens": 5000,
        "savings_percent": 25.0,
    }
    
    registry = get_instance_registry()
    registry.register_instance(**mock_remote)
    
    stats = registry.aggregate_stats()
    assert "remote-001" in [i["instance_id"] for i in stats["instances"]]
```

### Test Execution

```bash
pytest tests/e2e/test_public_telemetry.py -v

# Expected: 5 tests pass
# - Instance registration ✅
# - Cluster aggregation ✅
# - API endpoint ✅
# - HTML page loads ✅
# - A2A discovery ✅
```

---

## GitHub Pages Auto-Update (CI Workflow)

**File:** `.github/workflows/update-stats.yml`

```yaml
name: Update Stats Dashboard

on:
  schedule:
    - cron: '*/5 * * * *'  # Every 5 minutes
  workflow_dispatch:

jobs:
  update-stats:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Fetch cluster stats
        run: |
          curl -s https://api.corvin-labs.com/api/metrics/stats \
            > /tmp/stats.json
      
      - name: Update docs/stats.html (data injection)
        run: |
          python3 scripts/inject_stats.py /tmp/stats.json docs/stats.html
      
      - name: Commit and push
        run: |
          git config user.name "CorvinOS Bot"
          git config user.email "bot@corvin-labs.com"
          git add docs/stats.html
          git commit -m "chore: update telemetry stats" || true
          git push
```

---

## Deployment Checklist

| Component | Status | Notes |
|-----------|--------|-------|
| **Instance Registry** | ✅ DONE | `core/learning/instance_registry.py` |
| **Aggregation API** | ✅ DONE | Updated `/api/metrics/stats` |
| **GitHub Pages Site** | ✅ DONE | `docs/stats.html` |
| **Instance Geo-Location** | 📋 TODO | MaxMind GeoIP2 lookup |
| **World Map Component** | 📋 TODO | Leaflet + instance markers |
| **Public Dashboard** | 📋 TODO | React component @ /stats |
| **A2A Discovery** | 📋 TODO | Query remote instances |
| **E2E Tests** | 📋 TODO | Integration test suite |
| **CI Auto-Update** | 📋 TODO | GitHub Actions workflow |
| **corvin-labs.com Hosting** | 📋 TODO | Production deployment |

---

## Timeline

- **Today:** Instance Registry + API ✅
- **Day 2:** GitHub Pages + E2E Tests
- **Day 3:** Geo-Location + World Map
- **Day 4:** Production Deployment

---

## Success Criteria

✅ All CorvinOS instances appear on world map  
✅ Stats update every 5 seconds  
✅ GitHub Pages site shows historical data  
✅ Public dashboard accessible @ corvin-labs.com/stats  
✅ E2E tests all pass  
✅ No auth required for public dashboard  

---

## Reference

- Instance Registry: `core/learning/instance_registry.py`
- Stats API: `core/console/corvin_console/routes/vibe_metrics_api.py:/api/metrics/stats`
- HTML Template: `docs/stats.html`
- Test Suite: `tests/e2e/test_public_telemetry.py` (to be created)

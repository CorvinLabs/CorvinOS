# Dashboard Specification

**URL:** `https://corvin-labs.com/stats`  
**Framework:** Next.js 14+ / React 18 / TypeScript  
**Deployment:** Vercel (auto-deploy from GitHub)  
**Status:** Design ready for implementation  

---

## Dashboard Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Frontend (Next.js + React)                   │
├─────────────────────────────────────────────────────────────────┤
│ Pages:                                                          │
│  ├─ /stats                    (global overview)                │
│  ├─ /stats/instances          (instance list)                  │
│  ├─ /stats/instance/[id]      (instance detail)               │
│  └─ /stats/settings           (theme, filters)                │
├─────────────────────────────────────────────────────────────────┤
│ Components:                                                     │
│  ├─ KPICards (header metrics)                                 │
│  ├─ WorldMap (Mapbox, instance distribution)                 │
│  ├─ TimeSeriesChart (Recharts)                               │
│  ├─ InstanceTable (sortable, filterable)                     │
│  ├─ DistributionChart (pie/donut)                           │
│  └─ AuditTrail (hash chain visualization)                    │
├─────────────────────────────────────────────────────────────────┤
│ State Management:                                               │
│  ├─ TanStack Query (server state + caching)                  │
│  ├─ Zustand (UI state: filters, theme, layout)              │
│  └─ Local Storage (user preferences)                         │
├─────────────────────────────────────────────────────────────────┤
│ Real-time Updates:                                              │
│  ├─ 30s polling interval (via useQuery)                      │
│  ├─ (Optional) WebSocket for <2s latency                     │
│  └─ (Optional) Server-Sent Events (SSE)                      │
└─────────────────────────────────────────────────────────────────┘
        ↓
    Dashboard API
    /api/v1/dashboard/stats/global
    /api/v1/dashboard/timeseries/*
    /api/v1/dashboard/instances
    /api/v1/dashboard/instances/:id
        ↓
    Backend (Python/Flask)
        ↓
    Redis Cache (30s TTL)
        ↓
    InfluxDB (time-series)
```

---

## 1. Project Structure

```
core/console/corvin_console/web-next/
├── src/
│   ├── app/
│   │   ├── layout.tsx              # Root layout
│   │   ├── page.tsx                # /stats redirects here
│   │   └── stats/
│   │       ├── layout.tsx          # Stats layout (sidebar, nav)
│   │       ├── page.tsx            # /stats (global overview)
│   │       ├── instances/
│   │       │   ├── page.tsx        # /stats/instances (list)
│   │       │   └── [id]/
│   │       │       └── page.tsx    # /stats/instance/[id] (detail)
│   │       └── settings/
│   │           └── page.tsx        # /stats/settings (config)
│   │
│   ├── components/
│   │   ├── KPICards.tsx
│   │   ├── WorldMap.tsx
│   │   ├── TimeSeriesChart.tsx
│   │   ├── InstanceTable.tsx
│   │   ├── DistributionChart.tsx
│   │   ├── AuditTrail.tsx
│   │   ├── FilterBar.tsx
│   │   ├── Sidebar.tsx
│   │   └── ThemeToggle.tsx
│   │
│   ├── hooks/
│   │   ├── useDashboardStats.ts
│   │   ├── useInstances.ts
│   │   ├── useTimeSeries.ts
│   │   └── useSettings.ts
│   │
│   ├── lib/
│   │   ├── api.ts                  # API client (fetch wrapper)
│   │   ├── types.ts                # TypeScript interfaces
│   │   └── formatting.ts           # formatters (latency, cost, etc.)
│   │
│   └── styles/
│       ├── globals.css             # Tailwind globals
│       └── theme.css               # CSS variables (light/dark)
│
├── public/
│   └── mapbox-token.json           # Mapbox access token
│
├── next.config.js
├── tailwind.config.js
├── tsconfig.json
└── package.json
```

---

## 2. Global Overview Page (`/stats`)

### 2.1 Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  CORVINOSSYSTEM STATISTICS                      🌙 Settings ⚙  │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐  │
│  │1247     │ │3.8K     │ │98.3%    │ │1235     │ │$12.4K  │  │
│  │Instances│ │Active   │ │Global   │ │Reporting│ │Cost    │  │
│  │ ↑ 2%   │ │Users    │ │Uptime   │ │(5m)    │ │(Today) │  │
│  │         │ │ ↑ 5%   │ │         │ │ ↑ 0.5% │ │ ↑ 8%  │  │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘  │
│                                                                 │
│  Health Status:  ████████████░░░ 98.3% healthy                │
│  (Green: 1212 | Yellow: 23 | Red: 12 | Gray: 0)               │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  [WORLD MAP — Mapbox, Clustered pins]                          │
│  Green pins = Healthy                                           │
│  Yellow pins = Degraded                                         │
│  Red pins = Unhealthy                                           │
│  Gray pins = Offline                                            │
│  Click to drill-down to instance detail                         │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Instance Count (24h)      Latency p50/p95/p99 (24h)           │
│  ▲                          ▲                                   │
│  │  ╱╲  ╱╲                  │    ╱╲  ╱╲  ╱╲                     │
│  │ ╱  ╲╱  ╲                 │   ╱  ╲╱  ╲╱  ╲                    │
│  └──────────► time          └──────────────► time             │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Instances by Version    |  Instances by Region                │
│  ▶ 0.2.1  [█████] 71%    |  ▶ EU    [█████] 41%               │
│  ▶ 0.2.0  [██] 28%       |  ▶ NA    [████] 37%                │
│  ▶ 0.1.x  [░] 1%         |  ▶ APAC  [██] 18%                  │
│                          |  ▶ Others [░] 4%                   │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│ Instance List (sortable, filterable)                           │
│                                                                 │
│ [Version ▼] [Region ▼] [Status ▼]  [Search...]  [Export]      │
│                                                                 │
│ ID                   │Version│Region│Uptime│Users│Latency│Last │
│────────────────────────────────────────────────────────────────│
│corvin_abc123...      │0.2.1  │EU(DE)│99.1% │  3  │342ms  │2m   │
│corvin_def456...      │0.2.1  │NA(US)│98.5% │  5  │567ms  │5m   │
│corvin_ghi789...      │0.2.0  │APAC  │95.2% │  2  │812ms  │15m  │
│...                   │...    │...   │...   │...  │...    │...  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 Component: KPICards

```typescript
// src/components/KPICards.tsx

interface KPICard {
  label: string;
  value: string | number;
  trend?: {
    direction: 'up' | 'down' | 'neutral';
    percent: number;
  };
  icon?: ReactNode;
  color?: 'green' | 'blue' | 'amber' | 'red';
}

export function KPICards({ stats }: { stats: GlobalStats }) {
  const cards: KPICard[] = [
    {
      label: 'Instances',
      value: stats.instance_count,
      trend: {
        direction: stats.instance_count_trend > 0 ? 'up' : 'down',
        percent: Math.abs(stats.instance_count_trend),
      },
      icon: <Package2 />,
      color: 'blue',
    },
    {
      label: 'Active Users',
      value: formatNumber(stats.active_users),
      trend: stats.active_users_trend,
      icon: <Users />,
      color: 'green',
    },
    {
      label: 'Global Uptime',
      value: `${stats.global_uptime_percent}%`,
      icon: <Activity />,
      color: stats.global_uptime_percent > 95 ? 'green' : 'amber',
    },
    {
      label: 'Reporting (5m)',
      value: stats.instances_reporting_5m,
      icon: <Radio />,
      color: 'green',
    },
    {
      label: 'Cost (Today)',
      value: formatCurrency(stats.cost_today),
      trend: stats.cost_trend,
      icon: <DollarSign />,
      color: 'amber',
    },
  ];

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4">
      {cards.map((card) => (
        <KPICard key={card.label} {...card} />
      ))}
    </div>
  );
}
```

### 2.3 Component: WorldMap

```typescript
// src/components/WorldMap.tsx

import mapboxgl from 'mapbox-gl';
import { useEffect, useRef } from 'react';

export function WorldMap({ instances }: { instances: Instance[] }) {
  const mapContainer = useRef<HTMLDivElement>(null);
  const map = useRef<mapboxgl.Map | null>(null);

  useEffect(() => {
    if (!mapContainer.current) return;

    // Initialize map
    mapboxgl.accessToken = process.env.NEXT_PUBLIC_MAPBOX_TOKEN!;
    map.current = new mapboxgl.Map({
      container: mapContainer.current,
      style: 'mapbox://styles/mapbox/light-v11',
      center: [10, 50], // Europe centered
      zoom: 3,
    });

    // Add cluster sources + layers
    map.current.on('load', () => {
      map.current!.addSource('instances', {
        type: 'geojson',
        data: {
          type: 'FeatureCollection',
          features: instances.map((inst) => ({
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [inst.lon, inst.lat] },
            properties: {
              id: inst.instance_id,
              status: inst.status,
              version: inst.version,
              uptime: inst.uptime_percent,
            },
          })),
        },
        cluster: true,
        clusterMaxZoom: 14,
        clusterRadius: 50,
      });

      // Cluster layer
      map.current!.addLayer({
        id: 'clusters',
        type: 'circle',
        source: 'instances',
        filter: ['has', 'point_count'],
        paint: {
          'circle-color': '#51bbd6',
          'circle-radius': ['step', ['get', 'point_count'], 20, 100, 30, 750, 40],
        },
      });

      // Individual point layer (colored by status)
      map.current!.addLayer({
        id: 'instances-points',
        type: 'circle',
        source: 'instances',
        filter: ['!', ['has', 'point_count']],
        paint: {
          'circle-radius': 8,
          'circle-color': [
            'case',
            ['==', ['get', 'status'], 'healthy'],
            '#22c55e',
            ['==', ['get', 'status'], 'degraded'],
            '#eab308',
            ['==', ['get', 'status'], 'unhealthy'],
            '#ef4444',
            '#6b7280', // gray for offline
          ],
          'circle-opacity': 0.8,
          'circle-stroke-width': 1,
          'circle-stroke-color': '#fff',
        },
      });

      // Click handler
      map.current!.on('click', 'instances-points', (e) => {
        if (e.features && e.features[0]) {
          const instanceId = e.features[0].properties?.id;
          window.location.href = `/stats/instance/${instanceId}`;
        }
      });
    });

    return () => map.current?.remove();
  }, [instances]);

  return <div ref={mapContainer} className="w-full h-96" />;
}
```

### 2.4 Component: TimeSeriesChart

```typescript
// src/components/TimeSeriesChart.tsx

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts';

export function TimeSeriesChart({
  data,
  title,
  metrics,
}: {
  data: Array<{ timestamp: string; [key: string]: number }>;
  title: string;
  metrics: Array<{ key: string; label: string; color: string }>;
}) {
  return (
    <div className="bg-white dark:bg-slate-900 rounded-lg p-4">
      <h3 className="text-lg font-semibold mb-4">{title}</h3>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="timestamp" />
          <YAxis />
          <Tooltip />
          <Legend />
          {metrics.map((metric) => (
            <Line
              key={metric.key}
              type="monotone"
              dataKey={metric.key}
              stroke={metric.color}
              name={metric.label}
              dot={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
```

---

## 3. Instance Detail Page (`/stats/instance/[id]`)

### 3.1 Layout

```
┌─────────────────────────────────────────────────────────────────┐
│ ◀ Back                    Instance: corvin_abc123...           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│ Version: 0.2.1 | Region: EU (DE) | Deployment: docker         │
│ Uptime: 52h 15m | Health: ✓ Healthy | Last Report: 2m         │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│ [Overview] [Performance] [Features] [Audit] [Errors]           │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│ OVERVIEW TAB                                                    │
│                                                                 │
│ Boot Time: 2026-08-27T10:15:30Z                                │
│ Uptime: 52.25 hours                                            │
│ Active Users: 3                                                │
│ Sessions (Total): 247                                          │
│ Cost (Today): $45.32                                           │
│                                                                 │
│ Audit Chain: ✓ Healthy                                         │
│ Compliance Tripwire: ✓ Active                                  │
│ Boot Time: 8.2 seconds                                         │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│ PERFORMANCE TAB (7-day window)                                  │
│                                                                 │
│ Latency (p50/p95/p99)                                          │
│ [Line chart]                                                    │
│                                                                 │
│ Error Rate (%)                                                  │
│ [Line chart]                                                    │
│                                                                 │
│ CPU / Memory Usage                                              │
│ [Stacked area chart]                                            │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. Instance List Page (`/stats/instances`)

### 4.1 Filters & Sort

```typescript
interface InstanceListFilters {
  status?: 'healthy' | 'degraded' | 'unhealthy' | 'offline';
  region?: 'EU' | 'NA' | 'APAC' | 'LATAM' | 'MENA' | 'all';
  version?: string;
  deployment_type?: string;
  search?: string;
  sort_by?: 'users' | 'latency' | 'cost' | 'uptime';
  sort_order?: 'asc' | 'desc';
}
```

### 4.2 Table Component

```typescript
// src/components/InstanceTable.tsx

interface InstanceTableRow {
  instance_id: string;
  version: string;
  region: string;
  country: string;
  uptime_percent: number;
  active_users: number;
  avg_latency_ms: number;
  error_rate_percent: number;
  cost_today: number;
  status: 'healthy' | 'degraded' | 'unhealthy' | 'offline';
  last_report: string;
}

export function InstanceTable({ rows, onRowClick }: Props) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead>
          <tr>
            <th>ID</th>
            <th>Version</th>
            <th>Region</th>
            <th>Uptime</th>
            <th>Users</th>
            <th>Latency</th>
            <th>Errors</th>
            <th>Cost</th>
            <th>Last Report</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.instance_id}
              onClick={() => onRowClick(row.instance_id)}
              className="cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-800"
            >
              <td>{row.instance_id.slice(0, 16)}...</td>
              <td>{row.version}</td>
              <td>{row.region} ({row.country})</td>
              <td>{row.uptime_percent.toFixed(1)}%</td>
              <td>{row.active_users}</td>
              <td>{row.avg_latency_ms.toFixed(0)}ms</td>
              <td>{row.error_rate_percent.toFixed(2)}%</td>
              <td>${row.cost_today.toFixed(2)}</td>
              <td>{formatTimeAgo(row.last_report)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

---

## 5. Data Fetching Hooks

### 5.1 useDashboardStats

```typescript
// src/hooks/useDashboardStats.ts

import { useQuery } from '@tanstack/react-query';
import { dashboardAPI } from '@/lib/api';

export function useDashboardStats(range: '24h' | '7d' | '30d' = '24h') {
  return useQuery({
    queryKey: ['dashboard-stats', range],
    queryFn: () => dashboardAPI.getGlobalStats({ range }),
    refetchInterval: 30000, // 30s
    staleTime: 20000, // 20s
  });
}
```

### 5.2 useInstances

```typescript
// src/hooks/useInstances.ts

export function useInstances(filters: InstanceListFilters = {}) {
  return useQuery({
    queryKey: ['instances', filters],
    queryFn: () => dashboardAPI.getInstances(filters),
    refetchInterval: 60000, // 60s
    staleTime: 50000, // 50s
  });
}
```

---

## 6. API Client

### 6.1 API Wrapper

```typescript
// src/lib/api.ts

class DashboardAPI {
  private baseURL = process.env.NEXT_PUBLIC_API_URL || '/api/v1';

  async getGlobalStats(params: { range: string }) {
    const response = await fetch(
      `${this.baseURL}/dashboard/stats/global?range=${params.range}`
    );
    if (!response.ok) throw new Error('Failed to fetch global stats');
    return response.json();
  }

  async getInstances(filters: InstanceListFilters) {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value) params.append(key, String(value));
    });
    
    const response = await fetch(
      `${this.baseURL}/dashboard/instances?${params.toString()}`
    );
    if (!response.ok) throw new Error('Failed to fetch instances');
    return response.json();
  }

  async getInstance(instanceId: string) {
    const response = await fetch(
      `${this.baseURL}/dashboard/instances/${instanceId}`
    );
    if (!response.ok) throw new Error('Failed to fetch instance');
    return response.json();
  }

  async getTimeSeries(metric: string, params: TimeSeriesParams) {
    const query = new URLSearchParams(params as any);
    const response = await fetch(
      `${this.baseURL}/dashboard/timeseries/${metric}?${query.toString()}`
    );
    if (!response.ok) throw new Error(`Failed to fetch ${metric}`);
    return response.json();
  }
}

export const dashboardAPI = new DashboardAPI();
```

---

## 7. TypeScript Types

```typescript
// src/lib/types.ts

export interface GlobalStats {
  timestamp: string;
  instance_count: number;
  instance_count_trend: number;
  active_users: number;
  active_users_trend: number;
  global_uptime_percent: number;
  instances_reporting_5m: number;
  cost_today: number;
  cost_trend: number;
  avg_latency_ms: number;
  p95_latency_ms: number;
  p99_latency_ms: number;
  error_rate_percent: number;
  regions: Record<string, RegionalStats>;
  versions: Record<string, VersionStats>;
}

export interface Instance {
  instance_id: string;
  version: string;
  region: string;
  country: string;
  uptime_hours: number;
  uptime_percent: number;
  active_users: number;
  avg_latency_ms: number;
  error_rate_percent: number;
  cost_today: number;
  status: 'healthy' | 'degraded' | 'unhealthy' | 'offline';
  last_report: string;
  boot_time: string;
  lat: number;
  lon: number;
}

export interface InstanceDetail extends Instance {
  deployment_type: string;
  python_version: string;
  models_used: string[];
  plugins_installed: number;
  marketplace_plugins: number;
  audit_chain_healthy: boolean;
  compliance_tripwire_active: boolean;
  boot_time_seconds: number;
}
```

---

## 8. Styling & Theme

### 8.1 TailwindCSS Setup

```javascript
// tailwind.config.js

module.exports = {
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        status: {
          healthy: '#22c55e',
          degraded: '#eab308',
          unhealthy: '#ef4444',
          offline: '#6b7280',
        },
      },
    },
  },
  plugins: [],
};
```

### 8.2 Theme Toggle

```typescript
// src/components/ThemeToggle.tsx

import { useEffect, useState } from 'react';
import { Moon, Sun } from 'lucide-react';

export function ThemeToggle() {
  const [isDark, setIsDark] = useState(false);

  useEffect(() => {
    setIsDark(document.documentElement.classList.contains('dark'));
  }, []);

  const toggle = () => {
    document.documentElement.classList.toggle('dark');
    setIsDark(!isDark);
    localStorage.setItem('theme', isDark ? 'light' : 'dark');
  };

  return (
    <button onClick={toggle}>
      {isDark ? <Sun size={20} /> : <Moon size={20} />}
    </button>
  );
}
```

---

## 9. Performance Optimization

### 9.1 Code Splitting

```typescript
// app/stats/page.tsx

import dynamic from 'next/dynamic';

const WorldMap = dynamic(() => import('@/components/WorldMap'), {
  loading: () => <MapSkeleton />,
  ssr: false, // Map requires browser APIs
});

const Charts = dynamic(() => import('@/components/Charts'), {
  loading: () => <ChartsSkeleton />,
});

export default function StatsPage() {
  return (
    <>
      <KPICards />
      <Suspense fallback={<MapSkeleton />}>
        <WorldMap />
      </Suspense>
      <Suspense fallback={<ChartsSkeleton />}>
        <Charts />
      </Suspense>
    </>
  );
}
```

### 9.2 Query Caching Strategy

```typescript
// TanStack Query config

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 20 * 1000, // 20s
      gcTime: 60 * 1000, // 60s (formerly cacheTime)
      retry: 3,
      retryDelay: (attemptIndex) => Math.min(1000 * 2 ** attemptIndex, 30000),
    },
  },
});
```

---

## 10. Accessibility (WCAG 2.1 AA)

- **Color contrast:** Minimum 4.5:1 for text
- **Keyboard navigation:** All interactive elements accessible via Tab
- **Screen readers:** Proper ARIA labels, semantic HTML
- **Focus indicators:** Clear, visible focus ring on all buttons
- **Responsive:** Mobile (375px) → Tablet (768px) → Desktop (1920px)

---

## 11. Error Handling & Loading States

```typescript
export function useDashboardStats() {
  const { data, isLoading, isError, error } = useDashboardStats();

  if (isLoading) return <StatsSkeletonLoader />;
  if (isError) return <ErrorAlert error={error} />;
  return <KPICards stats={data} />;
}
```

---

## 12. Testing

### E2E Tests (Playwright)

```typescript
// e2e/dashboard.spec.ts

test('dashboard loads and displays global stats', async ({ page }) => {
  await page.goto('/stats');
  
  // Wait for KPI cards
  await page.waitForSelector('[data-testid="kpi-card"]');
  
  // Check values exist
  const instances = await page.textContent('[data-testid="kpi-instances"]');
  expect(instances).toMatch(/\d+/);
  
  // Map should be rendered
  await page.waitForSelector('.mapboxgl-canvas');
});

test('instance detail page loads on click', async ({ page }) => {
  await page.goto('/stats');
  
  // Click first instance in table
  await page.click('table tbody tr:first-child');
  
  // Navigate to detail page
  await page.waitForURL(/\/stats\/instance\/corvin_.*/);
  
  // Verify detail content
  await page.waitForSelector('[data-testid="instance-detail"]');
});
```

---

**Status:** ✅ Design ready for React/TypeScript implementation

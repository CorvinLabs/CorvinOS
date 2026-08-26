# Task Graph Viewer — Console Integration Guide

> **This integration is DONE (2026-08-24).** The guide is kept as background on
> the design; it is no longer a to-do list, and some of it describes shapes that
> were never built that way. What actually shipped:
>
> | Concern | Where it lives |
> |---|---|
> | API routes | `core/console/corvin_console/routes/task_graph_api.py` (mounted under `/v1/console`) |
> | Task list for the picker | `GET /v1/console/api/tasks/graphs` |
> | Data hooks | `src/hooks/useTaskGraph.ts` (`useTaskGraph`, `useTaskList`) |
> | Page | `src/pages/task-graph.tsx` |
> | Route | `/app/task-graph` in `src/App.tsx`, lazy entry in `src/lazy-pages.ts` |
> | Sidebar entry | "Task Graph" under Vibe Engineering in `src/components/layout.tsx` |
> | E2E | `tests/e2e/task-graph-viewer.spec.ts` (7 tests, green) |
>
> Two things this guide gets wrong: the route file is `task_graph_api.py`, not
> `tasks_graph.py`, and no `TaskGraphPanel.tsx` panel-registry wrapper was used —
> the viewer is a plain routed page. Note also that `nodes` comes over the wire
> as a **list**, not a map; `useTaskGraph` normalizes it.

## Overview

This guide explains how to integrate the **TaskGraphViewer** component into the Corvin Console web frontend and wire it to the backend API.

## Prerequisites

- Node.js 18+
- npm 9+
- React 18+
- D3.js 7+ (already available via mermaid)

## Backend Setup (Phase 1)

### Required API Endpoint

The frontend expects a backend API endpoint that returns task graph data:

```http
GET /v1/console/api/tasks/{taskId}/graph
Content-Type: application/json
```

### Expected Response Schema

```json
{
  "task_id": "task-123",
  "created_at": "2026-08-24T10:30:00Z",
  "nodes": {
    "node-1": {
      "id": "node-1",
      "type": "decision",
      "timestamp": "2026-08-24T10:30:01Z",
      "data": {
        "strategy": "beam_search",
        "iteration": 1,
        "confidence": 0.95
      }
    },
    "node-2": {
      "id": "node-2",
      "type": "checkpoint",
      "timestamp": "2026-08-24T10:30:02Z",
      "data": {
        "tokens_used": 2048,
        "context_size": 16000
      }
    }
  },
  "edges": [
    {
      "from_id": "node-1",
      "to_id": "node-2",
      "edge_type": "hard_dependency",
      "label": "strategy_decision",
      "metadata": {}
    }
  ],
  "nodes_by_type": {
    "decision": ["node-1"],
    "checkpoint": ["node-2"]
  },
  "iterations": {
    "1": "node-2"
  }
}
```

### Implementation in Backend

**File:** `core/console/corvin_console/routes/task_graph_api.py` (new)

```python
"""Task graph visualization API routes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from typing import Dict, List, Any, Optional

from corvin_console.task_graph import TaskGraphService

router = APIRouter(prefix="/api/tasks", tags=["tasks"])
graph_service = TaskGraphService()

class NodeData(BaseModel):
    """Task graph node."""
    id: str
    type: str  # decision, error, checkpoint, context, metric, subgoal
    timestamp: str
    data: Dict[str, Any]

class EdgeData(BaseModel):
    """Task graph edge."""
    from_id: str
    to_id: str
    edge_type: str  # hard_dependency, soft_dependency, data_flow, temporal
    label: str
    metadata: Dict[str, Any]

class TaskGraph(BaseModel):
    """Complete task execution graph."""
    task_id: str
    created_at: str
    nodes: Dict[str, NodeData]
    edges: List[EdgeData]
    nodes_by_type: Dict[str, List[str]]
    iterations: Dict[int, str]

@router.get("/{task_id}/graph", response_model=TaskGraph)
async def get_task_graph(task_id: str, tenant_id: str) -> TaskGraph:
    """
    Get task execution graph.

    Returns the complete DAG representing task execution flow:
    - All nodes (decisions, checkpoints, errors, context snapshots, metrics)
    - All edges (hard/soft dependencies, data flow, temporal sequence)
    - Metadata for visualization (node types, iterations)

    ADR-0400: Graph-Native Task Execution Model
    """
    try:
        graph = graph_service.get_graph(task_id, tenant_id)
        if not graph:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task graph not found for task {task_id}"
            )
        return graph
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error retrieving task graph"
        )

@router.get("/{task_id}/graph/query", response_model=Dict[str, Any])
async def query_task_graph(
    task_id: str,
    query_type: str,  # reachability, paths, impact
    node_id: str,
    tenant_id: str
) -> Dict[str, Any]:
    """
    Query task graph for specific analysis.

    Supported queries:
    - reachability: Find all nodes reachable from a given node
    - paths: Find all paths between two nodes
    - impact: Find all nodes impacted by a given node

    ADR-0400: Graph-Native Task Execution Model
    """
    try:
        result = graph_service.query_graph(
            task_id, node_id, query_type, tenant_id
        )
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
```

### Wire into Main App

**File:** `core/console/corvin_console/main.py` (or equivalent)

```python
from corvin_console.routes import task_graph_api

app = FastAPI(...)

# ... existing routes ...

router.include_router(task_graph_api.router, tags=["console-task-graph"])
```

## Frontend Integration (Phase 2)

### Step 1: Import Component

```typescript
// src/pages/TaskDetail.tsx
import { TaskGraphViewer } from "@/components/TaskGraphViewer";
import { useTaskGraph } from "@/hooks/useTaskGraph";
```

### Step 2: Create Panel Component

```typescript
// src/panels/TaskGraphPanel.tsx
import React from "react";
import { TaskGraphViewer } from "@/components/TaskGraphViewer";
import { useTaskGraph } from "@/hooks/useTaskGraph";

interface TaskGraphPanelProps {
  taskId: string;
}

export function TaskGraphPanel({ taskId }: TaskGraphPanelProps) {
  const { graph, loading, error, refetch } = useTaskGraph(taskId);

  return (
    <div className="h-full">
      <TaskGraphViewer
        taskId={taskId}
        graph={graph}
        loading={loading}
        error={error}
        onRefresh={refetch}
      />
    </div>
  );
}
```

### Step 3: Register in Panel Registry

**File:** `src/panels/registry.tsx`

```typescript
import { TaskGraphPanel } from "./TaskGraphPanel";

export const panelRegistry = {
  // ... existing panels
  
  taskGraph: {
    component: TaskGraphPanel,
    label: "Execution Graph",
    icon: "GitGraph",
    category: "analysis",
    order: 30,
  },
};
```

### Step 4: Add to Tab Navigation

**File:** `src/pages/TaskDetail.tsx` (or equivalent)

```typescript
import { Tabs, TabContent, TabList, TabTrigger } from "@/components/ui/tabs";
import { TaskGraphPanel } from "@/panels/TaskGraphPanel";

export function TaskDetail({ taskId }: { taskId: string }) {
  return (
    <Tabs defaultValue="overview">
      <TabList>
        <TabTrigger value="overview">Overview</TabTrigger>
        <TabTrigger value="logs">Logs</TabTrigger>
        <TabTrigger value="graph">Execution Graph</TabTrigger>
      </TabList>

      <TabContent value="overview">
        {/* Overview content */}
      </TabContent>

      <TabContent value="logs">
        {/* Logs content */}
      </TabContent>

      <TabContent value="graph" className="h-full">
        <TaskGraphPanel taskId={taskId} />
      </TabContent>
    </Tabs>
  );
}
```

## Component Usage

### Basic Example

```typescript
import { TaskGraphViewer } from "@/components/TaskGraphViewer";
import { useTaskGraph } from "@/hooks/useTaskGraph";

function MyTaskPage({ taskId }: { taskId: string }) {
  const { graph, loading, error, refetch } = useTaskGraph(taskId);

  return (
    <div className="h-screen">
      <TaskGraphViewer
        taskId={taskId}
        graph={graph}
        loading={loading}
        error={error}
        onRefresh={refetch}
      />
    </div>
  );
}
```

### With Error Handling

```typescript
function SafeTaskGraph({ taskId }: { taskId: string }) {
  const { graph, loading, error, refetch } = useTaskGraph(taskId);

  if (error) {
    return (
      <div className="p-4">
        <div className="rounded bg-red-100 p-4">
          <p className="font-semibold text-red-900">Error loading graph</p>
          <p className="text-sm text-red-700">{error}</p>
          <button
            onClick={refetch}
            className="mt-2 rounded bg-red-600 px-3 py-1 text-white"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <TaskGraphViewer
      taskId={taskId}
      graph={graph}
      loading={loading}
      error={error}
      onRefresh={refetch}
    />
  );
}
```

## Styling Integration

The component uses Tailwind CSS classes and standard CSS custom properties. Ensure your app supports:

```css
/* Light mode (default) */
:root {
  --background: white;
  --foreground: #1f2937;
  --muted-foreground: #6b7280;
  --card: #f9fafb;
  --border: #e5e7eb;
}

/* Dark mode */
@media (prefers-color-scheme: dark) {
  :root {
    --background: #1e293b;
    --foreground: #f1f5f9;
    --muted-foreground: #94a3b8;
    --card: #0f172a;
    --border: #334155;
  }
}
```

If using shadcn/ui, these are already defined.

## API Mocking (Development)

For development without a backend, mock the API response:

```typescript
// src/lib/api-mock.ts
export function setupMockApi() {
  if (typeof window === "undefined") return;

  // Mock the fetch for task graph endpoint
  const originalFetch = window.fetch;
  (window.fetch as any) = (url: string, ...args: any[]) => {
    if (url.includes("/v1/console/api/tasks/") && url.includes("/graph")) {
      return Promise.resolve(
        new Response(JSON.stringify(getMockTaskGraph()), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      );
    }
    return originalFetch(url, ...args);
  };
}

function getMockTaskGraph() {
  // Return mock data matching TaskGraph schema
  return {
    task_id: "mock-task-1",
    created_at: new Date().toISOString(),
    nodes: {
      // ... mock nodes
    },
    edges: [
      // ... mock edges
    ],
    nodes_by_type: {},
    iterations: {},
  };
}
```

## Build & Test

### Build

```bash
npm run build
```

### Run Tests

```bash
npm run test:e2e -- tests/e2e/task-graph-viewer.spec.ts
```

### Run Dev Server

```bash
npm run dev
```

Visit: http://localhost:5173/tasks/{taskId}/graph

## Troubleshooting

### Graph Not Loading

1. Check browser console for errors
2. Verify `/v1/console/api/tasks/{taskId}/graph` endpoint returns valid JSON
3. Ensure D3 is loaded (should be via mermaid)
4. Check CORS headers if cross-origin

### Slow Rendering

1. Check node count (should be < 200 for smooth interaction)
2. Profile in Chrome DevTools (Performance tab)
3. Check if simulation is still running (check `simulation.alpha()`)

### No Nodes Visible

1. Check that `graph.nodes` is not empty
2. Verify node positions are computed (check `positionsRef.current`)
3. Check SVG viewBox is correctly scaled

### Styling Issues

1. Ensure Tailwind CSS is built (`npm run build`)
2. Check that dark mode is enabled in browser
3. Verify custom CSS file is loaded (`src/styles/TaskGraphViewer.css`)

## Performance Tuning

### For Large Graphs (200+ nodes)

1. **Reduce simulation ticks:**
   ```typescript
   // taskGraphViz.ts, line ~48
   for (let i = 0; i < 150; i++) { // Reduce from 300
     simulation.tick();
   }
   ```

2. **Increase collision force:**
   ```typescript
   .force("collision", d3.forceCollide().radius(...).iterations(4)) // Increase from 2
   ```

3. **Use canvas rendering (Phase 3):**
   - Switch from SVG to Canvas via OffscreenCanvas
   - ~10x faster for 500+ nodes

### Memory Optimization

1. **Limit history:** Cache only last 5 graphs
2. **Lazy load edges:** Only render visible edges on initial load
3. **Destroy simulation:** Call `simulation.stop()` on unmount

## Browser DevTools

### Performance Profiling

```javascript
// In browser console
performance.mark('graph-start');
// ... render graph
performance.mark('graph-end');
performance.measure('graph', 'graph-start', 'graph-end');
console.log(performance.getEntriesByName('graph'));
```

### D3 Simulation Debugging

```javascript
// In browser console (after component mounted)
window.sim = d3Simulation; // Store reference
window.sim.alpha(); // Check cooling (0 = converged)
window.sim.tick(); // Manually tick
```

## Future Enhancements

### Phase 3 Roadmap

1. **Hierarchical View:** Collapse/expand by iteration
2. **Swimlane Layout:** Group by node type or iteration
3. **Timeline Export:** Export to timeline.json for playback
4. **Anomaly Highlighting:** Color by error rate
5. **Canvas Rendering:** For 500+ node graphs
6. **Real-time Updates:** WebSocket subscription

### Phase 4 Roadmap

1. **Plugin Extension:** Custom node/edge types
2. **Graph Mutations:** Allow plugins to add edges
3. **Advanced Queries:** SQL-like graph query language
4. **Export Formats:** Timeline, Gantt, Sankey

## Support

For issues, questions, or suggestions:

1. Check ADR-0400 (see Corvin-ADR repo for graph-native-task-execution-model)
2. Read [TaskGraphViewer README](./TASK_GRAPH_VIEWER_README.md)
3. File an issue with:
   - Browser & version
   - Node count
   - Error message & stack trace
   - Steps to reproduce

---

**Last Updated:** 2026-08-24  
**Status:** Production Ready

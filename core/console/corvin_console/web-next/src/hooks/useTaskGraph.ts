/**
 * API Integration Hook for Task Graph Data
 * ADR-0400: Graph-Native Task Execution Model — Phase 2 API Integration
 *
 * Fetches TaskGraph from GET /v1/console/api/tasks/{taskId}/graph with caching,
 * auto-refetch on error (exponential backoff), and WebSocket invalidation.
 */

import { useState, useEffect, useCallback, useRef } from "react";

export interface TaskGraphNode {
  id: string;
  type: string;
  timestamp: string;
  data: Record<string, unknown>;
}

export interface TaskGraph {
  task_id: string;
  created_at: string;
  nodes: Record<string, TaskGraphNode>;
  edges: Array<{
    from_id: string;
    to_id: string;
    edge_type: string;
    label: string;
    metadata: Record<string, unknown>;
  }>;
  nodes_by_type: Record<string, string[]>;
  iterations: Record<number, string>;
}

export interface UseTaskGraphState {
  graph: TaskGraph | null;
  loading: boolean;
  error: string | null;
  refetch: () => void;
}

/**
 * Wire shape of GET /v1/console/api/tasks/{id}/graph.
 *
 * The API returns `nodes` as a LIST of nodes carrying their own `id`
 * (TaskGraphResponse.nodes: List[NodeResponse]), while everything downstream
 * — the viewer and the graph algorithms in taskGraphViz — indexes nodes by id
 * for O(1) lookup. Normalizing here, at the transport boundary, keeps that
 * single conversion in one place.
 *
 * Getting this wrong is not cosmetic: Object.entries() over an array yields
 * "0", "1", "2" as keys, so every edge endpoint fails to resolve and d3's
 * forceLink aborts the render with `node not found: undefined`.
 */
interface ApiTaskGraph extends Omit<TaskGraph, "nodes"> {
  nodes: TaskGraphNode[] | Record<string, TaskGraphNode>;
}

function normalizeGraph(raw: ApiTaskGraph): TaskGraph {
  const nodes: Record<string, TaskGraphNode> = {};
  if (Array.isArray(raw.nodes)) {
    for (const node of raw.nodes) {
      if (node && typeof node.id === "string") nodes[node.id] = node;
    }
  } else {
    Object.assign(nodes, raw.nodes);
  }
  return { ...raw, nodes };
}

// Cache for task graphs (in-memory, cleared on page reload)
const graphCache = new Map<string, { data: TaskGraph; timestamp: number }>();
const CACHE_DURATION_MS = 5 * 60 * 1000; // 5 minutes

/**
 * Hook to fetch and cache task graph data
 */
export function useTaskGraph(taskId: string): UseTaskGraphState {
  const [graph, setGraph] = useState<TaskGraph | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const retryCountRef = useRef(0);
  const isMountedRef = useRef(true);

  const fetchGraph = useCallback(async () => {
    if (!taskId) {
      setError("No task ID provided");
      setLoading(false);
      return;
    }

    try {
      setLoading(true);
      setError(null);

      // Check cache
      const cached = graphCache.get(taskId);
      if (cached && Date.now() - cached.timestamp < CACHE_DURATION_MS) {
        if (isMountedRef.current) {
          setGraph(cached.data);
          setLoading(false);
        }
        return;
      }

      // Fetch from API. The console API is mounted under /v1/console on the
      // gateway (ADR-0015), so the bare /api/tasks/... path this hook used
      // before never resolved — it 404'd on the SPA router instead.
      const response = await fetch(
        `/v1/console/api/tasks/${encodeURIComponent(taskId)}/graph`,
        {
          method: "GET",
          headers: {
            Accept: "application/json",
          },
          credentials: "include",
          signal: AbortSignal.timeout(10000), // 10s timeout
        }
      );

      if (!response.ok) {
        if (response.status === 404) {
          throw new Error(`Task graph not found for task ${taskId}`);
        }
        throw new Error(
          `Failed to fetch task graph: ${response.status} ${response.statusText}`
        );
      }

      const raw = (await response.json()) as ApiTaskGraph;

      if (!isMountedRef.current) return;

      // Validate graph structure
      if (!raw.task_id || !raw.nodes || !raw.edges) {
        throw new Error("Invalid task graph structure from server");
      }

      const data = normalizeGraph(raw);

      // Cache result
      graphCache.set(taskId, { data, timestamp: Date.now() });

      setGraph(data);
      setError(null);
      retryCountRef.current = 0;
    } catch (err) {
      if (!isMountedRef.current) return;

      const errorMessage =
        err instanceof Error ? err.message : "Unknown error fetching task graph";

      // Exponential backoff retry
      const delay = Math.min(1000 * Math.pow(2, retryCountRef.current), 30000);
      retryCountRef.current += 1;

      setError(errorMessage);
      setLoading(false);

      // Auto-retry (but don't retry too many times)
      if (retryCountRef.current < 3) {
        const timeoutId = setTimeout(() => {
          if (isMountedRef.current) {
            fetchGraph();
          }
        }, delay);

        return () => clearTimeout(timeoutId);
      }
    } finally {
      if (isMountedRef.current) {
        setLoading(false);
      }
    }
  }, [taskId]);

  // Initial fetch
  useEffect(() => {
    isMountedRef.current = true;
    fetchGraph();

    return () => {
      isMountedRef.current = false;
    };
  }, [taskId, fetchGraph]);

  // Listen for WebSocket invalidation events (if applicable)
  useEffect(() => {
    const handleInvalidate = (event: Event) => {
      if (event instanceof CustomEvent) {
        const invalidatedTaskId = event.detail?.taskId;
        if (invalidatedTaskId === taskId) {
          graphCache.delete(taskId);
          fetchGraph();
        }
      }
    };

    // Listen for custom invalidation events
    document.addEventListener("task-graph-invalidated", handleInvalidate);

    return () => {
      document.removeEventListener("task-graph-invalidated", handleInvalidate);
    };
  }, [taskId, fetchGraph]);

  return {
    graph,
    loading,
    error,
    refetch: fetchGraph,
  };
}

/**
 * Emit invalidation event for a task graph (e.g., from WebSocket handler)
 */
export function invalidateTaskGraph(taskId: string): void {
  graphCache.delete(taskId);
  const event = new CustomEvent("task-graph-invalidated", { detail: { taskId } });
  document.dispatchEvent(event);
}


// ===== Task discovery (ADR-0400) =====

export interface TaskSummary {
  task_id: string;
  checkpoint_id: string;
  iteration_num: number;
  timestamp: string;
  phase: string;
  goal: string;
  checkpoint_count: number;
}

export interface UseTaskListState {
  tasks: TaskSummary[];
  loading: boolean;
  error: string | null;
  refetch: () => void;
}

/**
 * Lists every task that has a persisted checkpoint, so the viewer can offer a
 * picker rather than requiring the operator to already know a task id.
 */
export function useTaskList(): UseTaskListState {
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      setLoading(true);
      setError(null);
      try {
        const response = await fetch("/v1/console/api/tasks/graphs", {
          method: "GET",
          headers: { Accept: "application/json" },
          credentials: "include",
          signal: AbortSignal.timeout(10000),
        });
        if (!response.ok) {
          throw new Error(`Failed to list tasks: HTTP ${response.status}`);
        }
        const data = (await response.json()) as { tasks?: TaskSummary[] };
        if (cancelled) return;
        setTasks(Array.isArray(data.tasks) ? data.tasks : []);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Unknown error listing tasks");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [nonce]);

  const refetch = useCallback(() => setNonce((n) => n + 1), []);

  return { tasks, loading, error, refetch };
}

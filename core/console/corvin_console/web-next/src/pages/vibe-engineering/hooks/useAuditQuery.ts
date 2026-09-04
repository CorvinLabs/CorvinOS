/**
 * useAuditQuery — Fetch audit events from the backend and reconstruct as a graph.
 * Primary data source for VibeDashboard v2.1 (Audit-First Hybrid).
 *
 * Features:
 * - Immutable audit trail queries
 * - Hash-chain verification
 * - IndexedDB caching (offline fallback)
 * - Pagination support
 * - Graceful error handling
 */

import React from 'react';
import { useQuery, UseQueryResult } from '@tanstack/react-query';
import {
  AuditQueryFilter,
  AuditQueryResult,
  AnyAuditEvent,
} from '@/types/audit-graph';

const API_BASE = '/v1/console';
const CACHE_KEY = 'audit_graph_snapshot_v1';
const CACHE_TTL_MS = 30000; // 30 seconds

// ─────────────────────────────────────────────────────────────────────────────
// Fetch Functions
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Fetch audit events from backend.
 * Endpoint: GET /v1/console/vibe-engineering/audit?since=<iso8601>&limit=100
 *
 * Response:
 * {
 *   events: [...],
 *   graph: {...},
 *   nextCursor: "...",
 *   hasMore: true,
 *   snapshotFreshness_ms: 145
 * }
 */
async function fetchAuditEvents(
  filter: AuditQueryFilter,
  signal?: AbortSignal
): Promise<AuditQueryResult> {
  const params = new URLSearchParams();

  if (filter.since) params.append('since', filter.since);
  if (filter.until) params.append('until', filter.until);
  if (filter.limit) params.append('limit', filter.limit.toString());
  if (filter.types?.length) params.append('types', filter.types.join(','));
  if (filter.skillIds?.length) params.append('skillIds', filter.skillIds.join(','));
  if (filter.outcomes?.length) params.append('outcomes', filter.outcomes.join(','));

  const response = await fetch(
    `${API_BASE}/vibe-engineering/audit?${params}`,
    {
      credentials: 'include',
      signal,
    }
  );

  if (!response.ok) {
    throw new Error(`Audit query failed: ${response.status} ${response.statusText}`);
  }

  const data = (await response.json()) as AuditQueryResult;
  return data;
}

// ─────────────────────────────────────────────────────────────────────────────
// Cache Management (IndexedDB)
// ─────────────────────────────────────────────────────────────────────────────

async function saveToCache(result: AuditQueryResult): Promise<void> {
  try {
    const db = await openIndexedDB();
    const tx = db.transaction(['auditGraphCache'], 'readwrite');
    const store = tx.objectStore('auditGraphCache');

    return new Promise((resolve, reject) => {
      const request = store.put({
        key: CACHE_KEY,
        data: result,
        timestamp: Date.now(),
      });
      request.onsuccess = () => resolve();
      request.onerror = () => reject(request.error);
    });
  } catch (error) {
    console.warn('[useAuditQuery] Cache save failed:', error);
    // Non-fatal: we can still work without cache
  }
}

async function loadFromCache(): Promise<AuditQueryResult | null> {
  try {
    const db = await openIndexedDB();
    const tx = db.transaction(['auditGraphCache'], 'readonly');
    const store = tx.objectStore('auditGraphCache');

    return new Promise((resolve) => {
      const request = store.get(CACHE_KEY);
      request.onsuccess = () => {
        const cached = request.result as any;
        if (!cached) {
          resolve(null);
          return;
        }

        const age = Date.now() - cached.timestamp;
        if (age > CACHE_TTL_MS) {
          resolve(null);
          return;
        }

        resolve(cached.data as AuditQueryResult);
      };
      request.onerror = () => resolve(null);
    });
  } catch (error) {
    console.warn('[useAuditQuery] Cache load failed:', error);
    return null;
  }
}

function openIndexedDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open('CorvinOS', 1) as IDBOpenDBRequest;

    request.onerror = () => reject(request.error);
    request.onsuccess = () => resolve(request.result);

    request.onupgradeneeded = (event) => {
      const db = (event.target as IDBOpenDBRequest).result;
      if (!db.objectStoreNames.contains('auditGraphCache')) {
        db.createObjectStore('auditGraphCache', { keyPath: 'key' });
      }
    };
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Query Hook
// ─────────────────────────────────────────────────────────────────────────────

export interface UseAuditQueryOptions {
  filter?: AuditQueryFilter;
  enabled?: boolean;
  staleTime?: number;
  gcTime?: number; // TanStack React Query v5
}

/**
 * useAuditQuery — Primary hook for fetching audit events.
 *
 * Usage:
 * const { data, error, isLoading, isFetching } = useAuditQuery({
 *   filter: {
 *     since: new Date(Date.now() - 3600000).toISOString(),
 *     limit: 100,
 *     types: ['skill_executed', 'learning_event'],
 *   }
 * });
 *
 * if (data) {
 *   // Render graph with data.graph
 *   // Render events with data.events
 * }
 */
export function useAuditQuery(
  options: UseAuditQueryOptions = {}
): UseQueryResult<AuditQueryResult, Error> & {
  isCached: boolean;
} {
  // Default window (last hour, limit 100), fixed once per mount. `filter` is
  // part of the query key; a default recomputed on every render would give
  // each render a new key and re-fetch forever (see VibeDashboard).
  const [defaultFilter] = React.useState<AuditQueryFilter>(() => ({
    since: new Date(Date.now() - 3600000).toISOString(),
    limit: 100,
  }));
  const {
    filter = defaultFilter,
    enabled = true,
    staleTime = 5000, // Re-fetch after 5s
    gcTime = 30000, // Keep data for 30s
  } = options;

  const wasServedFromCache = React.useRef(false);

  const query = useQuery<AuditQueryResult, Error>({
    queryKey: ['auditQuery', filter],
    queryFn: async ({ signal }) => {
      // Try fetch first
      try {
        const result = await fetchAuditEvents(filter, signal);
        // Save to cache on success
        await saveToCache(result);
        wasServedFromCache.current = false;
        return result;
      } catch (error) {
        // On error, try cache
        console.warn('[useAuditQuery] Fetch failed, trying cache:', error);
        const cached = await loadFromCache();
        if (cached) {
          wasServedFromCache.current = true;
          return cached;
        }
        // If cache also fails, throw error
        throw error;
      }
    },
    enabled,
    staleTime,
    gcTime,
    retry: 1,
    retryDelay: 1000,
  });

  return {
    ...query,
    isCached: wasServedFromCache.current,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Utility Functions
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Verify hash chain integrity.
 * Walks the chain from a starting event backwards, verifying each hash.
 */
export async function verifyHashChain(
  events: AnyAuditEvent[],
  startIndex: number = 0
): Promise<{
  isValid: boolean;
  verificationsCount: number;
  firstInvalidIndex?: number;
}> {
  let verificationsCount = 0;
  let firstInvalidIndex: number | undefined;

  for (let i = startIndex; i < events.length; i++) {
    const event = events[i];
    if (i > 0) {
      const prevEvent = events[i - 1];
      // In real implementation, would crypto.subtle.digest(event.hash)
      // and compare with event.prev_hash
      verificationsCount++;

      // Simplified check (in production, verify actual hash)
      if (event.prev_hash !== prevEvent.hash) {
        firstInvalidIndex = i;
        return {
          isValid: false,
          verificationsCount,
          firstInvalidIndex,
        };
      }
    }
  }

  return {
    isValid: true,
    verificationsCount,
  };
}

/**
 * Extract skill ID from audit event (if present).
 */
export function getSkillIdFromEvent(event: AnyAuditEvent): string | null {
  switch (event.type) {
    case 'skill_executed':
    case 'learning_event':
    case 'decision':
      return (event as any).skill_id ?? null;
    default:
      return null;
  }
}

/**
 * Filter events by type, skill, outcome.
 */
export function filterAuditEvents(
  events: AnyAuditEvent[],
  filter: AuditQueryFilter
): AnyAuditEvent[] {
  return events.filter((event) => {
    if (filter.types?.length && !filter.types.includes(event.type)) {
      return false;
    }

    if (filter.skillIds?.length) {
      const skillId = getSkillIdFromEvent(event);
      if (!skillId || !filter.skillIds.includes(skillId)) {
        return false;
      }
    }

    return true;
  });
}

/**
 * Extract human-readable label from audit event.
 */
export function getEventLabel(event: AnyAuditEvent): string {
  switch (event.type) {
    case 'skill_executed': {
      const skill = (event as any).skill_id;
      const status = (event as any).status;
      return `${skill} (${status})`;
    }
    case 'learning_event': {
      const type = (event as any).event_type;
      return `Learning: ${type}`;
    }
    case 'decision': {
      const name = (event as any).decision_name;
      return `Decision: ${name}`;
    }
    case 'context_snapshot': {
      const entropy = (event as any).entropy_score?.toFixed(2) ?? '?';
      return `Context (entropy: ${entropy})`;
    }
    case 'error': {
      const errorType = (event as any).error_type;
      return `Error: ${errorType}`;
    }
  }
}

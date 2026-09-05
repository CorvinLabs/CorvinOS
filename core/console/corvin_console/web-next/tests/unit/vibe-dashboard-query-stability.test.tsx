/**
 * useAuditQuery must issue ONE audit request per mount, not one per render.
 *
 * 2026-09-04: /app/vibe-engineering showed a spinner forever and "Live • 0
 * events" while the backend logged ~33 GET /vibe-engineering/audit per second
 * (11,829 in six minutes). `since` was computed inline in the query filter,
 * the filter is part of the React Query key, so every render minted a new key
 * → new query → new fetch → re-render → … The data never "arrived" because
 * every arrival belonged to a key the component had already abandoned.
 *
 * The test drove this through VibeDashboard until 2026-09-05, when that panel
 * became the Learning Dashboard and stopped querying the audit chain. The
 * defect lives in the HOOK's default filter, so the test now drives the hook
 * directly — through real React Query over a mocked transport — and keeps
 * guarding it for the next consumer that mounts it.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useAuditQuery } from '@/pages/vibe-engineering/hooks/useAuditQuery';

/** Mounts the hook with NO filter, i.e. on its default — the shape that broke. */
function AuditConsumer() {
  const q = useAuditQuery();
  return (
    <div>
      <span data-testid="status">
        Live • {q.data?.graph.metadata.nodeCount ?? 0} events
      </span>
      <span data-testid="nodes">nodes:{q.data?.graph.nodes.length ?? 0}</span>
    </div>
  );
}

function auditPayload(n: number) {
  const events = Array.from({ length: n }, (_, i) => ({
    id: `e${i}`, type: 'decision', timestamp: `2026-09-04T00:00:0${i}Z`,
    hash: `h${i}`, prev_hash: i ? `h${i - 1}` : '', lom_hash: 'l', tenant_id: '_default',
    event_type: 'decision', details: {}, severity: 'INFO',
  }));
  return {
    events,
    graph: {
      nodes: events.map((e) => ({ id: e.id, type: e.type, timestamp: e.timestamp, hash: e.hash, lom_hash: 'l', label: e.id, data: e })),
      edges: events.slice(1).map((e, i) => ({ id: `${i}_${i + 1}`, source: `e${i}`, target: e.id, type: 'hash_chain', hash: e.prev_hash })),
      metadata: { chainHeight: n, nodeCount: n, edgeCount: Math.max(0, n - 1), timespan: { start: '', end: '' }, snapshotFreshness_ms: 0 },
    },
    nextCursor: null, hasMore: false, snapshotFreshness_ms: 0,
  };
}

describe('useAuditQuery request stability', () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockImplementation(async () => ({ ok: true, status: 200, statusText: 'OK', json: async () => auditPayload(3) }));
    vi.stubGlobal('fetch', fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it('fetches the audit chain once and exposes the graph from it', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <AuditConsumer />
      </QueryClientProvider>,
    );
    await screen.findByText('Live • 3 events', undefined, { timeout: 3000 });
    expect(screen.getByTestId('nodes')).toHaveTextContent('nodes:3');
    // Let any further render → refetch cycle surface before counting.
    await new Promise((r) => setTimeout(r, 400));
    const auditCalls = fetchMock.mock.calls.filter(([url]) => String(url).includes('/vibe-engineering/audit'));
    expect(auditCalls).toHaveLength(1);
    await waitFor(() => expect(qc.getQueryCache().getAll()).toHaveLength(1));
  });
});

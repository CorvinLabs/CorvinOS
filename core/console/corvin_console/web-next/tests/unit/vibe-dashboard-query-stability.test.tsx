/**
 * VibeDashboard must issue ONE audit query per mount, not one per render.
 *
 * 2026-09-04: /app/vibe-engineering showed a spinner forever and "Live • 0
 * events" while the backend logged ~33 GET /vibe-engineering/audit per second
 * (11,829 in six minutes). `since` was computed inline in the query filter,
 * the filter is part of the React Query key, so every render minted a new key
 * → new query → new fetch → re-render → … The data never "arrived" because
 * every arrival belonged to a key the component had already abandoned.
 *
 * This test drives the real hook + real React Query with a mocked transport
 * and counts the requests. Graph/inspector components are mocked: cytoscape
 * needs a canvas jsdom does not have, and the layout is covered headlessly by
 * audit-chain-graph-layout.test.ts.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { VibeDashboard } from '@/pages/vibe-engineering/VibeDashboard';

vi.mock('@/pages/vibe-engineering/components/AuditChainGraph', () => ({
  AuditChainGraph: ({ graph }: { graph: { nodes: unknown[] } }) => (
    <div data-testid="audit-graph">nodes:{graph.nodes.length}</div>
  ),
}));
vi.mock('@/pages/vibe-engineering/components/GraphInspector', () => ({
  GraphInspector: () => <div data-testid="graph-inspector" />,
}));

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

describe('VibeDashboard audit query stability', () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockImplementation(async () => ({ ok: true, status: 200, statusText: 'OK', json: async () => auditPayload(3) }));
    vi.stubGlobal('fetch', fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it('fetches the audit chain once and renders the graph from it', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/app/vibe-engineering?tab=graph']}>
          <VibeDashboard />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await screen.findByText('Live • 3 events', undefined, { timeout: 3000 });
    expect(screen.getByTestId('audit-graph')).toHaveTextContent('nodes:3');
    // Let any further render → refetch cycle surface before counting.
    await new Promise((r) => setTimeout(r, 400));
    const auditCalls = fetchMock.mock.calls.filter(([url]) => String(url).includes('/vibe-engineering/audit'));
    expect(auditCalls).toHaveLength(1);
    await waitFor(() => expect(qc.getQueryCache().getAll()).toHaveLength(1));
  });
});

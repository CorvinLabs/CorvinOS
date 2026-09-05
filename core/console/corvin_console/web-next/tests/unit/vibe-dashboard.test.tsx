/**
 * Unit tests for VibeDashboard — the ONE Vibe Engineering panel.
 *
 * The group used to be five sidebar entries (Dashboard · Brain Monitor ·
 * Context Intelligence · Learning Hub · Session Explorer). On 2026-09-05 the
 * four secondary panels were retired: everything lives in this panel's tabs,
 * so the sidebar no longer duplicates them.
 *
 * Covers: tab list, URL query-param sync, and that the Learning tab (ADR-0321)
 * stays registered — it was added to the dashboard and must not be dropped
 * again by a later edit.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { VibeDashboard } from '@/pages/vibe-engineering/VibeDashboard';

// The graph canvas (Cytoscape) and the learning charts (recharts) both need a
// real layout engine; stub them so the test asserts the dashboard's own shell.
vi.mock('@/pages/vibe-engineering/components/AuditChainGraph', () => ({
  AuditChainGraph: () => <div data-testid="audit-chain-graph">Graph</div>,
}));

vi.mock('@/pages/vibe-engineering/components/GraphInspector', () => ({
  GraphInspector: () => <div data-testid="graph-inspector">Inspector</div>,
}));

vi.mock('@/pages/vibe-engineering/components/LearningDashboard', () => ({
  default: () => <div data-testid="learning-dashboard">Learning Dashboard</div>,
}));

vi.mock('@/pages/vibe-engineering/hooks/useAuditQuery', () => ({
  useAuditQuery: () => ({
    data: {
      graph: { nodes: [], edges: [], metadata: { nodeCount: 0, edgeCount: 0 } },
      events: [],
      snapshotFreshness_ms: 0,
    },
    isLoading: false,
    isCached: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

describe('VibeDashboard Component', () => {
  const renderComponent = () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    return render(
      <QueryClientProvider client={client}>
        <BrowserRouter>
          <VibeDashboard />
        </BrowserRouter>
      </QueryClientProvider>,
    );
  };

  beforeEach(() => {
    window.history.pushState({}, '', '/app/vibe-engineering');
  });

  it('should render the main heading', () => {
    renderComponent();
    expect(screen.getByText('Vibe Engineering')).toBeInTheDocument();
  });

  it('should render all four tabs', () => {
    renderComponent();
    for (const label of [/Graph View/i, /Inspector/i, /Timeline/i, /Learning/i]) {
      expect(screen.getByRole('tab', { name: label })).toBeInTheDocument();
    }
  });

  it('should render the tab list as a 4-column grid', () => {
    const { container } = renderComponent();
    const tabsList = container.querySelector('[role="tablist"]');
    expect(tabsList).toBeInTheDocument();
    expect(tabsList).toHaveClass('grid');
    expect(tabsList).toHaveClass('grid-cols-4');
  });

  // Radix activates a trigger on mousedown, not on a synthetic click.
  it('should sync the selected tab into the URL and render it', async () => {
    renderComponent();
    fireEvent.mouseDown(screen.getByRole('tab', { name: /Learning/i }));
    await waitFor(() => {
      expect(window.location.search).toContain('tab=learning');
      expect(screen.getByTestId('learning-dashboard')).toBeInTheDocument();
    });
  });

  it('should open the tab named by ?tab= on mount', async () => {
    window.history.pushState({}, '', '/app/vibe-engineering?tab=learning');
    renderComponent();
    await waitFor(() => {
      expect(screen.getByTestId('learning-dashboard')).toBeInTheDocument();
    });
  });
});

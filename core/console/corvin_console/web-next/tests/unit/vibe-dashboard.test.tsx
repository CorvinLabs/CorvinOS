/**
 * Unit tests for the Vibe Engineering panel — the Learning Dashboard.
 *
 * The panel's shape has changed twice, so these tests pin the current one:
 *   - it was five sidebar entries (Dashboard · Brain Monitor · Context
 *     Intelligence · Learning Hub · Session Explorer) until 2026-09-05,
 *   - then one tabbed panel (Graph View · Inspector · Timeline · Learning),
 *   - and now the Learning view alone, with no tab bar at all.
 *
 * The route id stays `vibe-engineering`; only the visible name is "Learning
 * Dashboard".
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { VibeDashboard } from '@/pages/vibe-engineering/VibeDashboard';

// recharts needs a real layout box; the panel's own wiring is what's under test.
vi.mock('@/pages/vibe-engineering/components/LearningDashboard', () => ({
  default: () => (
    <div data-testid="learning-dashboard">
      <h1>Learning Dashboard</h1>
    </div>
  ),
}));

describe('Vibe Engineering panel', () => {
  const renderComponent = () =>
    render(
      <BrowserRouter>
        <VibeDashboard />
      </BrowserRouter>,
    );

  it('renders the learning dashboard', async () => {
    renderComponent();
    expect(await screen.findByTestId('learning-dashboard')).toBeInTheDocument();
  });

  it('is named Learning Dashboard', async () => {
    renderComponent();
    expect(
      await screen.findByRole('heading', { name: /learning dashboard/i }),
    ).toBeInTheDocument();
  });

  it('has no tab bar — the audit tabs were removed', () => {
    const { container } = renderComponent();
    expect(container.querySelector('[role="tablist"]')).toBeNull();
    expect(screen.queryAllByRole('tab')).toHaveLength(0);
  });

  it('renders no retired view', () => {
    renderComponent();
    for (const gone of [
      /graph view/i, /inspector/i, /timeline/i,
      /brain monitor/i, /context intelligence/i, /learning hub/i, /session explorer/i,
    ]) {
      expect(screen.queryByText(gone)).toBeNull();
    }
  });
});

/**
 * Unit tests for the Vibe Engineering panel (route id `vibe-engineering`,
 * sidebar label "Learnings").
 *
 * The panel's shape has changed several times, so these tests pin the
 * current one:
 *   - five sidebar entries (Dashboard · Brain Monitor · Context Intelligence ·
 *     Learning Hub · Session Explorer) until 2026-09-05,
 *   - then one tabbed panel (Graph View · Inspector · Timeline · Learning),
 *   - then the Learning view alone,
 *   - and since 2026-09-15 (ADR-0728 Phase 2 live-data wiring) four tabs:
 *     Maturity Metrics · Audit Events · System Metrics · Models, with
 *     Maturity Metrics open by default.
 *
 * 2026-09-17: a sibling FILE pages/vibe-engineering.tsx shadowed this
 * directory and the route crashed on nine 404s (95ecc2b6). This test renders
 * the directory's component; tests/unit/page-dir-shadow.test.ts guards the
 * resolution.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { VibeDashboard } from '@/pages/vibe-engineering/VibeDashboard';

// The tabs fetch live endpoints and recharts needs a layout box; the panel's
// own wiring (tab bar → which tab body mounts) is what's under test.
vi.mock('@/pages/vibe-engineering/components/MaturityDashboard', () => ({
  MaturityDashboard: () => <div data-testid="tab-maturity">Maturity body</div>,
}));
vi.mock('@/pages/vibe-engineering/tabs/MonitoringTab', () => ({
  MonitoringTab: () => <div data-testid="tab-metrics">Metrics body</div>,
}));
vi.mock('@/pages/vibe-engineering/tabs/ModelsTab', () => ({
  ModelsTab: () => <div data-testid="tab-models">Models body</div>,
}));

// "Audit Events" left this panel on 2026-09-20: the same
// /v1/console/v1/licensing/audit-events store backed both this tab and a
// standalone Licensing Audit panel, and it now lives once, in
// /app/compliance's "Learning events" section.
// "Learning Loops" joined on 2026-09-21 (ADR-0908). It is NOT the retired
// "Learning Hub" view of the old tabbed hub — it mounts LearningLoopsView, the
// same component /app/learning-loops renders, so the two surfaces cannot show
// different numbers for the same loops.
const TABS = ['Maturity Metrics', 'Learning Loops', 'System Metrics', 'Models'];

describe('Vibe Engineering panel', () => {
  const renderComponent = () =>
    render(
      <BrowserRouter>
        <VibeDashboard />
      </BrowserRouter>,
    );

  it('mounts the dashboard container', () => {
    renderComponent();
    expect(screen.getByTestId('vibe-dashboard-panel')).toBeInTheDocument();
  });

  it('offers exactly the Phase 2 tabs', () => {
    renderComponent();
    for (const label of TABS) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument();
    }
    // The tab is gone, not merely unlabelled — a renamed tab would still
    // mount the duplicate view this consolidation removed.
    expect(screen.queryByRole('button', { name: /audit/i })).toBeNull();
  });

  it('opens on Maturity Metrics', async () => {
    renderComponent();
    expect(await screen.findByTestId('tab-maturity')).toBeInTheDocument();
    expect(screen.queryByTestId('tab-metrics')).toBeNull();
  });

  it('switches tab bodies on click', async () => {
    renderComponent();
    fireEvent.click(screen.getByRole('button', { name: 'System Metrics' }));
    expect(await screen.findByTestId('tab-metrics')).toBeInTheDocument();
    expect(screen.queryByTestId('tab-maturity')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Models' }));
    expect(await screen.findByTestId('tab-models')).toBeInTheDocument();
  });

  it('renders no retired view', () => {
    renderComponent();
    // "Learning Loops" was on this list until 2026-09-21, when the tab was
    // added for real. The retired views below are the old hub's, and stay out.
    for (const gone of [
      /graph view/i, /inspector/i, /timeline/i,
      /brain monitor/i, /context intelligence/i, /learning hub/i, /session explorer/i,
    ]) {
      expect(screen.queryByText(gone)).toBeNull();
    }
  });

  it('mounts the shared Learning Loops view, not a second implementation', async () => {
    renderComponent();
    fireEvent.click(screen.getByRole('button', { name: 'Learning Loops' }));
    // The shared view's own copy — a private re-implementation would not carry it.
    expect(
      await screen.findByText(/Also available as its own panel at/i),
    ).toBeInTheDocument();
  });
});

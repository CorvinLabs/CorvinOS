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
vi.mock('@/pages/vibe-engineering/tabs/LicensingAuditTab', () => ({
  LicensingAuditTab: () => <div data-testid="tab-audit">Audit body</div>,
}));
vi.mock('@/pages/vibe-engineering/tabs/MonitoringTab', () => ({
  MonitoringTab: () => <div data-testid="tab-metrics">Metrics body</div>,
}));
vi.mock('@/pages/vibe-engineering/tabs/ModelsTab', () => ({
  ModelsTab: () => <div data-testid="tab-models">Models body</div>,
}));

const TABS = ['Maturity Metrics', 'Audit Events', 'System Metrics', 'Models'];

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

  it('offers exactly the four Phase 2 tabs', () => {
    renderComponent();
    for (const label of TABS) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument();
    }
  });

  it('opens on Maturity Metrics', async () => {
    renderComponent();
    expect(await screen.findByTestId('tab-maturity')).toBeInTheDocument();
    expect(screen.queryByTestId('tab-audit')).toBeNull();
  });

  it('switches tab bodies on click', async () => {
    renderComponent();
    fireEvent.click(screen.getByRole('button', { name: 'Audit Events' }));
    expect(await screen.findByTestId('tab-audit')).toBeInTheDocument();
    expect(screen.queryByTestId('tab-maturity')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Models' }));
    expect(await screen.findByTestId('tab-models')).toBeInTheDocument();
  });

  it('renders no retired view', () => {
    renderComponent();
    for (const gone of [
      /graph view/i, /inspector/i, /timeline/i,
      /brain monitor/i, /context intelligence/i, /learning hub/i, /session explorer/i,
      /learning loops/i,
    ]) {
      expect(screen.queryByText(gone)).toBeNull();
    }
  });
});

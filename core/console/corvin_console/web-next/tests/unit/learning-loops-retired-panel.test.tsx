/**
 * Retired routes must land where their content went — twice over.
 *
 * The standalone panel was retired into the Learnings dashboard's "Learning
 * Loops" tab on 2026-09-21 (ADR-0908) — both mounted the same
 * LearningLoopsView, so the panel was a second door to one room. Retiring a
 * route has two halves that fail independently and silently:
 *
 *  1. the old path must still resolve, or every existing link and bookmark 404s;
 *  2. `?tab=loops` must actually select that tab. A redirect to a query string
 *     nothing consumes opens the DEFAULT tab instead, which looks exactly like
 *     a working redirect until someone notices they landed on Maturity Metrics.
 *
 * Both are asserted here, with a positive control: the second test would also
 * pass if the dashboard rendered every tab at once, so it checks that a
 * DIFFERENT tab's body is absent too.
 */
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, Navigate, useSearchParams } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { VibeDashboard } from '@/pages/vibe-engineering/VibeDashboard';

vi.mock('@/components/learning-loops-view', () => ({
  LearningLoopsView: () => <div data-testid="loops-view" />,
}));
vi.mock('@/pages/vibe-engineering/components/MaturityDashboard', () => ({
  MaturityDashboard: () => <div data-testid="maturity-view" />,
}));
vi.mock('@/pages/vibe-engineering/tabs/MonitoringTab', () => ({
  MonitoringTab: () => <div data-testid="metrics-view" />,
}));

/** Renders whatever the current location is, so a redirect is observable. */
function LocationProbe() {
  const [params] = useSearchParams();
  return <div data-testid="probe-tab">{params.get('tab') ?? ''}</div>;
}

describe('retired Learnings surfaces', () => {
  it('redirects to the Learnings dashboard tab rather than 404ing', () => {
    render(
      <MemoryRouter initialEntries={['/app/learning-loops']}>
        <Routes>
          <Route
            path="/app/learning-loops"
            element={<Navigate to="/app/vibe-engineering?tab=loops" replace />}
          />
          <Route path="/app/vibe-engineering" element={<LocationProbe />} />
          <Route path="*" element={<div data-testid="not-found" />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.queryByTestId('not-found')).toBeNull();
    expect(screen.getByTestId('probe-tab').textContent).toBe('loops');
  });

  it('opens the Learning Loops tab for ?tab=loops, not the default tab', () => {
    render(
      <MemoryRouter initialEntries={['/app/vibe-engineering?tab=loops']}>
        <VibeDashboard />
      </MemoryRouter>,
    );
    expect(screen.getByTestId('loops-view')).toBeInTheDocument();
    // Positive control: a dashboard rendering every tab at once would satisfy
    // the assertion above while the parameter did nothing.
    expect(screen.queryByTestId('maturity-view')).toBeNull();
  });

  it('falls back to the default tab for an unknown ?tab=', () => {
    render(
      <MemoryRouter initialEntries={['/app/vibe-engineering?tab=nonsense']}>
        <VibeDashboard />
      </MemoryRouter>,
    );
    expect(screen.getByTestId('maturity-view')).toBeInTheDocument();
    expect(screen.queryByTestId('loops-view')).toBeNull();
  });

  // The retired "Models" tab (2026-09-21) read the same
  // /v1/console/v1/models/available the Models panel's Catalog tab reads. Its
  // links must reach that panel, not fall through the unknown-tab branch above
  // and land silently on Maturity Metrics — which is what would happen if
  // 'models' were simply deleted from TAB_IDS and nothing else.
  it('sends ?tab=models to the Models panel catalogue, not the default tab', () => {
    render(
      <MemoryRouter initialEntries={['/app/vibe-engineering?tab=models']}>
        <Routes>
          <Route path="/app/vibe-engineering" element={<VibeDashboard />} />
          <Route path="/app/models" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByTestId('probe-tab').textContent).toBe('catalog');
    expect(screen.queryByTestId('maturity-view')).toBeNull();
  });
});

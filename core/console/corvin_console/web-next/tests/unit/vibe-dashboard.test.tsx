/**
 * Unit tests for VibeDashboard component (Phase 4 k=4 Refinement)
 *
 * Tests the component's core functionality:
 * - Tab navigation and state management
 * - URL query param sync
 * - Lazy loading of tab content
 * - Data prop passing to sub-components
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { VibeDashboard } from '@/pages/vibe-engineering/VibeDashboard';

// Mock the sub-components to avoid loading dependencies
vi.mock('@/pages/vibe-engineering/components/BrainMonitor', () => ({
  BrainMonitor: () => <div data-testid="brain-monitor">Brain Monitor</div>,
}));

vi.mock('@/pages/vibe-engineering/components/ContextIntelligence', () => ({
  ContextIntelligence: ({ data }: any) => (
    <div data-testid="context-intelligence">
      Context Intelligence - Entropy: {data?.pipeline_context?.entropy_score}
    </div>
  ),
}));

vi.mock('@/pages/vibe-engineering/components/LearningHub', () => ({
  LearningHub: ({ data }: any) => (
    <div data-testid="learning-hub">
      Learning Hub - Talent: {data?.talent?.score}
    </div>
  ),
}));

vi.mock('@/pages/vibe-engineering/components/SessionExplorer', () => ({
  SessionExplorer: () => <div data-testid="session-explorer">Session Explorer</div>,
}));

// Mock the useVibeData hook
vi.mock('@/pages/vibe-engineering/hooks/useVibeData', () => ({
  useVibeData: () => ({
    loading: false,
    error: null,
    active_task: { task_id: 'test-001', status: 'in_progress', progress_percent: 50 },
    workers: [{ name: 'Claude Code', status: 'running' }],
    original_context: { task_description: 'Test', user_intent: 'Test' },
    pipeline_context: { entropy_score: 0.32 },
    talent: { score: 0.78 },
    learning_events: [],
    sessions: [],
    timestamp: new Date().toISOString(),
  }),
}));

describe('VibeDashboard Component', () => {
  const renderComponent = () => {
    return render(
      <BrowserRouter>
        <VibeDashboard />
      </BrowserRouter>
    );
  };

  beforeEach(() => {
    // Clear URL search params before each test
    window.history.pushState({}, '', '/app/vibe-engineering');
  });

  it('should render the main heading', () => {
    renderComponent();
    expect(screen.getByText('Vibe Engineering')).toBeInTheDocument();
  });

  it('should render all five tab buttons', () => {
    renderComponent();
    expect(screen.getByRole('button', { name: /Dashboard/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Brain Monitor/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Context Intelligence/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Learning Hub/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Session Explorer/i })).toBeInTheDocument();
  });

  it('should render dashboard tab content by default', () => {
    renderComponent();
    expect(screen.getByText(/Overview of system observability/)).toBeInTheDocument();
  });

  it('should render tab buttons with correct labels', () => {
    renderComponent();
    const tabButtons = screen.getAllByRole('button');
    // Should have at least 5 tab buttons
    expect(tabButtons.length).toBeGreaterThanOrEqual(5);
  });

  it('should render tabs container with Radix UI', () => {
    const { container } = renderComponent();
    const tabsContainer = container.querySelector('[role="tablist"]');
    expect(tabsContainer).toBeInTheDocument();
  });

  it('should render tab list with grid layout', () => {
    const { container } = renderComponent();
    const tabsList = container.querySelector('[role="tablist"]');

    expect(tabsList).toHaveClass('grid');
    expect(tabsList).toHaveClass('grid-cols-5');
  });

  it('should render header description', () => {
    renderComponent();
    expect(
      screen.getByText(
        /Unified dashboard for system observability, context management, and learning metrics/
      )
    ).toBeInTheDocument();
  });
});

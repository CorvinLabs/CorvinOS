import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import FeatureStatusDashboard from '../FeatureStatusDashboard';

global.fetch = vi.fn();

const mockFeaturesResponse = {
  flags_enabled: [
    {
      flag_id: 'auto_load_github_repo',
      release_tier: 'beta',
      error_rate_24h: 0.02,
      invocation_count_24h: 150,
      days_since_last_error: null,
      status: 'active',
    },
    {
      flag_id: 'vibe_engineering',
      release_tier: 'beta',
      error_rate_24h: 0.005,
      invocation_count_24h: 200,
      days_since_last_error: 5,
      status: 'active',
    },
    {
      flag_id: 'plugin_builder_enabled',
      release_tier: 'stable',
      error_rate_24h: 0.0,
      invocation_count_24h: 500,
      days_since_last_error: 30,
      status: 'active',
    },
  ],
  timestamp: new Date().toISOString(),
};

describe('FeatureStatusDashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
  });

  it('renders loading state initially', () => {
    (global.fetch as any).mockImplementation(
      () => new Promise(() => {}) // Never resolves
    );

    render(<FeatureStatusDashboard />);

    expect(screen.getByText(/Loading features/)).toBeInTheDocument();
  });

  it('fetches and displays features', async () => {
    (global.fetch as any).mockResolvedValueOnce({
      ok: true,
      json: async () => mockFeaturesResponse,
    });

    render(<FeatureStatusDashboard />);

    await waitFor(() => {
      expect(screen.getByText('auto_load_github_repo')).toBeInTheDocument();
      expect(screen.getByText('vibe_engineering')).toBeInTheDocument();
      expect(screen.getByText('plugin_builder_enabled')).toBeInTheDocument();
    });
  });

  it('displays error rate and invocation count', async () => {
    (global.fetch as any).mockResolvedValueOnce({
      ok: true,
      json: async () => mockFeaturesResponse,
    });

    render(<FeatureStatusDashboard />);

    await waitFor(() => {
      expect(screen.getByText(/2.00%/)).toBeInTheDocument(); // error_rate
      expect(screen.getByText(/Invocations: 150/)).toBeInTheDocument();
    });
  });

  it('filters by tier', async () => {
    (global.fetch as any).mockResolvedValueOnce({
      ok: true,
      json: async () => mockFeaturesResponse,
    });

    render(<FeatureStatusDashboard />);

    const tierSelect = await screen.findByDisplayValue('All Tiers');
    fireEvent.change(tierSelect, { target: { value: 'stable' } });

    await waitFor(() => {
      expect(screen.getByText('plugin_builder_enabled')).toBeInTheDocument();
      // Beta features should be hidden
      expect(screen.queryByText('auto_load_github_repo')).not.toBeInTheDocument();
    });
  });

  it('filters by search text', async () => {
    (global.fetch as any).mockResolvedValueOnce({
      ok: true,
      json: async () => mockFeaturesResponse,
    });

    render(<FeatureStatusDashboard />);

    const searchInput = await screen.findByPlaceholderText(/Search flag/);
    fireEvent.change(searchInput, { target: { value: 'vibe' } });

    await waitFor(() => {
      expect(screen.getByText('vibe_engineering')).toBeInTheDocument();
      // Other features should be hidden
      expect(screen.queryByText('auto_load_github_repo')).not.toBeInTheDocument();
    });
  });

  it('shows tier summary', async () => {
    (global.fetch as any).mockResolvedValueOnce({
      ok: true,
      json: async () => mockFeaturesResponse,
    });

    render(<FeatureStatusDashboard />);

    await waitFor(() => {
      expect(screen.getByText(/3 total features:/)).toBeInTheDocument();
      expect(screen.getByText(/0 alpha/)).toBeInTheDocument();
      expect(screen.getByText(/2 beta/)).toBeInTheDocument();
      expect(screen.getByText(/1 stable/)).toBeInTheDocument();
      expect(screen.getByText(/0 production/)).toBeInTheDocument();
    });
  });

  it('shows error on fetch failure', async () => {
    (global.fetch as any).mockRejectedValueOnce(new Error('API error'));

    render(<FeatureStatusDashboard />);

    await waitFor(() => {
      expect(screen.getByText(/API error/)).toBeInTheDocument();
    });
  });

  it('auto-refreshes every 5 minutes', async () => {
    (global.fetch as any).mockResolvedValue({
      ok: true,
      json: async () => mockFeaturesResponse,
    });

    render(<FeatureStatusDashboard />);

    // Initial load
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledTimes(1);
    });

    // Advance 5 minutes
    vi.advanceTimersByTime(5 * 60 * 1000);

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledTimes(2);
    });
  });
});

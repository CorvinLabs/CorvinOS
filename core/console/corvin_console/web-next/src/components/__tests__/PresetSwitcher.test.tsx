import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import PresetSwitcher from '../PresetSwitcher';

// Mock fetch
global.fetch = vi.fn();

describe('PresetSwitcher', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders preset options', () => {
    (global.fetch as any).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ preset: 'standard' }),
    });

    render(<PresetSwitcher />);

    expect(screen.getByText('Minimal')).toBeInTheDocument();
    expect(screen.getByText('Standard')).toBeInTheDocument();
    expect(screen.getByText('Advanced')).toBeInTheDocument();
  });

  it('loads current preset on mount', async () => {
    (global.fetch as any).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ preset: 'advanced' }),
    });

    render(<PresetSwitcher />);

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith('/v1/console/api/feature-status/preset');
    });
  });

  it('shows error when fetch fails', async () => {
    (global.fetch as any).mockRejectedValueOnce(new Error('Network error'));

    render(<PresetSwitcher />);

    await waitFor(() => {
      expect(screen.getByText(/Network error/)).toBeInTheDocument();
    });
  });

  it('handles preset change', async () => {
    (global.fetch as any)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ preset: 'standard' }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ preset: 'minimal' }),
      });

    render(<PresetSwitcher />);

    const minimalButton = await screen.findByRole('button', { name: /Minimal/ });
    fireEvent.click(minimalButton);

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith('/v1/console/api/feature-status/preset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ preset: 'minimal' }),
      });
    });
  });

  it('shows restart message after preset change', async () => {
    (global.fetch as any)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ preset: 'standard' }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ preset: 'advanced' }),
      });

    vi.spyOn(window, 'alert').mockImplementation(() => {});

    render(<PresetSwitcher />);

    const advancedButton = await screen.findByRole('button', { name: /Advanced/ });
    fireEvent.click(advancedButton);

    await waitFor(() => {
      expect(window.alert).toHaveBeenCalledWith(
        expect.stringContaining('restart')
      );
    });
  });
});

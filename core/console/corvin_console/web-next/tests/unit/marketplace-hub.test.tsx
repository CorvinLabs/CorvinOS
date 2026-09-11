import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import MarketplaceHub from '@/panels/marketplace-hub';
import * as api from '@/lib/api/marketplace-hub';

// Mock the API
jest.mock('@/lib/api/marketplace-hub');

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } },
});

const mockStatus = {
  plugins: { installed: 12, available: 48, status: 'active' as const },
  skills: { active: 3, available: 18, status: 'active' as const },
  tools: { registered: 5, available: 12, status: 'active' as const },
  connectors: { configured: 2, available: 8, status: 'pending' as const },
  layers: { builtin: 6, immutable: true, status: 'active' as const },
};

const renderWithQuery = (component: React.ReactElement) => {
  return render(<QueryClientProvider client={queryClient}>{component}</QueryClientProvider>);
};

describe('MarketplaceHub', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    // Mock the fetch for status endpoint
    global.fetch = jest.fn(() =>
      Promise.resolve({
        ok: true,
        json: async () => mockStatus,
      })
    ) as jest.Mock;
  });

  it('renders marketplace hub with title', async () => {
    renderWithQuery(<MarketplaceHub />);

    await waitFor(() => {
      expect(screen.getByText('Marketplace')).toBeInTheDocument();
    });
  });

  it('renders all 5 type cards', async () => {
    renderWithQuery(<MarketplaceHub />);

    await waitFor(() => {
      expect(screen.getByText('Plugins')).toBeInTheDocument();
      expect(screen.getByText('Skills')).toBeInTheDocument();
      expect(screen.getByText('Tools')).toBeInTheDocument();
      expect(screen.getByText('Connectors')).toBeInTheDocument();
      expect(screen.getByText('Layers')).toBeInTheDocument();
    });
  });

  it('displays correct counts for each type', async () => {
    renderWithQuery(<MarketplaceHub />);

    await waitFor(() => {
      expect(screen.getByText('12/48')).toBeInTheDocument(); // plugins
      expect(screen.getByText('3/18')).toBeInTheDocument(); // skills
      expect(screen.getByText('5/12')).toBeInTheDocument(); // tools
      expect(screen.getByText('2/8')).toBeInTheDocument(); // connectors
    });
  });

  it('displays status badges correctly', async () => {
    renderWithQuery(<MarketplaceHub />);

    await waitFor(() => {
      const badges = screen.getAllByText('active');
      expect(badges.length).toBeGreaterThan(0);
      expect(screen.getByText('pending')).toBeInTheDocument();
    });
  });

  it('filters cards by search query', async () => {
    renderWithQuery(<MarketplaceHub />);

    await waitFor(() => {
      const searchInput = screen.getByPlaceholderText(/Search by type/i);
      fireEvent.change(searchInput, { target: { value: 'plugins' } });

      expect(screen.getByText('Plugins')).toBeInTheDocument();
      expect(screen.queryByText('Skills')).not.toBeInTheDocument();
    });
  });

  it('shows all cards when search is cleared', async () => {
    renderWithQuery(<MarketplaceHub />);

    await waitFor(() => {
      const searchInput = screen.getByPlaceholderText(/Search by type/i);
      fireEvent.change(searchInput, { target: { value: 'tools' } });
      fireEvent.change(searchInput, { target: { value: '' } });

      expect(screen.getByText('Plugins')).toBeInTheDocument();
      expect(screen.getByText('Skills')).toBeInTheDocument();
    });
  });

  it('renders links to type-specific panels', async () => {
    renderWithQuery(<MarketplaceHub />);

    await waitFor(() => {
      const pluginLink = screen.getByRole('link', { name: /Go to Plugins Panel/i });
      expect(pluginLink).toHaveAttribute('href', '/console/plugin-center');
    });
  });

  it('marks layers card as read-only', async () => {
    renderWithQuery(<MarketplaceHub />);

    await waitFor(() => {
      expect(screen.getByText(/Read-only \(compliance-critical\)/)).toBeInTheDocument();
    });
  });

  it('handles loading state', async () => {
    global.fetch = jest.fn(() => new Promise(() => {})); // Never resolves

    renderWithQuery(<MarketplaceHub />);

    expect(screen.getByText(/Loading Marketplace/i)).toBeInTheDocument();
  });

  it('handles error state', async () => {
    global.fetch = jest.fn(() =>
      Promise.resolve({
        ok: false,
        json: async () => ({}),
      })
    ) as jest.Mock;

    renderWithQuery(<MarketplaceHub />);

    await waitFor(() => {
      expect(screen.getByText(/Error Loading Marketplace/i)).toBeInTheDocument();
    });
  });

  it('shows no results message for non-matching search', async () => {
    renderWithQuery(<MarketplaceHub />);

    await waitFor(() => {
      const searchInput = screen.getByPlaceholderText(/Search by type/i);
      fireEvent.change(searchInput, { target: { value: 'nonexistent' } });

      expect(screen.getByText(/No results found for/i)).toBeInTheDocument();
    });
  });

  it('is responsive on mobile viewport', () => {
    global.innerWidth = 375;
    renderWithQuery(<MarketplaceHub />);

    // Just verify component renders, responsive styles are CSS-based
    expect(screen.getByText('Marketplace')).toBeInTheDocument();
  });

  it('applies dark mode classes', () => {
    renderWithQuery(<MarketplaceHub />);

    const hub = screen.getByText('Marketplace').closest('.marketplace-hub');
    expect(hub).toHaveClass('dark:bg-slate-950');
  });
});

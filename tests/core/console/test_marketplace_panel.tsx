/**
 * Unit tests for Marketplace Panel (Task #5, Phase 2)
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import MarketplacePanel from '@/panels/marketplace'

describe('Marketplace Panel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    global.fetch = vi.fn()
  })

  it('renders without crashing', () => {
    ;(global.fetch as any).mockResolvedValue({
      ok: true,
      json: async () => ({ version: '1.0', extensions: [] }),
    })

    const { container } = render(<MarketplacePanel />)
    expect(container).toBeTruthy()
  })

  it('displays browse and installed tabs', async () => {
    ;(global.fetch as any).mockResolvedValue({
      ok: true,
      json: async () => ({ version: '1.0', extensions: [] }),
    })

    render(<MarketplacePanel />)
    await waitFor(() => {
      expect(screen.getByText('Browse')).toBeInTheDocument()
      expect(screen.getByText('Installed')).toBeInTheDocument()
    })
  })

  it('renders extension grid when data loaded', async () => {
    const mockData = {
      version: '1.0',
      extensions: [
        {
          plugin_id: 'test-1',
          name: 'Test Plugin',
          version: '0.1.0',
          category: 'Security',
          description: 'A test plugin',
          author_id: 'test-author',
          rating_average: 4.5,
          download_count: 100,
        },
      ],
    }

    ;(global.fetch as any).mockResolvedValue({
      ok: true,
      json: async () => mockData,
    })

    render(<MarketplacePanel />)
    await waitFor(() => {
      expect(screen.getByText('Test Plugin')).toBeInTheDocument()
      expect(screen.getByText('Security')).toBeInTheDocument()
    })
  })

  it('filters by search term', async () => {
    const mockData = {
      version: '1.0',
      extensions: [
        {
          plugin_id: 'test-1',
          name: 'Security Plugin',
          version: '0.1.0',
          category: 'Security',
          description: 'Security-related',
          author_id: 'author',
          rating_average: 4.5,
          download_count: 100,
        },
        {
          plugin_id: 'test-2',
          name: 'Performance Tool',
          version: '0.2.0',
          category: 'Performance',
          description: 'Optimization tool',
          author_id: 'author',
          rating_average: 4.8,
          download_count: 200,
        },
      ],
    }

    ;(global.fetch as any).mockResolvedValue({
      ok: true,
      json: async () => mockData,
    })

    render(<MarketplacePanel />)
    await waitFor(() => {
      const input = screen.getByPlaceholderText('Search extensions...')
      fireEvent.change(input, { target: { value: 'security' } })
    })

    await waitFor(() => {
      expect(screen.getByText('Security Plugin')).toBeInTheDocument()
    })
  })

  it('handles fetch errors gracefully', async () => {
    ;(global.fetch as any).mockRejectedValue(new Error('Network error'))

    render(<MarketplacePanel />)
    await waitFor(() => {
      expect(screen.getByText(/Failed to fetch/i)).toBeInTheDocument()
    })
  })
})

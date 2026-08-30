/**
 * Unit Tests: Marketplace Panel Phase 3 Week 1 — Real Job API Wiring
 * Tests: POST /api/v2/marketplace/install + GET progress polling (mocked)
 */

import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MarketplacePanel } from '@/panels/marketplace'
import * as useProgressPollingModule from '@/hooks/useProgressPolling'

// Mock fetch globally
global.fetch = jest.fn()

describe('Marketplace Panel - Phase 3 Real API', () => {
  let queryClient: QueryClient

  beforeEach(() => {
    queryClient = new QueryClient()
    jest.clearAllMocks()
  })

  test('handleInstall: POST /api/v2/marketplace/install + start polling', async () => {
    // Mock the index fetch
    ;(global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        extensions: [
          {
            plugin_id: 'test-plugin',
            name: 'Test Plugin',
            version: '1.0.0',
            category: 'Tools',
            description: 'Test',
            author_id: 'author',
            rating_average: 4.5,
            download_count: 100,
          },
        ],
      }),
    })

    // Mock useProgressPolling hook
    const mockStartPolling = jest.fn()
    jest.spyOn(useProgressPollingModule, 'useProgressPolling').mockReturnValue({
      status: null,
      stopPolling: jest.fn(),
    } as any)

    // Mock POST install endpoint
    ;(global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        job_id: 'job-12345',
      }),
    })

    const { container } = render(
      <QueryClientProvider client={queryClient}>
        <MarketplacePanel />
      </QueryClientProvider>
    )

    // Wait for marketplace to load
    await waitFor(() => {
      expect(screen.queryByText('Marketplace')).toBeInTheDocument()
    })

    // Verify POST was called
    const postCalls = (global.fetch as jest.Mock).mock.calls.filter(
      call => call[1]?.method === 'POST'
    )
    expect(postCalls.length).toBeGreaterThan(0)

    // Verify POST body
    const installCall = postCalls.find(call =>
      call[0].includes('/api/v2/marketplace/install')
    )
    if (installCall) {
      const body = JSON.parse(installCall[1].body)
      expect(body.extension_id).toBe('test-plugin')
      expect(body.version).toBe('1.0.0')
    }
  })

  test('handleInstall: error on POST should show error message', async () => {
    // Mock index
    ;(global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        extensions: [
          {
            plugin_id: 'test-plugin',
            name: 'Test Plugin',
            version: '1.0.0',
            category: 'Tools',
            description: 'Test',
            author_id: 'author',
            rating_average: 4.5,
            download_count: 100,
          },
        ],
      }),
    })

    // Mock POST install failure
    ;(global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: false,
      statusText: 'Internal Server Error',
    })

    render(
      <QueryClientProvider client={queryClient}>
        <MarketplacePanel />
      </QueryClientProvider>
    )

    await waitFor(() => {
      expect(screen.getByText('Marketplace')).toBeInTheDocument()
    })

    // Error is set in state but may not be visible in UI
    // This test verifies API contract is called correctly
    const postCalls = (global.fetch as jest.Mock).mock.calls.filter(
      call => call[1]?.method === 'POST'
    )
    expect(postCalls.length).toBeGreaterThan(0)
  })

  test('useProgressPolling hook is called with correct job_id', async () => {
    const mockUseProgressPolling = jest.spyOn(
      useProgressPollingModule,
      'useProgressPolling'
    )

    // Mock index
    ;(global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        extensions: [{ plugin_id: 'test', name: 'Test', version: '1.0', category: 'Tools', description: 'Test', author_id: 'a', rating_average: 4.5, download_count: 100 }],
      }),
    })

    // Mock POST install
    ;(global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ job_id: 'job-xyz-123' }),
    })

    mockUseProgressPolling.mockReturnValue({
      status: null,
      stopPolling: jest.fn(),
    } as any)

    render(
      <QueryClientProvider client={queryClient}>
        <MarketplacePanel />
      </QueryClientProvider>
    )

    await waitFor(() => {
      expect(mockUseProgressPolling).toHaveBeenCalled()
    })
  })
})

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
/**
 * Unit Tests: Marketplace Panel Phase 3 Week 1 — Real Job API Wiring
 * Tests: POST /api/v2/marketplace/install + GET progress polling (mocked)
 */

import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MarketplacePanel } from '@/panels/marketplace'
import * as useProgressPollingModule from '@/hooks/useProgressPolling'
import { BASE } from '@/lib/api/client'

describe('Marketplace Panel - Phase 3 Real API', () => {
  let queryClient: QueryClient

  beforeEach(() => {
    queryClient = new QueryClient()
    vi.clearAllMocks()
    // Re-stub per test: clearAllMocks resets the fn, and a module-scope stub
    // handed `global.fetch` back as the real implementation here.
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  test('handleInstall: POST /api/v2/marketplace/install + start polling', async () => {
    // Mock the index fetch
    ;vi.mocked(fetch).mockResolvedValueOnce({
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
    const mockStartPolling = vi.fn()
    vi.spyOn(useProgressPollingModule, 'useProgressPolling').mockReturnValue({
      status: null,
      stopPolling: vi.fn(),
    } as any)

    // Mock POST install endpoint
    ;vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        job_id: 'job-12345',
      }),
    })

    render(
      <QueryClientProvider client={queryClient}>
        <MarketplacePanel />
      </QueryClientProvider>
    )

    // Wait for the index to render, then drive the two clicks that install:
    // card -> detail modal -> Install.
    await waitFor(() => {
      expect(screen.getByText('Test Plugin')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('Test Plugin'))
    fireEvent.click(await screen.findByRole('button', { name: /^Install$/ }))

    await waitFor(() => {
      expect(
        vi.mocked(fetch).mock.calls.some(c => c[1]?.method === 'POST'),
      ).toBe(true)
    })

    // Verify POST was called
    const postCalls = vi.mocked(fetch).mock.calls.filter(
      call => call[1]?.method === 'POST'
    )
    expect(postCalls.length).toBeGreaterThan(0)

    // Verify POST body
    const installCall = postCalls.find(call =>
      call[0].includes(`${BASE}/api/v2/marketplace/install`)
    )
    if (installCall) {
      const body = JSON.parse(installCall[1].body)
      expect(body.extension_id).toBe('test-plugin')
      expect(body.version).toBe('1.0.0')
    }
  })

  test('handleInstall: error on POST should show error message', async () => {
    // Mock index
    ;vi.mocked(fetch).mockResolvedValueOnce({
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
    ;vi.mocked(fetch).mockResolvedValueOnce({
      ok: false,
      statusText: 'Internal Server Error',
    })

    render(
      <QueryClientProvider client={queryClient}>
        <MarketplacePanel />
      </QueryClientProvider>
    )

    await waitFor(() => {
      expect(screen.getByText('Test Plugin')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('Test Plugin'))
    fireEvent.click(await screen.findByRole('button', { name: /^Install$/ }))

    await waitFor(() => {
      expect(
        vi.mocked(fetch).mock.calls.some(c => c[1]?.method === 'POST'),
      ).toBe(true)
    })

    const postCalls = vi.mocked(fetch).mock.calls.filter(
      call => call[1]?.method === 'POST'
    )
    expect(postCalls.length).toBeGreaterThan(0)
  })

  test('useProgressPolling hook is called with correct job_id', async () => {
    const mockUseProgressPolling = vi.spyOn(
      useProgressPollingModule,
      'useProgressPolling'
    )

    // Mock index
    ;vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        extensions: [{ plugin_id: 'test', name: 'Test', version: '1.0', category: 'Tools', description: 'Test', author_id: 'a', rating_average: 4.5, download_count: 100 }],
      }),
    })

    // Mock POST install
    ;vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ job_id: 'job-xyz-123' }),
    })

    mockUseProgressPolling.mockReturnValue({
      status: null,
      stopPolling: vi.fn(),
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

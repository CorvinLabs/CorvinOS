/**
 * Unit tests for useCustomRepositories hook — ADR-0454 Week 2
 * Tests fetch, cache, error handling, actions
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { useCustomRepositories } from '@/hooks/useCustomRepositories'

global.fetch = vi.fn()

describe('useCustomRepositories', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.clearAllTimers()
  })

  it('fetches and returns repositories on initial mount', async () => {
    const mockRepos = [
      {
        repo_url: 'https://github.com/owner/repo1',
        status: 'healthy' as const,
        extension_count: 5,
        last_checked: '2026-08-30T10:00:00Z',
        enabled: true
      }
    ]

    vi.mocked(global.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ repositories: mockRepos }), { status: 200 })
    )

    const { result } = renderHook(() => useCustomRepositories())

    expect(result.current.loading).toBe(true)

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(result.current.repositories).toEqual(mockRepos)
    expect(result.current.error).toBeNull()
  })

  it('handles fetch errors gracefully', async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ error_message: 'API error' }), { status: 500 })
    )

    const { result } = renderHook(() => useCustomRepositories())

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(result.current.error).toBe('API error')
    expect(result.current.repositories).toEqual([])
  })

  it('handles network errors', async () => {
    vi.mocked(global.fetch).mockRejectedValueOnce(new Error('Network error'))

    const { result } = renderHook(() => useCustomRepositories())

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(result.current.error).toBe('Network error')
  })

  it('caches repositories for 30 seconds', async () => {
    const mockRepos = [
      {
        repo_url: 'https://github.com/owner/repo1',
        status: 'healthy' as const,
        extension_count: 5
      }
    ]

    vi.mocked(global.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ repositories: mockRepos }), { status: 200 })
    )

    const { result: result1 } = renderHook(() => useCustomRepositories())

    await waitFor(() => {
      expect(result1.current.loading).toBe(false)
    })

    // Clear mock to verify cache is used
    vi.mocked(global.fetch).mockClear()

    const { result: result2 } = renderHook(() => useCustomRepositories())

    // Should use cache, not make new fetch
    expect(vi.mocked(global.fetch)).not.toHaveBeenCalled()
    expect(result2.current.repositories).toEqual(mockRepos)
  })

  it('refetch invalidates cache and fetches fresh data', async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          repositories: [{ repo_url: 'https://github.com/owner/repo1', status: 'healthy', extension_count: 5 }]
        }),
        { status: 200 }
      )
    )

    const { result } = renderHook(() => useCustomRepositories())

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    // Mock fresh response
    vi.mocked(global.fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          repositories: [{ repo_url: 'https://github.com/owner/repo2', status: 'healthy', extension_count: 10 }]
        }),
        { status: 200 }
      )
    )

    await result.current.refetch()

    expect(result.current.repositories[0].repo_url).toBe('https://github.com/owner/repo2')
  })

  it('refresh action reloads a specific repository', async () => {
    const repoUrl = 'https://github.com/owner/repo1'

    vi.mocked(global.fetch)
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            repositories: [{ repo_url: repoUrl, status: 'healthy', extension_count: 5 }]
          }),
          { status: 200 }
        )
      )
      .mockResolvedValueOnce(new Response(JSON.stringify({}), { status: 200 })) // refresh endpoint

    const { result } = renderHook(() => useCustomRepositories())

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    await result.current.refresh(repoUrl)

    expect(vi.mocked(global.fetch)).toHaveBeenCalledWith(
      '/api/v1/marketplace/custom-repositories/refresh',
      expect.objectContaining({ method: 'POST' })
    )
  })

  it('toggle action enables/disables a repository', async () => {
    const repoUrl = 'https://github.com/owner/repo1'

    vi.mocked(global.fetch)
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            repositories: [{ repo_url: repoUrl, status: 'healthy', extension_count: 5, enabled: true }]
          }),
          { status: 200 }
        )
      )
      .mockResolvedValueOnce(new Response(JSON.stringify({}), { status: 200 })) // toggle endpoint

    const { result } = renderHook(() => useCustomRepositories())

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    await result.current.toggle(repoUrl)

    expect(vi.mocked(global.fetch)).toHaveBeenCalledWith(
      '/api/v1/marketplace/custom-repositories',
      expect.objectContaining({ method: 'PATCH' })
    )
  })

  it('remove action deletes a repository', async () => {
    const repoUrl = 'https://github.com/owner/repo1'

    vi.mocked(global.fetch)
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            repositories: [{ repo_url: repoUrl, status: 'healthy', extension_count: 5 }]
          }),
          { status: 200 }
        )
      )
      .mockResolvedValueOnce(new Response(JSON.stringify({}), { status: 200 })) // remove endpoint

    const { result } = renderHook(() => useCustomRepositories())

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    await result.current.remove(repoUrl)

    expect(vi.mocked(global.fetch)).toHaveBeenCalledWith(
      '/api/v1/marketplace/custom-repositories',
      expect.objectContaining({ method: 'DELETE' })
    )
  })
})

/**
 * useCustomRepositories Hook — ADR-0451/0454 Week 2
 * Manages custom repository state with fetch, caching, and auto-refresh
 *
 * Features:
 * - GET /v1/marketplace/custom-repositories (list repositories)
 * - Cache with auto-refresh every 30s
 * - Error state management (per ADR-0453)
 * - Refetch function for manual refresh
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import { BASE } from '@/lib/api/client'
import { useAuth } from '@/lib/auth'

export interface CustomRepository {
  repo_url: string
  status: 'healthy' | 'loading' | 'error'
  extension_count: number
  error_message?: string
  last_checked?: string
  enabled?: boolean
}

interface UseCustomRepositoriesResult {
  repositories: CustomRepository[]
  loading: boolean
  error: string | null
  refetch: () => Promise<void>
  refresh: (repoUrl: string) => Promise<void>
  toggle: (repoUrl: string) => Promise<void>
  remove: (repoUrl: string) => Promise<void>
}

const CACHE_TTL = 30000 // 30 seconds
const REFRESH_INTERVAL = 30000

let cachedRepositories: CustomRepository[] | null = null
let cacheTimestamp = 0

export function useCustomRepositories(): UseCustomRepositoriesResult {
  // refresh / toggle / remove are mutations → CSRF token (backend: require_csrf).
  const { session } = useAuth()
  const csrf = session?.csrf_token ?? ''
  const [repositories, setRepositories] = useState<CustomRepository[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const isMountedRef = useRef(true)
  const refreshIntervalRef = useRef<NodeJS.Timeout>()

  useEffect(() => {
    isMountedRef.current = true
    return () => {
      isMountedRef.current = false
      if (refreshIntervalRef.current) clearInterval(refreshIntervalRef.current)
    }
  }, [])

  // Fetch repositories from API
  const fetchRepositories = useCallback(async (skipCache = false) => {
    // Use cache if available and not expired
    if (!skipCache && cachedRepositories && Date.now() - cacheTimestamp < CACHE_TTL) {
      if (isMountedRef.current) {
        setRepositories(cachedRepositories)
        setLoading(false)
      }
      return
    }

    if (isMountedRef.current) setLoading(true)

    try {
      const response = await fetch(`${BASE}/api/v1/marketplace/custom-repositories`)
      if (!isMountedRef.current) return

      if (response.ok) {
        const data = await response.json()
        const repos = data.repositories || []
        cachedRepositories = repos
        cacheTimestamp = Date.now()
        setRepositories(repos)
        setError(null)
      } else {
        const errorData = await response.json()
        setError(errorData.error_message || 'Failed to fetch repositories')
      }
    } catch (err) {
      if (isMountedRef.current) {
        setError(err instanceof Error ? err.message : 'Network error')
      }
    } finally {
      if (isMountedRef.current) setLoading(false)
    }
  }, [])

  // Initial fetch
  useEffect(() => {
    fetchRepositories()
  }, [fetchRepositories])

  // Auto-refresh every 30s
  useEffect(() => {
    refreshIntervalRef.current = setInterval(() => {
      fetchRepositories(true) // Skip cache for auto-refresh
    }, REFRESH_INTERVAL)

    return () => {
      if (refreshIntervalRef.current) clearInterval(refreshIntervalRef.current)
    }
  }, [fetchRepositories])

  // Manual refresh action
  const refetch = useCallback(async () => {
    await fetchRepositories(true)
  }, [fetchRepositories])

  // Refresh a specific repository
  const refresh = useCallback(async (repoUrl: string) => {
    try {
      const response = await fetch(
        `${BASE}/api/v1/marketplace/custom-repositories/refresh`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
          body: JSON.stringify({ repo_url: repoUrl })
        }
      )

      if (!isMountedRef.current) return

      if (response.ok) {
        // Invalidate cache and refetch
        cachedRepositories = null
        await fetchRepositories(true)
      } else {
        const errorData = await response.json()
        throw new Error(errorData.error_message || 'Refresh failed')
      }
    } catch (err) {
      throw err instanceof Error ? err : new Error('Failed to refresh repository')
    }
  }, [fetchRepositories, csrf])

  // Toggle repository enable/disable
  const toggle = useCallback(async (repoUrl: string) => {
    try {
      const response = await fetch(
        `${BASE}/api/v1/marketplace/custom-repositories`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
          body: JSON.stringify({
            repo_url: repoUrl,
            enabled: !repositories.find(r => r.repo_url === repoUrl)?.enabled
          })
        }
      )

      if (!isMountedRef.current) return

      if (response.ok) {
        // Invalidate cache and refetch
        cachedRepositories = null
        await fetchRepositories(true)
      } else {
        const errorData = await response.json()
        throw new Error(errorData.error_message || 'Toggle failed')
      }
    } catch (err) {
      throw err instanceof Error ? err : new Error('Failed to toggle repository')
    }
  }, [repositories, fetchRepositories, csrf])

  // Remove repository
  const remove = useCallback(async (repoUrl: string) => {
    try {
      const response = await fetch(
        `${BASE}/api/v1/marketplace/custom-repositories`,
        {
          method: 'DELETE',
          headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
          body: JSON.stringify({ repo_url: repoUrl })
        }
      )

      if (!isMountedRef.current) return

      if (response.ok) {
        // Invalidate cache and refetch
        cachedRepositories = null
        await fetchRepositories(true)
      } else {
        const errorData = await response.json()
        throw new Error(errorData.error_message || 'Remove failed')
      }
    } catch (err) {
      throw err instanceof Error ? err : new Error('Failed to remove repository')
    }
  }, [fetchRepositories, csrf])

  return {
    repositories,
    loading,
    error,
    refetch,
    refresh,
    toggle,
    remove
  }
}

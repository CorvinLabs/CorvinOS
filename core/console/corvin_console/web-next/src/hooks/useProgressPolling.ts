/**
 * useProgressPolling — Hook for polling installation progress
 *
 * Usage: const progress = useProgressPolling(jobId, {interval: 2000})
 */

import { useState, useEffect, useRef } from 'react'
import { BASE } from '@/lib/api/client'

export interface ProgressStatus {
  status: 'pending' | 'in_progress' | 'completed' | 'failed'
  progress: number // 0-100
  message: string
}

interface UseProgressPollingOptions {
  interval?: number // ms between polls
  onComplete?: (status: ProgressStatus) => void
  onError?: (error: Error) => void
}

export const useProgressPolling = (
  jobId: string | null | undefined,
  options: UseProgressPollingOptions = {}
) => {
  const { interval = 2000, onComplete, onError } = options

  const [status, setStatus] = useState<ProgressStatus | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [isPolling, setIsPolling] = useState(false)
  const intervalIdRef = useRef<NodeJS.Timeout | null>(null)

  const pollProgress = async () => {
    if (!jobId) return

    try {
      // routes/marketplace_install.py (mounted under /api/v1/marketplace); there is no v2 API.
      const response = await fetch(`${BASE}/api/v1/marketplace/install/${encodeURIComponent(jobId)}/progress`)
      if (!response.ok) {
        throw new Error(`Failed to fetch progress: ${response.statusText}`)
      }

      const data: ProgressStatus = await response.json()
      setStatus(data)
      setError(null)

      // Stop polling if completed or failed
      if (data.status === 'completed' || data.status === 'failed') {
        setIsPolling(false)
        if (intervalIdRef.current) {
          clearInterval(intervalIdRef.current)
        }
        onComplete?.(data)
      }
    } catch (err) {
      const error = err instanceof Error ? err : new Error(String(err))
      setError(error)
      onError?.(error)
    }
  }

  // Start polling when jobId is provided
  useEffect(() => {
    if (!jobId) {
      setIsPolling(false)
      if (intervalIdRef.current) {
        clearInterval(intervalIdRef.current)
      }
      return
    }

    setIsPolling(true)

    // Poll immediately, then on interval
    pollProgress()

    intervalIdRef.current = setInterval(pollProgress, interval)

    return () => {
      if (intervalIdRef.current) {
        clearInterval(intervalIdRef.current)
      }
    }
  }, [jobId, interval])

  const stopPolling = () => {
    setIsPolling(false)
    if (intervalIdRef.current) {
      clearInterval(intervalIdRef.current)
    }
  }

  return {
    status,
    error,
    isPolling,
    stopPolling,
  }
}

export default useProgressPolling

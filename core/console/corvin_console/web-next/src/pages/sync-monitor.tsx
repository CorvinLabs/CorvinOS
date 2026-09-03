/**
 * Sync Monitor Panel — Live GitHub Sync Status
 *
 * Features:
 * - Real-time sync events via Server-Sent Events (SSE)
 * - Live status updates (syncing, success, error)
 * - Sync history log
 * - Worker control (start/stop)
 * - Auto-reconnect on connection loss
 */

import { useState, useEffect, useRef } from 'react'
import { Zap, CheckCircle, AlertCircle, Loader, RotateCw, Pause, Play } from 'lucide-react'
import { useAuth } from '@/lib/auth'

interface SyncEvent {
  event: string
  timestamp: string
  details: Record<string, unknown>
}

interface WorkerStatus {
  running: boolean
  interval_seconds: number
  last_sync?: string
  last_error?: string
  sync_count: number
  error_count: number
  uptime: string
}

export default function SyncMonitorPanel() {
  // Worker start/stop are mutations → CSRF token (backend: require_csrf).
  const { session } = useAuth()
  const csrf = session?.csrf_token ?? ''
  const [events, setEvents] = useState<SyncEvent[]>([])
  const [connected, setConnected] = useState(false)
  const [isSyncing, setIsSyncing] = useState(false)
  const [lastSyncResult, setLastSyncResult] = useState<SyncEvent | null>(null)
  const [workerStatus, setWorkerStatus] = useState<WorkerStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const eventSourceRef = useRef<EventSource | null>(null)
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null)

  // Fetch worker status on mount
  useEffect(() => {
    fetchWorkerStatus()
  }, [])

  // Connect to SSE stream
  useEffect(() => {
    connectToEventStream()

    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close()
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const connectToEventStream = () => {
    try {
      const eventSource = new EventSource('/v1/console/github/events')

      eventSource.onopen = () => {
        setConnected(true)
        setError(null)
        console.log('Connected to sync event stream')
      }

      eventSource.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data)
          const syncEvent: SyncEvent = {
            event: data.event,
            timestamp: data.timestamp,
            details: data.data || {},
          }

          // Add to event log
          setEvents((prev) => [syncEvent, ...prev.slice(0, 49)])

          // Update sync status
          if (data.event === 'sync_started') {
            setIsSyncing(true)
          } else if (data.event === 'sync_completed') {
            setIsSyncing(false)
            setLastSyncResult(syncEvent)
            fetchWorkerStatus()
          } else if (data.event === 'sync_failed') {
            setIsSyncing(false)
            setLastSyncResult(syncEvent)
            setError(String(syncEvent.details?.error) || 'Sync failed')
          }
        } catch (error) {
          console.error('Error parsing event:', error)
        }
      }

      eventSource.onerror = () => {
        setConnected(false)
        eventSource.close()
        // Try to reconnect after 5 seconds
        reconnectTimeoutRef.current = setTimeout(connectToEventStream, 5000)
      }

      eventSourceRef.current = eventSource
    } catch (error) {
      setError(`Failed to connect: ${error}`)
    }
  }

  const fetchWorkerStatus = async () => {
    try {
      const response = await fetch('/v1/console/github/worker/status')
      const status: WorkerStatus = await response.json()
      setWorkerStatus(status)
    } catch (error) {
      console.error('Failed to fetch worker status:', error)
    }
  }

  const handleStartWorker = async () => {
    try {
      const response = await fetch('/v1/console/github/worker/start', {
        method: 'POST',
        headers: { 'X-CSRF-Token': csrf },
      })
      const result = await response.json()
      if (result.success) {
        setWorkerStatus(result.status)
        setError(null)
      } else {
        setError(result.error || 'Failed to start worker')
      }
    } catch (error) {
      setError(`Error: ${error}`)
    }
  }

  const handleStopWorker = async () => {
    try {
      const response = await fetch('/v1/console/github/worker/stop', {
        method: 'POST',
        headers: { 'X-CSRF-Token': csrf },
      })
      const result = await response.json()
      if (result.success) {
        setWorkerStatus((prev) => prev ? { ...prev, running: false } : null)
        setError(null)
      } else {
        setError(result.error || 'Failed to stop worker')
      }
    } catch (error) {
      setError(`Error: ${error}`)
    }
  }

  const getStatusIcon = () => {
    if (isSyncing) {
      return <Loader className="text-blue-500 animate-spin" size={24} />
    } else if (lastSyncResult?.event === 'sync_completed') {
      return <CheckCircle className="text-green-500" size={24} />
    } else if (error) {
      return <AlertCircle className="text-red-500" size={24} />
    } else {
      return <Zap className="text-slate-400" size={24} />
    }
  }

  const getStatusText = () => {
    if (isSyncing) {
      return 'Syncing...'
    } else if (lastSyncResult?.event === 'sync_completed') {
      return 'Last sync: Success'
    } else if (error) {
      return 'Last sync: Failed'
    } else {
      return 'No sync yet'
    }
  }

  const formatTimestamp = (ts: string) => {
    try {
      return new Date(ts).toLocaleTimeString()
    } catch {
      return ts
    }
  }

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      {/* Live Status */}
      <div className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 p-6">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            {getStatusIcon()}
            <div>
              <h2 className="text-xl font-bold text-slate-900 dark:text-white">Sync Monitor</h2>
              <p className="text-sm text-slate-600 dark:text-slate-400">{getStatusText()}</p>
            </div>
          </div>

          {/* Connection Status */}
          <div className="flex items-center gap-2">
            <div className={`w-3 h-3 rounded-full ${connected ? 'bg-green-500' : 'bg-red-500'}`} />
            <span className="text-sm text-slate-600 dark:text-slate-400">
              {connected ? 'Connected' : 'Disconnected'}
            </span>
          </div>
        </div>

        {/* Error Display */}
        {error && (
          <div className="mb-4 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg">
            <p className="text-sm text-red-700 dark:text-red-400">{error}</p>
          </div>
        )}

        {/* Worker Status */}
        {workerStatus && (
          <div className="space-y-3 mb-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="bg-slate-50 dark:bg-slate-900/30 p-3 rounded">
                <p className="text-xs text-slate-600 dark:text-slate-400">Status</p>
                <p className={`font-semibold ${workerStatus.running ? 'text-green-600' : 'text-slate-600'}`}>
                  {workerStatus.running ? '✓ Running' : 'Stopped'}
                </p>
              </div>
              <div className="bg-slate-50 dark:bg-slate-900/30 p-3 rounded">
                <p className="text-xs text-slate-600 dark:text-slate-400">Interval</p>
                <p className="font-semibold text-slate-900 dark:text-white">
                  {workerStatus.interval_seconds}s
                </p>
              </div>
              <div className="bg-slate-50 dark:bg-slate-900/30 p-3 rounded">
                <p className="text-xs text-slate-600 dark:text-slate-400">Syncs</p>
                <p className="font-semibold text-slate-900 dark:text-white">
                  {workerStatus.sync_count} success / {workerStatus.error_count} errors
                </p>
              </div>
              <div className="bg-slate-50 dark:bg-slate-900/30 p-3 rounded">
                <p className="text-xs text-slate-600 dark:text-slate-400">Last Sync</p>
                <p className="font-semibold text-slate-900 dark:text-white text-xs">
                  {workerStatus.last_sync
                    ? formatTimestamp(workerStatus.last_sync)
                    : 'Never'}
                </p>
              </div>
            </div>

            {/* Worker Controls */}
            <div className="flex gap-2">
              {!workerStatus.running ? (
                <button
                  onClick={handleStartWorker}
                  className="flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg font-medium hover:bg-green-700 transition"
                >
                  <Play size={18} />
                  Start Worker
                </button>
              ) : (
                <button
                  onClick={handleStopWorker}
                  className="flex items-center gap-2 px-4 py-2 bg-red-600 text-white rounded-lg font-medium hover:bg-red-700 transition"
                >
                  <Pause size={18} />
                  Stop Worker
                </button>
              )}
              <button
                onClick={fetchWorkerStatus}
                className="flex items-center gap-2 px-4 py-2 bg-slate-200 dark:bg-slate-700 text-slate-900 dark:text-white rounded-lg font-medium hover:bg-slate-300 dark:hover:bg-slate-600 transition"
              >
                <RotateCw size={18} />
                Refresh
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Event Log */}
      <div className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 p-6">
        <h3 className="font-bold text-slate-900 dark:text-white mb-4">Sync Events (Last 50)</h3>

        {events.length === 0 ? (
          <p className="text-sm text-slate-500 text-center py-8">No events yet. Waiting for sync activity...</p>
        ) : (
          <div className="space-y-2 max-h-96 overflow-y-auto">
            {events.map((event, idx) => (
              <div
                key={idx}
                className={`p-3 rounded border text-sm ${
                  event.event === 'sync_completed'
                    ? 'bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-800'
                    : event.event === 'sync_failed'
                    ? 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800'
                    : event.event === 'sync_started'
                    ? 'bg-blue-50 dark:bg-blue-900/20 border-blue-200 dark:border-blue-800'
                    : 'bg-slate-50 dark:bg-slate-900/30 border-slate-200 dark:border-slate-700'
                }`}
              >
                <div className="flex justify-between items-start">
                  <span className="font-semibold">
                    {event.event === 'sync_started' && '🔄 Sync Started'}
                    {event.event === 'sync_completed' && '✓ Sync Completed'}
                    {event.event === 'sync_failed' && '✗ Sync Failed'}
                    {event.event === 'connected' && '✓ Connected'}
                    {event.event === 'status_updated' && 'Status Updated'}
                  </span>
                  <span className="text-xs text-slate-500">
                    {formatTimestamp(event.timestamp)}
                  </span>
                </div>

                {event.details && Object.keys(event.details).length > 0 && (
                  <div className="text-xs text-slate-600 dark:text-slate-400 mt-2">
                    {JSON.stringify(event.details, null, 2)
                      .split('\n')
                      .slice(0, 3)
                      .join('\n')}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Info */}
      <div className="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg p-4">
        <p className="text-sm text-blue-700 dark:text-blue-400">
          💡 The sync worker automatically uploads your skills to GitHub every 5 minutes. You can start/stop it
          manually or configure the interval. Events update in real-time via Server-Sent Events.
        </p>
      </div>
    </div>
  )
}

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
import { Zap, CheckCircle2, AlertCircle, Loader2, RotateCw, Pause, Play } from 'lucide-react'
import { useAuth } from '@/lib/auth'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

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
      return <Loader2 className="h-6 w-6 text-accent animate-spin" />
    } else if (lastSyncResult?.event === 'sync_completed') {
      return <CheckCircle2 className="h-6 w-6 text-emerald-600 dark:text-emerald-400" />
    } else if (error) {
      return <AlertCircle className="h-6 w-6 text-destructive" />
    } else {
      return <Zap className="h-6 w-6 text-muted-foreground" />
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

  const eventPanelClass = (event: string) =>
    cn(
      "p-3 rounded-lg border text-sm",
      event === 'sync_completed' && "border-emerald-500/30 bg-emerald-500/10",
      event === 'sync_failed' && "border-destructive/40 bg-destructive/10",
      event === 'sync_started' && "border-accent/30 bg-accent/10",
      event !== 'sync_completed' && event !== 'sync_failed' && event !== 'sync_started' && "border-border bg-muted/30",
    )

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      {/* Live Status */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              {getStatusIcon()}
              <div>
                <CardTitle>Sync Monitor</CardTitle>
                <CardDescription>{getStatusText()}</CardDescription>
              </div>
            </div>

            {/* Connection Status */}
            <div className="flex items-center gap-2">
              <div className={cn("w-2.5 h-2.5 rounded-full", connected ? "bg-emerald-500" : "bg-destructive")} />
              <span className="text-sm text-muted-foreground">
                {connected ? 'Connected' : 'Disconnected'}
              </span>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Error Display */}
          {error && (
            <div className="p-3 rounded-lg border border-destructive/40 bg-destructive/10">
              <p className="text-sm text-destructive">{error}</p>
            </div>
          )}

          {/* Worker Status */}
          {workerStatus && (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div className="bg-muted/40 p-3 rounded-lg">
                  <p className="text-xs text-muted-foreground">Status</p>
                  <p className={cn("font-semibold", workerStatus.running ? "text-emerald-600 dark:text-emerald-400" : "text-muted-foreground")}>
                    {workerStatus.running ? '✓ Running' : 'Stopped'}
                  </p>
                </div>
                <div className="bg-muted/40 p-3 rounded-lg">
                  <p className="text-xs text-muted-foreground">Interval</p>
                  <p className="font-semibold text-foreground">
                    {workerStatus.interval_seconds}s
                  </p>
                </div>
                <div className="bg-muted/40 p-3 rounded-lg">
                  <p className="text-xs text-muted-foreground">Syncs</p>
                  <p className="font-semibold text-foreground">
                    {workerStatus.sync_count} success / {workerStatus.error_count} errors
                  </p>
                </div>
                <div className="bg-muted/40 p-3 rounded-lg">
                  <p className="text-xs text-muted-foreground">Last Sync</p>
                  <p className="font-semibold text-foreground text-xs">
                    {workerStatus.last_sync
                      ? formatTimestamp(workerStatus.last_sync)
                      : 'Never'}
                  </p>
                </div>
              </div>

              {/* Worker Controls */}
              <div className="flex gap-2">
                {!workerStatus.running ? (
                  <Button variant="accent" onClick={handleStartWorker}>
                    <Play size={16} />
                    Start Worker
                  </Button>
                ) : (
                  <Button variant="destructive" onClick={handleStopWorker}>
                    <Pause size={16} />
                    Stop Worker
                  </Button>
                )}
                <Button variant="secondary" onClick={fetchWorkerStatus}>
                  <RotateCw size={16} />
                  Refresh
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Event Log */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Sync Events (Last 50)</CardTitle>
        </CardHeader>
        <CardContent>
          {events.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-8">No events yet. Waiting for sync activity...</p>
          ) : (
            <div className="space-y-2 max-h-96 overflow-y-auto">
              {events.map((event, idx) => (
                <div key={idx} className={eventPanelClass(event.event)}>
                  <div className="flex justify-between items-start">
                    <span className="font-semibold text-foreground">
                      {event.event === 'sync_started' && '🔄 Sync Started'}
                      {event.event === 'sync_completed' && '✓ Sync Completed'}
                      {event.event === 'sync_failed' && '✗ Sync Failed'}
                      {event.event === 'connected' && '✓ Connected'}
                      {event.event === 'status_updated' && 'Status Updated'}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {formatTimestamp(event.timestamp)}
                    </span>
                  </div>

                  {event.details && Object.keys(event.details).length > 0 && (
                    <div className="text-xs text-muted-foreground mt-2">
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
        </CardContent>
      </Card>

      {/* Info */}
      <div className="rounded-lg border border-border bg-muted/30 p-4">
        <p className="text-sm text-muted-foreground">
          The sync worker automatically uploads your skills to GitHub every 5 minutes. You can start/stop it
          manually or configure the interval. Events update in real-time via Server-Sent Events.
        </p>
      </div>
    </div>
  )
}

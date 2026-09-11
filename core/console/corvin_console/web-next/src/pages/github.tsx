/**
 * GitHub Integration Panel — Cross-Device-Learning Sync
 *
 * Location: /app/settings/github (single unified surface).
 *
 * Consolidation (2026-09-11): this used to be three separate pages —
 * this one (connect/verify), a standalone "Sync Monitor" (worker
 * start/stop + a Server-Sent-Events log that pointed at a route which
 * was never implemented, so it just reconnected every 5s forever), and
 * a standalone "Webhooks" page whose "Register Webhook" button never
 * called the GitHub API at all — it wrote a local placeholder file and
 * reported success unconditionally. Both of those routes now redirect
 * here (kept mounted for deep-link stability, dropped from the sidebar).
 *
 * There is exactly one sync mechanism: a polling worker (5-minute
 * interval, see routes/github_sync.py::GitHubSyncWorker). The
 * "Automatic Sync" toggle below is the single control for it — flipping
 * it persists `auto_sync` on the tenant's config AND starts/stops the
 * worker thread in the same request, so the two can never drift apart.
 * The backend also resumes sync for every tenant that had it enabled
 * before the last restart (app.py lifespan), so this switch reflects
 * what will actually happen, not just what's true until the next reboot.
 *
 * Features:
 * - URL input with format validation
 * - Real-time GitHub API connectivity check
 * - Sync status display (connected/disconnected/error)
 * - Automatic Sync toggle + live worker stats
 * - Disconnect button
 */

import { useState, useEffect, useRef, useCallback } from 'react'
import { Github, CheckCircle2, AlertCircle, Loader2, Trash2, Eye, EyeOff, Lock, RotateCw } from 'lucide-react'
import { fetchConsoleJson, fetchConsoleApi } from '@/lib/api-utils'
import { useAuth } from '@/lib/auth'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'

interface WorkerStatus {
  running: boolean
  interval_seconds: number
  last_sync?: string | null
  last_error?: string | null
  sync_count: number
  error_count: number
  uptime: string
}

interface GitHubStatus {
  connected: boolean
  configured: boolean
  owner?: string
  repo?: string
  url?: string
  auto_sync?: boolean
  last_verified?: string
  worker_status?: WorkerStatus
}

interface VerifyResult {
  connected: boolean
  details: {
    status: string
    error?: string
    repo_exists?: boolean
    repo_name?: string
    repo_url?: string
    repo_private?: boolean
    repo_description?: string
    rate_limit?: string
    http_code?: number
  }
}

const STATUS_POLL_MS = 15_000

export default function GitHubIntegrationPanel() {
  // Every mutation below carries the session's CSRF token (backend: require_csrf).
  const { session } = useAuth()
  const csrf = session?.csrf_token ?? ''
  const [url, setUrl] = useState('')
  const [token, setToken] = useState('')
  const [showToken, setShowToken] = useState(false)
  const [status, setStatus] = useState<GitHubStatus>({ connected: false, configured: false })
  const [isVerifying, setIsVerifying] = useState(false)
  const [verifyResult, setVerifyResult] = useState<VerifyResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isDirty, setIsDirty] = useState(false)
  const [autoSyncBusy, setAutoSyncBusy] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchStatus = useCallback(async () => {
    try {
      const data = await fetchConsoleJson<GitHubStatus>('/v1/console/github/status')
      setStatus(data)
    } catch (error) {
      console.error('Failed to fetch GitHub status:', error)
    }
  }, [])

  // Load current status on mount
  useEffect(() => {
    fetchStatus()
  }, [fetchStatus])

  // Poll worker stats while connected — there is no live event stream (the
  // previous "Sync Monitor" page connected to one that was never implemented
  // server-side), so this is the only source of truth for whether sync is
  // actually happening.
  useEffect(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
    if (status.connected) {
      pollRef.current = setInterval(fetchStatus, STATUS_POLL_MS)
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [status.connected, fetchStatus])

  // Format URL input to ensure https://github.com/owner/repo format
  const formatUrl = (input: string) => {
    let formatted = input.trim().toLowerCase()

    // Add https:// if missing
    if (!formatted.startsWith('http')) {
      formatted = `https://${formatted}`
    }

    // Ensure github.com
    if (!formatted.includes('github.com')) {
      return input
    }

    // Remove trailing slash
    formatted = formatted.replace(/\/$/, '')

    return formatted
  }

  const validateUrl = (input: string): string | null => {
    const pattern = /^https:\/\/github\.com\/[a-zA-Z0-9_-]+\/[a-zA-Z0-9_.-]+\/?$/
    if (!pattern.test(input)) {
      return 'Invalid GitHub URL format. Expected: https://github.com/owner/repo'
    }
    return null
  }

  const handleUrlChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const formatted = formatUrl(e.target.value)
    setUrl(formatted)
    setIsDirty(true)
    setError(null)
    setVerifyResult(null)
  }

  const handleVerify = async () => {
    // Validate URL format first
    const urlError = validateUrl(url)
    if (urlError) {
      setError(urlError)
      return
    }

    setIsVerifying(true)
    setError(null)
    setVerifyResult(null)

    try {
      const result: VerifyResult = await fetchConsoleJson('/v1/console/github/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        body: JSON.stringify({ url, token: token || undefined })
      })

      setVerifyResult(result)

      if (result.connected) {
        await fetchStatus()
        setIsDirty(false)
      } else if (result.details && result.details.error) {
        setError(result.details.error)
      } else {
        setError('Failed to connect to GitHub repository')
      }
    } catch (error) {
      setError(`Error: ${error instanceof Error ? error.message : 'Unknown error'}`)
    } finally {
      setIsVerifying(false)
    }
  }

  const handleDisconnect = async () => {
    if (!window.confirm('Disconnect from GitHub? Automatic sync will stop.')) {
      return
    }

    try {
      const response = await fetchConsoleApi('/v1/console/github/config', {
        method: 'DELETE',
        headers: { 'X-CSRF-Token': csrf },
      })
      if (response.ok) {
        setUrl('')
        setToken('')
        setStatus({ connected: false, configured: false })
        setVerifyResult(null)
        setIsDirty(false)
      }
    } catch (error) {
      setError(`Failed to disconnect: ${error}`)
    }
  }

  const handleToggleAutoSync = async (enabled: boolean) => {
    setAutoSyncBusy(true)
    try {
      const result = await fetchConsoleJson<{ auto_sync: boolean; worker_status: WorkerStatus }>(
        '/v1/console/github/auto-sync',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
          body: JSON.stringify({ enabled }),
        }
      )
      setStatus((prev) => ({ ...prev, auto_sync: result.auto_sync, worker_status: result.worker_status }))
      setError(null)
    } catch (error) {
      setError(`Failed to update automatic sync: ${error instanceof Error ? error.message : error}`)
    } finally {
      setAutoSyncBusy(false)
    }
  }

  const formatTimestamp = (ts?: string | null) => {
    if (!ts) return 'Never'
    try {
      return new Date(ts).toLocaleString()
    } catch {
      return ts
    }
  }

  const getStatusIcon = () => {
    if (status.connected) {
      return <CheckCircle2 className="text-emerald-600 dark:text-emerald-400" size={24} />
    } else if (error || verifyResult?.details?.status === 'error') {
      return <AlertCircle className="text-destructive" size={24} />
    } else if (isVerifying) {
      return <Loader2 className="text-accent animate-spin" size={24} />
    } else {
      return <Github className="text-muted-foreground" size={24} />
    }
  }

  const getStatusText = () => {
    if (status.connected) {
      return (
        <div>
          <p className="font-semibold text-emerald-600 dark:text-emerald-400">✓ Connected</p>
          <p className="text-sm text-muted-foreground">
            Repo: <code className="bg-muted px-2 py-1 rounded">{status.url}</code>
          </p>
          <p className="text-xs text-muted-foreground mt-1">
            Last verified: {status.last_verified ? new Date(status.last_verified).toLocaleString() : 'Never'}
          </p>
        </div>
      )
    } else if (error) {
      return (
        <div>
          <p className="font-semibold text-destructive">✗ Connection Failed</p>
          <p className="text-sm text-destructive">{error}</p>
        </div>
      )
    } else if (isVerifying) {
      return <p className="font-semibold text-accent">Verifying...</p>
    } else {
      return <p className="text-muted-foreground">Not connected</p>
    }
  }

  const worker = status.worker_status

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <Card>
        <CardContent className="p-6">
          {/* Header */}
          <div className="flex items-center gap-3 mb-6">
            {getStatusIcon()}
            <div>
              <h2 className="text-xl font-bold text-foreground">GitHub Integration</h2>
              <p className="text-sm text-muted-foreground">
                Connect your tenant to a GitHub repository for synchronized learning
              </p>
            </div>
          </div>

          {/* Status Display */}
          <div className="mb-6 p-4 bg-muted/40 rounded-lg border border-border">
            {getStatusText()}
          </div>

          {/* Connection Form */}
          <div className="space-y-4">
            {/* URL Input */}
            <div className="space-y-2">
              <Label htmlFor="gh-url">GitHub Repository URL</Label>
              <Input
                id="gh-url"
                type="text"
                value={url || status.url || ''}
                onChange={handleUrlChange}
                placeholder="https://github.com/owner/repo"
                disabled={status.connected && !isDirty}
              />
              <p className="text-xs text-muted-foreground">
                Required: https://github.com/owner/repo
              </p>
            </div>

            {/* Token Input */}
            <div className="space-y-2">
              <Label htmlFor="gh-repo-token">GitHub Personal Access Token (Optional)</Label>
              <div className="relative">
                <Input
                  id="gh-repo-token"
                  type={showToken ? 'text' : 'password'}
                  value={token}
                  onChange={(e) => {
                    setToken(e.target.value)
                    setIsDirty(true)
                    setError(null)
                  }}
                  placeholder="ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                  autoComplete="off"
                  spellCheck="false"
                  className="pr-10"
                />
                <button
                  type="button"
                  onClick={() => setShowToken(!showToken)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                >
                  {showToken ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
              <p className="text-xs text-muted-foreground">
                For higher API rate limits. Token is stored securely and never logged.
              </p>
            </div>

            {/* Verification Result */}
            {verifyResult && verifyResult.details && (
              <div className={
                verifyResult.connected
                  ? "p-4 rounded-lg border border-emerald-500/30 bg-emerald-500/10"
                  : "p-4 rounded-lg border border-destructive/40 bg-destructive/10"
              }>
                <div className="space-y-2">
                  <p className={verifyResult.connected ? "font-semibold text-emerald-700 dark:text-emerald-400" : "font-semibold text-destructive"}>
                    {verifyResult.connected ? '✓ Connected Successfully' : '✗ Connection Failed'}
                  </p>
                  {verifyResult.details?.repo_name && (
                    <p className="text-sm text-muted-foreground">
                      Repository: <strong className="text-foreground">{verifyResult.details.repo_name}</strong>
                    </p>
                  )}
                  {verifyResult.details?.repo_description && (
                    <p className="text-sm text-muted-foreground">
                      {verifyResult.details.repo_description}
                    </p>
                  )}
                  {verifyResult.details?.repo_private && (
                    <p className="text-xs text-muted-foreground flex items-center gap-1">
                      <Lock size={12} />Private repository
                    </p>
                  )}
                  <p className="text-xs text-muted-foreground">
                    API Rate Limit: {verifyResult.details?.rate_limit} remaining
                  </p>
                </div>
              </div>
            )}

            {/* Action Buttons */}
            <div className="flex gap-3 pt-4">
              {!status.connected ? (
                <Button
                  variant="accent"
                  className="flex-1"
                  onClick={handleVerify}
                  disabled={isVerifying || !url}
                >
                  {isVerifying ? 'Verifying...' : 'Connect Repository'}
                </Button>
              ) : (
                <>
                  <Button variant="destructive" onClick={handleDisconnect}>
                    <Trash2 size={18} />
                    Disconnect
                  </Button>
                  <Button
                    variant="secondary"
                    className="flex-1"
                    onClick={handleVerify}
                    disabled={isVerifying}
                  >
                    {isVerifying ? 'Verifying...' : 'Verify Connection'}
                  </Button>
                </>
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Automatic Sync — the one real sync mechanism */}
      {status.connected && (
        <Card>
          <CardContent className="p-6 space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="font-semibold text-foreground">Automatic Sync</h3>
                <p className="text-sm text-muted-foreground">
                  Uploads your tenant's skills to the repository every {worker ? Math.round(worker.interval_seconds / 60) : 5} minutes.
                  Resumes on its own after a server restart.
                </p>
              </div>
              <Switch
                checked={!!status.auto_sync}
                onCheckedChange={handleToggleAutoSync}
                disabled={autoSyncBusy}
              />
            </div>

            {worker && (
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <div className="bg-muted/40 p-3 rounded-lg">
                    <p className="text-xs text-muted-foreground">Worker</p>
                    <p className={cn("font-semibold", worker.running ? "text-emerald-600 dark:text-emerald-400" : "text-muted-foreground")}>
                      {worker.running ? '✓ Running' : 'Stopped'}
                    </p>
                  </div>
                  <div className="bg-muted/40 p-3 rounded-lg">
                    <p className="text-xs text-muted-foreground">Interval</p>
                    <p className="font-semibold text-foreground">{worker.interval_seconds}s</p>
                  </div>
                  <div className="bg-muted/40 p-3 rounded-lg">
                    <p className="text-xs text-muted-foreground">Syncs</p>
                    <p className="font-semibold text-foreground">
                      {worker.sync_count} success / {worker.error_count} errors
                    </p>
                  </div>
                  <div className="bg-muted/40 p-3 rounded-lg">
                    <p className="text-xs text-muted-foreground">Last Sync</p>
                    <p className="font-semibold text-foreground text-xs">{formatTimestamp(worker.last_sync)}</p>
                  </div>
                </div>

                {worker.last_error && (
                  <div className="p-3 rounded-lg border border-destructive/40 bg-destructive/10">
                    <p className="text-sm text-destructive">{worker.last_error}</p>
                  </div>
                )}

                <Button variant="secondary" size="sm" onClick={fetchStatus}>
                  <RotateCw size={14} />
                  Refresh
                </Button>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Footer Info */}
      <div className="rounded-lg border border-border bg-muted/30 p-4">
        <p className="text-xs text-muted-foreground">
          Sync is polling-based (checks every 5 minutes) — there is no GitHub webhook receiver, since that
          would require this console to be reachable from the public internet. Turn off Automatic Sync above
          to pause it without disconnecting the repository.
        </p>
      </div>
    </div>
  )
}

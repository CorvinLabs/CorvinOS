/**
 * GitHub Integration Panel — Cross-Device-Learning Sync
 *
 * Location: /app/settings/github (or modal in Skills Manager)
 *
 * Features:
 * - URL input with format validation
 * - Real-time GitHub API connectivity check
 * - Sync status display (connected/disconnected/error)
 * - Auto-sync toggle
 * - Disconnect button
 */

import { useState, useEffect } from 'react'
import { Github, CheckCircle2, AlertCircle, Loader2, Trash2, Eye, EyeOff, Lock } from 'lucide-react'
import { fetchConsoleJson, fetchConsoleApi } from '@/lib/api-utils'
import { useAuth } from '@/lib/auth'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

interface GitHubStatus {
  connected: boolean
  configured: boolean
  owner?: string
  repo?: string
  url?: string
  auto_sync?: boolean
  last_verified?: string
  last_sync?: string
  sync_status?: string
  sync_error?: string
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

  // Load current status on mount
  useEffect(() => {
    fetchStatus()
  }, [])

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

  const fetchStatus = async () => {
    try {
      const data = await fetchConsoleJson<GitHubStatus>('/v1/console/github/status')
      setStatus(data)
    } catch (error) {
      console.error('Failed to fetch GitHub status:', error)
    }
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
    if (!window.confirm('Disconnect from GitHub? Sync will be disabled.')) {
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

  return (
    <div className="max-w-2xl mx-auto">
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

          {/* Sync Status Details */}
          {status.connected && (
            <div className="mb-6 space-y-2 text-sm">
              <div className="flex justify-between items-center">
                <span className="text-muted-foreground">Auto-Sync:</span>
                <span className={status.auto_sync ? 'font-semibold text-emerald-600 dark:text-emerald-400' : 'font-semibold text-muted-foreground'}>
                  {status.auto_sync ? '✓ Enabled' : 'Disabled'}
                </span>
              </div>
              {status.last_sync && (
                <div className="flex justify-between items-center">
                  <span className="text-muted-foreground">Last Sync:</span>
                  <span className="text-foreground">
                    {new Date(status.last_sync).toLocaleString()}
                  </span>
                </div>
              )}
              {status.sync_status && (
                <div className="flex justify-between items-center">
                  <span className="text-muted-foreground">Sync Status:</span>
                  <span className={status.sync_status === 'success' ? 'font-semibold text-emerald-600 dark:text-emerald-400' : 'font-semibold text-amber-600 dark:text-amber-400'}>
                    {status.sync_status}
                  </span>
                </div>
              )}
            </div>
          )}

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

          {/* Footer Info */}
          <div className="mt-6 pt-4 border-t border-border">
            <p className="text-xs text-muted-foreground">
              When connected, your tenant will automatically sync skills and learning data with the GitHub repository.
              This enables cross-device learning synchronization.
            </p>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

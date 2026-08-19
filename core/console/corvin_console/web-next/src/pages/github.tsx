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
import { Github, CheckCircle, AlertCircle, Loader, Trash2 } from 'lucide-react'
import { fetchConsoleJson, fetchConsoleApi } from '@/lib/api-utils'

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
      const data = await fetchConsoleJson<GitHubStatus>('/api/console/github/status')
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
      const result: VerifyResult = await fetchConsoleJson('/api/console/github/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
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
      const response = await fetchConsoleApi('/api/console/github/config', { method: 'DELETE' })
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
      return <CheckCircle className="text-green-500" size={24} />
    } else if (error || verifyResult?.details?.status === 'error') {
      return <AlertCircle className="text-red-500" size={24} />
    } else if (isVerifying) {
      return <Loader className="text-blue-500 animate-spin" size={24} />
    } else {
      return <Github className="text-slate-400" size={24} />
    }
  }

  const getStatusText = () => {
    if (status.connected) {
      return (
        <div>
          <p className="font-semibold text-green-500">✓ Connected</p>
          <p className="text-sm text-slate-600 dark:text-slate-400">
            Repo: <code className="bg-slate-100 dark:bg-slate-900 px-2 py-1 rounded">{status.url}</code>
          </p>
          <p className="text-xs text-slate-500 mt-1">
            Last verified: {status.last_verified ? new Date(status.last_verified).toLocaleString() : 'Never'}
          </p>
        </div>
      )
    } else if (error) {
      return (
        <div>
          <p className="font-semibold text-red-500">✗ Connection Failed</p>
          <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
        </div>
      )
    } else if (isVerifying) {
      return <p className="font-semibold text-blue-500">Verifying...</p>
    } else {
      return <p className="text-slate-500">Not connected</p>
    }
  }

  return (
    <div className="max-w-2xl mx-auto">
      <div className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 p-6">
        {/* Header */}
        <div className="flex items-center gap-3 mb-6">
          {getStatusIcon()}
          <div>
            <h2 className="text-xl font-bold text-slate-900 dark:text-white">GitHub Integration</h2>
            <p className="text-sm text-slate-600 dark:text-slate-400">
              Connect your tenant to a GitHub repository for synchronized learning
            </p>
          </div>
        </div>

        {/* Status Display */}
        <div className="mb-6 p-4 bg-slate-50 dark:bg-slate-900/30 rounded-lg border border-slate-200 dark:border-slate-700">
          {getStatusText()}
        </div>

        {/* Sync Status Details */}
        {status.connected && (
          <div className="mb-6 space-y-2 text-sm">
            <div className="flex justify-between items-center">
              <span className="text-slate-600 dark:text-slate-400">Auto-Sync:</span>
              <span className={`font-semibold ${status.auto_sync ? 'text-green-600' : 'text-slate-500'}`}>
                {status.auto_sync ? '✓ Enabled' : 'Disabled'}
              </span>
            </div>
            {status.last_sync && (
              <div className="flex justify-between items-center">
                <span className="text-slate-600 dark:text-slate-400">Last Sync:</span>
                <span className="text-slate-900 dark:text-white">
                  {new Date(status.last_sync).toLocaleString()}
                </span>
              </div>
            )}
            {status.sync_status && (
              <div className="flex justify-between items-center">
                <span className="text-slate-600 dark:text-slate-400">Sync Status:</span>
                <span className={`font-semibold ${status.sync_status === 'success' ? 'text-green-600' : 'text-yellow-600'}`}>
                  {status.sync_status}
                </span>
              </div>
            )}
          </div>
        )}

        {/* Connection Form */}
        <div className="space-y-4">
          {/* URL Input */}
          <div>
            <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">
              GitHub Repository URL
            </label>
            <input
              type="text"
              value={url || status.url || ''}
              onChange={handleUrlChange}
              placeholder="https://github.com/owner/repo"
              className="w-full px-3 py-2 border border-slate-300 dark:border-slate-600 rounded-lg bg-white dark:bg-slate-700 text-slate-900 dark:text-white placeholder-slate-400 dark:placeholder-slate-500"
              disabled={status.connected && !isDirty}
            />
            <p className="text-xs text-slate-500 mt-1">
              Required: https://github.com/owner/repo
            </p>
          </div>

          {/* Token Input */}
          <div>
            <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">
              GitHub Personal Access Token (Optional)
            </label>
            <div className="relative">
              <input
                type={showToken ? 'text' : 'password'}
                value={token}
                onChange={(e) => {
                  setToken(e.target.value)
                  setIsDirty(true)
                }}
                placeholder="ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                className="w-full px-3 py-2 border border-slate-300 dark:border-slate-600 rounded-lg bg-white dark:bg-slate-700 text-slate-900 dark:text-white placeholder-slate-400 dark:placeholder-slate-500"
              />
              <button
                onClick={() => setShowToken(!showToken)}
                className="absolute right-3 top-2 text-slate-500 hover:text-slate-700 dark:hover:text-slate-300"
              >
                {showToken ? '✕' : '•'}
              </button>
            </div>
            <p className="text-xs text-slate-500 mt-1">
              For higher API rate limits. Token is stored securely and never logged.
            </p>
          </div>

          {/* Verification Result */}
          {verifyResult && verifyResult.details && (
            <div className={`p-4 rounded-lg border ${
              verifyResult.connected
                ? 'bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-800'
                : 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800'
            }`}>
              <div className="space-y-2">
                <p className={`font-semibold ${verifyResult.connected ? 'text-green-700 dark:text-green-400' : 'text-red-700 dark:text-red-400'}`}>
                  {verifyResult.connected ? '✓ Connected Successfully' : '✗ Connection Failed'}
                </p>
                {verifyResult.details?.repo_name && (
                  <p className="text-sm text-slate-600 dark:text-slate-400">
                    Repository: <strong>{verifyResult.details.repo_name}</strong>
                  </p>
                )}
                {verifyResult.details?.repo_description && (
                  <p className="text-sm text-slate-600 dark:text-slate-400">
                    {verifyResult.details.repo_description}
                  </p>
                )}
                {verifyResult.details?.repo_private && (
                  <p className="text-xs text-slate-500">🔒 Private repository</p>
                )}
                <p className="text-xs text-slate-500">
                  API Rate Limit: {verifyResult.details?.rate_limit} remaining
                </p>
              </div>
            </div>
          )}

          {/* Action Buttons */}
          <div className="flex gap-3 pt-4">
            {!status.connected ? (
              <button
                onClick={handleVerify}
                disabled={isVerifying || !url}
                className={`flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg font-medium transition ${
                  isVerifying || !url
                    ? 'opacity-50 cursor-not-allowed'
                    : 'hover:bg-blue-700'
                }`}
              >
                {isVerifying ? 'Verifying...' : 'Connect Repository'}
              </button>
            ) : (
              <>
                <button
                  onClick={handleDisconnect}
                  className="flex items-center gap-2 px-4 py-2 bg-red-600 text-white rounded-lg font-medium hover:bg-red-700 transition"
                >
                  <Trash2 size={18} />
                  Disconnect
                </button>
                <button
                  onClick={handleVerify}
                  disabled={isVerifying}
                  className="flex-1 px-4 py-2 bg-slate-200 dark:bg-slate-700 text-slate-900 dark:text-white rounded-lg font-medium hover:bg-slate-300 dark:hover:bg-slate-600 transition"
                >
                  {isVerifying ? 'Verifying...' : 'Verify Connection'}
                </button>
              </>
            )}
          </div>
        </div>

        {/* Footer Info */}
        <div className="mt-6 pt-4 border-t border-slate-200 dark:border-slate-700">
          <p className="text-xs text-slate-500 dark:text-slate-400">
            💡 When connected, your tenant will automatically sync skills and learning data with the GitHub repository.
            This enables cross-device learning synchronization.
          </p>
        </div>
      </div>
    </div>
  )
}

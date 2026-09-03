/**
 * GitHub Webhook Configuration Panel
 *
 * Features:
 * - Webhook registration via GitHub API
 * - Secret management
 * - Test webhook delivery
 * - Webhook status display
 * - Event filtering (push, PR, release)
 */

import { useState, useEffect } from 'react'
import { Zap, CheckCircle, Eye, EyeOff, Send } from 'lucide-react'
import { useAuth } from '@/lib/auth'

interface WebhookStatus {
  registered: boolean
  webhook_id?: string
  has_secret?: boolean
  events?: string[]
  url?: string
}

export default function WebhookConfigPanel() {
  // Every mutation below carries the session's CSRF token (backend: require_csrf).
  const { session } = useAuth()
  const csrf = session?.csrf_token ?? ''
  const [status, setStatus] = useState<WebhookStatus | null>(null)
  const [token, setToken] = useState('')
  const [secret, setSecret] = useState('')
  const [showSecret, setShowSecret] = useState(false)
  const [showToken, setShowToken] = useState(false)
  const [isRegistering, setIsRegistering] = useState(false)
  const [isTesting, setIsTesting] = useState(false)
  const [testEvent, setTestEvent] = useState('ping')
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  // Load webhook status on mount
  useEffect(() => {
    fetchWebhookStatus()
  }, [])

  const fetchWebhookStatus = async () => {
    try {
      const response = await fetch('/v1/console/github/webhook/status')
      if (response.ok) {
        const data: WebhookStatus = await response.json()
        setStatus(data)
      }
    } catch (error) {
      console.error('Failed to fetch webhook status:', error)
    }
  }

  const generateSecret = () => {
    const chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    let result = ''
    for (let i = 0; i < 32; i++) {
      result += chars.charAt(Math.floor(Math.random() * chars.length))
    }
    setSecret(result)
  }

  const handleRegister = async () => {
    if (!token) {
      setError('GitHub token is required')
      return
    }

    setIsRegistering(true)
    setError(null)
    setSuccess(null)

    try {
      const response = await fetch('/v1/console/github/webhook/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        body: JSON.stringify({
          token,
          webhook_secret: secret || undefined,
        })
      })

      const result = await response.json()

      if (response.ok) {
        setSuccess(`Webhook registered! ID: ${result.webhook_id}`)
        setToken('')
        setSecret('')
        fetchWebhookStatus()
      } else {
        setError(result.error || 'Failed to register webhook')
      }
    } catch (error) {
      setError(`Error: ${error}`)
    } finally {
      setIsRegistering(false)
    }
  }

  const handleTestWebhook = async () => {
    setIsTesting(true)
    setError(null)
    setSuccess(null)

    try {
      const response = await fetch('/v1/console/github/webhook/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        body: JSON.stringify({
          event_type: testEvent,
          secret: secret || undefined,
        })
      })

      const result = await response.json()

      if (result.success) {
        setSuccess(`Test webhook sent! Event: ${testEvent}`)
      } else {
        setError(result.error || 'Test webhook failed')
      }
    } catch (error) {
      setError(`Error: ${error}`)
    } finally {
      setIsTesting(false)
    }
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      {/* Status Card */}
      <div className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 p-6">
        <div className="flex items-center gap-3 mb-4">
          {status?.registered ? (
            <CheckCircle className="text-green-500" size={24} />
          ) : (
            <Zap className="text-slate-400" size={24} />
          )}
          <div>
            <h2 className="text-xl font-bold text-slate-900 dark:text-white">GitHub Webhooks</h2>
            <p className="text-sm text-slate-600 dark:text-slate-400">
              Event-driven synchronization from GitHub
            </p>
          </div>
        </div>

        {status?.registered ? (
          <div className="bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-lg p-4">
            <p className="text-sm font-semibold text-green-700 dark:text-green-400">
              ✓ Webhook Registered
            </p>
            <div className="mt-2 space-y-1 text-xs text-slate-600 dark:text-slate-400">
              <p>Webhook ID: <code className="bg-slate-100 dark:bg-slate-900 px-2 py-1 rounded">{status.webhook_id}</code></p>
              <p>Events: {status.events?.join(', ')}</p>
              <p>URL: <code className="bg-slate-100 dark:bg-slate-900 px-2 py-1 rounded text-xs truncate">{status.url}</code></p>
              <p>Secret: {status.has_secret ? '✓ Configured' : '✗ Not set'}</p>
            </div>
          </div>
        ) : (
          <div className="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg p-4">
            <p className="text-sm font-semibold text-blue-700 dark:text-blue-400">
              Webhook not registered
            </p>
            <p className="text-xs text-blue-600 dark:text-blue-300 mt-1">
              Register to enable event-driven sync from GitHub
            </p>
          </div>
        )}
      </div>

      {/* Registration Form */}
      <div className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 p-6">
        <h3 className="text-lg font-bold text-slate-900 dark:text-white mb-4">Register Webhook</h3>

        {error && (
          <div className="mb-4 p-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg">
            <p className="text-sm text-red-700 dark:text-red-400">{error}</p>
          </div>
        )}

        {success && (
          <div className="mb-4 p-4 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-lg">
            <p className="text-sm text-green-700 dark:text-green-400">{success}</p>
          </div>
        )}

        <div className="space-y-4">
          {/* Token Input */}
          <div>
            <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">
              GitHub Personal Access Token
            </label>
            <div className="relative">
              <input
                type={showToken ? 'text' : 'password'}
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                className="w-full px-3 py-2 border border-slate-300 dark:border-slate-600 rounded-lg bg-white dark:bg-slate-700 text-slate-900 dark:text-white placeholder-slate-400"
              />
              <button
                onClick={() => setShowToken(!showToken)}
                className="absolute right-3 top-2 text-slate-500 hover:text-slate-700"
              >
                {showToken ? <EyeOff size={18} /> : <Eye size={18} />}
              </button>
            </div>
            <p className="text-xs text-slate-500 mt-1">
              Token needs repo webhook permissions (admin:repo_hook)
            </p>
          </div>

          {/* Secret Input */}
          <div>
            <div className="flex justify-between items-center mb-2">
              <label className="text-sm font-medium text-slate-700 dark:text-slate-300">
                Webhook Secret (Optional)
              </label>
              <button
                onClick={generateSecret}
                className="text-xs px-2 py-1 bg-slate-200 dark:bg-slate-700 rounded hover:bg-slate-300 dark:hover:bg-slate-600"
              >
                Generate
              </button>
            </div>
            <div className="relative">
              <input
                type={showSecret ? 'text' : 'password'}
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
                placeholder="Optional webhook secret for verification"
                className="w-full px-3 py-2 border border-slate-300 dark:border-slate-600 rounded-lg bg-white dark:bg-slate-700 text-slate-900 dark:text-white placeholder-slate-400"
              />
              <button
                onClick={() => setShowSecret(!showSecret)}
                className="absolute right-3 top-2 text-slate-500 hover:text-slate-700"
              >
                {showSecret ? <EyeOff size={18} /> : <Eye size={18} />}
              </button>
            </div>
            <p className="text-xs text-slate-500 mt-1">
              Increases security. Webhook payloads will be signed with HMAC-SHA256.
            </p>
          </div>

          {/* Events Preview */}
          <div>
            <label className="text-sm font-medium text-slate-700 dark:text-slate-300 mb-2 block">
              Events to subscribe
            </label>
            <div className="space-y-1 text-sm">
              <label className="flex items-center gap-2">
                <input type="checkbox" checked disabled />
                <span>push</span>
              </label>
              <label className="flex items-center gap-2">
                <input type="checkbox" checked disabled />
                <span>pull_request</span>
              </label>
              <label className="flex items-center gap-2">
                <input type="checkbox" checked disabled />
                <span>release</span>
              </label>
            </div>
          </div>

          {/* Register Button */}
          <button
            onClick={handleRegister}
            disabled={isRegistering || !token}
            className={`w-full px-4 py-2 bg-blue-600 text-white rounded-lg font-medium transition ${
              isRegistering || !token
                ? 'opacity-50 cursor-not-allowed'
                : 'hover:bg-blue-700'
            }`}
          >
            {isRegistering ? 'Registering...' : 'Register Webhook'}
          </button>
        </div>
      </div>

      {/* Test Webhook */}
      {status?.registered && (
        <div className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 p-6">
          <h3 className="text-lg font-bold text-slate-900 dark:text-white mb-4">Test Webhook</h3>

          <div className="space-y-4">
            {/* Event Type Select */}
            <div>
              <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">
                Test Event Type
              </label>
              <select
                value={testEvent}
                onChange={(e) => setTestEvent(e.target.value)}
                className="w-full px-3 py-2 border border-slate-300 dark:border-slate-600 rounded-lg bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
              >
                <option value="ping">ping (Connection test)</option>
                <option value="push">push (Code pushed)</option>
                <option value="pull_request">pull_request (PR opened)</option>
                <option value="release">release (Version released)</option>
              </select>
            </div>

            {/* Test Button */}
            <button
              onClick={handleTestWebhook}
              disabled={isTesting}
              className={`w-full flex items-center justify-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg font-medium transition ${
                isTesting
                  ? 'opacity-50 cursor-not-allowed'
                  : 'hover:bg-green-700'
              }`}
            >
              <Send size={18} />
              {isTesting ? 'Sending...' : 'Send Test Event'}
            </button>
          </div>
        </div>
      )}

      {/* Info */}
      <div className="bg-slate-50 dark:bg-slate-900/30 border border-slate-200 dark:border-slate-700 rounded-lg p-4">
        <p className="text-sm text-slate-600 dark:text-slate-400">
          💡 <strong>How it works:</strong> When you push code or open a pull request on GitHub, we immediately sync your tenant skills. No waiting for the 5-minute poll interval.
        </p>
      </div>
    </div>
  )
}

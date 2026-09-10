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
import { Zap, CheckCircle2, Eye, EyeOff, Send, AlertTriangle } from 'lucide-react'
import { useAuth } from '@/lib/auth'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

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
      <Card>
        <CardHeader>
          <div className="flex items-center gap-3">
            {status?.registered ? (
              <CheckCircle2 className="h-6 w-6 text-emerald-600 dark:text-emerald-400" />
            ) : (
              <Zap className="h-6 w-6 text-muted-foreground" />
            )}
            <div>
              <CardTitle>GitHub Webhooks</CardTitle>
              <CardDescription>Event-driven synchronization from GitHub</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-0">
          {status?.registered ? (
            <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-4">
              <p className="flex items-center gap-1.5 text-sm font-semibold text-emerald-700 dark:text-emerald-400">
                <CheckCircle2 className="h-3.5 w-3.5" />Webhook registered
              </p>
              <div className="mt-2 space-y-1 text-xs text-muted-foreground">
                <p>Webhook ID: <code className="bg-muted px-2 py-0.5 rounded">{status.webhook_id}</code></p>
                <p>Events: {status.events?.join(', ')}</p>
                <p className="truncate">URL: <code className="bg-muted px-2 py-0.5 rounded text-xs">{status.url}</code></p>
                <p>Secret: {status.has_secret ? '✓ Configured' : '✗ Not set'}</p>
              </div>
            </div>
          ) : (
            <div className="rounded-lg border border-border bg-muted/40 p-4">
              <p className="text-sm font-semibold text-foreground">
                Webhook not registered
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                Register to enable event-driven sync from GitHub
              </p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Registration Form */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Register Webhook</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {error && (
            <div className="p-3 rounded-lg border border-destructive/40 bg-destructive/10 flex items-start gap-2">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5 text-destructive" />
              <p className="text-sm text-destructive">{error}</p>
            </div>
          )}

          {success && (
            <div className="p-3 rounded-lg border border-emerald-500/30 bg-emerald-500/10 flex items-start gap-2">
              <CheckCircle2 className="h-3.5 w-3.5 shrink-0 mt-0.5 text-emerald-600 dark:text-emerald-400" />
              <p className="text-sm text-emerald-700 dark:text-emerald-400">{success}</p>
            </div>
          )}

          {/* Token Input */}
          <div className="space-y-2">
            <Label htmlFor="gh-token">GitHub Personal Access Token</Label>
            <div className="relative">
              <Input
                id="gh-token"
                type={showToken ? 'text' : 'password'}
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
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
              Token needs repo webhook permissions (admin:repo_hook)
            </p>
          </div>

          {/* Secret Input */}
          <div className="space-y-2">
            <div className="flex justify-between items-center">
              <Label htmlFor="gh-secret">Webhook Secret (Optional)</Label>
              <Button variant="secondary" size="sm" className="h-7 text-xs" onClick={generateSecret}>
                Generate
              </Button>
            </div>
            <div className="relative">
              <Input
                id="gh-secret"
                type={showSecret ? 'text' : 'password'}
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
                placeholder="Optional webhook secret for verification"
                className="pr-10"
              />
              <button
                type="button"
                onClick={() => setShowSecret(!showSecret)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              >
                {showSecret ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
            <p className="text-xs text-muted-foreground">
              Increases security. Webhook payloads will be signed with HMAC-SHA256.
            </p>
          </div>

          {/* Events Preview */}
          <div className="space-y-2">
            <Label>Events to subscribe</Label>
            <div className="flex flex-wrap gap-1.5">
              <Badge variant="outline" className="text-xs">push</Badge>
              <Badge variant="outline" className="text-xs">pull_request</Badge>
              <Badge variant="outline" className="text-xs">release</Badge>
            </div>
          </div>

          {/* Register Button */}
          <Button
            variant="accent"
            className="w-full"
            onClick={handleRegister}
            disabled={isRegistering || !token}
          >
            {isRegistering ? 'Registering...' : 'Register Webhook'}
          </Button>
        </CardContent>
      </Card>

      {/* Test Webhook */}
      {status?.registered && (
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Test Webhook</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {/* Event Type Select */}
            <div className="space-y-2">
              <Label htmlFor="test-event">Test Event Type</Label>
              <select
                id="test-event"
                value={testEvent}
                onChange={(e) => setTestEvent(e.target.value)}
                className={cn(
                  "flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm",
                  "ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                )}
              >
                <option value="ping">ping (Connection test)</option>
                <option value="push">push (Code pushed)</option>
                <option value="pull_request">pull_request (PR opened)</option>
                <option value="release">release (Version released)</option>
              </select>
            </div>

            {/* Test Button */}
            <Button
              className="w-full"
              onClick={handleTestWebhook}
              disabled={isTesting}
            >
              <Send size={16} />
              {isTesting ? 'Sending...' : 'Send Test Event'}
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Info */}
      <div className="rounded-lg border border-border bg-muted/30 p-4">
        <p className="text-sm text-muted-foreground">
          <strong className="text-foreground">How it works:</strong> When you push code or open a pull request on GitHub, we immediately sync your tenant skills. No waiting for the 5-minute poll interval.
        </p>
      </div>
    </div>
  )
}

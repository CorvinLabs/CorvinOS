/**
 * Audit Viewer Panel — GDPR Art. 30, 32 Compliance
 *
 * Features:
 * - Real-time audit log viewer
 * - Hash chain verification
 * - Event search/filter
 * - Statistics dashboard
 * - Export capability
 */

import { useState, useEffect } from 'react'
import { Shield, AlertCircle, CheckCircle, Search, RefreshCw, Download } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

interface AuditEvent {
  timestamp: string
  event_type: string
  action: string
  subject: string
  tenant_id: string
  operator_id: string
  details: Record<string, unknown>
  hash: string
  previous_hash?: string
}

interface AuditStats {
  total_events: number
  chain_valid: boolean
  events_by_type: Record<string, number>
  time_range?: { first: string; last: string }
}

const selectClasses = cn(
  "flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm",
  "ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
)

export default function AuditViewerPanel() {
  const [stats, setStats] = useState<AuditStats | null>(null)
  const [events, setEvents] = useState<AuditEvent[]>([])
  const [searchQuery, setSearchQuery] = useState('')
  const [filterType, setFilterType] = useState<string | null>(null)
  const [filterAction, setFilterAction] = useState<string | null>(null)
  const [isVerifying, setIsVerifying] = useState(false)
  const [verifyResult, setVerifyResult] = useState<{valid: boolean; errors: string[]} | null>(null)
  const [loading, setLoading] = useState(false)

  // Load data on mount
  useEffect(() => {
    fetchStats()
    fetchEvents()
  }, [])

  const fetchStats = async () => {
    try {
      const response = await fetch('/api/console/audit/stats')
      const data = await response.json()
      setStats(data)
    } catch (error) {
      console.error('Failed to fetch audit stats:', error)
    }
  }

  const fetchEvents = async () => {
    setLoading(true)
    try {
      const response = await fetch('/api/console/audit/events?limit=100')
      const data = await response.json()
      setEvents(data.events || [])
    } catch (error) {
      console.error('Failed to fetch audit events:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleSearch = async () => {
    if (!searchQuery) {
      fetchEvents()
      return
    }

    setLoading(true)
    try {
      const response = await fetch('/api/console/audit/events/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          event_type: filterType,
          action: filterAction,
          subject_contains: searchQuery,
          limit: 100,
        })
      })

      const data = await response.json()
      setEvents(data.results || [])
    } catch (error) {
      console.error('Failed to search events:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleVerify = async () => {
    setIsVerifying(true)
    setVerifyResult(null)

    try {
      const response = await fetch('/api/console/audit/verify', { method: 'POST' })
      const data = await response.json()
      setVerifyResult({ valid: data.valid, errors: data.errors || [] })
    } catch (error) {
      setVerifyResult({ valid: false, errors: [`Error: ${error}`] })
    } finally {
      setIsVerifying(false)
    }
  }

  const handleExport = async () => {
    try {
      const response = await fetch('/api/console/audit/events?limit=10000')
      const data = await response.json()

      const csvContent = [
        ['Timestamp', 'Event Type', 'Action', 'Subject', 'Operator', 'Details', 'Hash'].join(','),
        ...data.events.map((e: AuditEvent) =>
          [
            e.timestamp,
            e.event_type,
            e.action,
            e.subject,
            e.operator_id,
            JSON.stringify(e.details).replace(/,/g, ';'),
            e.hash,
          ].join(',')
        ),
      ].join('\n')

      const blob = new Blob([csvContent], { type: 'text/csv' })
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `audit-log-${new Date().toISOString()}.csv`
      a.click()
    } catch (error) {
      console.error('Failed to export:', error)
    }
  }

  const formatTime = (ts: string) => {
    try {
      return new Date(ts).toLocaleString()
    } catch {
      return ts
    }
  }

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Statistics */}
      {stats && (
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <Card>
            <CardContent className="p-4 space-y-0">
              <p className="text-xs text-muted-foreground">Total Events</p>
              <p className="text-2xl font-bold text-foreground">
                {stats.total_events}
              </p>
            </CardContent>
          </Card>

          <Card className={cn(
            stats.chain_valid
              ? "border-emerald-500/30"
              : "border-destructive/40"
          )}>
            <CardContent className="p-4 space-y-0">
              <p className="text-xs text-muted-foreground">Chain Valid</p>
              <p className={cn(
                "text-2xl font-bold",
                stats.chain_valid
                  ? "text-emerald-600 dark:text-emerald-400"
                  : "text-destructive"
              )}>
                {stats.chain_valid ? '✓ Yes' : '✗ No'}
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="p-4 space-y-0">
              <p className="text-xs text-muted-foreground">Event Types</p>
              <p className="text-2xl font-bold text-foreground">
                {Object.keys(stats.events_by_type || {}).length}
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="p-4 space-y-0">
              <p className="text-xs text-muted-foreground">Time Range</p>
              <p className="text-xs font-mono text-muted-foreground">
                {stats.time_range ? `${stats.time_range.first.split('T')[0]}` : 'N/A'}
              </p>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Chain Verification */}
      <Card>
        <CardContent className="p-6 space-y-0">
          <div className="flex justify-between items-center mb-4">
            <h2 className="text-xl font-bold text-foreground flex items-center gap-2">
              <Shield size={20} />
              Chain Verification
            </h2>
            <Button variant="accent" onClick={handleVerify} disabled={isVerifying}>
              {isVerifying ? 'Verifying...' : 'Verify Chain'}
            </Button>
          </div>

          {verifyResult && (
            <div className={cn(
              "p-4 rounded-lg border",
              verifyResult.valid
                ? "bg-emerald-500/10 border-emerald-500/30"
                : "bg-destructive/10 border-destructive/40"
            )}>
              <div className="flex items-start gap-2">
                {verifyResult.valid ? (
                  <CheckCircle className="text-emerald-600 dark:text-emerald-400 flex-shrink-0" size={20} />
                ) : (
                  <AlertCircle className="text-destructive flex-shrink-0" size={20} />
                )}
                <div className="flex-1">
                  <p className={cn(
                    "font-semibold",
                    verifyResult.valid ? "text-emerald-700 dark:text-emerald-400" : "text-destructive"
                  )}>
                    {verifyResult.valid ? '✓ Chain Valid' : '✗ Chain Invalid'}
                  </p>
                  {verifyResult.errors.length > 0 && (
                    <ul className="text-xs text-destructive mt-2 space-y-1">
                      {verifyResult.errors.slice(0, 5).map((err, idx) => (
                        <li key={idx}>• {err}</li>
                      ))}
                      {verifyResult.errors.length > 5 && (
                        <li>• ... and {verifyResult.errors.length - 5} more</li>
                      )}
                    </ul>
                  )}
                </div>
              </div>
            </div>
          )}

          <p className="text-xs text-muted-foreground mt-4">
            Verifies SHA256 hash chain integrity (GDPR Art. 32). Each event is cryptographically signed.
          </p>
        </CardContent>
      </Card>

      {/* Event Search & Filter */}
      <Card>
        <CardContent className="p-6 space-y-4">
          <h3 className="text-lg font-bold text-foreground flex items-center gap-2">
            <Search size={20} />
            Search Events
          </h3>

          {/* Search Input */}
          <div className="flex gap-2">
            <Input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search by subject (e.g., github/owner/repo)"
              onKeyPress={(e) => e.key === 'Enter' && handleSearch()}
              className="flex-1"
            />
            <Button variant="accent" onClick={handleSearch}>
              Search
            </Button>
            <Button variant="secondary" onClick={handleExport}>
              <Download size={18} />
              Export CSV
            </Button>
          </div>

          {/* Filters */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            <select
              value={filterType || ''}
              onChange={(e) => setFilterType(e.target.value || null)}
              className={cn(selectClasses, "text-sm")}
            >
              <option value="">All Event Types</option>
              <option value="github_integration">GitHub Integration</option>
              <option value="sync">Sync</option>
              <option value="webhook">Webhook</option>
              <option value="config">Config</option>
            </select>

            <select
              value={filterAction || ''}
              onChange={(e) => setFilterAction(e.target.value || null)}
              className={cn(selectClasses, "text-sm")}
            >
              <option value="">All Actions</option>
              <option value="started">Started</option>
              <option value="completed">Completed</option>
              <option value="failed">Failed</option>
              <option value="connected">Connected</option>
            </select>
          </div>
        </CardContent>
      </Card>

      {/* Events Table */}
      <Card className="overflow-hidden">
        <div className="p-4 border-b border-border flex justify-between items-center">
          <h3 className="font-bold text-foreground">Recent Events ({events.length})</h3>
          <Button variant="ghost" size="sm" className="h-8 w-8 p-0" onClick={fetchEvents} disabled={loading}>
            <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
          </Button>
        </div>

        {events.length === 0 ? (
          <div className="p-8 text-center text-muted-foreground">
            No events found
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-muted/40 border-b border-border">
                <tr>
                  <th className="px-4 py-2 text-left">Timestamp</th>
                  <th className="px-4 py-2 text-left">Event Type</th>
                  <th className="px-4 py-2 text-left">Action</th>
                  <th className="px-4 py-2 text-left">Subject</th>
                  <th className="px-4 py-2 text-left">Operator</th>
                  <th className="px-4 py-2 font-mono text-xs">Hash</th>
                </tr>
              </thead>
              <tbody>
                {events.map((event, idx) => (
                  <tr key={idx} className="border-b border-border hover:bg-muted/20">
                    <td className="px-4 py-2 text-xs">{formatTime(event.timestamp)}</td>
                    <td className="px-4 py-2">
                      <Badge variant="accent" className="text-xs">
                        {event.event_type}
                      </Badge>
                    </td>
                    <td className="px-4 py-2">{event.action}</td>
                    <td className="px-4 py-2 text-xs font-mono">{event.subject}</td>
                    <td className="px-4 py-2 text-xs">{event.operator_id}</td>
                    <td className="px-4 py-2 font-mono text-xs text-muted-foreground truncate" title={event.hash}>
                      {event.hash.substring(0, 8)}...
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Footer */}
      <div className="bg-accent/10 border border-accent/30 rounded-lg p-4">
        <p className="text-sm text-accent-foreground/90 flex items-start gap-2">
          <Shield size={16} className="shrink-0 mt-0.5" />
          <span><strong>GDPR Compliance:</strong> All sync events are logged in a cryptographically signed audit trail.
          The SHA256 hash chain ensures tamper-detection. Daily verification recommended.</span>
        </p>
      </div>
    </div>
  )
}

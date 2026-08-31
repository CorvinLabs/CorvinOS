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

interface AuditEvent {
  timestamp: string
  event_type: string
  action: string
  subject: string
  tenant_id: string
  operator_id: string
  details: Record<string, any>
  hash: string
  previous_hash?: string
}

interface AuditStats {
  total_events: number
  chain_valid: boolean
  events_by_type: Record<string, number>
  time_range?: { first: string; last: string }
}

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
          <div className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 p-4">
            <p className="text-xs text-slate-600 dark:text-slate-400">Total Events</p>
            <p className="text-2xl font-bold text-slate-900 dark:text-white">
              {stats.total_events}
            </p>
          </div>

          <div className={`bg-white dark:bg-slate-800 rounded-lg border p-4 ${
            stats.chain_valid
              ? 'border-green-200 dark:border-green-800'
              : 'border-red-200 dark:border-red-800'
          }`}>
            <p className="text-xs text-slate-600 dark:text-slate-400">Chain Valid</p>
            <p className={`text-2xl font-bold ${
              stats.chain_valid
                ? 'text-green-600'
                : 'text-red-600'
            }`}>
              {stats.chain_valid ? '✓ Yes' : '✗ No'}
            </p>
          </div>

          <div className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 p-4">
            <p className="text-xs text-slate-600 dark:text-slate-400">Event Types</p>
            <p className="text-2xl font-bold text-slate-900 dark:text-white">
              {Object.keys(stats.events_by_type || {}).length}
            </p>
          </div>

          <div className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 p-4">
            <p className="text-xs text-slate-600 dark:text-slate-400">Time Range</p>
            <p className="text-xs font-mono text-slate-600 dark:text-slate-400">
              {stats.time_range ? `${stats.time_range.first.split('T')[0]}` : 'N/A'}
            </p>
          </div>
        </div>
      )}

      {/* Chain Verification */}
      <div className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 p-6">
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-xl font-bold text-slate-900 dark:text-white flex items-center gap-2">
            <Shield size={20} />
            Chain Verification
          </h2>
          <button
            onClick={handleVerify}
            disabled={isVerifying}
            className={`px-4 py-2 bg-blue-600 text-white rounded-lg font-medium transition ${
              isVerifying ? 'opacity-50 cursor-not-allowed' : 'hover:bg-blue-700'
            }`}
          >
            {isVerifying ? 'Verifying...' : 'Verify Chain'}
          </button>
        </div>

        {verifyResult && (
          <div className={`p-4 rounded-lg border ${
            verifyResult.valid
              ? 'bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-800'
              : 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800'
          }`}>
            <div className="flex items-start gap-2">
              {verifyResult.valid ? (
                <CheckCircle className="text-green-600 flex-shrink-0" size={20} />
              ) : (
                <AlertCircle className="text-red-600 flex-shrink-0" size={20} />
              )}
              <div className="flex-1">
                <p className={`font-semibold ${verifyResult.valid ? 'text-green-700 dark:text-green-400' : 'text-red-700 dark:text-red-400'}`}>
                  {verifyResult.valid ? '✓ Chain Valid' : '✗ Chain Invalid'}
                </p>
                {verifyResult.errors.length > 0 && (
                  <ul className="text-xs text-red-600 dark:text-red-400 mt-2 space-y-1">
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

        <p className="text-xs text-slate-600 dark:text-slate-400 mt-4">
          💡 Verifies SHA256 hash chain integrity (GDPR Art. 32). Each event is cryptographically signed.
        </p>
      </div>

      {/* Event Search & Filter */}
      <div className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 p-6">
        <h3 className="text-lg font-bold text-slate-900 dark:text-white mb-4 flex items-center gap-2">
          <Search size={20} />
          Search Events
        </h3>

        <div className="space-y-4">
          {/* Search Input */}
          <div className="flex gap-2">
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search by subject (e.g., github/owner/repo)"
              onKeyPress={(e) => e.key === 'Enter' && handleSearch()}
              className="flex-1 px-3 py-2 border border-slate-300 dark:border-slate-600 rounded-lg bg-white dark:bg-slate-700 text-slate-900 dark:text-white"
            />
            <button
              onClick={handleSearch}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700"
            >
              Search
            </button>
            <button
              onClick={handleExport}
              className="px-4 py-2 bg-slate-200 dark:bg-slate-700 text-slate-900 dark:text-white rounded-lg font-medium hover:bg-slate-300 dark:hover:bg-slate-600 flex items-center gap-2"
            >
              <Download size={18} />
              Export CSV
            </button>
          </div>

          {/* Filters */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            <select
              value={filterType || ''}
              onChange={(e) => setFilterType(e.target.value || null)}
              className="px-3 py-2 border border-slate-300 dark:border-slate-600 rounded-lg bg-white dark:bg-slate-700 text-slate-900 dark:text-white text-sm"
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
              className="px-3 py-2 border border-slate-300 dark:border-slate-600 rounded-lg bg-white dark:bg-slate-700 text-slate-900 dark:text-white text-sm"
            >
              <option value="">All Actions</option>
              <option value="started">Started</option>
              <option value="completed">Completed</option>
              <option value="failed">Failed</option>
              <option value="connected">Connected</option>
            </select>
          </div>
        </div>
      </div>

      {/* Events Table */}
      <div className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 overflow-hidden">
        <div className="p-4 border-b border-slate-200 dark:border-slate-700 flex justify-between items-center">
          <h3 className="font-bold text-slate-900 dark:text-white">Recent Events ({events.length})</h3>
          <button
            onClick={fetchEvents}
            disabled={loading}
            className="px-2 py-1 text-sm hover:bg-slate-100 dark:hover:bg-slate-700 rounded"
          >
            <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>

        {events.length === 0 ? (
          <div className="p-8 text-center text-slate-600 dark:text-slate-400">
            No events found
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 dark:bg-slate-900/50 border-b border-slate-200 dark:border-slate-700">
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
                  <tr key={idx} className="border-b border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-900/30">
                    <td className="px-4 py-2 text-xs">{formatTime(event.timestamp)}</td>
                    <td className="px-4 py-2">
                      <span className="px-2 py-1 bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 rounded text-xs">
                        {event.event_type}
                      </span>
                    </td>
                    <td className="px-4 py-2">{event.action}</td>
                    <td className="px-4 py-2 text-xs font-mono">{event.subject}</td>
                    <td className="px-4 py-2 text-xs">{event.operator_id}</td>
                    <td className="px-4 py-2 font-mono text-xs text-slate-600 dark:text-slate-400 truncate" title={event.hash}>
                      {event.hash.substring(0, 8)}...
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg p-4">
        <p className="text-sm text-blue-700 dark:text-blue-400">
          🔐 <strong>GDPR Compliance:</strong> All sync events are logged in a cryptographically signed audit trail.
          The SHA256 hash chain ensures tamper-detection. Daily verification recommended.
        </p>
      </div>
    </div>
  )
}

import { useEffect, useState, useCallback } from 'react';
import { Loader2, AlertCircle, Download, Filter, ChevronLeft, ChevronRight } from 'lucide-react';

interface AuditEvent {
  timestamp: string;
  event_type: string;
  user_id_redacted: string;
  outcome: 'allowed' | 'denied' | 'error';
  details?: string;
}

interface AuditResponse {
  events: AuditEvent[];
  total: number;
  limit: number;
  offset: number;
}

const EVENT_TYPE_FILTERS = ['loaded', 'verified', 'denied', 'error'] as const;
type EventTypeFilter = typeof EVENT_TYPE_FILTERS[number];

const getOutcomeColor = (outcome: string) => {
  if (outcome === 'allowed') return 'bg-green-500/20 text-green-700';
  if (outcome === 'denied') return 'bg-red-500/20 text-red-700';
  return 'bg-yellow-500/20 text-yellow-700';
};

const formatTimestamp = (ts: string): string => {
  try {
    const date = new Date(ts);
    return date.toLocaleString('en-US', {
      year: '2-digit', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit'
    });
  } catch {
    return ts;
  }
};

export function LicensingAuditTab() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [totalCount, setTotalCount] = useState(0);
  const [currentPage, setCurrentPage] = useState(0);
  const [selectedFilter, setSelectedFilter] = useState<EventTypeFilter | 'all'>('all');
  const [sortDesc, setSortDesc] = useState(true);

  const PAGE_SIZE = 50;
  const offset = currentPage * PAGE_SIZE;

  const fetchEvents = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({
        limit: PAGE_SIZE.toString(),
        offset: offset.toString(),
        sort: sortDesc ? 'timestamp_desc' : 'timestamp_asc',
      });

      if (selectedFilter !== 'all') {
        params.append('event_type', selectedFilter);
      }

      const res = await fetch(`/v1/console/v1/licensing/audit-events?${params.toString()}`);
      if (!res.ok) {
        throw new Error(`API ${res.status}: ${res.statusText}`);
      }

      const data: AuditResponse = await res.json();
      setEvents(data.events || []);
      setTotalCount(data.total || 0);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load audit events');
    } finally {
      setLoading(false);
    }
  }, [offset, sortDesc, selectedFilter]);

  useEffect(() => {
    fetchEvents();
  }, [fetchEvents]);

  const handleExportCSV = useCallback(() => {
    if (events.length === 0) {
      alert('No events to export');
      return;
    }

    const headers = ['Timestamp', 'Event Type', 'User ID (Redacted)', 'Outcome', 'Details'];
    const rows = events.map((e) => [
      formatTimestamp(e.timestamp),
      e.event_type,
      e.user_id_redacted,
      e.outcome,
      e.details || '',
    ]);

    const csv = [
      headers.join(','),
      ...rows.map((r) => r.map((v) => `"${v}"`).join(',')),
    ].join('\n');

    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `audit-events-${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
  }, [events]);

  const totalPages = Math.ceil(totalCount / PAGE_SIZE);
  const canPrevious = currentPage > 0;
  const canNext = currentPage < totalPages - 1;

  if (loading && events.length === 0) {
    return (
      <div className="flex justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-4 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold">Licensing Audit Events</h3>
          <p className="text-sm text-muted-foreground">
            All licensing and verification events ({totalCount.toLocaleString()} total)
          </p>
        </div>
        <button
          onClick={handleExportCSV}
          className="flex items-center gap-2 px-3 py-2 rounded bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90"
        >
          <Download className="h-4 w-4" />
          Export CSV
        </button>
      </div>

      {/* Error Alert */}
      {error && (
        <div className="flex items-center gap-2 p-3 rounded bg-red-500/10 text-red-700 text-sm border border-red-500/20">
          <AlertCircle className="h-4 w-4" />
          {error}
        </div>
      )}

      {/* Filters */}
      <div className="flex items-center gap-2 flex-wrap">
        <Filter className="h-4 w-4 text-muted-foreground" />
        <button
          onClick={() => { setSelectedFilter('all'); setCurrentPage(0); }}
          className={`px-3 py-1 rounded text-sm font-medium transition-all ${
            selectedFilter === 'all'
              ? 'bg-primary text-primary-foreground'
              : 'bg-muted text-muted-foreground hover:bg-muted/80'
          }`}
        >
          All Events
        </button>
        {EVENT_TYPE_FILTERS.map((filter) => (
          <button
            key={filter}
            onClick={() => { setSelectedFilter(filter); setCurrentPage(0); }}
            className={`px-3 py-1 rounded text-sm font-medium transition-all capitalize ${
              selectedFilter === filter
                ? 'bg-primary text-primary-foreground'
                : 'bg-muted text-muted-foreground hover:bg-muted/80'
            }`}
          >
            {filter}
          </button>
        ))}
        <button
          onClick={() => setSortDesc(!sortDesc)}
          className="px-3 py-1 rounded text-sm font-medium bg-muted text-muted-foreground hover:bg-muted/80"
          title={sortDesc ? 'Newest first' : 'Oldest first'}
        >
          {sortDesc ? '↓ Newest' : '↑ Oldest'}
        </button>
      </div>

      {/* Table */}
      <div className="border rounded-lg overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-muted/50 border-b">
            <tr>
              <th className="px-4 py-3 text-left font-medium">Timestamp</th>
              <th className="px-4 py-3 text-left font-medium">Event Type</th>
              <th className="px-4 py-3 text-left font-medium">User (Redacted)</th>
              <th className="px-4 py-3 text-left font-medium">Outcome</th>
              <th className="px-4 py-3 text-left font-medium">Details</th>
            </tr>
          </thead>
          <tbody>
            {events.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-muted-foreground">
                  No audit events found
                </td>
              </tr>
            ) : (
              events.map((event, idx) => (
                <tr key={`${event.timestamp}-${idx}`} className="border-b hover:bg-muted/30">
                  <td className="px-4 py-3 text-xs font-mono">{formatTimestamp(event.timestamp)}</td>
                  <td className="px-4 py-3">{event.event_type}</td>
                  <td className="px-4 py-3 font-mono text-xs">{event.user_id_redacted}</td>
                  <td className="px-4 py-3">
                    <span className={`px-2 py-1 rounded text-xs font-medium ${getOutcomeColor(event.outcome)}`}>
                      {event.outcome}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs text-muted-foreground max-w-xs truncate">
                    {event.details || '—'}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <div className="flex items-center justify-between">
        <div className="text-sm text-muted-foreground">
          Page {currentPage + 1} of {Math.max(1, totalPages)} • Showing {events.length} of {totalCount.toLocaleString()} total
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setCurrentPage(Math.max(0, currentPage - 1))}
            disabled={!canPrevious}
            className="flex items-center gap-1 px-3 py-2 rounded text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed bg-muted text-muted-foreground hover:bg-muted/80"
          >
            <ChevronLeft className="h-4 w-4" />
            Previous
          </button>
          <button
            onClick={() => setCurrentPage(Math.min(totalPages - 1, currentPage + 1))}
            disabled={!canNext}
            className="flex items-center gap-1 px-3 py-2 rounded text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed bg-muted text-muted-foreground hover:bg-muted/80"
          >
            Next
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div className="text-xs text-muted-foreground pt-2 border-t">
        Source: Licensing Audit Store • PII-safe (redacted) • Last updated: {new Date().toLocaleTimeString()}
      </div>
    </div>
  );
}

export default LicensingAuditTab;

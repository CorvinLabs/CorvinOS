import { useEffect, useState } from 'react';
import { Loader2, AlertCircle } from 'lucide-react';

interface AuditEvent {
  id: string;
  timestamp: string;
  event_type: string;
  status: string;
  user_id: string;
  reason?: string;
}

export function LicensingAuditTab() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchEvents = async () => {
      try {
        const res = await fetch('/v1/licensing/audit-events?limit=50');
        if (!res.ok) throw new Error(`API ${res.status}`);
        const data = await res.json();
        setEvents(data.events || []);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load');
      } finally {
        setLoading(false);
      }
    };

    fetchEvents();
    const interval = setInterval(fetchEvents, 30000); // Refresh every 30s
    return () => clearInterval(interval);
  }, []);

  if (loading) return <div className="flex justify-center py-8"><Loader2 className="h-5 w-5 animate-spin" /></div>;

  return (
    <div className="space-y-4 p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold">Audit Events (PII Redacted)</h3>
        {error && <span className="flex items-center gap-2 text-xs text-red-500"><AlertCircle className="h-4 w-4" />{error}</span>}
      </div>

      {events.length === 0 ? (
        <div className="text-sm text-muted-foreground">No events yet</div>
      ) : (
        <div className="border rounded-lg overflow-hidden">
          <table className="w-full text-xs">
            <thead className="bg-muted border-b">
              <tr>
                <th className="px-3 py-2 text-left">Timestamp</th>
                <th className="px-3 py-2 text-left">Event Type</th>
                <th className="px-3 py-2 text-left">Status</th>
                <th className="px-3 py-2 text-left">User (Redacted)</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {events.map((e) => (
                <tr key={e.id} className="hover:bg-muted/50">
                  <td className="px-3 py-2">{new Date(e.timestamp).toLocaleString()}</td>
                  <td className="px-3 py-2">{e.event_type}</td>
                  <td className="px-3 py-2">
                    <span className={`px-2 py-1 rounded text-xs font-medium ${
                      e.status === 'granted' ? 'bg-green-500/20 text-green-700' : 'bg-red-500/20 text-red-700'
                    }`}>
                      {e.status}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">{e.user_id}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="text-xs text-muted-foreground">
        Compliance: ADR-0297 PII filtering applied • Last sync: {new Date().toLocaleTimeString()}
      </div>
    </div>
  );
}

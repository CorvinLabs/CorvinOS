/**
 * Licensing audit tab — content-free learning records (ADR-0856).
 *
 * Previously showed a granted/denied `status` the API derived from
 * `signal > 0.7` on a dict, and a "User (Redacted)" column fed by a hardcoded
 * "[REDACTED]" literal — a column that displayed the same constant for every
 * row while implying a redaction step had run on real data.
 */
import { useEffect, useState } from 'react';
import { Loader2, AlertCircle } from 'lucide-react';

interface AuditEvent {
  id: string;
  timestamp: string;
  event_type: string;
  skill_id: string;
  outcome: string | null;
  lom: string;
  audit_ref: string | null;
}

export function LicensingAuditTab() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [note, setNote] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchEvents = async () => {
      try {
        const res = await fetch('/v1/console/v1/licensing/audit-events?limit=50');
        if (!res.ok) throw new Error(`API ${res.status}`);
        const data = await res.json();
        setEvents(data.events || []);
        setNote(data.available === false ? (data.detail || 'Event store not available on this build.') : '');
        setError(null);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load');
      } finally {
        setLoading(false);
      }
    };

    fetchEvents();
    const interval = setInterval(fetchEvents, 30000);
    return () => clearInterval(interval);
  }, []);

  if (loading) return <div className="flex justify-center py-8"><Loader2 className="h-5 w-5 animate-spin" /></div>;

  return (
    <div className="space-y-4 p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold">Audit Events</h3>
        {error && <span className="flex items-center gap-2 text-xs text-red-500"><AlertCircle className="h-4 w-4" />{error}</span>}
      </div>

      {note && <div className="text-sm text-muted-foreground">{note}</div>}

      {events.length === 0 ? (
        <div className="text-sm text-muted-foreground">No events yet</div>
      ) : (
        <div className="border rounded-lg overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="bg-muted border-b">
              <tr>
                <th className="px-3 py-2 text-left">Timestamp</th>
                <th className="px-3 py-2 text-left">Event Type</th>
                <th className="px-3 py-2 text-left">Skill</th>
                <th className="px-3 py-2 text-left">Outcome</th>
                <th className="px-3 py-2 text-left">Chain Ref</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {events.map((e) => (
                <tr key={e.id} className="hover:bg-muted/50">
                  <td className="px-3 py-2 whitespace-nowrap">
                    {e.timestamp ? new Date(e.timestamp).toLocaleString('en-US') : '—'}
                  </td>
                  <td className="px-3 py-2">{e.event_type || '—'}</td>
                  <td className="px-3 py-2 font-mono">{e.skill_id || '—'}</td>
                  <td className="px-3 py-2">
                    {e.outcome ? (
                      <span className={`px-2 py-1 rounded text-xs font-medium ${
                        e.outcome === 'success' || e.outcome === 'completed'
                          ? 'bg-green-500/20 text-green-700'
                          : 'bg-red-500/20 text-red-700'
                      }`}>
                        {e.outcome}
                      </span>
                    ) : (
                      <span className="text-muted-foreground">{'—'}</span>
                    )}
                  </td>
                  <td className="px-3 py-2 font-mono text-muted-foreground">
                    {e.audit_ref ? `${e.audit_ref.substring(0, 8)}...` : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="text-xs text-muted-foreground">
        Records are content-free by construction: ids, type, skill and a hash-chain
        reference, never payloads or user identity.
      </div>
    </div>
  );
}

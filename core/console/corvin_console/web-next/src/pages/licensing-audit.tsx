/**
 * Licensing Audit — the tenant's learning events, content-free (ADR-0856).
 *
 * Rebuilt 2026-09-16. The previous page displayed a `status` of
 * granted/denied/expired and a `user_id` column. Neither existed: the API
 * derived "granted" from `e.signal > 0.7` where `signal` is a dict, and it
 * hardcoded user_id to "[REDACTED]" for every row. The fetch also omitted the
 * /v1/console prefix, so it 404'd and the table was always empty.
 *
 * What the store really holds per record: event type, skill id, an outcome for
 * outcome records, the Line of Moral Responsibility and an audit_ref into the
 * hash chain. No user identity — the disk record is content-free by
 * construction, which is the actual compliance property worth showing.
 */
import { useState, useEffect } from "react";
import { Lock, Download, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";

interface AuditEvent {
  id: string;
  timestamp: string;
  event_type: string;
  skill_id: string;
  outcome: string | null;
  lom: string;
  audit_ref: string | null;
}

const OUTCOME_CLASS: Record<string, string> = {
  success: "bg-green-100 text-green-800",
  completed: "bg-green-100 text-green-800",
  failure: "bg-red-100 text-red-800",
  failed: "bg-red-100 text-red-800",
};

export function LicensingAuditPage() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [note, setNote] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [outcomeFilter, setOutcomeFilter] = useState<string>("all");

  useEffect(() => {
    void fetchAuditEvents();
    const interval = setInterval(() => void fetchAuditEvents(), 30000);
    return () => clearInterval(interval);
  }, []);

  const fetchAuditEvents = async () => {
    setLoading(true);
    try {
      const response = await fetch("/v1/console/v1/licensing/audit-events?limit=200");
      if (!response.ok) {
        setEvents([]);
        setNote(`Request failed (HTTP ${response.status}).`);
        return;
      }
      const data = await response.json();
      setEvents(data.events || []);
      setNote(
        data.available === false
          ? data.detail || "Event store not available on this build."
          : "",
      );
    } catch (error) {
      console.error("Failed to fetch audit events:", error);
      setEvents([]);
      setNote("Request failed.");
    } finally {
      setLoading(false);
    }
  };

  const handleExport = () => {
    const csv = [
      ["ID", "Timestamp", "Event Type", "Skill", "Outcome", "LoM", "Audit Ref"],
      ...events.map((e) => [
        e.id,
        e.timestamp,
        e.event_type,
        e.skill_id,
        e.outcome ?? "",
        e.lom,
        e.audit_ref ?? "",
      ]),
    ]
      .map((row) => row.map((cell) => `"${String(cell).replace(/"/g, '""')}"`).join(","))
      .join("\n");

    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `licensing-audit-${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const needle = filter.toLowerCase();
  const filtered = events.filter((e) => {
    const matchesFilter =
      needle === "" ||
      e.id.toLowerCase().includes(needle) ||
      e.event_type.toLowerCase().includes(needle) ||
      (e.skill_id || "").toLowerCase().includes(needle) ||
      (e.lom || "").toLowerCase().includes(needle);
    const matchesOutcome = outcomeFilter === "all" || e.outcome === outcomeFilter;
    return matchesFilter && matchesOutcome;
  });

  const outcomes = Array.from(
    new Set(events.map((e) => e.outcome).filter((o): o is string => !!o)),
  ).sort();

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Lock className="h-6 w-6" />
          <h1 className="text-2xl font-bold">Licensing Audit</h1>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => void fetchAuditEvents()} disabled={loading}>
            <RefreshCw className="h-4 w-4 mr-2" />
            Refresh
          </Button>
          <Button variant="outline" size="sm" onClick={handleExport} disabled={events.length === 0}>
            <Download className="h-4 w-4 mr-2" />
            Export
          </Button>
        </div>
      </div>

      <Card className="p-4">
        <div className="space-y-4">
          <div className="flex gap-4">
            <Input
              placeholder="Filter by id, type, skill or LoM..."
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              className="flex-1"
            />
            <select
              value={outcomeFilter}
              onChange={(e) => setOutcomeFilter(e.target.value)}
              className="px-3 py-2 border rounded-md"
            >
              <option value="all">All outcomes</option>
              {outcomes.map((o) => (
                <option key={o} value={o}>{o}</option>
              ))}
            </select>
          </div>

          {note && <div className="text-sm text-muted-foreground">{note}</div>}

          {loading ? (
            <div className="text-center py-8 text-muted-foreground">
              Loading audit events...
            </div>
          ) : (
            <div className="border rounded-lg overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-muted border-b">
                  <tr>
                    <th className="px-4 py-2 text-left">ID</th>
                    <th className="px-4 py-2 text-left">Timestamp</th>
                    <th className="px-4 py-2 text-left">Event Type</th>
                    <th className="px-4 py-2 text-left">Skill</th>
                    <th className="px-4 py-2 text-left">Outcome</th>
                    <th className="px-4 py-2 text-left">Chain Ref</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.length > 0 ? (
                    filtered.map((event) => (
                      <tr key={event.id} className="border-b hover:bg-muted/50">
                        <td className="px-4 py-2 font-mono text-xs">
                          {event.id ? `${event.id.substring(0, 8)}...` : "—"}
                        </td>
                        <td className="px-4 py-2 text-xs whitespace-nowrap">
                          {event.timestamp
                            ? new Date(event.timestamp).toLocaleString("en-US")
                            : "—"}
                        </td>
                        <td className="px-4 py-2">{event.event_type || "—"}</td>
                        <td className="px-4 py-2 font-mono text-xs">{event.skill_id || "—"}</td>
                        <td className="px-4 py-2">
                          {event.outcome ? (
                            <Badge
                              className={OUTCOME_CLASS[event.outcome] ?? ""}
                              variant="outline"
                            >
                              {event.outcome}
                            </Badge>
                          ) : (
                            <span className="text-muted-foreground">{"—"}</span>
                          )}
                        </td>
                        <td className="px-4 py-2 font-mono text-xs text-muted-foreground">
                          {event.audit_ref ? `${event.audit_ref.substring(0, 8)}...` : "—"}
                        </td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={6} className="text-center py-8 text-muted-foreground">
                        No audit events found
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}

          <div className="text-sm text-muted-foreground">
            Showing {filtered.length} of {events.length} events. Records are content-free:
            they carry ids, type, skill and a chain reference, never payloads or user identity.
          </div>
        </div>
      </Card>
    </div>
  );
}

export default LicensingAuditPage;

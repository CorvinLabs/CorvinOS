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
  status: "granted" | "denied" | "expired";
  user_id: string;
  reason?: string;
}

export function LicensingAuditPage() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | AuditEvent["status"]>("all");

  useEffect(() => {
    fetchAuditEvents();
    const interval = setInterval(fetchAuditEvents, 30000);
    return () => clearInterval(interval);
  }, []);

  const fetchAuditEvents = async () => {
    setLoading(true);
    try {
      const response = await fetch("/v1/licensing/audit-events");
      const data = await response.json();
      setEvents(data.events || []);
    } catch (error) {
      console.error("Failed to fetch audit events:", error);
      setEvents([]);
    } finally {
      setLoading(false);
    }
  };

  const handleExport = () => {
    const csv = [
      ["ID", "Timestamp", "Event Type", "Status", "User ID", "Reason"],
      ...events.map((e) => [
        e.id,
        e.timestamp,
        e.event_type,
        e.status,
        e.user_id,
        e.reason || "",
      ]),
    ]
      .map((row) => row.map((cell) => `"${cell}"`).join(","))
      .join("\n");

    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `licensing-audit-${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const filtered = events.filter((e) => {
    const matchesFilter =
      e.id.includes(filter) ||
      e.event_type.includes(filter) ||
      e.user_id.includes(filter);
    const matchesStatus = statusFilter === "all" || e.status === statusFilter;
    return matchesFilter && matchesStatus;
  });

  const statusBadgeColor = {
    granted: "bg-green-100 text-green-800",
    denied: "bg-red-100 text-red-800",
    expired: "bg-yellow-100 text-yellow-800",
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Lock className="h-6 w-6" />
          <h1 className="text-2xl font-bold">Licensing Audit</h1>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={fetchAuditEvents}
            disabled={loading}
          >
            <RefreshCw className="h-4 w-4 mr-2" />
            Refresh
          </Button>
          <Button variant="outline" size="sm" onClick={handleExport}>
            <Download className="h-4 w-4 mr-2" />
            Export
          </Button>
        </div>
      </div>

      <Card className="p-4">
        <div className="space-y-4">
          <div className="flex gap-4">
            <Input
              placeholder="Filter by ID, type, or user..."
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              className="flex-1"
            />
            <select
              value={statusFilter}
              onChange={(e) =>
                setStatusFilter(e.target.value as typeof statusFilter)
              }
              className="px-3 py-2 border rounded-md"
            >
              <option value="all">All Statuses</option>
              <option value="granted">Granted</option>
              <option value="denied">Denied</option>
              <option value="expired">Expired</option>
            </select>
          </div>

          {loading ? (
            <div className="text-center py-8 text-muted-foreground">
              Loading audit events...
            </div>
          ) : (
            <div className="border rounded-lg overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-muted border-b">
                  <tr>
                    <th className="px-4 py-2 text-left">ID</th>
                    <th className="px-4 py-2 text-left">Timestamp</th>
                    <th className="px-4 py-2 text-left">Event Type</th>
                    <th className="px-4 py-2 text-left">Status</th>
                    <th className="px-4 py-2 text-left">User ID</th>
                    <th className="px-4 py-2 text-left">Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.length > 0 ? (
                    filtered.map((event) => (
                      <tr key={event.id} className="border-b hover:bg-muted/50">
                        <td className="px-4 py-2 font-mono text-xs">
                          {event.id.substring(0, 8)}...
                        </td>
                        <td className="px-4 py-2 text-xs">
                          {new Date(event.timestamp).toLocaleString()}
                        </td>
                        <td className="px-4 py-2">{event.event_type}</td>
                        <td className="px-4 py-2">
                          <Badge
                            className={statusBadgeColor[event.status]}
                            variant="outline"
                          >
                            {event.status}
                          </Badge>
                        </td>
                        <td className="px-4 py-2">{event.user_id}</td>
                        <td className="px-4 py-2 text-muted-foreground">
                          {event.reason || "—"}
                        </td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={6} className="text-center py-8">
                        No audit events found
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}

          <div className="text-sm text-muted-foreground">
            Showing {filtered.length} of {events.length} events
          </div>
        </div>
      </Card>
    </div>
  );
}

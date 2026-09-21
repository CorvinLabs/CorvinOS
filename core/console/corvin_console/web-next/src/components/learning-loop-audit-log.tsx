import React, { useState } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Filter, Download } from "lucide-react";

interface AuditEvent {
  timestamp: string;
  event_type: string;
  skill_id?: string;
  signal?: string;
  outcome?: string;
  metadata?: Record<string, unknown>;
}

interface Props {
  events: AuditEvent[];
  loopId: string;
}

export const LearningLoopAuditLog: React.FC<Props> = ({ events, loopId }) => {
  const [filterText, setFilterText] = useState("");
  const [filterType, setFilterType] = useState<string | null>(null);

  const filtered = events.filter((e) => {
    if (filterText && !e.event_type.toLowerCase().includes(filterText.toLowerCase())) return false;
    if (filterType && e.event_type !== filterType) return false;
    return true;
  });

  const eventTypes = Array.from(new Set(events.map((e) => e.event_type)));

  const exportJSON = () => {
    const data = { loop_id: loopId, events: filtered, exported_at: new Date().toISOString() };
    const json = JSON.stringify(data, null, 2);
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${loopId}-events-${new Date().toISOString().split("T")[0]}.json`;
    a.click();
  };

  const exportCSV = () => {
    const headers = ["timestamp", "event_type", "skill_id", "signal", "outcome"];
    const rows = filtered.map((e) => [
      e.timestamp,
      e.event_type,
      e.skill_id || "",
      e.signal || "",
      e.outcome || "",
    ]);
    const csv = [headers, ...rows].map((r) => r.map((v) => `"${v}"`).join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${loopId}-events-${new Date().toISOString().split("T")[0]}.csv`;
    a.click();
  };

  return (
    <div className="space-y-4">
      <div className="flex gap-2 items-center flex-wrap">
        <div className="flex-1 min-w-48">
          <Input
            placeholder="Filter by event type..."
            value={filterText}
            onChange={(e) => setFilterText(e.target.value)}
            className="h-9"
          />
        </div>
        <Button variant="outline" size="sm" onClick={exportJSON}>
          <Download className="h-4 w-4 mr-1" />
          JSON
        </Button>
        <Button variant="outline" size="sm" onClick={exportCSV}>
          <Download className="h-4 w-4 mr-1" />
          CSV
        </Button>
      </div>

      <div className="flex gap-1 flex-wrap">
        <Badge
          variant={filterType === null ? "default" : "outline"}
          className="cursor-pointer"
          onClick={() => setFilterType(null)}
        >
          All ({events.length})
        </Badge>
        {eventTypes.map((type) => {
          const count = events.filter((e) => e.event_type === type).length;
          return (
            <Badge
              key={type}
              variant={filterType === type ? "default" : "outline"}
              className="cursor-pointer"
              onClick={() => setFilterType(type)}
            >
              {type} ({count})
            </Badge>
          );
        })}
      </div>

      <div className="border rounded-lg max-h-96 overflow-y-auto space-y-1 p-2">
        {filtered.length === 0 ? (
          <div className="text-center text-gray-500 py-4">No events match filter</div>
        ) : (
          filtered.map((event, i) => (
            <div key={i} className="border rounded p-2 bg-gray-50 dark:bg-gray-900 text-sm">
              <div className="flex justify-between items-start">
                <div>
                  <p className="font-medium text-gray-900 dark:text-gray-100">{event.event_type}</p>
                  <p className="text-xs text-gray-600 dark:text-gray-400">
                    {new Date(event.timestamp).toLocaleString()}
                  </p>
                </div>
                {event.outcome && (
                  <Badge
                    variant="outline"
                    className={event.outcome === "success" ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"}
                  >
                    {event.outcome}
                  </Badge>
                )}
              </div>
              <div className="text-xs text-gray-600 dark:text-gray-400 mt-1">
                {event.skill_id && <span>Skill: {event.skill_id}</span>}
                {event.signal && <span className="ml-2">Signal: {event.signal}</span>}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
};

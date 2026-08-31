import { useState } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { ChevronDown, ChevronUp, Code } from 'lucide-react';

interface DebugPanelProps {
  data: any;
}

export function DebugPanel({ data }: DebugPanelProps) {
  const [expanded, setExpanded] = useState(false);

  if (!data?.debug) {
    return null;
  }

  const { events_count, latest_event, all_events } = data.debug;

  return (
    <Card className="border-yellow-400/50 bg-yellow-500/5">
      <CardHeader
        className="pb-2 cursor-pointer hover:bg-yellow-500/10"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Code className="h-4 w-4 text-yellow-600" />
            <CardTitle className="text-sm">DEBUG: Real Data Inspector</CardTitle>
          </div>
          {expanded ? (
            <ChevronUp className="h-4 w-4" />
          ) : (
            <ChevronDown className="h-4 w-4" />
          )}
        </div>
        <p className="text-xs text-yellow-600 mt-1">{events_count} real events loaded from session logs</p>
      </CardHeader>

      {expanded && (
        <CardContent className="space-y-3">
          {/* Latest Event */}
          <div className="p-3 bg-secondary/30 rounded border border-yellow-400/30">
            <p className="text-xs font-mono font-bold mb-1">Latest Event:</p>
            <pre className="text-[10px] overflow-x-auto whitespace-pre-wrap break-words">
              {JSON.stringify(latest_event, null, 2)}
            </pre>
          </div>

          {/* Event Count Stats */}
          <div className="grid grid-cols-2 gap-2">
            <div className="p-2 bg-secondary/20 rounded text-center">
              <p className="text-2xl font-bold">{events_count}</p>
              <p className="text-xs text-muted-foreground">Total Events</p>
            </div>
            <div className="p-2 bg-secondary/20 rounded text-center">
              <p className="text-2xl font-bold">{all_events?.length || 0}</p>
              <p className="text-xs text-muted-foreground">Visible</p>
            </div>
          </div>

          {/* All Events (Scrollable) */}
          <div className="max-h-48 overflow-y-auto border border-yellow-400/20 rounded p-2 bg-black/20">
            <p className="text-xs font-mono font-bold mb-2">All Events:</p>
            <div className="space-y-1">
              {all_events?.slice(0, 20).map((e: any, i: number) => (
                <div key={i} className="text-[9px] font-mono p-1 bg-secondary/20 rounded">
                  <span className="text-yellow-600">[{e.seq || i}]</span>{' '}
                  <span className="text-blue-400">{e.event}</span>{' '}
                  <span className="text-muted-foreground">({e.persona || e.engine || '?'})</span>
                </div>
              ))}
            </div>
          </div>

          <p className="text-xs text-yellow-600 italic">
            💡 Tip: Open browser console to inspect &apos;window.__VIBE_DEBUG__&apos;
          </p>
        </CardContent>
      )}
    </Card>
  );
}

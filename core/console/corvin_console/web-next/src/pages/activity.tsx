/**
 * Universal Activity Hub — Chat as Kommandozentrale.
 *
 * Shows all actions performed from the Chat that were registered in other
 * console panels (Compute, Data Sources, Forge, Skills, …).
 */
import * as React from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Activity, BarChart3, BookOpen, Cpu, Database,
  Globe, RefreshCw, Users, Wrench, Zap,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { getActivityFeed, type ActivityEntry } from "@/lib/api";

// ── Panel icon + destination link mapping ────────────────────────────────────

const PANEL_META: Record<string, { icon: React.ElementType; href: string; color: string }> = {
  compute:     { icon: Cpu,      href: "/app/compute",      color: "text-purple-400" },
  datasources: { icon: Database, href: "/app/data-sources", color: "text-blue-400" },
  forge:       { icon: Wrench,   href: "/app/forge",        color: "text-amber-400" },
  skills:      { icon: BookOpen, href: "/app/skills",       color: "text-green-400" },
  a2a:         { icon: Globe,    href: "/app/agent-hub",    color: "text-cyan-400" },
  orgs:        { icon: Users,    href: "/app/orgs",         color: "text-rose-400" },
  workflows:   { icon: Zap,      href: "/app/workflows",    color: "text-indigo-400" },
};

const PANEL_FILTERS = [
  { key: null,          label: "All" },
  { key: "compute",     label: "Compute" },
  { key: "datasources", label: "Data Sources" },
  { key: "forge",       label: "Forge" },
  { key: "skills",      label: "Skills" },
  { key: "a2a",         label: "A2A" },
  { key: "orgs",        label: "Orgs" },
  { key: "workflows",   label: "Workflows" },
];

// ── Time helper ──────────────────────────────────────────────────────────────

function timeAgo(ts: number): string {
  const diff = Date.now() / 1000 - ts;
  if (diff < 60)     return "just now";
  if (diff < 3600)   return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400)  return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

// ── Single activity card ─────────────────────────────────────────────────────

function ActivityCard({ entry }: { entry: ActivityEntry }) {
  const meta = PANEL_META[entry.panel];
  const Icon = meta?.icon ?? Activity;
  const iconColor = meta?.color ?? "text-gray-400";

  const chatLabel = entry.chat_key
    ? entry.chat_key.split(":").pop()?.slice(-6) ?? entry.chat_key
    : "?";

  return (
    <Card className="border-border/50 hover:border-border transition-colors">
      <CardContent className="p-4">
        <div className="flex items-start gap-3">
          <div className={`mt-0.5 shrink-0 ${iconColor}`}>
            <Icon className="h-4 w-4" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-medium text-foreground truncate">
                {entry.summary}
              </span>
              <Badge variant="secondary" className="text-xs shrink-0">
                {entry.action_label || entry.action}
              </Badge>
            </div>
            <div className="flex items-center gap-2 mt-1 flex-wrap">
              {meta ? (
                <Link
                  to={meta.href}
                  className="text-xs text-muted-foreground hover:text-accent transition-colors"
                >
                  → {entry.panel_label}
                </Link>
              ) : (
                <span className="text-xs text-muted-foreground">{entry.panel}</span>
              )}
              <span className="text-xs text-muted-foreground">·</span>
              <span className="text-xs text-muted-foreground font-mono">
                Chat #{chatLabel}
              </span>
              <span className="text-xs text-muted-foreground">·</span>
              <span className="text-xs text-muted-foreground">
                {timeAgo(entry.ts)}
              </span>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ── Skeleton loader ──────────────────────────────────────────────────────────

function ActivitySkeleton() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 6 }).map((_, i) => (
        <Card key={i} className="border-border/50">
          <CardContent className="p-4">
            <div className="flex items-start gap-3">
              <Skeleton className="h-4 w-4 mt-0.5 rounded" />
              <div className="flex-1 space-y-2">
                <Skeleton className="h-4 w-3/4" />
                <Skeleton className="h-3 w-1/2" />
              </div>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

// ── Main page ────────────────────────────────────────────────────────────────

export function ActivityFeedPage() {
  const [panel, setPanel] = React.useState<string | null>(null);

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["activity-feed", panel],
    queryFn: ({ signal }) => getActivityFeed({ limit: 200, panel: panel ?? undefined }, signal),
    refetchInterval: 15_000,
  });

  const items = data?.items ?? [];

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <BarChart3 className="h-5 w-5 text-accent" />
            <h1 className="text-xl font-semibold">Activity Feed</h1>
          </div>
          <p className="text-sm text-muted-foreground">
            All actions triggered from Chat — synced across every panel.
          </p>
        </div>
        <Button
          variant="ghost"
          size="icon"
          onClick={() => void refetch()}
          disabled={isFetching}
        >
          <RefreshCw className={`h-4 w-4 ${isFetching ? "animate-spin" : ""}`} />
        </Button>
      </div>

      {/* Panel filter chips */}
      <div className="flex flex-wrap gap-2">
        {PANEL_FILTERS.map(({ key, label }) => (
          <button
            key={key ?? "all"}
            onClick={() => setPanel(key)}
            className={`px-3 py-1 rounded-full text-xs font-medium border transition-colors ${
              panel === key
                ? "bg-accent text-accent-foreground border-accent"
                : "bg-transparent text-muted-foreground border-border hover:border-accent/50"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Content */}
      {isLoading ? (
        <ActivitySkeleton />
      ) : error ? (
        <Card className="border-destructive/30">
          <CardContent className="p-4 text-sm text-destructive">
            Failed to load activity feed.
          </CardContent>
        </Card>
      ) : items.length === 0 ? (
        <Card className="border-border/50">
          <CardContent className="p-8 text-center text-muted-foreground">
            <Activity className="h-8 w-8 mx-auto mb-3 opacity-30" />
            <p className="text-sm">No chat-initiated activity yet.</p>
            <p className="text-xs mt-1">
              Run a compute job, register a data source, or create a skill from the Chat — it will appear here.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-2">
          {items.map((entry, i) => (
            <ActivityCard key={`${entry.ts}-${i}`} entry={entry} />
          ))}
        </div>
      )}

      {items.length > 0 && (
        <p className="text-xs text-muted-foreground text-center">
          {items.length} {items.length === 1 ? "entry" : "entries"} — refreshes every 15 s
        </p>
      )}
    </div>
  );
}

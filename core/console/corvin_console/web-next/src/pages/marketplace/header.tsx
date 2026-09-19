/**
 * Marketplace header — identical on every tab (ADR-0892 D1): the four counts
 * an operator orients by (index size, installed plugins, packages, MCP tools),
 * each read from its own real backend and each rendered as "—" until that
 * backend answered or "unavailable" when it did not (never a fabricated 0).
 * The caption is the deploy marker (see tabs.ts).
 */
import { Blocks } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { Card, CardContent } from "@/components/ui/card";
import { MARKER_HEADER, type TabId } from "./tabs";
import { getIndexStats, isUnavailable, listInstalledPlugins, listPackages, listTools } from "./api";

export const KEY_INDEX = ["marketplace", "index"] as const;
export const KEY_STATS = ["marketplace", "stats"] as const;
export const KEY_INSTALLED = ["marketplace", "installed"] as const;
export const KEY_PACKAGES = ["marketplace", "packages"] as const;
export const KEY_TOOLS = ["marketplace", "tools"] as const;

function Count({ label, value, onClick, testId }: {
  label: string; value: string; onClick: () => void; testId: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="text-left rounded-md px-2 py-1 hover:bg-muted"
      data-testid={testId}
      title={`Open ${label}`}
    >
      <span className="text-muted-foreground">{label}:</span>{" "}
      <span className="font-mono font-medium tabular-nums">{value}</span>
    </button>
  );
}

/** "—" while loading, "unavailable" on an error that is NOT a "not on this
 *  build" answer (those are a real count of nothing to manage: "n/a"). */
function fmt(q: { isLoading: boolean; isError: boolean; error: unknown }, n: number | undefined): string {
  if (q.isLoading) return "—";
  if (q.isError) return isUnavailable(q.error) ? "n/a" : "unavailable";
  return n === undefined ? "—" : String(n);
}

export function MarketplaceHeader({ onGoTo }: { onGoTo: (tab: TabId) => void }) {
  const stats = useQuery({ queryKey: [...KEY_STATS], queryFn: ({ signal }) => getIndexStats(signal), retry: false });
  const installed = useQuery({ queryKey: [...KEY_INSTALLED], queryFn: ({ signal }) => listInstalledPlugins(signal), retry: false });
  const packages = useQuery({ queryKey: [...KEY_PACKAGES], queryFn: ({ signal }) => listPackages(signal), retry: false });
  const tools = useQuery({ queryKey: [...KEY_TOOLS], queryFn: ({ signal }) => listTools(signal), retry: false });

  return (
    <div className="space-y-4">
      <div>
        <div className="flex items-center gap-3 mb-1">
          <Blocks className="w-8 h-8 text-accent" />
          <h1 className="text-3xl font-bold">Marketplace</h1>
        </div>
        <p className="text-muted-foreground">{MARKER_HEADER}</p>
      </div>

      <Card>
        <CardContent className="py-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
          <Count label="In the index" value={fmt(stats, stats.data?.total_plugins)} onClick={() => onGoTo("browse")} testId="count-index" />
          <Count label="Installed plugins" value={fmt(installed, installed.data?.total)} onClick={() => onGoTo("installed")} testId="count-installed" />
          <Count label="Skill packages" value={fmt(packages, packages.data?.total)} onClick={() => onGoTo("packages")} testId="count-packages" />
          <Count label="MCP tools" value={fmt(tools, tools.data?.count)} onClick={() => onGoTo("tools")} testId="count-tools" />
          {stats.data?.generated_at && (
            <span className="text-xs text-muted-foreground ml-auto">
              Index generated {new Date(stats.data.generated_at).toLocaleString("en-US")}
            </span>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

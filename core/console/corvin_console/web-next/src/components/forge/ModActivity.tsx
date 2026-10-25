import { useQuery } from "@tanstack/react-query";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Puzzle } from "lucide-react";
import {
  describeWindow,
  emptyMessage,
  formatScore,
  formatTime,
  type ModActivityResponse,
} from "@/lib/mod-activity";

/** Thrown for a 404 so the card can say "not available on this build" instead of showing an error. */
class NotAvailable extends Error {}

async function fetchModActivity(): Promise<ModActivityResponse> {
  const res = await fetch("/v1/console/forge/mod-activity", { credentials: "same-origin" });
  if (res.status === 404) throw new NotAvailable("not available");
  if (!res.ok) throw new Error(`Mod activity request failed (${res.status})`);
  return (await res.json()) as ModActivityResponse;
}

/**
 * Recent actions reported by Claude Code mods, read from this tenant's audit chain.
 *
 * The list is a WINDOW over the end of the chain and says so; the entries are reported by the mod itself and
 * are not independently verified. Nothing is shown when the endpoint is missing — never sample data.
 */
export default function ModActivity() {
  const { data, error, isLoading } = useQuery({
    queryKey: ["forge", "mod-activity"],
    queryFn: fetchModActivity,
    retry: false,
    refetchInterval: 30_000,
  });

  return (
    <Card data-testid="mod-activity">
      <CardContent className="py-4 space-y-3">
        <div className="flex items-center gap-2">
          <Puzzle className="w-4 h-4 text-accent" />
          <h3 className="font-semibold">Mod activity</h3>
          <Badge variant="outline" className="text-[10px]">
            reported by the mod
          </Badge>
        </div>

        {isLoading && <p className="text-sm text-muted-foreground">Loading mod activity…</p>}

        {error instanceof NotAvailable && (
          <p className="text-sm text-muted-foreground">Mod activity is not available on this build.</p>
        )}
        {error && !(error instanceof NotAvailable) && (
          <p className="text-sm text-destructive" role="alert">
            {(error as Error).message}
          </p>
        )}

        {data && (
          <>
            <p className="text-xs text-muted-foreground">{describeWindow(data.window)}</p>
            {data.events.length === 0 ? (
              <p className="text-sm text-muted-foreground">{emptyMessage(data.window)}</p>
            ) : (
              <ul className="divide-y divide-border text-sm">
                {data.events.map((e, i) => (
                  <li key={`${e.ts}-${i}`} className="py-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
                    <span className="font-mono text-xs text-muted-foreground">{formatTime(e.ts)}</span>
                    <span>{e.mod ?? "unknown mod"}</span>
                    <Badge variant="secondary" className="text-[10px]">
                      {e.command ?? "unknown command"}
                    </Badge>
                    <span className="text-muted-foreground">{e.skill ?? "unknown skill"}</span>
                    <span className="ml-auto tabular-nums">{formatScore(e.score)}</span>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

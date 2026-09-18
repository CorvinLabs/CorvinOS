/**
 * Models console header — identical on every tab (ADR-0885 D1).
 *
 * Three things every tab presupposes: how Claude Code is authenticated, the
 * turn pins that actually serve (ADR-0759), and the ONE counting window with
 * its two actions (ADR-0760: "Reset counters to now" moves a timestamp,
 * "Show full history" removes it — nothing is ever deleted). The caption is
 * the deploy marker (see tabs.ts).
 */
import { Clock, Database } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useAuth } from "@/lib/auth";
import { useState } from "react";
import { ClaudeCodeAuthStatus } from "./components/engine-parts";
import { usePins } from "./hooks/use-pins";
import { useUsageWindow } from "./hooks/use-usage-window";
import { useCostOptimizerStatus } from "./hooks/use-cost-status";
import { MARKER_HEADER } from "./tabs";
import type { TabId } from "./tabs";

const shortId = (id: string | null, whenNull: string) =>
  id ? id.replace(/^claude-/, "").replace(/-\d{8}$/, "") : whenNull;

export function ModelsHeader({ onGoTo }: { onGoTo: (tab: TabId) => void }) {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const status = useCostOptimizerStatus(false);
  const { pins, loading: pinsLoading, error: pinsError } = usePins(status.data);
  // Fabricate nothing: "—" until the setting answered; a null OS pin means the
  // adaptive default, a null worker pin means the engine's default (the API's
  // own words), and a failed read says so.
  const osLabel = pinsError ? "unavailable" : !pins ? "—" : shortId(pins.os_model, "adaptive");
  const workerLabel = pinsError ? "unavailable" : !pins ? "—" : shortId(pins.worker_model, "engine default");
  const win = useUsageWindow(csrf);
  const [confirm, setConfirm] = useState(false);
  const w = win.window;

  return (
    <div className="space-y-4">
      <div>
        <div className="flex items-center gap-3 mb-1">
          <Database className="w-8 h-8 text-accent" />
          <h1 className="text-3xl font-bold">Models</h1>
        </div>
        <p className="text-muted-foreground">{MARKER_HEADER}</p>
      </div>

      <ClaudeCodeAuthStatus />

      <Card>
        <CardContent className="py-3 flex flex-wrap items-center justify-between gap-x-6 gap-y-2 text-sm">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-1">
            <span>
              <span className="text-muted-foreground">OS turn:</span>{" "}
              <button
                type="button"
                className="font-mono font-medium hover:underline"
                onClick={() => onGoTo("routing")}
                title="Change on the Routing tab"
              >
                {osLabel}
              </button>
            </span>
            <span>
              <span className="text-muted-foreground">Worker turn:</span>{" "}
              <button
                type="button"
                className="font-mono font-medium hover:underline"
                onClick={() => onGoTo("routing")}
                title="Change on the Routing tab"
              >
                {workerLabel}
              </button>
            </span>
            <span>
              <span className="text-muted-foreground">Engine:</span>{" "}
              <span className="font-mono font-medium">{pinsLoading ? "—" : pins?.default_engine ?? "—"}</span>
            </span>
          </div>
          <div className="flex items-center gap-2">
            <Clock size={15} className="text-muted-foreground shrink-0" />
            {w?.active ? (
              <span>
                Counting since{" "}
                <span className="font-medium">
                  {new Date(w.since_iso as string).toLocaleString("en-US")}
                </span>
              </span>
            ) : (
              <span className="text-muted-foreground">Counting window: full history</span>
            )}
            <Button
              size="sm"
              variant={confirm ? "destructive" : "outline"}
              disabled={win.reset.isPending}
              onClick={() => {
                if (!confirm) { setConfirm(true); return; }
                win.reset.mutate(undefined, { onSettled: () => setConfirm(false) });
              }}
            >
              {confirm ? "Click again to confirm" : "Reset counters to now"}
            </Button>
            {confirm && (
              <Button size="sm" variant="ghost" onClick={() => setConfirm(false)}>
                Cancel
              </Button>
            )}
            {w?.active && !confirm && (
              <Button size="sm" variant="ghost" disabled={win.clear.isPending}
                      onClick={() => win.clear.mutate()}>
                Show full history
              </Button>
            )}
          </div>
          {pinsError && (
            <p className="w-full text-xs text-destructive">The engine settings could not be loaded — the pins above are unknown, not empty.</p>
          )}
          <p className="w-full text-xs text-muted-foreground">
            This only moves the counting window every tab counts over. The audit trail is
            append-only and hash-chained — nothing is deleted, and “Show full history”
            brings every turn back.
            {(win.reset.isError || win.clear.isError) && (
              <span className="text-destructive"> The window could not be changed.</span>
            )}
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

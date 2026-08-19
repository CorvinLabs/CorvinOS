import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Boxes, Info, Layers, Power, Wand2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  applyLddPreset,
  getLdd,
  setLddLayer,
  setLddMaster,
  getQualityLayers,
  setQualityLayer,
  setQualityLayerMaster,
  type LddLayer,
  type QualityLayer,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";

type PendingAction =
  | { kind: "master"; enabled: boolean }
  | { kind: "layer"; layer: string; enabled: boolean }
  | { kind: "preset"; name: string }
  | { kind: "quality-master"; enabled: boolean }
  | { kind: "quality-layer"; layer: string; enabled: boolean };

export function LddPage() {
  const { session } = useAuth();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["ldd"],
    queryFn: ({ signal }) => getLdd(signal),
    refetchInterval: 60_000,
  });
  const qQuality = useQuery({
    queryKey: ["quality-layers"],
    queryFn: ({ signal }) => getQualityLayers(signal),
    refetchInterval: 60_000,
  });

  const [toast, setToast] = React.useState<{ kind: "ok" | "err"; msg: string } | null>(null);

  const mutation = useMutation({
    mutationFn: async ({ action }: { action: PendingAction }) => {
      if (action.kind === "master") {
        return setLddMaster(action.enabled, session!.csrf_token);
      }
      if (action.kind === "layer") {
        return setLddLayer(action.layer, action.enabled, session!.csrf_token);
      }
      if (action.kind === "quality-master") {
        return setQualityLayerMaster(action.enabled, session!.csrf_token);
      }
      if (action.kind === "quality-layer") {
        return setQualityLayer(action.layer, action.enabled, session!.csrf_token);
      }
      return applyLddPreset(action.name, session!.csrf_token);
    },
    onSuccess: async (_data, vars) => {
      const a = vars.action;
      const msg =
        a.kind === "master"
          ? `Master switch ${a.enabled ? "on" : "off"}`
          : a.kind === "layer"
            ? `${a.layer} ${a.enabled ? "enabled" : "disabled"}`
            : a.kind === "quality-master"
              ? `Quality layers ${a.enabled ? "on" : "off"}`
              : a.kind === "quality-layer"
                ? `${a.layer} ${a.enabled ? "enabled" : "disabled"}`
                : `Preset '${a.name}' applied`;
      setToast({ kind: "ok", msg });
      await qc.invalidateQueries({ queryKey: ["ldd"] });
      await qc.invalidateQueries({ queryKey: ["quality-layers"] });
    },
    onError: (e: Error) => {
      setToast({ kind: "err", msg: e.message });
      throw e;
    },
  });

  if (q.isLoading || (q.isError && q.isFetching)) {
    return (
      <div className="mx-auto max-w-5xl space-y-4">
        <Skeleton className="h-10 w-1/3" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (q.isError || !q.data) {
    return (
      <Card className="border-destructive/40 bg-destructive/5">
        <CardContent className="py-4 text-sm text-destructive">
          LDD config failed to load: {(q.error as Error | undefined)?.message ?? "unknown"}
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      {!q.data.master_enabled && (
        <Card className="border-red-500/40 bg-red-500/5">
          <CardContent className="flex items-center justify-between py-4">
            <div className="flex items-center gap-3">
              <div className="h-3 w-3 rounded-full bg-red-500 animate-pulse" />
              <span className="text-sm font-medium text-red-700 dark:text-red-400">
                LDD Master is disabled — all layers are OFF. Enable LDD to use quality settings.
              </span>
            </div>
            <Button
              size="sm"
              onClick={() => mutation.mutate({ action: { kind: "master", enabled: true } })}
              disabled={mutation.isPending}
            >
              Enable LDD Now
            </Button>
          </CardContent>
        </Card>
      )}

      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-serif text-3xl font-light tracking-tight">AI Quality Settings</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Fine-grained controls for the AI's reasoning and quality behaviours. Turning off a
            parent setting also disables its dependent settings automatically.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={q.data.master_enabled ? "default" : "danger"}>
            master · {q.data.master_enabled ? "ON" : "OFF"}
          </Badge>
          <Button
            variant={q.data.master_enabled ? "outline" : "accent"}
            size="sm"
            onClick={() => mutation.mutate({ action: { kind: "master", enabled: !q.data.master_enabled } })}
            disabled={mutation.isPending}
          >
            <Power className="h-4 w-4" />
            {q.data.master_enabled ? "Disable quality checks" : "Enable quality checks"}
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Wand2 className="h-4 w-4 text-accent" />
            <CardTitle className="text-base">Presets</CardTitle>
          </div>
          <CardDescription>
            One-click preset. You can fine-tune individual settings afterwards.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          {q.data.presets.map((p) => (
            <Button
              key={p}
              variant="outline"
              size="sm"
              onClick={() => mutation.mutate({ action: { kind: "preset", name: p } })}
              disabled={mutation.isPending}
            >
              {p}
            </Button>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Layers className="h-4 w-4 text-accent" />
            <CardTitle className="text-base">Settings ({q.data.layers.length})</CardTitle>
          </div>
          <CardDescription>
            <span className="font-mono">saved</span> reflects your last saved value;{" "}
            <span className="font-mono">active</span> is what the AI currently uses (a persona
            may override this global setting for its own chats).
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-1.5">
          {q.data.layers.map((l) => (
            <LayerRow key={l.id} layer={l} onToggle={(en) => mutation.mutate({ action: { kind: "layer", layer: l.id, enabled: en } })} />
          ))}
        </CardContent>
      </Card>

      <Card className="border-amber-500/40 bg-amber-500/5">
        <CardContent className="flex items-start gap-3 py-3 text-xs">
          <Info className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-300" />
          <span>
            Changes here apply globally. Individual personas can override these settings — edit
            a persona to set per-persona behaviour. A persona's preset always takes priority
            over this global configuration for its own chats.
          </span>
        </CardContent>
      </Card>

      {qQuality.isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : qQuality.data ? (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Layers className="h-4 w-4 text-accent" />
                <CardTitle className="text-base">Quality Gate & Disciplines</CardTitle>
              </div>
              <Button
                variant={qQuality.data.globally_enabled ? "outline" : "accent"}
                size="sm"
                onClick={() => mutation.mutate({ action: { kind: "quality-master", enabled: !qQuality.data.globally_enabled } })}
                disabled={mutation.isPending}
              >
                <Power className="h-4 w-4" />
                {qQuality.data.globally_enabled ? "Disable" : "Enable"}
              </Button>
            </div>
            <CardDescription>
              Quality gate and engineering disciplines. Disable for prototyping, enable for production.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {qQuality.data.layers.map((layer) => (
              <QualityLayerRow
                key={layer.id}
                layer={layer}
                onToggle={(en) => mutation.mutate({ action: { kind: "quality-layer", layer: layer.id, enabled: en } })}
              />
            ))}
          </CardContent>
        </Card>
      ) : null}

      {toast && (
        <div
          className={
            toast.kind === "ok"
              ? "fixed bottom-6 right-6 z-50 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-4 py-2 text-sm text-emerald-700 shadow-lg dark:text-emerald-300"
              : "fixed bottom-6 right-6 z-50 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-2 text-sm text-destructive shadow-lg"
          }
          onClick={() => setToast(null)}
        >
          {toast.msg}
        </div>
      )}

    </div>
  );
}

function QualityLayerRow({
  layer,
  onToggle,
}: {
  layer: QualityLayer;
  onToggle: (enabled: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between rounded-md border border-border/60 bg-card/40 px-3 py-2.5">
      <div className="min-w-0 flex-1 flex flex-col">
        <div className="flex min-w-0 items-center gap-2 text-sm font-medium">
          <Boxes className="h-4 w-4 shrink-0 text-muted-foreground" />
          <span className="min-w-0 truncate font-mono">{layer.id}</span>
        </div>
        <div className="mt-0.5 text-[11px] text-muted-foreground">
          <span>{layer.name}</span>
        </div>
      </div>
      <Button
        size="sm"
        variant={layer.configured ? "accent" : "outline"}
        onClick={() => onToggle(!layer.configured)}
        className="font-mono"
      >
        {layer.configured ? "ON" : "OFF"}
      </Button>
    </div>
  );
}

function LayerRow({
  layer,
  onToggle,
}: {
  layer: LddLayer;
  onToggle: (enabled: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between rounded-md border border-border/60 bg-card/40 px-3 py-2.5">
      <div className="min-w-0 flex-1 flex flex-col">
        <div className="flex min-w-0 flex-wrap items-center gap-1.5 text-sm font-medium">
          <Boxes className="h-4 w-4 shrink-0 text-muted-foreground" />
          <span className="font-mono">{layer.id}</span>
          {layer.depends_on && (
            <Badge variant="outline" className="shrink-0 font-mono text-[10px]">
              ↳ {layer.depends_on}
            </Badge>
          )}
        </div>
        <div className="mt-0.5 flex items-center gap-3 text-[11px] text-muted-foreground">
          <span>
            configured: <span className="font-mono">{String(layer.configured)}</span>
          </span>
          <span>
            effective: <span className="font-mono">{String(layer.effective)}</span>
          </span>
        </div>
      </div>
      <Button
        size="sm"
        variant={layer.configured ? "accent" : "outline"}
        onClick={() => onToggle(!layer.configured)}
        className="font-mono"
      >
        {layer.configured ? "ON" : "OFF"}
      </Button>
    </div>
  );
}

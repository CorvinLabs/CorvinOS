/**
 * Sensitive data (Models > Routing). One decision: where a turn that carries personal or
 * confidential data runs instead of being refused. Lists only providers whose locality the
 * operator declared (`eligible_providers`); with none, it says so and offers no control that
 * would be refused. Saving goes through `PUT /settings/engine/sensitive-route`; a 422 shows
 * fixed copy, never the server's sentence.
 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/lib/auth";
import { ApiError } from "@/lib/api/client";
import {
  SENSITIVE_ROUTE_KEY, canSave, getSensitiveRoute, localityLabel, putSensitiveRoute, routeState,
} from "../sensitive-route";

export function SensitiveRouteCard() {
  const qc = useQueryClient();
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const q = useQuery({ queryKey: SENSITIVE_ROUTE_KEY, queryFn: ({ signal }) => getSensitiveRoute(signal) });
  const [enabled, setEnabled] = useState(false);
  const [provider, setProvider] = useState("");
  const [modelId, setModelId] = useState("");

  const data = q.data;
  useEffect(() => {
    if (!data) return;
    setEnabled(data.enabled);
    setProvider(data.provider);
    setModelId(data.model_id);
  }, [data]);

  const save = useMutation({
    mutationFn: () => putSensitiveRoute({ enabled, provider, model_id: modelId }, csrf),
    onSuccess: (next) => qc.setQueryData(SENSITIVE_ROUTE_KEY, next),
  });

  const state = data ? routeState(data) : null;
  const dirty = !!data && (enabled !== data.enabled || provider !== data.provider || modelId !== data.model_id);
  const valid = !!data && (!enabled || canSave(provider, modelId, data));
  const refused = save.error instanceof ApiError && save.error.status === 422;

  return (
    <Card data-testid="sensitive-route-card">
      <CardHeader>
        <CardTitle className="text-lg">Sensitive data</CardTitle>
        <p className="text-sm text-muted-foreground">
          A turn that carries personal or confidential data is refused on the default engine.
          Switch this on to run it on a provider you declared as local or EU-hosted instead.
          Secrets and credentials are never rerouted.
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        {q.isLoading && <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />}
        {q.isError && (
          <p className="text-sm text-destructive flex items-center gap-2" role="alert">
            <AlertTriangle className="w-4 h-4" /> The sensitive-data setting could not be loaded.
          </p>
        )}

        {data && state === "no_target" && (
          <div className="rounded-lg border border-dashed p-3 text-sm" data-testid="sensitive-route-empty">
            <p className="font-medium">No admissible target</p>
            <p className="text-muted-foreground mt-1">
              No provider is declared as local or EU cloud, so sensitive turns keep being refused.
              Declare a provider&apos;s locality in the tenant policy first
              (<span className="font-mono">spec.data_classification.provider_compliance</span>);
              it then appears here.
            </p>
          </div>
        )}

        {data && state === "stale" && (
          <p className="text-sm text-amber-600 flex items-start gap-2" role="alert" data-testid="sensitive-route-stale">
            <AlertTriangle className="w-4 h-4 mt-0.5" />
            The saved target is no longer admissible. Sensitive turns are refused until you pick a
            declared provider or switch the route off.
          </p>
        )}

        {data && data.eligible_providers.length > 0 && (
          <>
            <div className="flex items-center gap-2">
              <input
                id="sensitive-route-enabled" type="checkbox" checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
              />
              <Label htmlFor="sensitive-route-enabled">Run sensitive turns on a declared provider</Label>
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-1">
                <Label htmlFor="sensitive-route-provider">Provider</Label>
                <select
                  id="sensitive-route-provider" className="w-full rounded-md border bg-background p-2 text-sm"
                  value={provider} disabled={!enabled} onChange={(e) => setProvider(e.target.value)}
                >
                  <option value="">Select a provider</option>
                  {data.eligible_providers.map((p) => (
                    <option key={p.provider} value={p.provider}>{p.label}</option>
                  ))}
                </select>
              </div>
              <div className="space-y-1">
                <Label htmlFor="sensitive-route-model">Model id</Label>
                <input
                  id="sensitive-route-model" className="w-full rounded-md border bg-background p-2 text-sm font-mono"
                  value={modelId} disabled={!enabled} onChange={(e) => setModelId(e.target.value)}
                  placeholder="as the provider names it"
                />
              </div>
            </div>

            <ul className="space-y-1 text-sm" data-testid="sensitive-route-engines">
              {data.eligible_providers.map((p) => (
                <li key={p.provider} className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{p.label}</span>
                  <Badge variant="outline">{localityLabel(p.locality)}</Badge>
                  <span className="text-xs text-muted-foreground">
                    takes {p.admitted_classes.length ? p.admitted_classes.join(", ").toLowerCase() : "no class"}
                  </span>
                </li>
              ))}
            </ul>

            <div className="flex items-center gap-3">
              <Button
                onClick={() => save.mutate()} disabled={!dirty || !valid || save.isPending}
                data-testid="sensitive-route-save"
              >
                {save.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : "Save"}
              </Button>
              {save.isSuccess && !dirty && (
                <span className="text-sm text-green-600 flex items-center gap-1"><Check className="w-4 h-4" /> Saved</span>
              )}
              {refused && (
                <span className="text-sm text-destructive" role="alert">
                  This target is not admissible. Check the provider declaration and the model id.
                </span>
              )}
              {save.isError && !refused && (
                <span className="text-sm text-destructive" role="alert">Saving failed.</span>
              )}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

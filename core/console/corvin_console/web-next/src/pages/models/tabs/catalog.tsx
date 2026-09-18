/**
 * Catalog tab (ADR-0885 D1) — the engine registry with its published rates
 * (ADR-0856), read-only. The one price source the Cost tab and the learner
 * bill against. "Use for …" hands a model to the Routing tab, which applies
 * it once its option list has loaded and says so if the pinned engine's source
 * does not offer it.
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2, RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { getCatalog } from "../api";
import { usePins } from "../hooks/use-pins";

/** Published rates are per 1k tokens; per-million reads better at these sizes. */
const perMillion = (perThousand: number | null | undefined): string =>
  perThousand === null || perThousand === undefined ? "—" : `$${(perThousand * 1000).toFixed(2)}`;

export function CatalogTab({ onUseFor }: { onUseFor: (id: string, turn: "os" | "worker") => void }) {
  const q = useQuery({ queryKey: ["catalog-models"], queryFn: ({ signal }) => getCatalog(signal), staleTime: 60_000 });
  const { pins } = usePins();
  const [filter, setFilter] = useState("");
  const [pricedOnly, setPricedOnly] = useState(false);

  const models = useMemo(() => q.data?.models ?? [], [q.data]);
  const note = q.isError
    ? "Model registry request failed."
    : q.data && q.data.available === false
      ? (q.data.detail ?? "Model registry not available on this build.")
      : "";
  const shown = models.filter((m) =>
    (!pricedOnly || m.priced) &&
    (!filter || m.id.toLowerCase().includes(filter.toLowerCase()) || m.name.toLowerCase().includes(filter.toLowerCase())));
  // Ranked by OUTPUT rate: the larger of the two on every current model and
  // what a long generation is dominated by. Unpriced models are left out of
  // the ranking rather than sorted as if they were free.
  const byCost = models.filter((m) => m.priced && m.output_usd_per_1k !== null)
    .sort((a, b) => (a.output_usd_per_1k ?? 0) - (b.output_usd_per_1k ?? 0));

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <input
            id="catalog-filter"
            className="h-9 rounded-md border bg-background px-3 text-sm"
            placeholder="Search models…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          <label className="text-sm flex items-center gap-2">
            <input id="catalog-priced-only" type="checkbox" checked={pricedOnly} onChange={(e) => setPricedOnly(e.target.checked)} />
            priced only
          </label>
          <span className="text-xs text-muted-foreground">
            {models.length} models · {models.filter((m) => !m.priced).length} unpriced
          </span>
        </div>
        <Button variant="outline" size="sm" onClick={() => void q.refetch()} disabled={q.isFetching}>
          <RefreshCw className={`h-4 w-4 mr-2 ${q.isFetching ? "animate-spin" : ""}`} /> Refresh
        </Button>
      </div>

      {q.isLoading ? (
        <div className="py-8 flex justify-center"><Loader2 className="w-6 h-6 animate-spin text-muted-foreground" /></div>
      ) : note ? (
        <Card className="p-4"><div className="text-sm text-muted-foreground">{note}</div></Card>
      ) : (
        <>
          {byCost.length > 1 && (
            <Card className="p-4">
              <h3 className="text-lg font-semibold mb-1">Lowest output rate</h3>
              <p className="text-xs text-muted-foreground mb-4">
                Published rates, not measured spend. Output is ranked because it is the larger rate on every current model.
              </p>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
                {byCost.slice(0, 3).map((m) => (
                  <div key={m.id} className="p-3 rounded-lg border">
                    <div className="font-medium break-all">{m.name}</div>
                    <div className="text-sm text-muted-foreground">{perMillion(m.output_usd_per_1k)} / 1M output</div>
                  </div>
                ))}
              </div>
            </Card>
          )}

          <Card className="p-4">
            <h3 className="text-lg font-semibold mb-1">All models <span className="text-muted-foreground">({shown.length})</span></h3>
            <p className="text-xs text-muted-foreground mb-4">
              Declared by the engine registry. Rates are the published first-party card; “—” means the
              model is not on it, never that it is free. Pins are changed on the Routing tab.
            </p>
            <div className="space-y-2 max-h-[32rem] overflow-y-auto">
              {shown.map((m) => {
                const isOs = pins?.os_model === m.id;
                const isWorker = pins?.worker_model === m.id;
                return (
                  <div key={m.id} className="p-3 rounded-lg border">
                    <div className="flex items-start justify-between gap-4 flex-wrap">
                      <div className="min-w-0">
                        <div className="font-medium break-all flex items-center gap-2 flex-wrap">
                          {m.name}
                          {isOs && <Badge variant="ok" className="font-normal">pinned OS</Badge>}
                          {isWorker && <Badge variant="ok" className="font-normal">pinned worker</Badge>}
                        </div>
                        <div className="text-xs text-muted-foreground break-all">{m.id}</div>
                        <div className="flex flex-wrap gap-1 mt-2">
                          {m.engines.map((e) => <Badge key={e} variant="secondary">{e}</Badge>)}
                          {m.turns.map((t) => <Badge key={t} variant="outline">{t}</Badge>)}
                        </div>
                      </div>
                      <div className="text-right text-sm shrink-0">
                        <div>{perMillion(m.input_usd_per_1k)} in</div>
                        <div>{perMillion(m.output_usd_per_1k)} out</div>
                        <div className="text-xs text-muted-foreground">per 1M tokens</div>
                        <div className="mt-2 flex gap-1 justify-end">
                          {m.turns.includes("os") && (
                            <Button size="sm" variant="ghost" onClick={() => onUseFor(m.id, "os")}>Use for OS turn</Button>
                          )}
                          {m.turns.includes("worker") && (
                            <Button size="sm" variant="ghost" onClick={() => onUseFor(m.id, "worker")}>Use for worker turn</Button>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })}
              {shown.length === 0 && (
                <div className="text-sm text-muted-foreground">
                  {models.length === 0 ? "The engine registry declares no models." : "No model matches the filter."}
                </div>
              )}
            </div>
          </Card>
        </>
      )}
    </div>
  );
}

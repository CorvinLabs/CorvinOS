/**
 * Model Selection — the engine registry with its published rates (ADR-0856).
 *
 * Rebuilt 2026-09-16. The previous page could never have worked:
 *  - it fetched /v1/models/available, missing the /v1/console prefix, so every
 *    request 404'd and the list was permanently empty;
 *  - it ranked "Fastest Models" by `latency_ms`, a field nothing on this
 *    install measures — the API returned the constants 50/20/10;
 *  - it showed one `cost_per_1k`, but input and output bill at rates that
 *    differ 5x on every current model;
 *  - "Save Configuration" POSTed to a route that reported success and
 *    persisted nothing, so a changed model silently reverted on reload.
 *
 * This page now READS. Engine/model pins are written by Settings -> Engine,
 * which owns spec.engine_models in tenant.corvin.yaml (ADR-0759).
 */
import { useState, useEffect } from "react";
import { Brain, ExternalLink, RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

interface Model {
  id: string;
  name: string;
  engines: string[];
  turns: string[];
  input_usd_per_1k: number | null;
  output_usd_per_1k: number | null;
  priced: boolean;
}

interface ModelsResponse {
  models?: Model[];
  available?: boolean;
  detail?: string;
  unpriced?: string[];
}

interface ConfigResponse {
  config?: {
    tenant_id?: string;
    default_engine?: string | null;
    os_model?: string | null;
    worker_model?: string | null;
  };
  available?: boolean;
  detail?: string;
}

/** Published rates are per 1k tokens; per-million reads better at these sizes. */
const perMillion = (perThousand: number | null | undefined): string =>
  perThousand === null || perThousand === undefined
    ? "—"
    : `$${(perThousand * 1000).toFixed(2)}`;

export function ModelSelectionPage() {
  const [models, setModels] = useState<Model[]>([]);
  const [modelsNote, setModelsNote] = useState<string>("");
  const [config, setConfig] = useState<ConfigResponse["config"]>(undefined);
  const [configNote, setConfigNote] = useState<string>("");
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    try {
      const [mRes, cRes] = await Promise.all([
        fetch("/v1/console/v1/models/available"),
        fetch("/v1/console/v1/models/config"),
      ]);

      const mData: ModelsResponse = mRes.ok ? await mRes.json() : {};
      setModels(mData.models ?? []);
      setModelsNote(
        mRes.ok
          ? mData.available === false
            ? mData.detail ?? "Model registry not available on this build."
            : ""
          : `Model registry request failed (HTTP ${mRes.status}).`,
      );

      const cData: ConfigResponse = cRes.ok ? await cRes.json() : {};
      setConfig(cData.config);
      setConfigNote(
        cRes.ok
          ? cData.available === false
            ? cData.detail ?? "Engine configuration not readable on this build."
            : ""
          : `Engine configuration request failed (HTTP ${cRes.status}).`,
      );
    } catch (error) {
      console.error("Failed to load model data:", error);
      setModels([]);
      setModelsNote("Model registry request failed.");
      setConfigNote("Engine configuration request failed.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const pinned = (id: string | null | undefined) =>
    id ? models.find((m) => m.id === id) : undefined;

  // Ranked by OUTPUT rate: it is the larger of the two on every current model
  // and the one a long generation is dominated by. Unpriced models are left
  // out of the ranking rather than sorted as if they were free.
  const byCost = models
    .filter((m) => m.priced && m.output_usd_per_1k !== null)
    .sort((a, b) => (a.output_usd_per_1k ?? 0) - (b.output_usd_per_1k ?? 0));

  const renderPins = () => {
    if (configNote) {
      return <div className="text-sm text-muted-foreground">{configNote}</div>;
    }
    if (!config) {
      return <div className="text-sm text-muted-foreground">No engine configuration.</div>;
    }
    const rows: Array<[string, string | null | undefined]> = [
      ["OS turn", config.os_model],
      ["Worker turn", config.worker_model],
      ["Default engine", config.default_engine],
    ];
    return (
      <div className="grid gap-4 sm:grid-cols-3">
        {rows.map(([label, value]) => {
          const model = pinned(value);
          return (
            <div key={label}>
              <div className="text-sm font-medium text-muted-foreground">{label}</div>
              <div className="text-lg font-semibold break-all">{value || "—"}</div>
              {model && (
                <div className="text-xs text-muted-foreground mt-1">
                  {perMillion(model.input_usd_per_1k)} in {"·"}{" "}
                  {perMillion(model.output_usd_per_1k)} out / 1M
                </div>
              )}
            </div>
          );
        })}
      </div>
    );
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Brain className="h-6 w-6" />
          <h1 className="text-2xl font-bold">Model Selection</h1>
          <Badge variant="secondary">Learnable Skill (T3.1)</Badge>
        </div>
        <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
          <RefreshCw className={`h-4 w-4 mr-2 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      {/* Tabbed Interface */}
      <Tabs defaultValue="overview" className="space-y-4">
        <TabsList className="grid w-full grid-cols-4">
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="metrics">Metrics</TabsTrigger>
          <TabsTrigger value="feedback">Feedback</TabsTrigger>
          <TabsTrigger value="registry">Registry</TabsTrigger>
        </TabsList>

        {/* Overview Tab */}
        <TabsContent value="overview" className="space-y-4">
          <Card className="p-4">
            <div className="flex items-start justify-between gap-4 mb-4">
              <h3 className="text-lg font-semibold">Active configuration</h3>
              <Button asChild variant="outline" size="sm">
                <Link to="/settings/engine">
                  Change in Engine settings
                  <ExternalLink className="h-4 w-4 ml-2" />
                </Link>
              </Button>
            </div>
            {renderPins()}
            <p className="text-xs text-muted-foreground mt-4">
              This page is read-only. Engine and model pins are written by Settings {"→"} Engine,
              which owns them in the tenant configuration.
            </p>
          </Card>
        </TabsContent>

        {/* Metrics Tab (New) */}
        <TabsContent value="metrics">
          <ModelSelectionMetricsPanel skillId="os.model_selector" autoRefreshMs={5000} />
        </TabsContent>

        {/* Feedback Tab (New) */}
        <TabsContent value="feedback">
          <ModelSelectionFeedbackForm onFeedbackSubmitted={() => {}} />
        </TabsContent>

        {/* Registry Tab */}
        <TabsContent value="registry" className="space-y-4">
          {loading ? (
            <div className="text-center py-8 text-muted-foreground">Loading models{"…"}</div>
          ) : modelsNote ? (
            <Card className="p-4">
              <div className="text-sm text-muted-foreground">{modelsNote}</div>
            </Card>
          ) : (
            <>
              {byCost.length > 1 && (
                <Card className="p-4">
                  <h3 className="text-lg font-semibold mb-1">Lowest output rate</h3>
                  <p className="text-xs text-muted-foreground mb-4">
                    Published rates, not measured spend. Output is ranked because it is the
                    larger rate on every current model.
                  </p>
                  <div className="space-y-2">
                    {byCost.slice(0, 3).map((model) => (
                      <div key={model.id} className="p-3 rounded-lg border">
                        <div className="font-medium break-all">{model.name}</div>
                        <div className="text-sm text-muted-foreground">
                          {perMillion(model.output_usd_per_1k)} / 1M output
                        </div>
                      </div>
                    ))}
                  </div>
                </Card>
              )}

              <Card className="p-4">
                <h3 className="text-lg font-semibold mb-1">
                  All models <span className="text-muted-foreground">({models.length})</span>
                </h3>
                <p className="text-xs text-muted-foreground mb-4">
                  Declared by the engine registry. Rates are the published first-party card;
                  {" — "} means the model is not on it, never that it is free.
                </p>
                <div className="space-y-2 max-h-96 overflow-y-auto">
                  {models.map((model) => (
                    <div key={model.id} className="p-3 rounded-lg border">
                      <div className="flex items-start justify-between gap-4">
                        <div className="min-w-0">
                          <div className="font-medium break-all">{model.name}</div>
                          <div className="text-xs text-muted-foreground break-all">{model.id}</div>
                          <div className="flex flex-wrap gap-1 mt-2">
                            {model.engines.map((e) => (
                              <Badge key={e} variant="secondary">{e}</Badge>
                            ))}
                            {model.turns.map((t) => (
                              <Badge key={t} variant="outline">{t}</Badge>
                            ))}
                          </div>
                        </div>
                        <div className="text-right text-sm shrink-0">
                          <div>{perMillion(model.input_usd_per_1k)} in</div>
                          <div>{perMillion(model.output_usd_per_1k)} out</div>
                          <div className="text-xs text-muted-foreground">per 1M tokens</div>
                        </div>
                      </div>
                    </div>
                  ))}
                  {models.length === 0 && (
                    <div className="text-sm text-muted-foreground">
                      The engine registry declares no models.
                    </div>
                  )}
                </div>
              </Card>
            </>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}

export default ModelSelectionPage;

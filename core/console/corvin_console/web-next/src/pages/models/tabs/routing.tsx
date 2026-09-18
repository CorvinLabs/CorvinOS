/**
 * Routing tab (ADR-0885 D1/D4).
 *
 * Block 1 — Turn pins: what ACTUALLY serves an OS turn and a worker turn
 *   (`spec.engine_models`, ADR-0759), written through the existing
 *   `PUT /settings/engine`. That route REPLACES the whole map, so this form
 *   always submits the full map it read. Its own dirty state, its own button.
 * Block 2 — Classifier overrides: the shadow classifier's per-tier choice
 *   (`PUT /v1/engine/config`). Advisory — it never decides what serves a turn;
 *   the label says so. Its own button, because the two routes validate and
 *   fail differently and one half-successful "Save" would hide which half.
 * Block 3 — External providers, from the ADR-0181 registry.
 *
 * `preselect`/`turn` is the Catalog hand-off, applied only once the option
 * list has loaded (a card that resets on data load would otherwise overwrite
 * it) and validated against the options.
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { useAuth } from "@/lib/auth";
import { ApiError } from "@/lib/api/client";
import {
  getEngineConfig, getProviderModels, setEngineConfig, setOsEngineSetting,
  type TaskType,
} from "@/lib/api/engines";
import { ClaudeSourceLine, TaskTypeCard, fmtInt, useAuthLabel, useClaudeModels, useProviders }
  from "../components/engine-parts";
import { ENGINE_SETTING_KEY, pinsOf, useOsEngineSetting } from "../hooks/use-pins";
import { COST_STATUS_KEY } from "../hooks/use-cost-status";

const NATIVE = "__native__";

interface PinForm { os_model: string; worker_model: string; provider: string }

export function RoutingTab({
  active, preselect, preselectTurn, onPreselectConsumed,
}: {
  active: boolean;
  preselect: string | null;
  preselectTurn: string | null;
  onPreselectConsumed: () => void;
}) {
  const qc = useQueryClient();
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";

  // ── Block 1: turn pins ────────────────────────────────────────────────
  const settingQ = useOsEngineSetting();
  const claudeQ = useClaudeModels();
  const providersQ = useProviders();
  const authLabel = useAuthLabel();
  const [form, setForm] = useState<PinForm | null>(null);
  const [preselectNote, setPreselectNote] = useState<string | null>(null);

  const served = pinsOf(settingQ.data);
  // Initialise (and re-sync when not dirty) from what the server serves.
  const isDirty = !!form && !!served && (
    form.os_model !== (served.os_model ?? "") ||
    form.worker_model !== (served.worker_model ?? "") ||
    form.provider !== (served.provider ?? NATIVE)
  );
  useEffect(() => {
    if (served && (form === null || !isDirty)) {
      const next = { os_model: served.os_model ?? "", worker_model: served.worker_model ?? "", provider: served.provider ?? NATIVE };
      if (!form || form.os_model !== next.os_model || form.worker_model !== next.worker_model || form.provider !== next.provider) {
        setForm(next);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settingQ.data]);

  const providerIsNative = !form || form.provider === NATIVE || form.provider === "anthropic";
  const providerModelsQ = useQuery({
    queryKey: ["provider-models", form?.provider],
    queryFn: ({ signal }) => getProviderModels(form!.provider, signal),
    enabled: !!form && !providerIsNative,
    staleTime: 60_000,
  });
  const options = useMemo(() => {
    if (providerIsNative) return (claudeQ.data?.models ?? []).map((m) => ({ id: m.id, label: m.label }));
    return providerModelsQ.data?.models ?? [];
  }, [providerIsNative, claudeQ.data, providerModelsQ.data]);
  const optionsLoaded = providerIsNative ? !!claudeQ.data : !!providerModelsQ.data;

  // Catalog hand-off: apply once the options are known, validate, clear.
  useEffect(() => {
    if (!preselect || !form || !optionsLoaded) return;
    const offered = options.some((o) => o.id === preselect);
    if (offered) {
      const key = preselectTurn === "worker" ? "worker_model" : "os_model";
      setForm({ ...form, [key]: preselect });
      setPreselectNote(null);
    } else {
      setPreselectNote(`${preselect} is not offered by the pinned engine's model source.`);
    }
    onPreselectConsumed();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preselect, preselectTurn, optionsLoaded]);

  const savePins = useMutation({
    mutationFn: () => {
      const setting = settingQ.data!;
      const engine = setting.default_engine ?? "claude_code";
      return setOsEngineSetting(
        {
          default_engine: engine,
          engine_models: {
            ...setting.engine_models,
            [engine]: {
              os_model: form!.os_model || null,
              worker_model: form!.worker_model || null,
              provider: form!.provider === NATIVE ? null : form!.provider,
            },
          },
        },
        csrf,
      );
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [...ENGINE_SETTING_KEY] });
      qc.invalidateQueries({ queryKey: [...COST_STATUS_KEY] });
    },
  });

  // ── Block 2: classifier overrides (advisory) ──────────────────────────
  const configQ = useQuery({
    queryKey: ["engine-config"],
    queryFn: ({ signal }) => getEngineConfig(signal),
    staleTime: 5_000,
    refetchInterval: active ? 30_000 : false,
  });
  const saveOverride = useMutation({
    mutationFn: (args: { taskType: TaskType; selected_model: string; provider: string | null }) =>
      setEngineConfig(
        {
          [args.taskType]: {
            task_type: args.taskType,
            selected_model: args.selected_model,
            provider: args.provider,
            alternatives: configQ.data?.models[args.taskType]?.alternatives ?? [],
          },
        },
        csrf,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["engine-config"] }),
  });

  const config = configQ.data;
  const providers = providersQ.data ?? {};

  return (
    <div className="space-y-8">
      {/* ── Block 1 ── */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Turn pins</CardTitle>
          <p className="text-sm text-muted-foreground">
            What actually serves a turn. A pinned model wins over the adaptive selector;
            leave a pin empty for the engine's default.
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          {settingQ.isLoading || !form ? (
            <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
          ) : (
            <>
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
                <div>
                  <Label className="text-sm font-medium mb-2 block">Model source</Label>
                  <Select
                    value={form.provider}
                    onChange={(e) => setForm({ ...form, provider: e.target.value, os_model: "", worker_model: "" })}
                  >
                    <option value={NATIVE}>Claude (native)</option>
                    {Object.entries(providers).map(([id, p]) => (
                      <option key={id} value={id}>{p.label}</option>
                    ))}
                  </Select>
                  {providerIsNative && (
                    <ClaudeSourceLine catalog={claudeQ.data} error={claudeQ.error} authLabel={authLabel} />
                  )}
                  {!providerIsNative && providerModelsQ.data && !providerModelsQ.data.reachable && (
                    <p className="text-xs text-amber-700 dark:text-amber-300 mt-1.5 flex items-start gap-1">
                      <AlertTriangle className="w-3 h-3 mt-0.5 shrink-0" />
                      {providerModelsQ.data.error || "Provider unreachable — its model list is the last known one."}
                    </p>
                  )}
                </div>
                <div>
                  <Label className="text-sm font-medium mb-2 block">OS turn model</Label>
                  <Select value={form.os_model} onChange={(e) => setForm({ ...form, os_model: e.target.value })}>
                    <option value="">Engine default (adaptive)</option>
                    {options.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
                  </Select>
                </div>
                <div>
                  <Label className="text-sm font-medium mb-2 block">Worker turn model</Label>
                  <Select value={form.worker_model} onChange={(e) => setForm({ ...form, worker_model: e.target.value })}>
                    <option value="">Engine default</option>
                    {options.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
                  </Select>
                </div>
              </div>
              {preselectNote && (
                <p className="text-xs text-amber-700 dark:text-amber-300">{preselectNote}</p>
              )}
              {(settingQ.data?.compliance_warnings ?? []).map((w) => (
                <p key={w} className="text-xs text-amber-700 dark:text-amber-300 flex items-start gap-1">
                  <AlertTriangle className="w-3 h-3 mt-0.5 shrink-0" />{w}
                </p>
              ))}
              <div className="flex items-center gap-3 flex-wrap">
                <Button size="sm" disabled={!isDirty || savePins.isPending} onClick={() => savePins.mutate()}>
                  {savePins.isPending ? <Loader2 className="w-4 h-4 animate-spin mr-1.5" /> : <Check className="w-4 h-4 mr-1.5" />}
                  Save pins
                </Button>
                {isDirty && (
                  <Button size="sm" variant="ghost" onClick={() => served && setForm({
                    os_model: served.os_model ?? "", worker_model: served.worker_model ?? "", provider: served.provider ?? NATIVE,
                  })}>
                    Discard
                  </Button>
                )}
                {savePins.isSuccess && !isDirty && (
                  <span className="text-xs text-emerald-700 dark:text-emerald-300">Saved — audited.</span>
                )}
                {savePins.isError && (
                  <span className="text-xs text-destructive">
                    {savePins.error instanceof ApiError && savePins.error.status === 503
                      ? "The engine model registry could not be loaded — the pins were not saved."
                      : savePins.error instanceof ApiError && savePins.error.status === 422
                        ? `Not saved: ${savePins.error.message}`
                        : "The pins could not be saved."}
                  </span>
                )}
              </div>
              <p className="text-xs text-muted-foreground">
                Engine: <span className="font-mono">{served?.default_engine}</span>. Pins are written
                to the tenant configuration and every save is an audited event.
              </p>
            </>
          )}
        </CardContent>
      </Card>

      {/* ── Block 2 ── */}
      <div>
        <div className="flex items-baseline justify-between flex-wrap gap-2 mb-1">
          <h2 className="text-xl font-semibold">Classifier overrides</h2>
          <Badge variant="secondary" className="font-normal">advisory</Badge>
        </div>
        <p className="text-sm text-muted-foreground mb-4">
          The shadow classifier's per-tier choice. It observes and records every turn; it does
          not decide what serves one — the pins above do. Confidence reflects real classified
          turns.
        </p>
        {configQ.isLoading ? (
          <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
        ) : !config ? (
          <p className="text-sm text-destructive flex items-center gap-2">
            <AlertTriangle className="w-4 h-4" /> The classifier configuration could not be loaded.
          </p>
        ) : (
          <div className="space-y-6">
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              <div className="lg:col-span-2">
                <TaskTypeCard
                  config={config.models.corvinOS}
                  totalClassified={config.total_samples}
                  saving={saveOverride.isPending}
                  onSave={(patch) => saveOverride.mutate({ taskType: "corvinOS", ...patch })}
                />
              </div>
              <Card>
                <CardContent className="pt-6">
                  <p className="text-sm text-muted-foreground">Classified turns in this window</p>
                  <p className="text-3xl font-bold tabular-nums">{fmtInt(config.total_samples)}</p>
                  <p className="text-xs text-muted-foreground mt-2">
                    {(["SIMPLE", "MEDIUM", "COMPLEX"] as const).map((t) => {
                      const n = config.models[t]?.classified_count ?? 0;
                      const pct = config.total_samples > 0 ? Math.round((n / config.total_samples) * 100) : 0;
                      return `${t.toLowerCase()} ${pct}%`;
                    }).join(" · ")}
                  </p>
                  <p className="text-xs text-muted-foreground mt-1">
                    Shares of the turns classified in the counting window (see header). Outcome
                    samples on the cards are lifetime and say so.
                  </p>
                </CardContent>
              </Card>
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              {(["SIMPLE", "MEDIUM", "COMPLEX"] as const).map((taskType) => (
                <TaskTypeCard
                  key={taskType}
                  config={config.models[taskType]}
                  totalClassified={config.total_samples}
                  saving={saveOverride.isPending}
                  onSave={(patch) => saveOverride.mutate({ taskType, ...patch })}
                />
              ))}
            </div>
            {saveOverride.isError && (
              <p className="text-xs text-destructive">
                {saveOverride.error instanceof ApiError ? `Not saved: ${saveOverride.error.message}` : "The override could not be saved."}
              </p>
            )}
          </div>
        )}
      </div>

      {/* ── Block 3 ── */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">External providers</CardTitle>
          <p className="text-sm text-muted-foreground">
            The provider registry. A provider is selected per card above; its models are
            fetched live when it is chosen.
          </p>
        </CardHeader>
        <CardContent>
          {providersQ.isLoading ? (
            <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
          ) : Object.keys(providers).length === 0 ? (
            <p className="text-sm text-muted-foreground">No external provider is registered on this build.</p>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {Object.entries(providers).map(([id, p]) => (
                <div key={id} className="rounded-lg border p-3 text-sm">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium">{p.label}</span>
                    <Badge variant="outline">{p.kind}</Badge>
                  </div>
                  <p className="text-xs text-muted-foreground mt-1 font-mono break-all">{id} · {p.model_source}</p>
                  {p.credential_env && (
                    <p className="text-xs text-muted-foreground mt-1">
                      Credential: <span className="font-mono">{p.credential_env}</span> (Settings → API Keys)
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

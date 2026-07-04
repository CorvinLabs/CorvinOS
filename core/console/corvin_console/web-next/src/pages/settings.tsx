/**
 * Settings — tenant configuration editor.
 * Shows and edits the 6 known config files (tenant policy, data policy, LDD, etc.)
 * Mutations require confirmation.
 */
import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Edit2, FileText, HeartPulse, Loader2, RefreshCw, Save, Upload, Wrench, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ReauthDialog } from "@/components/reauth-dialog";
import { Switch } from "@/components/ui/switch";
import { useAuth } from "@/lib/auth";
import { api, updateSettingsFile, getAutoUpdate, setAutoUpdate, getDelegationBudget, setDelegationBudget, getHealingConfig, setHealingConfig, type DelegationBudgetResponse, type HealingConfigResponse } from "@/lib/api";
import { cn } from "@/lib/utils";
import { HelpTooltip } from "@/components/ui/help-tooltip";

interface SettingsFile {
  label: string;
  path: string;
  present: boolean;
  mode_octal: string | null;
  size_b: number;
  mtime: number | null;
  body: string | null;
  description: string | null;
  kind: string;
}

interface SettingsResponse {
  tenant_id: string;
  ts: number;
  global_dir: string;
  files: SettingsFile[];
  present_count: number;
  total_count: number;
  edit_phase: string;
}

const KIND_PLACEHOLDER: Record<string, string> = {
  yaml: `# YAML configuration
# See docs/claude-ref/ for field reference
`,
  json: `{
  "example": true
}
`,
};

function formatTs(ts: number | null): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString(undefined, {
    month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

function FileCard({
  file,
  csrf,
  onSaved,
}: {
  file: SettingsFile;
  csrf: string;
  onSaved: () => void;
}) {
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState(file.body ?? KIND_PLACEHOLDER[file.kind] ?? "");
  const [reauthOpen, setReauthOpen] = React.useState(false);
  const [saved, setSaved] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const startEdit = () => {
    setDraft(file.body ?? KIND_PLACEHOLDER[file.kind] ?? "");
    setError(null);
    setEditing(true);
  };

  const cancel = () => {
    setEditing(false);
    setError(null);
  };

  const save = async () => {
    setError(null);
    try {
      await updateSettingsFile(file.label, draft, csrf);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
      setEditing(false);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <Card className={cn("transition-all", !file.present && !editing && "opacity-70")}>
      <CardContent className="pt-4 pb-3 space-y-3">
        {/* Header */}
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-2 min-w-0">
            <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
            <span className="font-mono text-sm font-medium truncate">{file.label}</span>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {saved && <Check className="h-4 w-4 text-emerald-500" />}
            {file.present ? (
              <Badge variant="outline" className="text-[10px] text-emerald-600 dark:text-emerald-400 border-emerald-500/40">
                present
              </Badge>
            ) : (
              <Badge variant="outline" className="text-[10px] text-muted-foreground">
                not created
              </Badge>
            )}
            <Badge variant="secondary" className="font-mono text-[10px]">{file.kind}</Badge>
            {!editing && (
              <Button size="sm" variant="outline" onClick={startEdit} className="h-7 px-2 text-xs gap-1">
                <Edit2 className="h-3 w-3" />
                {file.present ? "Edit" : "Create"}
              </Button>
            )}
          </div>
        </div>

        {/* Meta */}
        <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-[11px] text-muted-foreground font-mono">
          <span className="truncate max-w-sm" title={file.path}>{file.path}</span>
          {file.present && (
            <>
              <span>{file.size_b} B</span>
              {file.mode_octal && <span>mode {file.mode_octal}</span>}
              <span>modified {formatTs(file.mtime)}</span>
            </>
          )}
        </div>

        {file.description && !editing && (
          <p className="text-xs text-muted-foreground">{file.description}</p>
        )}

        {/* Read-only body (when not editing) */}
        {!editing && file.present && file.body && (
          <pre className="max-h-48 overflow-auto rounded-md border border-border/60 bg-muted/30 px-3 py-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-foreground">
            {file.body}
          </pre>
        )}

        {!editing && !file.present && (
          <p className="text-[11px] text-muted-foreground italic">
            File does not exist yet — click <strong>Create</strong> to add it.
          </p>
        )}

        {/* Edit mode */}
        {editing && (
          <div className="space-y-2">
            <Label className="text-xs font-medium">
              Content <span className="text-muted-foreground font-normal">({file.kind})</span>
            </Label>
            <Textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              className="font-mono text-xs min-h-[200px] resize-y"
              spellCheck={false}
            />
            {error && (
              <p className="text-xs text-destructive bg-destructive/10 rounded px-2 py-1.5">{error}</p>
            )}
            <div className="flex items-center gap-2 justify-end">
              <Button variant="ghost" size="sm" onClick={cancel}>
                <X className="h-3.5 w-3.5 mr-1" /> Cancel
              </Button>
              <Button
                size="sm"
                onClick={() => setReauthOpen(true)}
                disabled={!draft.trim()}
              >
                <Save className="h-3.5 w-3.5 mr-1" /> Save
              </Button>
            </div>
          </div>
        )}
      </CardContent>

      <ReauthDialog
        open={reauthOpen}
        onOpenChange={setReauthOpen}
        title={`Save ${file.label}`}
        description={`Writing configuration files requires confirmation.`}
        onConfirm={save}
      />
    </Card>
  );
}

function AutoUpdateCard({ csrf }: { csrf: string }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["auto-update"],
    queryFn: ({ signal }) => getAutoUpdate(signal),
  });
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const toggle = async (next: boolean) => {
    setError(null);
    setSaving(true);
    try {
      await setAutoUpdate(next, csrf);
      qc.invalidateQueries({ queryKey: ["auto-update"] });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const enabled = q.data?.enabled ?? true;

  return (
    <Card>
      <CardContent className="pt-4 pb-3">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2 min-w-0">
            <RefreshCw className="h-4 w-4 shrink-0 text-muted-foreground" />
            <div className="min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-sm font-medium">Auto-update on startup</span>
                {q.data?.version && q.data.version !== "unknown" && (
                  <Badge variant="secondary" className="font-mono text-[10px]">
                    v{q.data.version}
                  </Badge>
                )}
              </div>
              <p className="text-[11px] text-muted-foreground mt-0.5">
                Runs <span className="font-mono">pip install --upgrade corvinos</span> each time CorvinOS starts.
                Disable if you manage versions manually or are offline.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {saving && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
            {q.isLoading ? (
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            ) : (
              <Switch
                checked={enabled}
                onCheckedChange={toggle}
                disabled={saving}
                aria-label="Auto-update on startup"
              />
            )}
          </div>
        </div>
        {error && (
          <p className="mt-2 text-xs text-destructive bg-destructive/10 rounded px-2 py-1.5">{error}</p>
        )}
      </CardContent>
    </Card>
  );
}

function TelemetryCard({ csrf }: { csrf: string }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["healing-config"],
    queryFn: ({ signal }) => getHealingConfig(signal),
  });
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const toggle = async (next: boolean) => {
    setError(null);
    setSaving(true);
    try {
      await setHealingConfig({ telemetry_enabled: next }, csrf);
      qc.invalidateQueries({ queryKey: ["healing-config"] });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const enabled = q.data?.telemetry_enabled ?? true;

  return (
    <Card>
      <CardContent className="pt-4 pb-3">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2 min-w-0">
            <Upload className="h-4 w-4 shrink-0 text-muted-foreground" />
            <div className="min-w-0">
              <span className="text-sm font-medium">Send healing telemetry</span>
              <p className="text-[11px] text-muted-foreground mt-0.5">
                Anonymised self-healing events are uploaded to CorvinLabs/CorvinLogs for
                public transparency. No prompts, no message content, no PII.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {saving && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
            {q.isLoading ? (
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            ) : (
              <Switch
                checked={enabled}
                onCheckedChange={toggle}
                disabled={saving}
                aria-label="Send healing telemetry"
              />
            )}
          </div>
        </div>
        {error && (
          <p className="mt-2 text-xs text-destructive bg-destructive/10 rounded px-2 py-1.5">{error}</p>
        )}
      </CardContent>
    </Card>
  );
}

function HealingCard({ csrf }: { csrf: string }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["healing-config"],
    queryFn: ({ signal }) => getHealingConfig(signal),
  });
  const [saving, setSaving] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const toggle = async (patch: Partial<HealingConfigResponse>, key: string) => {
    setError(null);
    setSaving(key);
    try {
      await setHealingConfig(patch, csrf);
      qc.invalidateQueries({ queryKey: ["healing-config"] });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(null);
    }
  };

  const healingEnabled = q.data?.healing_enabled ?? true;
  const riskyEnabled = q.data?.risky_enabled ?? false;

  return (
    <Card>
      <CardContent className="pt-4 pb-3 space-y-4">
        {/* Self-healing enabled */}
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2 min-w-0">
            <HeartPulse className="h-4 w-4 shrink-0 text-muted-foreground" />
            <div className="min-w-0">
              <span className="text-sm font-medium">Self-healing enabled</span>
              <p className="text-[11px] text-muted-foreground mt-0.5">
                Corvin automatically detects and repairs common runtime issues
                (engine failures, config errors).
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {saving === "healing" && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
            {q.isLoading ? (
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            ) : (
              <Switch
                checked={healingEnabled}
                onCheckedChange={(next) => toggle({ healing_enabled: next }, "healing")}
                disabled={saving !== null}
                aria-label="Self-healing enabled"
              />
            )}
          </div>
        </div>

        <div className="border-t border-border/60" />

        {/* Allow code changes */}
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2 min-w-0">
            <Wrench className="h-4 w-4 shrink-0 text-muted-foreground" />
            <div className="min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-sm font-medium">Allow code changes</span>
                {riskyEnabled ? (
                  <Badge variant="outline" className="text-[10px] text-red-600 dark:text-red-400 border-red-500/40">
                    risky
                  </Badge>
                ) : (
                  <Badge variant="outline" className="text-[10px] text-amber-600 dark:text-amber-400 border-amber-500/40">
                    safe mode
                  </Badge>
                )}
              </div>
              <p className="text-[11px] text-muted-foreground mt-0.5">
                Permits the healing system to apply patches to Python source files.
                Requires code to be writable.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {saving === "risky" && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
            {q.isLoading ? (
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            ) : (
              <Switch
                checked={riskyEnabled}
                onCheckedChange={(next) => toggle({ risky_enabled: next }, "risky")}
                disabled={saving !== null}
                aria-label="Allow code changes"
              />
            )}
          </div>
        </div>

        {error && (
          <p className="text-xs text-destructive bg-destructive/10 rounded px-2 py-1.5">{error}</p>
        )}
      </CardContent>
    </Card>
  );
}

const BUDGET_LABELS: Record<string, { label: string; unit: string; description: string }> = {
  timeout_seconds:   { label: "Worker timeout",     unit: "s",       description: "Max seconds a single worker subprocess may run." },
  max_worker_turns:  { label: "Max turns / worker", unit: "turns",   description: "Max tool-call turns per claude worker." },
  max_loops:         { label: "Max iterations",     unit: "loops",   description: "How many planner→worker cycles the ACS orchestrator runs." },
  max_wall_time:     { label: "Max wall time",      unit: "s",       description: "Hard overall time limit for a full delegation run." },
  max_total_workers: { label: "Max workers",        unit: "workers", description: "How many parallel worker processes ACS may spawn per run." },
  max_depth:         { label: "Max nesting depth",  unit: "levels",  description: "Maximum recursion depth for nested delegation calls." },
};

function DelegationBudgetCard({ csrf }: { csrf: string }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["delegation-budget"],
    queryFn: ({ signal }) => getDelegationBudget(signal),
  });

  type BudgetValues = DelegationBudgetResponse["values"];
  const [draft, setDraft] = React.useState<Partial<BudgetValues>>({});
  const [dirty, setDirty] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [saved, setSaved] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (q.data) {
      setDraft({ ...q.data.values });
      setDirty(false);
    }
  }, [q.data]);

  const handleChange = (key: keyof BudgetValues, raw: string) => {
    const num = parseInt(raw, 10);
    if (isNaN(num)) return;
    setDraft((d) => ({ ...d, [key]: num }));
    setDirty(true);
    setSaved(false);
    setError(null);
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await setDelegationBudget(draft, csrf);
      setSaved(true);
      setDirty(false);
      setTimeout(() => setSaved(false), 2500);
      qc.invalidateQueries({ queryKey: ["delegation-budget"] });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const reset = () => {
    if (q.data) { setDraft({ ...q.data.values }); setDirty(false); setError(null); }
  };

  const meta = q.data?.meta ?? {};

  return (
    <Card>
      <CardContent className="pt-4 pb-3 space-y-4">
        <div className="flex items-center justify-between gap-2">
          <div>
            <p className="text-sm font-medium">Delegation Budget</p>
            <p className="text-[11px] text-muted-foreground mt-0.5">
              Limits for ACS worker processes spawned by the console chat.
            </p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {saving && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
            {saved && <Check className="h-4 w-4 text-emerald-500" />}
            {dirty && !saving && (
              <>
                <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={reset}>
                  <X className="h-3 w-3 mr-1" />Reset
                </Button>
                <Button size="sm" className="h-7 px-3 text-xs" onClick={save}>
                  <Save className="h-3 w-3 mr-1" />Save
                </Button>
              </>
            )}
          </div>
        </div>

        {q.isLoading && (
          <div className="flex justify-center py-4">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        )}

        {!q.isLoading && q.data && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {(Object.keys(BUDGET_LABELS) as (keyof BudgetValues)[]).map((key) => {
              const info = BUDGET_LABELS[key];
              const m = meta[key] ?? { min: 1, max: 99999, default: (q.data.values as Record<string, number>)[key] };
              const val = (draft as Record<string, number>)[key] ?? m.default;
              return (
                <div key={key} className="space-y-1">
                  <div className="flex items-center gap-1.5">
                    <Label className="text-xs font-medium">{info.label}</Label>
                    <HelpTooltip title={info.label} side="top" width="sm">
                      {info.description}
                      <br /><span className="text-muted-foreground">Range: {m.min}–{m.max} {info.unit}</span>
                    </HelpTooltip>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <input
                      type="number"
                      min={m.min}
                      max={m.max}
                      value={val}
                      onChange={(e) => handleChange(key, e.target.value)}
                      className="w-full rounded-md border border-input bg-background px-2.5 py-1 text-sm font-mono tabular-nums shadow-sm focus:outline-none focus:ring-1 focus:ring-ring"
                    />
                    <span className="text-[11px] text-muted-foreground shrink-0 w-12">{info.unit}</span>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {error && (
          <p className="text-xs text-destructive bg-destructive/10 rounded px-2 py-1.5">{error}</p>
        )}
      </CardContent>
    </Card>
  );
}

export function SettingsPage() {
  const { session } = useAuth();
  const qc = useQueryClient();

  const q = useQuery({
    queryKey: ["settings"],
    queryFn: ({ signal }) => api<SettingsResponse>("/settings", { signal }),
    refetchInterval: 60_000,   // fallback if SSE drops
  });

  if (q.isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const data = q.data;

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="font-serif text-3xl font-light tracking-tight">Settings</h1>
            <HelpTooltip title="Configuration files" side="right" width="lg">
              These are the raw YAML/JSON config files that control Corvin's behaviour.
              <br /><br />
              <strong>tenant.corvin.yaml</strong> — engines, bridges, compliance zone.
              <br />
              <strong>ldd.json</strong> — Loss-Driven Development layer toggles.
              <br />
              <strong>data_policy.yaml</strong> — data handling rules.
              <br /><br />
              Each save requires re-authentication to prevent accidental changes.
            </HelpTooltip>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            Tenant configuration files. Each save requires confirmation.
          </p>
        </div>
        {data && (
          <div className="flex items-center gap-2">
            <Badge variant="outline" className="text-xs">
              {data.present_count}/{data.total_count} files present
            </Badge>
            <Badge variant="outline" className="font-mono text-[10px] text-muted-foreground">
              {data.tenant_id}
            </Badge>
          </div>
        )}
      </div>

      <div className="space-y-2">
        <h2 className="text-sm font-semibold text-foreground">Updates</h2>
        <AutoUpdateCard csrf={session!.csrf_token} />
      </div>

      <div className="space-y-2">
        <h2 className="text-sm font-semibold text-foreground">Self-healing</h2>
        <HealingCard csrf={session!.csrf_token} />
        <TelemetryCard csrf={session!.csrf_token} />
      </div>

      <div className="space-y-2">
        <h2 className="text-sm font-semibold text-foreground">Agentic Compute</h2>
        <DelegationBudgetCard csrf={session!.csrf_token} />
      </div>

      {data && (
        <>
          <div className="rounded-lg border border-border bg-muted/20 px-4 py-3 text-xs text-muted-foreground">
            <span className="font-medium text-foreground">Config directory: </span>
            <span className="break-all font-mono">{data.global_dir}</span>
          </div>

          <div className="space-y-3">
            {data.files.map((f) => (
              <FileCard
                key={f.label}
                file={f}
                csrf={session!.csrf_token}
                onSaved={() => qc.invalidateQueries({ queryKey: ["settings"] })}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

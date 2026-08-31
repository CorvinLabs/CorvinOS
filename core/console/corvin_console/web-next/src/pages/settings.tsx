/**
 * Settings — tenant configuration editor.
 * Shows and edits the 6 known config files (tenant policy, data policy, LDD, etc.)
 * Mutations require confirmation.
 */
import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, Copy, Cpu, Edit2, FileText, FlaskConical, HeartPulse, Loader2, RefreshCw, Save, Server, Upload, Users, Wrench, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ReauthDialog } from "@/components/reauth-dialog";
import { Switch } from "@/components/ui/switch";
import { useAuth } from "@/lib/auth";
import { useNavigate } from "react-router-dom";
import { api, updateSettingsFile, getAutoUpdate, setAutoUpdate, getServiceTier, setServiceTier, getDelegationBudget, setDelegationBudget, getHealingConfig, setHealingConfig, getInstanceStats, getFeatureFlags, setFeatureFlag, getWorkerEngine, setWorkerEngine, type DelegationBudgetResponse, type FeatureFlagState, type HealingConfigResponse, type WorkerEngineMode } from "@/lib/api";
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

function ServiceTierCard({ csrf }: { csrf: string }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["service-tier"],
    queryFn: ({ signal }) => getServiceTier(signal),
  });
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [manualCommand, setManualCommand] = React.useState<string | null>(null);
  const [reauthOpen, setReauthOpen] = React.useState(false);

  const apply = async (next: boolean) => {
    setError(null);
    setManualCommand(null);
    setSaving(true);
    try {
      const res = await setServiceTier(next, csrf);
      if (!res.applied && res.manual_command) {
        setManualCommand(res.manual_command);
      }
      qc.invalidateQueries({ queryKey: ["service-tier"] });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      throw e;
    } finally {
      setSaving(false);
    }
  };

  const toggle = (next: boolean) => {
    if (next) {
      // Registering a boot-time service is the more consequential
      // direction (needs admin/root, keeps running unattended) — confirm
      // first. Turning it back off just removes that registration.
      setReauthOpen(true);
    } else {
      void apply(false);
    }
  };

  const alwaysOn = q.data?.always_on ?? false;
  const available = q.data?.available ?? true;

  return (
    <Card>
      <CardContent className="pt-4 pb-3">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2 min-w-0">
            <Server className="h-4 w-4 shrink-0 text-muted-foreground" />
            <div className="min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-sm font-medium">Always-on (survives reboot without login)</span>
                <Badge variant={alwaysOn ? "default" : "secondary"} className="text-[10px]">
                  {alwaysOn ? "Stufe 2" : "Stufe 1"}
                </Badge>
              </div>
              <p className="text-[11px] text-muted-foreground mt-0.5">
                Off (default): CorvinOS starts automatically when you log in. On: it also
                starts at boot, before anyone logs in — needs admin/root once to enable.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {saving && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
            {q.isLoading ? (
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            ) : (
              <Switch
                checked={alwaysOn}
                onCheckedChange={toggle}
                disabled={saving || !available}
                aria-label="Always-on system service"
              />
            )}
          </div>
        </div>
        {!available && (
          <p className="mt-2 text-xs text-muted-foreground bg-muted/50 rounded px-2 py-1.5">
            Not available on this install.
          </p>
        )}
        {manualCommand && (
          <p className="mt-2 text-xs text-muted-foreground bg-muted/50 rounded px-2 py-1.5">
            Needs administrator/root privileges — run this once, then toggle again:{" "}
            <code className="font-mono">{manualCommand}</code>
          </p>
        )}
        {error && (
          <p className="mt-2 text-xs text-destructive bg-destructive/10 rounded px-2 py-1.5">{error}</p>
        )}
      </CardContent>

      <ReauthDialog
        open={reauthOpen}
        onOpenChange={setReauthOpen}
        title="Enable always-on mode"
        description="CorvinOS registers itself as a system service that starts at boot, even before anyone logs in. This may prompt for administrator/root privileges."
        onConfirm={() => apply(true)}
      />
    </Card>
  );
}

function TelemetryCard({ csrf }: { csrf: string }) {
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

  // All three channels are default-ON (opt-out). Everything transmitted is
  // strictly anonymous / content-free — no prompts, no message content, no PII.
  const rows: {
    key: keyof HealingConfigResponse; label: string; desc: string;
    patchKey: string;
  }[] = [
    {
      key: "ping_enabled", patchKey: "ping",
      label: "Anonymous instance ping",
      desc: "A random installation id + version, once a day — lets us count how " +
            "many CorvinOS instances exist. Nothing else, no PII.",
    },
    {
      key: "error_enabled", patchKey: "error",
      label: "Error diagnostics",
      desc: "Scrubbed, content-free crash signatures (error type, code file, " +
            "function) so bugs get fixed. Never prompts or user data.",
    },
    {
      key: "telemetry_enabled", patchKey: "healing",
      label: "Self-healing traces",
      desc: "Anonymised self-healing events uploaded to CorvinLabs/CorvinLogs for " +
            "public transparency. No prompts, no message content, no PII.",
    },
  ];

  return (
    <Card>
      <CardContent className="pt-4 pb-3 space-y-3">
        <div>
          <span className="text-sm font-semibold">Telemetry &amp; privacy</span>
          <p className="text-[11px] text-muted-foreground mt-0.5">
            On by default so the project sees real usage and can fix bugs. Everything
            sent is anonymous and content-free (GDPR Art. 6(1)(f) legitimate interest).
            Turn any channel off here at any time.
          </p>
        </div>
        {rows.map((row) => {
          const enabled = (q.data?.[row.key] as boolean | undefined) ?? true;
          return (
            <div key={row.patchKey} className="flex items-center justify-between gap-4 border-t border-border/60 pt-3 first:border-t-0 first:pt-0">
              <div className="flex items-center gap-2 min-w-0">
                <Upload className="h-4 w-4 shrink-0 text-muted-foreground" />
                <div className="min-w-0">
                  <span className="text-sm font-medium">{row.label}</span>
                  <p className="text-[11px] text-muted-foreground mt-0.5">{row.desc}</p>
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {saving === row.patchKey && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
                {q.isLoading ? (
                  <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                ) : (
                  <Switch
                    checked={enabled}
                    onCheckedChange={(next) => toggle({ [row.key]: next }, row.patchKey)}
                    disabled={saving !== null}
                    aria-label={row.label}
                  />
                )}
              </div>
            </div>
          );
        })}
        {error && (
          <p className="mt-2 text-xs text-destructive bg-destructive/10 rounded px-2 py-1.5">{error}</p>
        )}
      </CardContent>
    </Card>
  );
}

function InstanceStatsCard() {
  const q = useQuery({
    queryKey: ["instance-stats"],
    queryFn: ({ signal }) => getInstanceStats(signal),
    refetchInterval: 300_000,   // refresh every 5 min
    retry: 1,
  });

  // If error or loading, show nothing (graceful degradation)
  if (q.isError || (!q.data && !q.isLoading)) return null;

  return (
    <Card>
      <CardContent className="pt-4 pb-3">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2 min-w-0">
            <Users className="h-4 w-4 shrink-0 text-muted-foreground" />
            <div>
              <p className="text-sm font-medium">Active CorvinOS instances</p>
              <p className="text-[11px] text-muted-foreground mt-0.5">
                Anonymised count across all opted-in installations.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3 shrink-0">
            {q.isLoading ? (
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            ) : q.data ? (
              <>
                <div className="text-right">
                  <p className="text-xs text-muted-foreground">7 days</p>
                  <p className="text-lg font-mono font-semibold tabular-nums">
                    ~{q.data.active_7d}
                  </p>
                </div>
                <div className="text-right">
                  <p className="text-xs text-muted-foreground">30 days</p>
                  <p className="text-lg font-mono font-semibold tabular-nums text-muted-foreground">
                    ~{q.data.active_30d}
                  </p>
                </div>
              </>
            ) : null}
          </div>
        </div>
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

/**
 * Worker engine — which engine performs a turn.
 *
 * `native` is the default: Claude Code does the work in-process and the only
 * auto-delegation left is big-data-shaped work, which still goes to ACS.
 * TDE only ever runs when it is selected here.
 */
const WORKER_ENGINE_COPY: Record<WorkerEngineMode, { label: string; desc: string }> = {
  native: {
    label: "Native (Claude Code)",
    desc: "Default. Claude Code does the work in-process. Only big-data-shaped " +
          "tasks are handed to ACS workers — everything else stays native.",
  },
  acs: {
    label: "ACS (manager + workers)",
    desc: "Substantial tasks fan out to ACS worker agents. Costs agentic-compute " +
          "units from the shared daily pool.",
  },
  tde: {
    label: "TDE (Tiered Delegation Engine)",
    desc: "Substantial tasks run as tiered, per-step routed delegation. Off " +
          "unless selected — falls back to native when TDE is unavailable or the " +
          "compute pool is exhausted.",
  },
};

function WorkerEngineCard({ csrf }: { csrf: string }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["worker-engine"],
    queryFn: ({ signal }) => getWorkerEngine(signal),
  });
  const [saving, setSaving] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const select = async (mode: WorkerEngineMode) => {
    if (mode === q.data?.mode) return;
    setError(null);
    setSaving(mode);
    try {
      await setWorkerEngine(mode, csrf);
      qc.invalidateQueries({ queryKey: ["worker-engine"] });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(null);
    }
  };

  const current = q.data?.mode ?? "native";
  const modes = q.data?.modes ?? (["native", "acs", "tde"] as WorkerEngineMode[]);

  return (
    <Card>
      <CardContent className="pt-4 pb-3 space-y-3">
        <div className="flex items-center gap-2">
          <Cpu className="h-4 w-4 shrink-0 text-muted-foreground" />
          <span className="text-sm font-medium">Worker engine</span>
          {q.isLoading && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
        </div>
        <div className="space-y-2">
          {modes.map((mode) => {
            const copy = WORKER_ENGINE_COPY[mode];
            const active = current === mode;
            return (
              <button
                key={mode}
                type="button"
                role="radio"
                aria-checked={active}
                disabled={saving !== null || q.isLoading}
                onClick={() => void select(mode)}
                className={cn(
                  "w-full rounded-md border px-3 py-2 text-left transition-colors",
                  active
                    ? "border-primary bg-primary/5"
                    : "border-border hover:bg-muted/50",
                  saving !== null && "opacity-60",
                )}
              >
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{copy?.label ?? mode}</span>
                  {mode === (q.data?.default ?? "native") && (
                    <Badge variant="secondary" className="text-[10px]">default</Badge>
                  )}
                  {active && <Check className="ml-auto h-4 w-4 text-primary" />}
                  {saving === mode && <Loader2 className="ml-auto h-4 w-4 animate-spin" />}
                </div>
                <p className="mt-0.5 text-[11px] text-muted-foreground">{copy?.desc}</p>
              </button>
            );
          })}
        </div>
        {error && (
          <p className="text-xs text-destructive bg-destructive/10 rounded px-2 py-1.5">{error}</p>
        )}
      </CardContent>
    </Card>
  );
}

/** A shell-command line with a copy button — used for the lock-out off-ramp. */
function CommandBlock({ command }: { command: string }) {
  const [copied, setCopied] = React.useState(false);
  return (
    <div className="flex items-center gap-2 rounded border border-border bg-muted/60 px-2.5 py-2">
      <code
        data-testid="feature-recovery-command"
        className="min-w-0 flex-1 select-all break-all font-mono text-[11px]"
      >
        {command}
      </code>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        className="h-6 shrink-0 px-2"
        aria-label="Copy command"
        onClick={() => {
          void navigator.clipboard?.writeText(command).then(
            () => {
              setCopied(true);
              window.setTimeout(() => setCopied(false), 1500);
            },
            () => undefined,   // clipboard blocked (non-secure context) — the code is select-all anyway
          );
        }}
      >
        {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
      </Button>
    </div>
  );
}

/**
 * Feature flags — new features ship dark; this is where they get switched on.
 *
 * SELF-LOCKING flags get a confirmation gate. A normal flag is reversible from
 * this same panel, so a bare switch is honest UI. `headless_api_mode` is not:
 * turning it on unmounts /console/, and the panel the operator just clicked is
 * gone on the next boot. Rendering that as an ordinary checkbox is the actual
 * defect — it promises a reversibility the flag does not have.
 *
 * So enabling a self-locking flag requires an explicit confirmation that names
 * the consequence AND shows the CLI off-ramp *before* the door shuts, which is
 * the only moment the operator can still read it here. Disabling one is also
 * confirmed, because it is a boot-affecting deployment change — but the tone
 * there is a restart notice, not a warning.
 *
 * Which flags are self-locking is decided by the backend registry
 * (`feature_flags.FeatureFlag.self_locking`), never by a flag id hard-coded in
 * this file — otherwise the next self-locking flag ships without the warning.
 */
export function FeatureFlagsCard({ csrf }: { csrf: string }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["feature-flags"],
    queryFn: ({ signal }) => getFeatureFlags(signal),
  });
  const [saving, setSaving] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  // Pending self-locking toggle awaiting confirmation. null = no dialog open.
  const [pending, setPending] = React.useState<{ flag: FeatureFlagState; next: boolean } | null>(null);

  const apply = async (id: string, enabled: boolean) => {
    setError(null);
    setSaving(id);
    try {
      await setFeatureFlag(id, enabled, csrf);
      qc.invalidateQueries({ queryKey: ["feature-flags"] });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(null);
    }
  };

  /**
   * Every switch goes through here. A self-locking flag is diverted into the
   * dialog instead of being written straight through — fail-closed: nothing is
   * persisted until the operator confirms.
   */
  const requestToggle = (f: FeatureFlagState, next: boolean) => {
    if (f.self_locking) {
      setError(null);
      setPending({ flag: f, next });
      return;
    }
    void apply(f.id, next);
  };

  const confirmPending = async () => {
    if (!pending) return;
    const { flag, next } = pending;
    setPending(null);
    await apply(flag.id, next);
  };

  const features = q.data?.features ?? [];

  return (
    <Card>
      <CardContent className="pt-4 pb-3 space-y-3">
        <div className="flex items-center gap-2">
          <FlaskConical className="h-4 w-4 shrink-0 text-muted-foreground" />
          <span className="text-sm font-medium">Optional features</span>
          {q.isLoading && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
        </div>
        <p className="text-[11px] text-muted-foreground">
          New features ship switched off, so an update never changes how your install
          behaves. Turn one on here when you want it; off restores the previous behavior.
        </p>
        {!q.isLoading && features.length === 0 && (
          <p className="text-xs text-muted-foreground bg-muted/50 rounded px-2 py-1.5">
            No optional features on this version.
          </p>
        )}
        <div className="space-y-2">
          {features.map((f) => (
            <div key={f.id} className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  {f.self_locking && (
                    <span data-testid={`feature-warning-${f.id}`} title="Removes the Console web interface — needs the CLI to undo">
                      <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-500" aria-label="Self-locking feature" />
                    </span>
                  )}
                  <span className="text-sm font-medium">{f.label}</span>
                  <Badge variant="outline" className="font-mono text-[10px]">{f.id}</Badge>
                  {f.source === "tenant_yaml" && (
                    <Badge variant="secondary" className="text-[10px]">from tenant.corvin.yaml</Badge>
                  )}
                  {f.self_locking && (
                    <Badge variant="secondary" className="text-[10px] text-amber-600 dark:text-amber-400">
                      no way back from the UI
                    </Badge>
                  )}
                </div>
                <p className="mt-0.5 text-[11px] text-muted-foreground">{f.description}</p>
                {f.self_locking && f.recovery_command && (
                  <p className="mt-1 text-[11px] text-amber-600 dark:text-amber-400">
                    Undo needs a terminal:{" "}
                    <code className="select-all font-mono">{f.recovery_command}</code>
                  </p>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {saving === f.id && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
                <Switch
                  checked={f.enabled}
                  onCheckedChange={(next) => requestToggle(f, next)}
                  disabled={saving !== null}
                  aria-label={f.label}
                />
              </div>
            </div>
          ))}
        </div>
        {error && (
          <p className="text-xs text-destructive bg-destructive/10 rounded px-2 py-1.5">{error}</p>
        )}
      </CardContent>

      {/* Confirmation gate — nothing is written until the operator confirms. */}
      <Dialog open={pending !== null} onOpenChange={(open) => { if (!open) setPending(null); }}>
        <DialogContent className="max-w-lg" data-testid="feature-self-lock-dialog">
          {pending && (
            <>
              <DialogHeader>
                <DialogTitle className="flex items-center gap-2">
                  {pending.next && <AlertTriangle className="h-5 w-5 shrink-0 text-amber-500" />}
                  {pending.next
                    ? `Turn on ${pending.flag.label}?`
                    : `Turn off ${pending.flag.label}?`}
                </DialogTitle>
                <DialogDescription>
                  {pending.next ? (
                    <>
                      This disables the Console web interface. After the next restart
                      there is no <code className="font-mono">/console/</code> page — so
                      you cannot come back to this panel to switch it off again. The REST
                      API stays available.
                    </>
                  ) : (
                    <>
                      Turning off API-Only Mode re-enables the web interface. Restart the
                      service for the Console to be served again.
                    </>
                  )}
                </DialogDescription>
              </DialogHeader>

              {pending.next && pending.flag.recovery_command && (
                <div className="space-y-1.5">
                  <p className="text-xs font-medium">
                    Write this down first — it is the only way back:
                  </p>
                  <CommandBlock command={pending.flag.recovery_command} />
                  <p className="text-[11px] text-muted-foreground">
                    Run it on the machine hosting Corvin, then restart the service.
                  </p>
                </div>
              )}

              <DialogFooter>
                <Button variant="ghost" onClick={() => setPending(null)} disabled={saving !== null}>
                  Cancel
                </Button>
                <Button
                  variant={pending.next ? "destructive" : "default"}
                  onClick={() => void confirmPending()}
                  disabled={saving !== null}
                  data-testid="feature-self-lock-confirm"
                >
                  {pending.next ? "Disable the web interface" : "Re-enable the web interface"}
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>
    </Card>
  );
}

export function SettingsPage() {
  const { session } = useAuth();
  const navigate = useNavigate();
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
        <h2 className="text-sm font-semibold text-foreground">Integrations</h2>
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-start justify-between">
              <div className="space-y-1.5">
                <div className="flex items-center gap-2">
                  <svg className="h-5 w-5" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M12 0c-6.626 0-12 5.373-12 12 0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23.957-.266 1.983-.399 3.003-.404 1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v 3.293c0 .319.192.694.801.576 4.765-1.589 8.199-6.086 8.199-11.386 0-6.627-5.373-12-12-12z"/>
                  </svg>
                  <h3 className="font-semibold">Tenant Repository Sync</h3>
                </div>
                <p className="text-sm text-muted-foreground">
                  Connect your tenant-native skills to a GitHub repository for version control, collaboration, and automated sync across instances.
                </p>
              </div>
              <Button
                onClick={() => navigate("/app/settings/github")}
                className="whitespace-nowrap ml-4"
              >
                Configure GitHub
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>

      <div className="space-y-2">
        <h2 className="text-sm font-semibold text-foreground">Updates</h2>
        <AutoUpdateCard csrf={session!.csrf_token} />
      </div>

      <div className="space-y-2">
        <h2 className="text-sm font-semibold text-foreground">Autostart</h2>
        <ServiceTierCard csrf={session!.csrf_token} />
      </div>

      <div className="space-y-2">
        <h2 className="text-sm font-semibold text-foreground">Self-healing</h2>
        <HealingCard csrf={session!.csrf_token} />
        <TelemetryCard csrf={session!.csrf_token} />
        <InstanceStatsCard />
      </div>

      <div className="space-y-2">
        <h2 className="text-sm font-semibold text-foreground">Worker Engine</h2>
        <WorkerEngineCard csrf={session!.csrf_token} />
      </div>

      <div className="space-y-2">
        <h2 className="text-sm font-semibold text-foreground">Features</h2>
        <FeatureFlagsCard csrf={session!.csrf_token} />
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

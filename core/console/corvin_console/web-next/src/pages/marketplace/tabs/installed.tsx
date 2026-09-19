/**
 * Installed tab (ADR-0892 D3) — the tenant registry plus what is registered
 * in this process (`/plugins`, plugins.py). Enable / disable / uninstall /
 * settings go through the existing CSRF-signed routes; every one changes
 * registry.yaml (and hot-loads or unloads the plugin) and writes a
 * hash-chained plugin.* event. `runtime_loaded` is shown NEXT TO `enabled`
 * because self-healing may unload a plugin without touching the operator's
 * registry — showing only the flag would be the silent false display
 * hot-reload was introduced to remove.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { SettingsForm } from "../components/settings-form";
import { ApiError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { KEY_INDEX, KEY_INSTALLED } from "../header";
import { KEY_CAPABILITIES, KEY_MANIFEST } from "./browse";
import {
  disablePlugin, enablePlugin, getPluginHealth, listInstalledPlugins, uninstallPlugin,
  updatePluginSettings, type PluginSummary,
} from "../api";
import type { TabId } from "../tabs";

function actionError(e: unknown, fallback: string): string {
  if (e instanceof ApiError && e.status === 403) return "Not allowed — the compliance layer or your role refuses this change.";
  if (e instanceof ApiError && e.status === 409) return "Refused — the plugin's current state does not allow this (disable it before uninstalling).";
  if (e instanceof ApiError && e.status === 404) return "Not available — the plugin console surface is switched off on this build.";
  return fallback;
}

function Row({ p, csrf, lifecycleEnabled, health }: {
  p: PluginSummary; csrf: string; lifecycleEnabled: boolean;
  health?: { ok: boolean; message: string } | undefined;
}) {
  const qc = useQueryClient();
  const [msg, setMsg] = useState<string | null>(null);
  const [confirmUninstall, setConfirmUninstall] = useState(false);
  const [editing, setEditing] = useState(false);
  // A plugin that declares a console panel puts it in the sidebar on enable and
  // takes it out on disable/uninstall (state.PluginLifecycle._sync_console_panel)
  // — the sidebar reads the manifest, so both manifest queries are refreshed.
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: [...KEY_INSTALLED] });
    qc.invalidateQueries({ queryKey: [...KEY_INDEX] });
    qc.invalidateQueries({ queryKey: [...KEY_MANIFEST] });
    qc.invalidateQueries({ queryKey: [...KEY_CAPABILITIES] });
  };
  const enable = useMutation({
    mutationFn: () => enablePlugin(p.plugin_id, csrf, p.requires_consent),
    onSuccess: () => { setMsg("Enabled — audited."); invalidate(); },
    onError: (e) => setMsg(actionError(e, "Not enabled — the plugin failed to load; the registry was rolled back.")),
  });
  const disable = useMutation({
    mutationFn: () => disablePlugin(p.plugin_id, csrf),
    onSuccess: () => { setMsg("Disabled — audited."); invalidate(); },
    onError: (e) => setMsg(actionError(e, "Not disabled.")),
  });
  const uninstall = useMutation({
    mutationFn: () => uninstallPlugin(p.plugin_id, csrf),
    onSuccess: () => { setMsg("Uninstalled — the record and its instance directory are gone; the audit trail is retained."); invalidate(); },
    onError: (e) => setMsg(actionError(e, "Not uninstalled.")),
  });
  const save = useMutation({
    mutationFn: (settings: Record<string, unknown>) => updatePluginSettings(p.plugin_id, settings, csrf),
    onSuccess: () => { setMsg("Settings saved — audited."); setEditing(false); invalidate(); },
    onError: (e) => setMsg(actionError(e, "Not saved — a value does not match the plugin's settings schema.")),
  });
  const busy = enable.isPending || disable.isPending || uninstall.isPending || save.isPending;
  const canMutate = lifecycleEnabled && !!csrf && !busy;

  return (
    <Card data-testid={`installed-row-${p.plugin_id}`}>
      <CardContent className="p-4 space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="font-semibold">{p.display_name || p.plugin_id}</div>
            <div className="text-xs text-muted-foreground font-mono">{p.plugin_id} · v{p.version} · {p.plugin_type}</div>
          </div>
          <div className="flex flex-wrap gap-1">
            <Badge variant="outline" title="Provenance, not a capability tier">{p.origin}</Badge>
            <Badge variant={p.enabled ? "secondary" : "outline"}>{p.enabled ? "enabled" : "disabled"}</Badge>
            <Badge variant={p.runtime_loaded ? "secondary" : "outline"}>{p.runtime_loaded ? "running" : "not running"}</Badge>
            {p.contained_by && <Badge variant="danger">{p.contained_by}</Badge>}
          </div>
        </div>

        <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-0.5 text-xs">
          <dt className="text-muted-foreground">Runs</dt><dd>{p.locality || "—"}</dd>
          <dt className="text-muted-foreground">Network egress</dt>
          <dd>{p.network_egress || "—"}{p.egress_hosts?.length ? ` → ${p.egress_hosts.join(", ")}` : ""}</dd>
          <dt className="text-muted-foreground">PII risk</dt><dd>{p.pii_risk || "—"}</dd>
          {p.requires_consent && (<><dt className="text-muted-foreground">Consent</dt><dd>enabling records your consent (non-builtin origin)</dd></>)}
          {p.installed_at && (<><dt className="text-muted-foreground">Installed</dt><dd>{new Date(p.installed_at).toLocaleString("en-US")}</dd></>)}
          {p.last_error_type && (<><dt className="text-muted-foreground">Last error</dt><dd className="font-mono">{p.last_error_type}</dd></>)}
          {health && (<><dt className="text-muted-foreground">Health</dt><dd>{health.ok ? "ok" : `unhealthy — ${health.message}`}</dd></>)}
        </dl>

        <div className="flex flex-wrap gap-2">
          {p.enabled ? (
            <Button size="sm" variant="secondary" disabled={!canMutate} onClick={() => disable.mutate()}>Disable</Button>
          ) : (
            <Button size="sm" variant="accent" disabled={!canMutate} onClick={() => enable.mutate()}>
              {p.requires_consent ? "Enable (with consent)" : "Enable"}
            </Button>
          )}
          <Button size="sm" variant="outline" disabled={!canMutate} onClick={() => setEditing((v) => !v)}>
            {editing ? "Close settings" : "Settings"}
          </Button>
          {!confirmUninstall ? (
            <Button size="sm" variant="outline" disabled={!canMutate || p.enabled}
                    title={p.enabled ? "Disable the plugin first" : undefined}
                    onClick={() => setConfirmUninstall(true)}>Uninstall</Button>
          ) : (
            <>
              <Button size="sm" variant="destructive" disabled={!canMutate} onClick={() => { setConfirmUninstall(false); uninstall.mutate(); }}>
                Confirm: remove {p.plugin_id} from this tenant
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirmUninstall(false)}>Cancel</Button>
            </>
          )}
          {busy && <Loader2 className="w-4 h-4 animate-spin text-muted-foreground self-center" />}
        </div>

        {editing && (
          <SettingsForm id={p.plugin_id} schema={p.settings_schema} value={p.settings ?? {}}
                        saving={!canMutate} onSave={(settings) => save.mutate(settings)} />
        )}

        {msg && <p className="text-xs text-muted-foreground" data-testid={`installed-msg-${p.plugin_id}`}>{msg}</p>}
      </CardContent>
    </Card>
  );
}

export function InstalledTab({ onGoTo }: { onGoTo: (tab: TabId) => void }) {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const q = useQuery({ queryKey: [...KEY_INSTALLED], queryFn: ({ signal }) => listInstalledPlugins(signal), retry: false });
  const health = useQuery({ queryKey: ["marketplace", "plugin-health"], queryFn: ({ signal }) => getPluginHealth(signal), retry: false });

  if (q.isLoading) {
    return <div className="py-16 flex justify-center"><Loader2 className="w-8 h-8 animate-spin text-muted-foreground" /></div>;
  }
  if (q.isError) {
    const off = q.error instanceof ApiError && q.error.status === 404;
    return (
      <Card className="border-destructive/30 bg-destructive/10">
        <CardContent className="py-6 flex items-center gap-2 text-destructive text-sm">
          <AlertCircle size={18} />
          {off ? "The plugin console surface is switched off on this build." : "The installed plugins could not be loaded — the registry may be unreadable; nothing was changed."}
        </CardContent>
      </Card>
    );
  }
  const plugins = q.data?.plugins ?? [];
  const lifecycleEnabled = q.data?.lifecycle_enabled ?? false;

  return (
    <div className="space-y-4">
      {!lifecycleEnabled && (
        <p className="text-xs text-amber-600 dark:text-amber-400 flex items-center gap-1.5">
          <AlertCircle size={12} /> Runtime plugin changes are switched off (plugin_runtime_lifecycle) — this tab is read-only.
        </p>
      )}
      <p className="text-xs text-muted-foreground" data-testid="installed-summary">
        {plugins.length} plugins — registry records of this tenant plus builtins running in this process.
        {health.data && !health.data.monitoring_enabled && " Health monitoring is off."}
        {" "}<button type="button" className="underline" onClick={() => onGoTo("browse")}>Browse the index</button> to install more.
      </p>
      {plugins.length === 0 ? (
        <div className="py-10 text-center text-sm text-muted-foreground border border-dashed border-border rounded-lg">
          No plugin is installed for this tenant.
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {plugins.map((p) => (
            <Row key={p.plugin_id} p={p} csrf={csrf} lifecycleEnabled={lifecycleEnabled}
                 health={health.data?.plugins?.[p.plugin_id]} />
          ))}
        </div>
      )}
    </div>
  );
}

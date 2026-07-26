/**
 * Plugins — console UI for the per-tenant plugin registry (ADR-0233 Phase 4).
 *
 * Behind the `plugin_console_surface` feature flag. When the flag is off the
 * REST routes answer 404, and this page says so plainly instead of rendering an
 * error — a dark feature is absent, not broken.
 *
 * Two flags, two levels of capability:
 *   • plugin_console_surface   → this page and its routes exist at all
 *   • plugin_runtime_lifecycle → mutations allowed; when off the page is read-only
 */

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Puzzle,
  RefreshCw,
  Save,
  ShieldAlert,
  ToggleLeft,
  ToggleRight,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { JsonSchemaForm } from "@/components/JsonSchemaForm";
import {
  ApiError,
  disablePlugin,
  enablePlugin,
  listPlugins,
  PluginSummary,
  uninstallPlugin,
  updatePluginSettings,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

const ORIGIN_BADGE: Record<string, string> = {
  builtin: "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200",
  vetted: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200",
  community: "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200",
};

const PII_BADGE: Record<string, string> = {
  none: "bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300",
  low: "bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300",
  medium: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200",
  high: "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200",
};

function Badge({ text, className }: { text: string; className?: string }) {
  return (
    <span
      className={cn(
        "rounded px-1.5 py-0.5 text-xs font-medium",
        className ?? "bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300",
      )}
    >
      {text}
    </span>
  );
}

// ── One plugin card ───────────────────────────────────────────────────────────

interface CardProps {
  plugin: PluginSummary;
  csrf: string;
  mutable: boolean;
}

function PluginCard({ plugin, csrf, mutable }: CardProps) {
  const qc = useQueryClient();
  const [draft, setDraft] = React.useState<Record<string, unknown>>(plugin.settings);
  const [fieldErrors, setFieldErrors] = React.useState<Record<string, string>>({});
  const [notice, setNotice] = React.useState<string | null>(null);

  // Re-sync when the server's copy changes (another tab, another operator).
  React.useEffect(() => {
    setDraft(plugin.settings);
  }, [plugin.settings]);

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["plugins"] });
  };

  const enableMut = useMutation({
    mutationFn: (consent: boolean) => enablePlugin(plugin.plugin_id, csrf, consent),
    onSuccess: () => {
      setNotice(null);
      invalidate();
    },
    onError: (err: unknown) => {
      // 409 on a consent-gated plugin is not a failure — it is the gate asking.
      if (err instanceof ApiError && err.status === 409) {
        setNotice(
          `${plugin.display_name} needs explicit consent: origin ${plugin.origin}, ` +
            `PII risk ${plugin.pii_risk}. Confirm to enable it.`,
        );
      } else {
        setNotice(err instanceof Error ? err.message : "enable failed");
      }
    },
  });

  const disableMut = useMutation({
    mutationFn: () => disablePlugin(plugin.plugin_id, csrf),
    onSuccess: invalidate,
    onError: (err: unknown) =>
      setNotice(err instanceof Error ? err.message : "disable failed"),
  });

  const saveMut = useMutation({
    mutationFn: () => updatePluginSettings(plugin.plugin_id, draft, csrf),
    onSuccess: () => {
      setFieldErrors({});
      setNotice("Settings saved.");
      invalidate();
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError && err.status === 422) {
        setNotice(`Rejected: ${err.message}`);
      } else {
        setNotice(err instanceof Error ? err.message : "save failed");
      }
    },
  });

  const removeMut = useMutation({
    mutationFn: () => uninstallPlugin(plugin.plugin_id, csrf),
    onSuccess: invalidate,
    onError: (err: unknown) =>
      setNotice(err instanceof Error ? err.message : "uninstall failed"),
  });

  const dirty = JSON.stringify(draft) !== JSON.stringify(plugin.settings);
  const busy =
    enableMut.isPending || disableMut.isPending || saveMut.isPending || removeMut.isPending;

  return (
    <Card>
      <CardContent className="space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <Puzzle className="h-4 w-4 shrink-0 text-muted-foreground" />
          <span className="font-medium">{plugin.display_name}</span>
          <span className="text-xs text-muted-foreground">v{plugin.version}</span>
          <Badge text={plugin.plugin_type} />
          <Badge text={plugin.origin} className={ORIGIN_BADGE[plugin.origin]} />
          <Badge text={`PII: ${plugin.pii_risk}`} className={PII_BADGE[plugin.pii_risk]} />
          {plugin.requires_consent && (
            <span className="flex items-center gap-1 text-xs text-amber-600 dark:text-amber-500">
              <ShieldAlert className="h-3 w-3" /> consent required
            </span>
          )}
          {plugin.enabled && !plugin.runtime_loaded && (
            <span className="flex items-center gap-1 text-xs text-amber-600 dark:text-amber-500">
              <AlertTriangle className="h-3 w-3" /> enabled but not running
              {plugin.contained_by === "healing_unloaded" && " — disabled by self-healing"}
              {plugin.contained_by === "healing_escalated" && " — self-healing gave up, needs a look"}
              {plugin.contained_by === "not_loaded" && " — nothing loaded it (no class_path, or boot has not reached it)"}
            </span>
          )}
          {plugin.enabled && plugin.runtime_loaded && plugin.contained_by?.startsWith("breaker_") && (
            <span className="flex items-center gap-1 text-xs text-amber-600 dark:text-amber-500">
              <AlertTriangle className="h-3 w-3" /> circuit {plugin.contained_by.replace("breaker_", "")}
            </span>
          )}
          {plugin.last_error_type && (
            <span className="flex items-center gap-1 text-xs text-red-600 dark:text-red-400">
              <AlertTriangle className="h-3 w-3" /> last error: {plugin.last_error_type}
            </span>
          )}

          <div className="ml-auto flex items-center gap-2">
            {plugin.enabled ? (
              <Button
                variant="outline"
                size="sm"
                disabled={!mutable || busy}
                onClick={() => disableMut.mutate()}
              >
                <ToggleRight className="mr-1 h-4 w-4 text-green-600" /> Enabled
              </Button>
            ) : (
              <Button
                variant="outline"
                size="sm"
                disabled={!mutable || busy}
                onClick={() => enableMut.mutate(false)}
              >
                <ToggleLeft className="mr-1 h-4 w-4" /> Disabled
              </Button>
            )}
            <Button
              variant="ghost"
              size="sm"
              disabled={!mutable || busy || plugin.enabled}
              title={
                plugin.enabled ? "Disable the plugin before uninstalling" : "Uninstall"
              }
              onClick={() => removeMut.mutate()}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        </div>

        {plugin.dependencies.length > 0 && (
          <p className="text-xs text-muted-foreground">
            Depends on: {plugin.dependencies.join(", ")}
          </p>
        )}

        {notice && (
          <div className="space-y-2 rounded border border-amber-300 bg-amber-50 p-2 text-xs dark:border-amber-800 dark:bg-amber-950">
            <p>{notice}</p>
            {enableMut.isError && plugin.requires_consent && !plugin.enabled && (
              <Button
                size="sm"
                variant="outline"
                disabled={!mutable || busy}
                onClick={() => enableMut.mutate(true)}
              >
                I understand — enable anyway
              </Button>
            )}
          </div>
        )}

        <div className="rounded border p-3">
          <JsonSchemaForm
            schema={plugin.settings_schema}
            values={draft}
            errors={fieldErrors}
            disabled={!mutable || busy}
            onChange={(key, value) => setDraft((prev) => ({ ...prev, [key]: value }))}
          />
          {Object.keys((plugin.settings_schema?.properties as object) ?? {}).length > 0 && (
            <div className="mt-3 flex items-center gap-2">
              <Button
                size="sm"
                disabled={!mutable || busy || !dirty}
                onClick={() => saveMut.mutate()}
              >
                <Save className="mr-1 h-4 w-4" /> Save
              </Button>
              {dirty && (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busy}
                  onClick={() => setDraft(plugin.settings)}
                >
                  Reset
                </Button>
              )}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export function PluginsPage() {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? null;
  const qc = useQueryClient();

  const { data, isLoading, error } = useQuery({
    queryKey: ["plugins"],
    queryFn: ({ signal }) => listPlugins(signal),
    retry: false,
  });

  // 404 means the surface flag is off — say that, don't show a red error.
  const surfaceOff = error instanceof ApiError && error.status === 404;

  return (
    <div className="space-y-4 p-4">
      <div className="flex items-center gap-2">
        <Puzzle className="h-5 w-5" />
        <h1 className="text-lg font-semibold">Plugins</h1>
        <Button
          variant="ghost"
          size="sm"
          className="ml-auto"
          onClick={() => void qc.invalidateQueries({ queryKey: ["plugins"] })}
        >
          <RefreshCw className="h-4 w-4" />
        </Button>
      </div>

      {isLoading && (
        <div className="space-y-2">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      )}

      {surfaceOff && (
        <Card>
          <CardContent className="p-4 text-sm">
            The plugin surface is switched off. Enable{" "}
            <code className="rounded bg-muted px-1">plugin_console_surface</code> in
            Settings → Features to manage plugins from here.
          </CardContent>
        </Card>
      )}

      {error && !surfaceOff && (
        <Card>
          <CardContent className="p-4 text-sm text-red-600 dark:text-red-400">
            {error instanceof Error ? error.message : "Failed to load plugins."}
          </CardContent>
        </Card>
      )}

      {data && !data.lifecycle_enabled && (
        <Card>
          <CardContent className="p-4 text-sm">
            Read-only: runtime changes are switched off. Enable{" "}
            <code className="rounded bg-muted px-1">plugin_runtime_lifecycle</code> in
            Settings → Features to install, enable or reconfigure plugins.
          </CardContent>
        </Card>
      )}

      {data && data.total === 0 && (
        <Card>
          <CardContent className="p-4 text-sm text-muted-foreground">
            No plugins installed for this tenant.
          </CardContent>
        </Card>
      )}

      {data?.plugins.map((plugin) => (
        <PluginCard
          key={plugin.plugin_id}
          plugin={plugin}
          csrf={csrf ?? ""}
          mutable={Boolean(csrf) && data.lifecycle_enabled}
        />
      ))}
    </div>
  );
}

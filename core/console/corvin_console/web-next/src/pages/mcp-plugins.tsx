/**
 * MCP Plugin Manager — console UI (ADR-0096 M3).
 *
 * Shows all installed MCP tools with their activation status per scope.
 * Allows installing new tools, activating/deactivating per scope, and removal.
 */

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Package,
  Plus,
  RefreshCw,
  Trash2,
  ToggleLeft,
  ToggleRight,
  XCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  activateMcpPlugin,
  deactivateMcpPlugin,
  installMcpPlugin,
  listMcpPlugins,
  McpToolSummary,
  removeMcpPlugin,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

// ── Helpers ───────────────────────────────────────────────────────────────────

const SCOPES = ["user", "session", "project", "tenant"] as const;

const LOCALITY_BADGE: Record<string, string> = {
  local: "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200",
  eu_cloud: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200",
  us_cloud: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200",
  unknown: "bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300",
};

function LocalityBadge({ locality }: { locality?: string }) {
  const loc = locality || "unknown";
  return (
    <span className={cn("text-xs px-2 py-0.5 rounded font-mono", LOCALITY_BADGE[loc] || LOCALITY_BADGE.unknown)}>
      {loc}
    </span>
  );
}

// ── Install dialog ────────────────────────────────────────────────────────────

function InstallForm({ csrf, onDone }: { csrf: string; onDone: () => void }) {
  const [source, setSource] = React.useState("");
  const [allowUnpin, setAllowUnpin] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const install = useMutation({
    mutationFn: () => installMcpPlugin(source.trim(), csrf, allowUnpin),
    onSuccess: () => {
      setSource("");
      setError(null);
      onDone();
    },
    onError: (err: Error) => setError(err.message),
  });

  return (
    <div className="border rounded-lg p-4 bg-muted/30 space-y-3">
      <p className="text-sm font-medium">Install new MCP tool</p>
      <div className="flex gap-2">
        <input
          className="flex-1 border rounded px-3 py-1.5 text-sm bg-background focus:outline-none focus:ring-1 focus:ring-primary"
          placeholder="npm:@scope/pkg@ver  |  github:owner/repo@v1.2  |  pip:mcp-tool@1.0  |  local:/path"
          value={source}
          onChange={(e) => setSource(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !install.isPending && install.mutate()}
        />
        <Button
          size="sm"
          disabled={!source.trim() || install.isPending}
          onClick={() => install.mutate()}
        >
          {install.isPending ? <RefreshCw className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />}
          Install
        </Button>
      </div>
      <label className="flex items-center gap-2 text-xs text-muted-foreground cursor-pointer">
        <input
          type="checkbox"
          checked={allowUnpin}
          onChange={(e) => setAllowUnpin(e.target.checked)}
          className="rounded"
        />
        Allow unverified GitHub branch installs (--allow-unpin)
      </label>
      {error && (
        <div className="flex items-start gap-2 text-destructive text-xs">
          <AlertCircle className="h-3 w-3 mt-0.5 shrink-0" />
          {error}
        </div>
      )}
    </div>
  );
}

// ── Tool card ─────────────────────────────────────────────────────────────────

function ToolCard({ tool, csrf }: { tool: McpToolSummary; csrf: string }) {
  const [open, setOpen] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const qc = useQueryClient();

  const refresh = () => qc.invalidateQueries({ queryKey: ["mcp-plugins"] });

  const activate = useMutation({
    mutationFn: (scope: string) => activateMcpPlugin(tool.id, scope, csrf),
    onSuccess: () => { setError(null); refresh(); },
    onError: (err: Error) => setError(err.message),
  });

  const deactivate = useMutation({
    mutationFn: (scope: string) => deactivateMcpPlugin(tool.id, scope, csrf),
    onSuccess: () => { setError(null); refresh(); },
    onError: (err: Error) => setError(err.message),
  });

  const remove = useMutation({
    mutationFn: () => removeMcpPlugin(tool.id, csrf),
    onSuccess: () => { setError(null); refresh(); },
    onError: (err: Error) => setError(err.message),
  });

  const isBusy = activate.isPending || deactivate.isPending || remove.isPending;

  return (
    <div className="border rounded-lg overflow-hidden">
      <button
        className="w-full flex items-center justify-between gap-2 px-4 py-3 bg-muted/20 hover:bg-muted/40 transition-colors text-left"
        onClick={() => setOpen((o) => !o)}
      >
        <div className="flex items-center gap-3 min-w-0">
          <Package className="h-4 w-4 shrink-0 text-muted-foreground" />
          <span className="font-mono text-sm font-medium truncate">{tool.id}</span>
          {tool.active ? (
            <CheckCircle2 className="h-4 w-4 text-green-500 shrink-0" />
          ) : (
            <XCircle className="h-4 w-4 text-muted-foreground shrink-0" />
          )}
          <LocalityBadge locality={tool.compliance?.locality} />
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-xs text-muted-foreground hidden sm:block truncate max-w-[200px]">
            {tool.source}
          </span>
          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </div>
      </button>

      {open && (
        <div className="px-4 py-3 space-y-4 bg-background">
          {/* Source + runtime */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
            <div>
              <p className="text-muted-foreground mb-1">Source</p>
              <p className="font-mono break-all">{tool.source}</p>
            </div>
            {tool.runtime && (
              <div>
                <p className="text-muted-foreground mb-1">Runtime</p>
                <p className="font-mono">{tool.runtime.command} {tool.runtime.args?.join(" ")}</p>
              </div>
            )}
            <div>
              <p className="text-muted-foreground mb-1">Installed</p>
              <p>{tool.installed_at ? new Date(tool.installed_at).toLocaleDateString("de-DE") : "—"}</p>
            </div>
            <div>
              <p className="text-muted-foreground mb-1">Network egress</p>
              <p className="font-mono">{tool.compliance?.network_egress || "unknown"}</p>
            </div>
          </div>

          {/* Secrets */}
          {tool.secrets.length > 0 && (
            <div>
              <p className="text-xs text-muted-foreground mb-1">Secrets required</p>
              <div className="flex flex-wrap gap-2">
                {tool.secrets.map((s) => (
                  <span key={s.name} className="text-xs font-mono px-2 py-0.5 rounded bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-200">
                    {s.name}{s.required ? " *" : ""}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Scope toggles */}
          <div>
            <p className="text-xs text-muted-foreground mb-2">Activation scopes</p>
            <div className="flex flex-wrap gap-2">
              {SCOPES.map((scope) => {
                const active = tool.active_scopes.includes(scope);
                return (
                  <button
                    key={scope}
                    disabled={isBusy}
                    onClick={() => active ? deactivate.mutate(scope) : activate.mutate(scope)}
                    className={cn(
                      "flex items-center gap-1.5 text-xs px-3 py-1.5 rounded border transition-colors",
                      active
                        ? "bg-primary text-primary-foreground border-primary"
                        : "bg-background text-muted-foreground border-border hover:border-primary",
                      isBusy && "opacity-50 cursor-not-allowed",
                    )}
                  >
                    {active
                      ? <ToggleRight className="h-3 w-3" />
                      : <ToggleLeft className="h-3 w-3" />}
                    {scope}
                  </button>
                );
              })}
            </div>
          </div>

          {error && (
            <div className="flex items-start gap-2 text-destructive text-xs">
              <AlertCircle className="h-3 w-3 mt-0.5 shrink-0" />
              {error}
            </div>
          )}

          {/* Remove */}
          <div className="flex justify-end pt-1 border-t">
            <Button
              variant="ghost"
              size="sm"
              disabled={remove.isPending}
              onClick={() => remove.mutate()}
              className="text-destructive hover:text-destructive hover:bg-destructive/10"
            >
              <Trash2 className="h-3 w-3 mr-1" />
              Remove
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function McpPluginsPage() {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? null;
  const [showInstall, setShowInstall] = React.useState(false);
  const qc = useQueryClient();

  const { data, isLoading, error } = useQuery({
    queryKey: ["mcp-plugins"],
    queryFn: ({ signal }) => listMcpPlugins(signal),
    staleTime: 10_000,
  });

  if (isLoading) {
    return (
      <div className="max-w-3xl mx-auto p-6 space-y-4">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="max-w-3xl mx-auto p-6">
        <Card>
          <CardContent className="pt-6 flex items-center gap-3 text-destructive">
            <AlertCircle className="h-5 w-5 shrink-0" />
            <p className="text-sm">{(error as Error).message}</p>
          </CardContent>
        </Card>
      </div>
    );
  }

  const tools = data?.tools ?? [];
  const active = tools.filter((t) => t.active).length;

  return (
    <div className="max-w-3xl mx-auto p-6 space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">MCP Plugins</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {tools.length} installed · {active} active
            {data?.tenant_id && ` · tenant: ${data.tenant_id}`}
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => qc.invalidateQueries({ queryKey: ["mcp-plugins"] })}
          >
            <RefreshCw className="h-3 w-3 mr-1" />
            Refresh
          </Button>
          <Button size="sm" onClick={() => setShowInstall((s) => !s)}>
            <Plus className="h-3 w-3 mr-1" />
            Install
          </Button>
        </div>
      </div>

      {/* Install form */}
      {showInstall && csrf && (
        <InstallForm
          csrf={csrf}
          onDone={() => {
            setShowInstall(false);
            qc.invalidateQueries({ queryKey: ["mcp-plugins"] });
          }}
        />
      )}

      {/* Tool list */}
      {tools.length === 0 ? (
        <Card>
          <CardContent className="pt-6 text-center text-muted-foreground text-sm py-12">
            <Package className="h-8 w-8 mx-auto mb-3 opacity-40" />
            No MCP tools installed yet.
            <br />
            Use the Install button or{" "}
            <code className="font-mono text-xs">corvin-mcp install npm:&lt;package&gt;</code>.
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {tools.map((tool) =>
            csrf ? (
              <ToolCard key={tool.id} tool={tool} csrf={csrf} />
            ) : (
              <div key={tool.id} className="border rounded-lg px-4 py-3 flex items-center gap-3">
                <Package className="h-4 w-4 text-muted-foreground" />
                <span className="font-mono text-sm">{tool.id}</span>
                <LocalityBadge locality={tool.compliance?.locality} />
              </div>
            )
          )}
        </div>
      )}

      {/* Reference */}
      <p className="text-xs text-muted-foreground">
        CLI: <code className="font-mono">corvin-mcp install|activate|deactivate|list|show|remove|secrets</code>
        &nbsp;·&nbsp;Scopes: session → project → user → tenant (narrowest-wins at spawn)
      </p>
    </div>
  );
}

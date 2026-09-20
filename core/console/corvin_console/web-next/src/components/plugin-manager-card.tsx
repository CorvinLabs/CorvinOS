/**
 * Plugin Manager Card — operator enable/disable for the tenant's plugins
 * (ADR-0903). Replaces the legacy Feature Whitelist.
 *
 * Written against the REAL contract of `routes/plugins.py`, not an assumed
 * one. The first draft (2026-09-20) did not compile and would not have worked
 * if it had: it called `api.get`/`api.post` (there is no such object — `api()`
 * IS the function, and it already prefixes `/v1/console`), posted to
 * `/plugins/toggle` (no such route; the mechanism is
 * `POST /plugins/{id}/enable` and `.../disable`), and read `boot_layer`,
 * `description` and `last_audit_event` off rows that carry none of them.
 *
 * Every field below exists on `PluginOut`. Nothing is invented: a plugin whose
 * runtime state diverges from its registry state says so (self-healing may
 * contain a plugin without rewriting the operator's config), and a refusal
 * comes back from the server — the compliance layer is not guessed at
 * client-side, it is `PluginDisableRefused` → 403 with a reason.
 */
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Loader2, ShieldAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { api, ApiError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { HelpTooltip } from "@/components/ui/help-tooltip";

/** One row of `routes/plugins.py::PluginOut`. */
export interface PluginInfo {
  plugin_id: string;
  version: string;
  display_name: string;
  plugin_type: string;
  origin: string;
  pii_risk: string;
  locality: string;
  network_egress: string;
  egress_hosts: string[];
  enabled: boolean;
  /** Registered in THIS process right now — can diverge from `enabled`. */
  runtime_loaded: boolean;
  /** Set when healing, not an operator, changed the runtime state. */
  contained_by: string | null;
  requires_consent: boolean;
  dependencies: string[];
  installed_at: string | null;
  last_error_type: string | null;
}

/** `routes/plugins.py::RefusedPluginOut` — a plugin this process would not load. */
interface RefusedPlugin {
  plugin_id: string;
  event_type: string;
  reason: string;
  origin?: string | null;
  error_type?: string | null;
}

interface PluginListOut {
  plugins: PluginInfo[];
  total: number;
  lifecycle_enabled: boolean;
  refused: RefusedPlugin[];
  refused_total: number;
}

const PLUGINS_KEY = ["console-plugins"] as const;

/** Provenance, not privilege: `origin` is what `PluginOut` actually carries.
 *  (The boot layer is a separate axis and is not on this payload — see
 *  CLAUDE.md "three orthogonal axes, never conflated".) */
const ORIGIN_STYLE: Record<string, string> = {
  builtin: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200",
  vetted: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200",
  community: "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200",
};

export function PluginManagerCard({ csrf }: { csrf: string }) {
  const qc = useQueryClient();
  const { session } = useAuth();

  const q = useQuery({
    queryKey: [...PLUGINS_KEY],
    queryFn: ({ signal }) => api<PluginListOut>("/plugins", { signal }),
    enabled: !!session,
    staleTime: 30_000,
  });

  const [pending, setPending] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const toggle = useMutation({
    mutationFn: ({ id, next }: { id: string; next: boolean }) =>
      api<PluginInfo>(`/plugins/${encodeURIComponent(id)}/${next ? "enable" : "disable"}`, {
        method: "POST",
        body: {},
        csrf,
      }),
    onMutate: ({ id }) => {
      setPending(id);
      setError(null);
    },
    onError: (e) =>
      setError(
        // A 403 here is the compliance layer refusing, with its own reason —
        // surface the server's words rather than a generic failure.
        e instanceof ApiError && e.status === 403
          ? `Refused: ${e.message}`
          : e instanceof Error
            ? e.message
            : String(e),
      ),
    onSettled: () => {
      setPending(null);
      qc.invalidateQueries({ queryKey: [...PLUGINS_KEY] });
    },
  });

  const plugins = q.data?.plugins ?? [];
  const refused = q.data?.refused ?? [];
  const lifecycle = q.data?.lifecycle_enabled ?? false;

  return (
    <Card>
      <CardContent className="pt-4 pb-3 space-y-4">
        <div className="flex items-start justify-between gap-2">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold">Plugin Manager</span>
              <HelpTooltip title="Plugin Manager">
                Enable or disable the plugins registered for this tenant. Every change is a
                hash-chained audit event. Compliance plugins cannot be disabled — the server
                refuses and says why.
              </HelpTooltip>
            </div>
            <p className="text-[11px] text-muted-foreground mt-0.5">
              Registry records for this tenant, plus anything loaded in this process.
            </p>
          </div>
          {q.isFetching && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground shrink-0" />}
        </div>

        {!lifecycle && q.data && (
          <p className="text-[11px] text-amber-600 dark:text-amber-400">
            Runtime lifecycle is off on this build — a change is written to the registry and
            takes effect on the next start, not immediately.
          </p>
        )}

        {q.isLoading && (
          <div className="flex justify-center py-6">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        )}

        {q.isError && (
          <div className="flex items-start gap-2 p-2 bg-destructive/10 rounded text-destructive text-xs">
            <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
            <p>The plugin registry could not be loaded.</p>
          </div>
        )}

        {!q.isLoading && !q.isError && plugins.length === 0 && (
          <p className="text-sm text-muted-foreground py-4 text-center">
            No plugin is registered for this tenant.
          </p>
        )}

        {plugins.length > 0 && (
          <div className="space-y-2 border-t border-border/60 pt-3">
            {plugins.map((p) => (
              <div
                key={p.plugin_id}
                className="flex items-start justify-between gap-4 p-3 rounded-lg border border-border/40"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap mb-1">
                    <span className="text-sm font-medium">{p.display_name}</span>
                    <Badge
                      variant="secondary"
                      className={`text-[10px] font-mono ${ORIGIN_STYLE[p.origin] ?? ""}`}
                    >
                      {p.origin}
                    </Badge>
                    <Badge variant="outline" className="text-[10px] font-mono">
                      v{p.version}
                    </Badge>
                    <Badge variant="outline" className="text-[10px] font-mono">
                      {p.plugin_type}
                    </Badge>
                  </div>

                  <p className="text-[11px] text-muted-foreground font-mono break-all">
                    {p.plugin_id}
                  </p>
                  <p className="text-[11px] text-muted-foreground mt-1">
                    Runs {p.locality} · egress {p.network_egress}
                    {p.egress_hosts.length > 0 && ` (${p.egress_hosts.join(", ")})`}
                    {p.pii_risk && ` · PII risk ${p.pii_risk}`}
                  </p>

                  {/* enabled vs. runtime_loaded can legitimately diverge — showing
                      only the first is the silent-false-display this field exists
                      to prevent. */}
                  {p.enabled && !p.runtime_loaded && (
                    <p className="mt-1 text-[10px] text-amber-600 dark:text-amber-400">
                      Enabled in the registry but not loaded in this process
                      {p.contained_by ? ` — contained by ${p.contained_by}` : ""}.
                    </p>
                  )}
                  {p.last_error_type && (
                    <p className="mt-1 text-[10px] text-destructive font-mono">
                      Last error: {p.last_error_type}
                    </p>
                  )}
                  {p.requires_consent && (
                    <p className="mt-1 text-[10px] text-muted-foreground">Requires consent.</p>
                  )}
                </div>

                <div className="flex items-center gap-2 shrink-0">
                  {pending === p.plugin_id && (
                    <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                  )}
                  <Switch
                    checked={p.enabled}
                    onCheckedChange={(next) => toggle.mutate({ id: p.plugin_id, next })}
                    disabled={pending !== null}
                    aria-label={`Toggle ${p.display_name}`}
                  />
                </div>
              </div>
            ))}
          </div>
        )}

        {/* A plugin this process refused is not the same as one that is absent —
            listing only what loaded is what made a refusal invisible. */}
        {refused.length > 0 && (
          <div className="border-t border-border/60 pt-3 space-y-1">
            <div className="flex items-center gap-1.5 text-xs font-medium">
              <ShieldAlert className="h-3.5 w-3.5 text-amber-600 dark:text-amber-400" />
              Refused or failed to load ({refused.length})
            </div>
            {refused.map((r) => (
              <p key={`${r.plugin_id}:${r.event_type}`} className="text-[11px] text-muted-foreground">
                <span className="font-mono">{r.plugin_id}</span> — {r.reason}
                {r.error_type ? ` (${r.error_type})` : ""}
              </p>
            ))}
          </div>
        )}

        {error && (
          <div className="flex items-start gap-2 p-2 bg-destructive/10 rounded text-destructive text-xs">
            <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
            <p>{error}</p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default PluginManagerCard;

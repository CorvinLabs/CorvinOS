/**
 * MCP tools tab (ADR-0892 D4) — the MCP Plugin Manager (ADR-0096) through
 * mcp_plugins.py: install from an npm:/pip:/github: source (github tarballs
 * are SHA-256 pinned; branch heads are refused unless explicitly allowed),
 * activate/deactivate for the two scopes the console may manage (user,
 * tenant), remove. When the manager is genuinely absent the backend answers
 * 503 and this tab says so — it does not render an empty catalogue as "no
 * tools".
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { KEY_TOOLS } from "../header";
import {
  activateTool, deactivateTool, installTool, isUnavailable, listTools, removeTool,
  type McpScope, type McpTool,
} from "../api";

const SCOPES: McpScope[] = ["user", "tenant"];

function ToolRow({ t, csrf }: { t: McpTool; csrf: string }) {
  const qc = useQueryClient();
  const [msg, setMsg] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const invalidate = () => qc.invalidateQueries({ queryKey: [...KEY_TOOLS] });
  const toggle = useMutation({
    mutationFn: ({ scope, on }: { scope: McpScope; on: boolean }) =>
      on ? activateTool(t.id, scope, csrf) : deactivateTool(t.id, scope, csrf),
    onSuccess: (_r, v) => { setMsg(`${v.on ? "Activated" : "Deactivated"} for ${v.scope} scope — audited.`); invalidate(); },
    onError: () => setMsg("Not changed — the manager refused the scope change (compliance or an unknown tool)."),
  });
  const remove = useMutation({
    mutationFn: () => removeTool(t.id, csrf),
    onSuccess: () => { setMsg("Removed — audited."); invalidate(); },
    onError: (e) => setMsg(e instanceof ApiError && e.status === 404 ? "Not removed — the tool is not in the catalogue." : "Not removed."),
  });
  const busy = toggle.isPending || remove.isPending;
  const runtime = typeof t.runtime === "string" ? t.runtime : t.runtime ? JSON.stringify(t.runtime) : "—";

  return (
    <Card data-testid={`tool-row-${t.id}`}>
      <CardContent className="p-4 space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="font-semibold font-mono">{t.id}</div>
            <div className="text-xs text-muted-foreground font-mono truncate">{t.source}</div>
          </div>
          <Badge variant={t.active ? "secondary" : "outline"}>{t.active ? `active: ${t.active_scopes.join(", ")}` : "inactive"}</Badge>
        </div>
        <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-0.5 text-xs">
          <dt className="text-muted-foreground">Runtime</dt><dd className="font-mono truncate">{runtime}</dd>
          {t.sha256 && (<><dt className="text-muted-foreground">Pinned</dt><dd className="font-mono truncate">sha256:{t.sha256}</dd></>)}
          {t.installed_at && (<><dt className="text-muted-foreground">Installed</dt><dd>{new Date(t.installed_at).toLocaleString("en-US")}</dd></>)}
          {t.secrets.length > 0 && (<><dt className="text-muted-foreground">Secrets</dt><dd>{t.secrets.map((s) => `${s.name}${s.required ? " (required)" : ""}`).join(", ")}</dd></>)}
        </dl>
        <div className="flex flex-wrap items-center gap-2">
          {SCOPES.map((scope) => {
            const on = t.active_scopes.includes(scope);
            return (
              <Button key={scope} size="sm" variant={on ? "secondary" : "outline"} disabled={busy || !csrf}
                      onClick={() => toggle.mutate({ scope, on: !on })}>
                {on ? `Deactivate (${scope})` : `Activate (${scope})`}
              </Button>
            );
          })}
          {!confirm ? (
            <Button size="sm" variant="outline" disabled={busy || !csrf} onClick={() => setConfirm(true)}>Remove</Button>
          ) : (
            <>
              <Button size="sm" variant="destructive" disabled={busy} onClick={() => { setConfirm(false); remove.mutate(); }}>Confirm: remove {t.id}</Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirm(false)}>Cancel</Button>
            </>
          )}
          {busy && <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />}
        </div>
        {msg && <p className="text-xs text-muted-foreground" data-testid={`tool-msg-${t.id}`}>{msg}</p>}
      </CardContent>
    </Card>
  );
}

export function ToolsTab() {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();
  const q = useQuery({ queryKey: [...KEY_TOOLS], queryFn: ({ signal }) => listTools(signal), retry: false });
  const [source, setSource] = useState("");
  const [allowUnpin, setAllowUnpin] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const install = useMutation({
    mutationFn: () => installTool(source.trim(), allowUnpin, csrf),
    onSuccess: (r) => { setMsg(`Installed ${r.tool.id} — inactive until you activate a scope. Audited.`); setSource(""); qc.invalidateQueries({ queryKey: [...KEY_TOOLS] }); },
    onError: (e) => setMsg(e instanceof ApiError && e.status === 400
      ? "Not installed — the source was refused (unknown scheme, unpinned branch, compliance, or the package manager is missing on this host)."
      : "Not installed."),
  });

  if (q.isLoading) {
    return <div className="py-16 flex justify-center"><Loader2 className="w-8 h-8 animate-spin text-muted-foreground" /></div>;
  }
  if (q.isError) {
    return (
      <Card className={isUnavailable(q.error) ? "" : "border-destructive/30 bg-destructive/10"}>
        <CardContent className={`py-6 flex items-center gap-2 text-sm ${isUnavailable(q.error) ? "text-muted-foreground" : "text-destructive"}`} data-testid="tools-unavailable">
          <AlertCircle size={18} />
          {isUnavailable(q.error)
            ? "The MCP Plugin Manager is not available on this build — nothing to manage here."
            : "The MCP tool catalogue could not be loaded."}
        </CardContent>
      </Card>
    );
  }
  const tools = q.data?.tools ?? [];

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="p-4 space-y-2">
          <div className="text-sm font-medium">Install a tool</div>
          <div className="flex flex-wrap items-center gap-2">
            <Input className="flex-1 min-w-[260px] font-mono" placeholder="npm:<package>  ·  pip:<package>  ·  github:<owner>/<repo>@<tag>"
                   value={source} onChange={(e) => setSource(e.target.value)} aria-label="Tool source" />
            <label className="flex items-center gap-2 text-xs">
              <input type="checkbox" checked={allowUnpin} onChange={(e) => setAllowUnpin(e.target.checked)} /> allow an unpinned GitHub branch
            </label>
            <Button variant="accent" size="sm" disabled={install.isPending || !csrf || !source.trim()} onClick={() => install.mutate()}>
              {install.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : null} Install
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            A GitHub source is downloaded and SHA-256 pinned; npm and pip sources record an npx/uvx runtime entry. A tool does nothing until a scope is activated.
          </p>
          {msg && <p className="text-xs text-muted-foreground" data-testid="tools-msg">{msg}</p>}
        </CardContent>
      </Card>

      <p className="text-xs text-muted-foreground" data-testid="tools-summary">{tools.length} tools in this tenant's catalogue.</p>
      {tools.length === 0 ? (
        <div className="py-10 text-center text-sm text-muted-foreground border border-dashed border-border rounded-lg">No MCP tool installed yet.</div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {tools.map((t) => <ToolRow key={t.id} t={t} csrf={csrf} />)}
        </div>
      )}
    </div>
  );
}

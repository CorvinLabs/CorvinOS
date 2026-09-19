/**
 * Browse tab (ADR-0892 D2) — the marketplace index (Corvin-Marketplace
 * index/plugins.json, ADR-0511) with each entry's LOCAL state from the backend:
 * installable or not (and why not), installed, enabled, running. "Install" is
 * the real synchronous install job (marketplace_install.py); its final status
 * is rendered from the response. An entry this build cannot install shows the
 * blocker instead of a button — the old panel offered a button that always
 * failed for the three contributor entries.
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CheckCircle, ExternalLink, Loader2, Search } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { KEY_INDEX, KEY_INSTALLED, KEY_STATS } from "../header";
import { installIndexPlugin, listIndex, type IndexPlugin, type InstallResult } from "../api";
import type { TabId } from "../tabs";

type Outcome = { kind: "ok" | "already" | "failed" | "license" | "error"; text: string };

function outcomeOf(r: InstallResult): Outcome {
  if (r.status === "completed" && r.already_installed) return { kind: "already", text: "Already installed." };
  if (r.status === "completed") return { kind: "ok", text: "Installed — disabled until you enable it on the Installed tab." };
  return { kind: "failed", text: r.error ? `Not installed: ${r.error}` : "Not installed." };
}

function outcomeOfError(e: unknown): Outcome {
  if (e instanceof ApiError && e.status === 403) {
    return { kind: "license", text: "Not installed — this plugin needs a capability your licence tier does not include." };
  }
  if (e instanceof ApiError && e.status === 404) {
    return { kind: "error", text: "Not installed — the plugin console surface is switched off on this build." };
  }
  return { kind: "error", text: "Not installed — the install request did not succeed." };
}

const tierLabel = (t: string) => (t === "buildin" ? "built-in" : t);

export function BrowseTab({ onGoTo }: { onGoTo: (tab: TabId) => void }) {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();
  const q = useQuery({ queryKey: [...KEY_INDEX], queryFn: ({ signal }) => listIndex(signal), retry: false });

  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [tier, setTier] = useState("");
  const [onlyInstallable, setOnlyInstallable] = useState(false);
  const [detail, setDetail] = useState<IndexPlugin | null>(null);
  const [outcomes, setOutcomes] = useState<Record<string, Outcome>>({});

  const install = useMutation({
    mutationFn: (p: IndexPlugin) => installIndexPlugin(p.id, p.version, csrf),
    onSuccess: (r, p) => {
      setOutcomes((o) => ({ ...o, [p.id]: outcomeOf(r) }));
      qc.invalidateQueries({ queryKey: [...KEY_INDEX] });
      qc.invalidateQueries({ queryKey: [...KEY_INSTALLED] });
      qc.invalidateQueries({ queryKey: [...KEY_STATS] });
    },
    onError: (e, p) => setOutcomes((o) => ({ ...o, [p.id]: outcomeOfError(e) })),
  });

  const all = useMemo(() => q.data?.plugins ?? [], [q.data]);
  const categories = useMemo(() => Array.from(new Set(all.map((p) => p.category))).sort(), [all]);
  const tiers = useMemo(() => Array.from(new Set(all.map((p) => p.tier))).sort(), [all]);
  const rows = useMemo(() => {
    const s = search.trim().toLowerCase();
    return all.filter((p) =>
      (!category || p.category === category) &&
      (!tier || p.tier === tier) &&
      (!onlyInstallable || p.installable) &&
      (!s || p.name.toLowerCase().includes(s) || p.description.toLowerCase().includes(s) ||
        p.id.toLowerCase().includes(s) || (p.tags ?? []).some((t) => t.toLowerCase().includes(s))),
    );
  }, [all, search, category, tier, onlyInstallable]);

  if (q.isLoading) {
    return <div className="py-16 flex justify-center"><Loader2 className="w-8 h-8 animate-spin text-muted-foreground" /></div>;
  }
  if (q.isError) {
    return (
      <Card className="border-destructive/30 bg-destructive/10">
        <CardContent className="py-6 flex items-center gap-2 text-destructive text-sm">
          <AlertCircle size={18} /> The marketplace index could not be loaded.
        </CardContent>
      </Card>
    );
  }

  const installableCount = all.filter((p) => p.installable).length;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex-1 min-w-[220px]">
          <span className="sr-only">Search the index</span>
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
            <Input className="pl-9" placeholder="Search name, description, id or tag" value={search}
                   onChange={(e) => setSearch(e.target.value)} aria-label="Search the index" />
          </div>
        </label>
        <label className="text-xs text-muted-foreground flex flex-col gap-1">
          Category
          <select className="h-9 rounded-md border border-input bg-background px-2 text-sm text-foreground"
                  value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Category">
            <option value="">all</option>
            {categories.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
        <label className="text-xs text-muted-foreground flex flex-col gap-1">
          Tier
          <select className="h-9 rounded-md border border-input bg-background px-2 text-sm text-foreground"
                  value={tier} onChange={(e) => setTier(e.target.value)} aria-label="Tier">
            <option value="">all</option>
            {tiers.map((t) => <option key={t} value={t}>{tierLabel(t)}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm h-9">
          <input type="checkbox" checked={onlyInstallable} onChange={(e) => setOnlyInstallable(e.target.checked)} />
          installable on this build only
        </label>
      </div>

      <p className="text-xs text-muted-foreground" data-testid="browse-summary">
        {rows.length} of {all.length} entries shown · {installableCount} installable on this build
        {all.length - installableCount > 0 && ` · ${all.length - installableCount} not installable (no local source — remote download is not offered)`}
      </p>

      {rows.length === 0 ? (
        <div className="py-10 text-center text-sm text-muted-foreground border border-dashed border-border rounded-lg">
          No entry matches.
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {rows.map((p) => {
            const out = outcomes[p.id];
            const busy = install.isPending && install.variables?.id === p.id;
            return (
              <Card key={p.id} data-testid={`index-card-${p.id}`}>
                <CardContent className="p-4 space-y-3">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <button type="button" className="font-semibold hover:underline text-left" onClick={() => setDetail(p)}>
                        {p.name}
                      </button>
                      <div className="text-xs text-muted-foreground font-mono truncate">{p.id}</div>
                    </div>
                    <div className="flex flex-col items-end gap-1 shrink-0">
                      <Badge variant="outline">v{p.version}</Badge>
                      <Badge variant="secondary">{tierLabel(p.tier)}</Badge>
                    </div>
                  </div>
                  <p className="text-sm text-muted-foreground line-clamp-3">{p.description}</p>
                  <div className="flex flex-wrap gap-1 text-xs">
                    <Badge variant="outline">{p.category}</Badge>
                    {p.installed && <Badge variant="secondary">installed</Badge>}
                    {p.enabled && <Badge variant="secondary">enabled</Badge>}
                    {p.runtime_loaded && <Badge variant="secondary">running</Badge>}
                  </div>
                  {p.installed ? (
                    <Button variant="secondary" size="sm" className="w-full" onClick={() => onGoTo("installed")}>
                      Manage on the Installed tab
                    </Button>
                  ) : p.installable ? (
                    <Button variant="accent" size="sm" className="w-full" disabled={busy || !csrf}
                            onClick={() => install.mutate(p)}>
                      {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : null} Install
                    </Button>
                  ) : (
                    <p className="text-xs text-amber-600 dark:text-amber-400 flex items-start gap-1.5">
                      <AlertCircle size={12} className="mt-0.5 shrink-0" />
                      <span>Not installable on this build: {p.install_blocker}</span>
                    </p>
                  )}
                  {out && (
                    <p className={`text-xs flex items-start gap-1.5 ${out.kind === "ok" || out.kind === "already" ? "text-emerald-600 dark:text-emerald-400" : "text-destructive"}`}
                       data-testid={`install-outcome-${p.id}`}>
                      {out.kind === "ok" || out.kind === "already" ? <CheckCircle size={12} className="mt-0.5 shrink-0" /> : <AlertCircle size={12} className="mt-0.5 shrink-0" />}
                      <span>{out.text}</span>
                    </p>
                  )}
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}

      <Dialog open={detail !== null} onOpenChange={(o) => { if (!o) setDetail(null); }}>
        <DialogContent className="max-w-2xl">
          {detail && (
            <>
              <DialogHeader>
                <DialogTitle>{detail.name} <span className="text-muted-foreground font-normal">v{detail.version}</span></DialogTitle>
                <DialogDescription className="font-mono text-xs">{detail.id}</DialogDescription>
              </DialogHeader>
              <div className="space-y-3 text-sm">
                <p>{detail.description}</p>
                <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-xs">
                  <dt className="text-muted-foreground">Tier</dt><dd>{tierLabel(detail.tier)}</dd>
                  <dt className="text-muted-foreground">Category</dt><dd>{detail.category}</dd>
                  <dt className="text-muted-foreground">Author</dt><dd>{detail.author || "—"}</dd>
                  <dt className="text-muted-foreground">License</dt>
                  <dd>{detail.license_url ? <a className="underline" href={detail.license_url} target="_blank" rel="noreferrer">{detail.license}</a> : detail.license || "—"}</dd>
                  <dt className="text-muted-foreground">Source</dt>
                  <dd>{detail.distribution?.source_url
                    ? <a className="underline inline-flex items-center gap-1" href={detail.distribution.source_url} target="_blank" rel="noreferrer">{detail.distribution.source_url} <ExternalLink size={12} /></a>
                    : "—"}</dd>
                  <dt className="text-muted-foreground">Requires</dt><dd>{detail.requires_version || "—"}</dd>
                  <dt className="text-muted-foreground">Dependencies</dt><dd>{detail.dependencies?.length ? detail.dependencies.join(", ") : "none"}</dd>
                  <dt className="text-muted-foreground">Registry id</dt><dd className="font-mono">{detail.registry_id ?? "—"}</dd>
                  <dt className="text-muted-foreground">On this build</dt>
                  <dd>{detail.installed ? `installed${detail.enabled ? ", enabled" : ", disabled"}${detail.runtime_loaded ? ", running" : ""}`
                    : detail.installable ? "installable" : `not installable: ${detail.install_blocker}`}</dd>
                </dl>
                {detail.readme_url && (
                  <a className="underline text-xs inline-flex items-center gap-1" href={detail.readme_url} target="_blank" rel="noreferrer">
                    README <ExternalLink size={12} />
                  </a>
                )}
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

/**
 * Browse tab (ADR-0892 D2, amended 2026-09-20) — the marketplace index
 * (Corvin-Marketplace index/plugins.json, ADR-0511) in its two tiers:
 *
 *  - Built-in: maintainer-reviewed source under plugins/buildin — installs as
 *    origin `vetted`, enable needs no consent.
 *  - Contributor: community source under plugins/contributor — installs as
 *    origin `community`; enabling records the operator's explicit consent
 *    (ADR-0233 deny-by-default consent gate). The Video Producer lives here.
 *
 * Each entry carries its LOCAL state from the backend (installable or the
 * blocker, installed, enabled, running). "Install" starts the real install
 * JOB (`wait: false`): the backend advances it through its own phases —
 * index check, source resolution, manifest gate, licence, record,
 * registration — and the bar below the card is those phases read back by
 * polling, never a timer. After a completed install the card offers
 * "Enable now" (with consent for community origin); enabling a plugin that
 * declares a console panel puts it in the sidebar, so the manifest queries are
 * invalidated on every lifecycle change.
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CheckCircle, ExternalLink, Loader2, Search, ShieldCheck, Users } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { ApiError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { KEY_INDEX, KEY_INSTALLED, KEY_STATS } from "../header";
import {
  KEY_UPDATES, enablePlugin, getInstallProgress, listIndex, listPluginUpdates, refusedEscalations,
  startInstallJob, updateIndexPlugin,
  type IndexPlugin, type InstallJob, type PluginUpdate,
} from "../api";
import type { TabId } from "../tabs";
import { InstallFlowModal } from "../components/install-flow-modal";

/** Query keys the sidebar reads (adapters/capabilities.ts) — invalidated after
 *  every lifecycle change so a plugin panel appears/disappears without a reload. */
export const KEY_MANIFEST = ["console-manifest"] as const;
export const KEY_CAPABILITIES = ["capabilities"] as const;

export type Outcome = { kind: "ok" | "already" | "failed" | "license" | "error" | "enabled"; text: string };

export function outcomeOfJob(j: InstallJob): Outcome {
  if (j.status === "completed" && /already/i.test(j.message)) return { kind: "already", text: "Already installed." };
  if (j.status === "completed" && j.enabled) return { kind: "enabled", text: "Installed and enabled — audited. A panel this plugin declares is now in the sidebar." };
  if (j.status === "completed") return { kind: "ok", text: "Installed — disabled until you enable it." };
  if (j.error === "license_required") return { kind: "license", text: "Not installed — this plugin needs a capability your licence tier does not include." };
  return { kind: "failed", text: j.error ? `Not installed: ${j.error}` : "Not installed." };
}

function outcomeOfError(e: unknown): Outcome {
  if (e instanceof ApiError && e.status === 403) return { kind: "license", text: "Not installed — this plugin needs a capability your licence tier does not include." };
  if (e instanceof ApiError && e.status === 404) return { kind: "error", text: "Not installed — the plugin console surface is switched off on this build." };
  return { kind: "error", text: "Not installed — the install request did not succeed." };
}

const TIER = {
  buildin: { label: "Built-in", origin: "vetted", icon: ShieldCheck,
    blurb: "Maintainer-reviewed source from the marketplace's buildin tree. Installs as origin “vetted”; enabling needs no consent." },
  contributor: { label: "Contributor", origin: "community", icon: Users,
    blurb: "Community source from the marketplace's contributor tree. Installs as origin “community”; enabling records your explicit consent." },
} as const;
type TierKey = keyof typeof TIER;
const tierKey = (t: string): TierKey => (t === "buildin" ? "buildin" : "contributor");

/** Polls one install job until it is terminal. */
function useInstallJob(jobId: string | null, onDone: (j: InstallJob) => void) {
  const q = useQuery({
    queryKey: ["marketplace", "install-job", jobId],
    queryFn: ({ signal }) => getInstallProgress(jobId as string, signal),
    enabled: jobId !== null,
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return s === "completed" || s === "failed" ? false : 300;
    },
    retry: false,
  });
  const done = q.data && (q.data.status === "completed" || q.data.status === "failed");
  useEffect(() => { if (done && q.data) onDone(q.data); }, [done, q.data, onDone]);
  return q.data ?? null;
}

function EntryCard({ p, csrf, onGoTo, update }: {
  p: IndexPlugin; csrf: string; onGoTo: (t: TabId) => void; update?: PluginUpdate | undefined;
}) {
  const qc = useQueryClient();
  const [installModalOpen, setInstallModalOpen] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [detail, setDetail] = useState(false);
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: [...KEY_INDEX] });
    qc.invalidateQueries({ queryKey: [...KEY_INSTALLED] });
    qc.invalidateQueries({ queryKey: [...KEY_STATS] });
    qc.invalidateQueries({ queryKey: [...KEY_MANIFEST] });
    qc.invalidateQueries({ queryKey: [...KEY_CAPABILITIES] });
    qc.invalidateQueries({ queryKey: [...KEY_UPDATES] });
  };
  // Update state machine: idle -> pending (spinner) -> success | error. The button
  // exists only while the server reports a newer version for this installed plugin.
  const [pendingEscalations, setPendingEscalations] = useState<string[] | null>(null);
  const doUpdate = useMutation({
    mutationFn: (approve: boolean) => updateIndexPlugin(update!.plugin_id, csrf, approve),
    onSuccess: (r) => {
      setPendingEscalations(null);
      setOutcome({ kind: "ok", text: `Updated ${r.from_version} → ${r.to_version} — audited.` +
        (r.restart_recommended ? " Restart the console to run the new code." : "") });
      invalidate();
    },
    onError: (e) => {
      const esc = refusedEscalations(e);
      if (esc) { setPendingEscalations(esc); setOutcome(null); return; }
      const reason = e instanceof Error && e.message ? ` Reason: ${e.message}` : "";
      setOutcome({ kind: "error", text: `Not updated — the new version could not be installed; the installed version was kept.${reason}` });
    },
  });
  const community = tierKey(p.tier) === "contributor";
  const enable = useMutation({
    mutationFn: () => enablePlugin(p.registry_id as string, csrf, community),
    onSuccess: () => { setOutcome({ kind: "enabled", text: community ? "Enabled with your consent — audited. A panel this plugin declares is now in the sidebar." : "Enabled — audited. A panel this plugin declares is now in the sidebar." }); invalidate(); },
    onError: () => setOutcome({ kind: "error", text: "Not enabled — the plugin refused to load; the registry was rolled back." }),
  });
  const justInstalled = outcome?.kind === "ok" || outcome?.kind === "already";
  const tier = TIER[tierKey(p.tier)];

  return (
    <Card data-testid={`index-card-${p.id}`}>
      <CardContent className="p-4 space-y-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <button type="button" className="font-semibold hover:underline text-left" onClick={() => setDetail(true)}>{p.name}</button>
            <div className="text-xs text-muted-foreground font-mono truncate">{p.id}</div>
          </div>
          <div className="flex flex-col items-end gap-1 shrink-0">
            <Badge variant="outline" title={update ? "installed version" : "version"}>v{update?.installed_version ?? p.version}</Badge>
            <Badge variant="secondary" title={`origin ${tier.origin}`}>{tier.label.toLowerCase()}</Badge>
          </div>
        </div>
        <p className="text-sm text-muted-foreground line-clamp-3">{p.description}</p>
        <div className="flex flex-wrap gap-1 text-xs">
          <Badge variant="outline">{p.category}</Badge>
          {p.installed && <Badge variant="secondary">installed</Badge>}
          {p.enabled && <Badge variant="secondary">enabled</Badge>}
          {p.runtime_loaded && <Badge variant="secondary">running</Badge>}
        </div>

        {justInstalled && !p.enabled && outcome?.kind !== "enabled" ? (
          <Button variant="accent" size="sm" className="w-full" disabled={enable.isPending || !csrf || !p.registry_id}
                  onClick={() => enable.mutate()} data-testid={`enable-now-${p.id}`}>
            {enable.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
            {community ? "Enable now (with consent)" : "Enable now"}
          </Button>
        ) : p.installed ? (
          <div className="space-y-2">
            {update && !pendingEscalations && (
              <Button variant="accent" size="sm" className="w-full" data-testid={`update-btn-${p.id}`}
                      disabled={!csrf || doUpdate.isPending} onClick={() => doUpdate.mutate(false)}>
                {doUpdate.isPending ? <><Loader2 className="w-4 h-4 animate-spin" /> Updating…</> : `Update to v${update.latest_version}`}
              </Button>
            )}
            {update && pendingEscalations && (
              <div className="rounded border border-amber-500/40 p-3 text-xs space-y-2" data-testid={`update-escalations-${p.id}`}>
                <p>Version {update.latest_version} widens what this plugin may do:</p>
                <ul className="list-disc pl-4">{pendingEscalations.map((x) => <li key={x}>{x}</li>)}</ul>
                <div className="flex gap-2">
                  <Button size="sm" variant="destructive" disabled={!csrf || doUpdate.isPending} onClick={() => doUpdate.mutate(true)}>
                    {doUpdate.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : null} Approve and update
                  </Button>
                  <Button size="sm" variant="secondary" onClick={() => setPendingEscalations(null)}>Cancel</Button>
                </div>
              </div>
            )}
            <Button variant="secondary" size="sm" className="w-full" onClick={() => onGoTo("installed")}>
              Manage on the Installed tab
            </Button>
          </div>
        ) : p.installable ? (
          <Button variant="accent" size="sm" className="w-full" disabled={!csrf} onClick={() => setInstallModalOpen(true)}>
            Install
          </Button>
        ) : (
          <p className="text-xs text-amber-600 dark:text-amber-400 flex items-start gap-1.5">
            <AlertCircle size={12} className="mt-0.5 shrink-0" />
            <span>Not installable on this build: {p.install_blocker}</span>
          </p>
        )}

        {outcome && (
          <p className={`text-xs flex items-start gap-1.5 ${["ok", "already", "enabled"].includes(outcome.kind) ? "text-emerald-600 dark:text-emerald-400" : "text-destructive"}`}
             data-testid={`install-outcome-${p.id}`}>
            {["ok", "already", "enabled"].includes(outcome.kind) ? <CheckCircle size={12} className="mt-0.5 shrink-0" /> : <AlertCircle size={12} className="mt-0.5 shrink-0" />}
            <span>{outcome.text}</span>
          </p>
        )}
      </CardContent>

      <InstallFlowModal
        plugin={p}
        csrf={csrf}
        open={installModalOpen}
        onOpenChange={setInstallModalOpen}
        onSuccess={(enabled) => { setOutcome(enabled ? { kind: "enabled", text: "Installed and enabled — audited. A panel this plugin declares is now in the sidebar." } : { kind: "ok", text: "Installed — disabled until you enable it." }); invalidate(); }}
        onGoTo={onGoTo}
      />

      <Dialog open={detail} onOpenChange={setDetail}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{p.name} <span className="text-muted-foreground font-normal">v{p.version}</span></DialogTitle>
            <DialogDescription className="font-mono text-xs">{p.id}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3 text-sm">
            <p>{p.description}</p>
            <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-xs">
              <dt className="text-muted-foreground">Tier</dt><dd>{tier.label} — installs as origin {tier.origin}{tier.origin === "community" ? ", consent on enable" : ""}</dd>
              <dt className="text-muted-foreground">Category</dt><dd>{p.category}</dd>
              <dt className="text-muted-foreground">Author</dt><dd>{p.author || "—"}</dd>
              <dt className="text-muted-foreground">License</dt>
              <dd>{p.license_url ? <a className="underline" href={p.license_url} target="_blank" rel="noreferrer">{p.license}</a> : p.license || "—"}</dd>
              <dt className="text-muted-foreground">Source</dt>
              <dd>{p.distribution?.source_url
                ? <a className="underline inline-flex items-center gap-1" href={p.distribution.source_url} target="_blank" rel="noreferrer">{p.distribution.source_url} <ExternalLink size={12} /></a>
                : "—"}</dd>
              <dt className="text-muted-foreground">Requires</dt><dd>{p.requires_version || "—"}</dd>
              <dt className="text-muted-foreground">Registry id</dt><dd className="font-mono">{p.registry_id ?? "—"}</dd>
              <dt className="text-muted-foreground">On this build</dt>
              <dd>{p.installed ? `installed${p.enabled ? ", enabled" : ", disabled"}${p.runtime_loaded ? ", running" : ""}`
                : p.installable ? "installable" : `not installable: ${p.install_blocker}`}</dd>
            </dl>
            {p.readme_url && (
              <a className="underline text-xs inline-flex items-center gap-1" href={p.readme_url} target="_blank" rel="noreferrer">README <ExternalLink size={12} /></a>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

function TierSection({ tier, rows, total, csrf, onGoTo, updates }: {
  tier: TierKey; rows: IndexPlugin[]; total: number; csrf: string; onGoTo: (t: TabId) => void;
  updates: Map<string, PluginUpdate>;
}) {
  const t = TIER[tier];
  const Icon = t.icon;
  const installed = rows.filter((p) => p.installed).length;
  return (
    <section className="space-y-3" data-testid={`tier-${tier}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold flex items-center gap-2"><Icon size={16} className="text-accent" /> {t.label}</h3>
        <span className="text-xs text-muted-foreground" data-testid={`tier-${tier}-summary`}>
          {rows.length} of {total} shown · {installed} installed
        </span>
      </div>
      <p className="text-xs text-muted-foreground">{t.blurb}</p>
      {rows.length === 0 ? (
        <div className="py-6 text-center text-xs text-muted-foreground border border-dashed border-border rounded-lg">No entry matches in this tier.</div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {rows.map((p) => <EntryCard key={p.id} p={p} csrf={csrf} onGoTo={onGoTo} update={updates.get(p.id)} />)}
        </div>
      )}
    </section>
  );
}

export function BrowseTab({ onGoTo }: { onGoTo: (tab: TabId) => void }) {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const q = useQuery({ queryKey: [...KEY_INDEX], queryFn: ({ signal }) => listIndex(signal), retry: false });

  // 404/503 (plugin surface off) just means "no updates known" — the listing still works.
  const upd = useQuery({ queryKey: [...KEY_UPDATES], queryFn: ({ signal }) => listPluginUpdates(false, signal), retry: false });
  const updates = useMemo(() => new Map((upd.data?.updates ?? []).map((u) => [u.plugin_id, u])), [upd.data]);

  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [onlyInstallable, setOnlyInstallable] = useState(false);

  const all = useMemo(() => q.data?.plugins ?? [], [q.data]);
  const categories = useMemo(() => Array.from(new Set(all.map((p) => p.category))).sort(), [all]);
  const rows = useMemo(() => {
    const s = search.trim().toLowerCase();
    return all.filter((p) =>
      (!category || p.category === category) &&
      (!onlyInstallable || p.installable) &&
      (!s || p.name.toLowerCase().includes(s) || p.description.toLowerCase().includes(s) ||
        p.id.toLowerCase().includes(s) || (p.tags ?? []).some((t) => t.toLowerCase().includes(s))),
    );
  }, [all, search, category, onlyInstallable]);

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
  const byTier = (k: TierKey, list: IndexPlugin[]) => list.filter((p) => tierKey(p.tier) === k);

  return (
    <div className="space-y-6">
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
        <label className="flex items-center gap-2 text-sm h-9">
          <input type="checkbox" checked={onlyInstallable} onChange={(e) => setOnlyInstallable(e.target.checked)} />
          installable on this build only
        </label>
      </div>

      <p className="text-xs text-muted-foreground" data-testid="browse-summary">
        {rows.length} of {all.length} entries shown · {installableCount} installable on this build
        {all.length - installableCount > 0 && ` · ${all.length - installableCount} not installable (no local source — remote download is not offered)`}
      </p>

      <TierSection tier="buildin" rows={byTier("buildin", rows)} total={byTier("buildin", all).length} csrf={csrf} onGoTo={onGoTo} updates={updates} />
      <TierSection tier="contributor" rows={byTier("contributor", rows)} total={byTier("contributor", all).length} csrf={csrf} onGoTo={onGoTo} updates={updates} />
    </div>
  );
}

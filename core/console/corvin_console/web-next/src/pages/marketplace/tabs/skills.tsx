/**
 * Skills tab (ADR-0682 inside the ONE marketplace, ADR-0892) — the skill
 * catalogue: search, filter by domain/tier, sort, and open a skill's full
 * metadata. It lists what SkillInstaller (ADR-0680) has installed on THIS
 * host, so it offers no install button: the one Phase 6 shipped called a
 * route that answered "queued" and installed nothing. A skill arrives as a
 * package — the empty state links to the Packages tab.
 */
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle, Loader2, Search, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { getSkillDetail, isUnavailable, searchSkills } from "../api";
import type { TabId } from "../tabs";

// Closed enums of core/skills/skill_marketplace.py (SkillDomain / SkillTier).
const DOMAINS = ["routing", "learning", "optimization", "integration", "security", "observability", "other"];
const TIERS = ["compliance", "core", "installed", "community"];
const SORTS: Array<[string, string]> = [
  ["relevance", "Relevance"],
  ["popularity", "Most installed"],
  ["rating", "Rating"],
  ["recency", "Newest"],
  ["alphabetical", "A–Z"],
];

const SELECT = "h-9 rounded-md border border-input bg-background px-2 text-sm text-foreground";

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function SkillsTab({ onGoTo }: { onGoTo: (tab: TabId) => void }) {
  const [search, setSearch] = useState("");
  const [domain, setDomain] = useState("");
  const [tier, setTier] = useState("");
  const [sortBy, setSortBy] = useState("relevance");
  const [detailId, setDetailId] = useState<string | null>(null);
  const q = useDebounced(search.trim(), 250);

  const list = useQuery({
    queryKey: ["marketplace", "skills", q, domain, tier, sortBy],
    queryFn: ({ signal }) => searchSkills({ q, domain, tier, sort_by: sortBy }, signal),
    retry: false,
  });
  const detail = useQuery({
    queryKey: ["marketplace", "skill", detailId],
    queryFn: ({ signal }) => getSkillDetail(detailId as string, signal),
    enabled: detailId !== null,
    retry: false,
  });

  const skills = list.data?.skills ?? [];
  const total = list.data?.total ?? 0;

  return (
    <div className="space-y-4" data-testid="marketplace-skills">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex-1 min-w-[220px]">
          <span className="sr-only">Search skills</span>
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
            <Input className="pl-9" placeholder="Search name, description or tag" value={search}
                   onChange={(e) => setSearch(e.target.value)} aria-label="Search skills" />
          </div>
        </label>
        <label className="text-xs text-muted-foreground flex flex-col gap-1">
          Domain
          <select className={SELECT} value={domain} onChange={(e) => setDomain(e.target.value)} aria-label="Domain">
            <option value="">all</option>
            {DOMAINS.map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        </label>
        <label className="text-xs text-muted-foreground flex flex-col gap-1">
          Tier
          <select className={SELECT} value={tier} onChange={(e) => setTier(e.target.value)} aria-label="Tier">
            <option value="">all</option>
            {TIERS.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
        <label className="text-xs text-muted-foreground flex flex-col gap-1">
          Sort
          <select className={SELECT} value={sortBy} onChange={(e) => setSortBy(e.target.value)} aria-label="Sort">
            {SORTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
      </div>

      {list.isLoading && (
        <p className="text-sm text-muted-foreground flex items-center gap-2">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading skills…
        </p>
      )}

      {list.isError && (
        <Card className={isUnavailable(list.error) ? "" : "border-destructive/30 bg-destructive/10"}>
          <CardContent className={`py-6 flex items-center gap-2 text-sm ${isUnavailable(list.error) ? "text-muted-foreground" : "text-destructive"}`}>
            <AlertCircle className="w-4 h-4" />
            {isUnavailable(list.error)
              ? "The skill catalogue is not available on this build."
              : "The skill catalogue could not be read."}
          </CardContent>
        </Card>
      )}

      {list.isSuccess && (
        <p className="text-xs text-muted-foreground" data-testid="skills-summary">
          {skills.length} of {total} installed skills shown
        </p>
      )}

      {list.isSuccess && total === 0 && (
        <Card>
          <CardContent className="py-8 text-center space-y-3">
            <Sparkles className="w-8 h-8 mx-auto text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              No skills are installed on this host yet. Skills arrive as skill packages.
            </p>
            <Button variant="outline" size="sm" onClick={() => onGoTo("packages")}>Open Packages</Button>
          </CardContent>
        </Card>
      )}

      {skills.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {skills.map((s) => (
            <button key={s.skill_id} type="button" onClick={() => setDetailId(s.skill_id)}
                    className="text-left" data-testid={`skill-card-${s.skill_id}`}>
              <Card className="h-full hover:border-accent transition-colors">
                <CardContent className="p-4 space-y-2">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="font-medium truncate">{s.name}</div>
                      <div className="text-xs font-mono text-muted-foreground truncate">{s.skill_id}@{s.version}</div>
                    </div>
                    <Badge variant="secondary">{s.tier}</Badge>
                  </div>
                  {s.description && <p className="text-sm text-muted-foreground line-clamp-2">{s.description}</p>}
                  <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                    <Badge variant="outline">{s.domain}</Badge>
                    <span className="tabular-nums">{s.install_count.toLocaleString("en-US")} installs</span>
                    {s.rating > 0 && <span className="tabular-nums">★ {s.rating.toFixed(1)}</span>}
                  </div>
                </CardContent>
              </Card>
            </button>
          ))}
        </div>
      )}

      <Dialog open={detailId !== null} onOpenChange={(o) => { if (!o) setDetailId(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{detail.data?.name ?? detailId}</DialogTitle>
            <DialogDescription className="font-mono">
              {detail.data ? `${detail.data.skill_id}@${detail.data.version}` : ""}
            </DialogDescription>
          </DialogHeader>
          {detail.isLoading && <Loader2 className="w-4 h-4 animate-spin" />}
          {detail.isError && <p className="text-sm text-destructive">The skill details could not be read.</p>}
          {detail.data && (
            <div className="space-y-3 text-sm">
              {detail.data.description && <p>{detail.data.description}</p>}
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1">
                <dt className="text-muted-foreground">Domain</dt><dd>{detail.data.domain}</dd>
                <dt className="text-muted-foreground">Tier</dt><dd>{detail.data.tier}</dd>
                <dt className="text-muted-foreground">Origin</dt><dd>{detail.data.origin}</dd>
                <dt className="text-muted-foreground">Installs</dt><dd className="tabular-nums">{detail.data.install_count.toLocaleString("en-US")}</dd>
                {detail.data.updated_at && (<><dt className="text-muted-foreground">Updated</dt><dd>{detail.data.updated_at}</dd></>)}
              </dl>
              {detail.data.tags.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {detail.data.tags.map((t) => <Badge key={t} variant="outline">{t}</Badge>)}
                </div>
              )}
              {detail.data.dependencies.length > 0 && (
                <div>
                  <div className="text-muted-foreground mb-1">Dependencies</div>
                  <ul className="list-disc ml-5 font-mono text-xs">
                    {detail.data.dependencies.map((d) => <li key={d}>{d}</li>)}
                  </ul>
                </div>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

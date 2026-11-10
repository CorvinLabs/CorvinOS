/**
 * Projects - the projects of this tenant: a folder you own, indexed here.
 *
 * Data: GET /v1/console/projects and /projects/templates (routes/projects.py). The list is the
 * registry's index; a project whose folder is gone is SHOWN as missing, never dropped. Nothing here is
 * sample data: no project, no card. A card's title opens the project's inspector (/app/projects/:id).
 */
import { Link } from "react-router-dom";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Blocks, FolderKanban, Plus, RefreshCw } from "lucide-react";
import { NewProjectDialog } from "./wizard-dialog";
import { AdoptSection, RootEditor } from "./manage";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api/client";
import { listProjects, listTemplates, type ProjectSummary } from "@/lib/api/projects";
import { cn } from "@/lib/utils";
import { emptyState, featureChips, offFeatures, sortProjects, stateOf, templateLabel } from "./encodings";
import { AccessBadge, ClassBadge } from "./badges";
import { CLASS_META, NO_FILTER, ROLE_LABEL, accessOf, classOf, filterProjects, presentValues, roleLabel, type Filter } from "./roles";

const PROJECTS_KEY = ["projects", "list"] as const;
const TEMPLATES_KEY = ["projects", "templates"] as const;

function ProjectCard({ p }: { p: ProjectSummary }) {
  const st = stateOf(p);
  const chips = featureChips(p);
  const off = offFeatures(p);
  return (
    <Card className="min-w-0" data-testid="project-card" data-project-id={p.id} data-state={p.state}>
      <CardContent className="space-y-3 p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="flex min-w-0 items-center gap-2 text-base font-semibold" data-testid="project-title">
              <Link to={`/app/projects/${encodeURIComponent(p.id)}`} className="min-w-0 truncate hover:underline" title={p.title ?? p.id}
                data-testid="project-open">{p.title ?? p.id}</Link>
              {p.builtin && <Badge variant="secondary" data-testid="project-builtin">Built in</Badge>}
            </h2>
            <p className="font-mono text-xs text-muted-foreground" data-testid="project-id">{p.id}</p>
          </div>
          <Badge
            data-testid="project-state"
            variant="outline"
            className={cn(st.tone === "ok" && "border-emerald-500/40 text-emerald-700 dark:text-emerald-300",
              st.tone === "bad" && "border-red-500/40 text-red-700 dark:text-red-300")}
          >
            {st.label}
          </Badge>
        </div>
        {st.detail && (
          <p className="flex items-start gap-2 text-sm text-red-700 dark:text-red-300" data-testid="project-reason">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            <span>{st.detail}</span>
          </p>
        )}
        {p.state === "ok" && (
          <div className="flex flex-wrap items-center gap-1.5" aria-label="Role and protection" data-testid="project-protection">
            {roleLabel(p.role) && <span className="text-xs font-medium" data-testid="project-role">{roleLabel(p.role)}</span>}
            <ClassBadge cls={classOf(p)} />
            <AccessBadge access={accessOf(p)} />
          </div>
        )}
        {chips.length > 0 && (
          <ul className="flex flex-wrap gap-1.5" aria-label="Active features" data-testid="project-features">
            {chips.map((c) => (
              <li key={c}><Badge variant="secondary">{c}</Badge></li>
            ))}
          </ul>
        )}
        {off.length > 0 && (
          <p className="text-xs text-muted-foreground" data-testid="project-off">Off: {off.join(", ")}</p>
        )}
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <dt>Template</dt><dd>{templateLabel(p.template)}</dd>
          {p.docs_mode && (<><dt>Docs</dt><dd>{p.docs_mode === "nested" ? "own repository" : "inside the project repository"}</dd></>)}
          <dt>Folder</dt><dd className="truncate font-mono" title={p.path} data-testid="project-path">{p.path}</dd>
        </dl>
      </CardContent>
    </Card>
  );
}

export default function ProjectsPage() {
  const [wizardOpen, setWizardOpen] = useState(false);
  const projects = useQuery({ queryKey: PROJECTS_KEY, queryFn: ({ signal }) => listProjects(signal), refetchInterval: 10_000 });
  const templates = useQuery({ queryKey: TEMPLATES_KEY, queryFn: ({ signal }) => listTemplates(signal), staleTime: 30_000 });

  const [filter, setFilter] = useState<Filter>(NO_FILTER);
  const all = projects.data ? sortProjects(projects.data.projects) : [];
  const mine = all.filter((p) => !p.builtin);                      // Corvin itself is always there; "empty" is about the user's own projects
  const present = presentValues(all);
  const list = filterProjects(all, filter);
  const empty = emptyState(templates.data);
  const loadError = projects.error instanceof ApiError ? projects.error.message : projects.error ? "Could not load projects." : null;

  return (
    <div className="mx-auto w-full max-w-5xl space-y-5 p-4 md:p-6" data-testid="projects-page">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h1 className="flex items-center gap-2 text-xl font-semibold">
            <FolderKanban className="h-5 w-5" aria-hidden /> Projects
          </h1>
          {projects.data && (
            <p className="text-sm text-muted-foreground" data-testid="projects-root">
              Projects live under <span className="break-all font-mono">{projects.data.projects_root}</span>
              <RootEditor current={projects.data.projects_root} />
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={() => setWizardOpen(true)} disabled={templates.data?.available === false}
            data-testid="new-project-button">
            <Plus className="mr-2 h-4 w-4" aria-hidden /> New project
          </Button>
          <AdoptSection />
          <Button variant="outline" size="sm" onClick={() => { void projects.refetch(); void templates.refetch(); }}
          disabled={projects.isFetching} aria-label="Refresh projects">
          <RefreshCw className={cn("mr-2 h-4 w-4", projects.isFetching && "animate-spin")} aria-hidden /> Refresh
        </Button>
        </div>
      </header>
      <NewProjectDialog open={wizardOpen} onOpenChange={setWizardOpen} />

      {loadError && (
        <div role="alert" className="rounded-lg border border-red-500/40 p-3 text-sm text-red-700 dark:text-red-300" data-testid="projects-error">
          {loadError}
        </div>
      )}

      {projects.isLoading && <Skeleton className="h-32 w-full" data-testid="projects-loading" />}

      {mine.length > 1 && (present.roles.length > 1 || present.classes.length > 1) && (
        <div className="flex flex-wrap items-center gap-3 text-sm" role="group" aria-label="Filter projects" data-testid="projects-filter">
          {present.roles.length > 1 && (
            <label className="flex items-center gap-2">Role
              <select className="rounded border bg-background px-2 py-1 text-sm" value={filter.role} data-testid="filter-role"
                onChange={(e) => setFilter({ ...filter, role: e.target.value as Filter["role"] })}>
                <option value="all">All roles</option>
                {present.roles.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
              </select>
            </label>
          )}
          {present.classes.length > 1 && (
            <label className="flex items-center gap-2">Data class
              <select className="rounded border bg-background px-2 py-1 text-sm" value={filter.cls} data-testid="filter-class"
                onChange={(e) => setFilter({ ...filter, cls: e.target.value as Filter["cls"] })}>
                <option value="all">All classes</option>
                {present.classes.map((c) => <option key={c} value={c}>{CLASS_META[c].label}</option>)}
              </select>
            </label>
          )}
          <span className="text-xs text-muted-foreground" data-testid="filter-count">{list.length} of {all.length}</span>
        </div>
      )}

      {all.length > 0 && list.length === 0 && (
        <p className="text-sm text-muted-foreground" data-testid="filter-empty">
          No project matches this filter. <button type="button" className="underline" onClick={() => setFilter(NO_FILTER)}>Show all</button>
        </p>
      )}

      {list.length > 0 && (
        <section className="grid grid-cols-1 gap-4 md:grid-cols-2" aria-label="Projects" data-testid="projects-list">
          {list.map((p) => <ProjectCard key={p.id} p={p} />)}
        </section>
      )}

      {projects.data && mine.length === 0 && (
        <Card data-testid="projects-empty">
          <CardContent className="space-y-3 p-8 text-center">
            <FolderKanban className="mx-auto h-8 w-8 text-muted-foreground" aria-hidden />
            <h2 className="text-lg font-semibold" data-testid="projects-empty-headline">{empty.headline}</h2>
            <p className="mx-auto max-w-prose text-sm text-muted-foreground" data-testid="projects-empty-body">{empty.body}</p>
            {empty.needsPlugin && (
              <Button asChild variant="default" size="sm">
                <Link to="/app/marketplace"><Blocks className="mr-2 h-4 w-4" aria-hidden /> Open the Marketplace</Link>
              </Button>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

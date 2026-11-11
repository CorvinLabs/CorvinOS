/**
 * "At a glance" on the project detail page: the few numbers an owner wants before opening a tab - open tasks, knowledge entries,
 * videos, files not committed. Each number comes from an endpoint that already exists; a tile whose source is off is not drawn
 * (inspector-logic.ts `glanceTiles`). A tile that leads somewhere is one link; the rest only informs. While a number loads the tile
 * says so, and when it cannot be loaded the tile says that instead of showing a zero.
 */
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { getChanges, getStructure, type ProjectDetail } from "@/lib/api/projects";
import { getTaskItems } from "@/lib/api/task-tracking";
import { cn } from "@/lib/utils";
import { changesGlance, glanceTiles, knowledgeGlance, tasksGlance, videosGlance, type GlanceId, type GlanceTile, type GlanceValue } from "./inspector-logic";

type Loaded = { state: "loading" } | { state: "error"; retry: () => void } | { state: "none" } | { state: "ok"; v: GlanceValue };

/** The task board is read through the same scope the Tasks page uses: Corvin itself is "bound", a project is "project:<id>". */
function useTiles(d: ProjectDetail, id: GlanceId): Loaded {
  const scope = d.builtin ? "bound" : `project:${d.id}`;
  const tasks = useQuery({ queryKey: ["projects", "glance", "tasks", d.id], enabled: id === "tasks", retry: false, staleTime: 30_000,
    queryFn: ({ signal }) => getTaskItems(signal, false, scope).then((r) => r.summary) });
  // Knowledge and Videos read the same structure the Structure tab reads, so opening that tab afterwards costs nothing
  const structure = useQuery({ queryKey: ["projects", "structure", d.id], enabled: id === "knowledge" || id === "videos", retry: false, staleTime: 30_000,
    queryFn: ({ signal }) => getStructure(d.id, signal) });
  const changes = useQuery({ queryKey: ["projects", "glance", "changes", d.id], enabled: id === "changes", retry: false, staleTime: 15_000,
    queryFn: ({ signal }) => getChanges(d.id, null, signal) });
  const q = id === "tasks" ? tasks : id === "changes" ? changes : structure;
  if (q.isLoading) return { state: "loading" };
  if (q.error || !q.data) return { state: "error", retry: () => void q.refetch() };
  if (id === "tasks") return { state: "ok", v: tasksGlance(tasks.data!) };
  if (id === "knowledge") return { state: "ok", v: knowledgeGlance(structure.data!.kb_counts) };
  if (id === "videos") return { state: "ok", v: videosGlance(structure.data!.videos) };
  const c = changesGlance(changes.data!);
  return c ? { state: "ok", v: c } : { state: "none" };
}

function Tile({ d, tile }: { d: ProjectDetail; tile: GlanceTile }) {
  const got = useTiles(d, tile.id);
  if (got.state === "none") return null;           // not a git folder: nothing to say, so no tile
  const body = (
    <CardContent className="space-y-1 p-4">
      <p className="flex items-center justify-between text-xs font-medium text-muted-foreground">
        <span>{tile.label}</span>
        {tile.tab && got.state === "ok" && <ChevronRight className="h-4 w-4" aria-hidden />}
      </p>
      {got.state === "loading" && <><Skeleton className="h-7 w-12" data-testid="glance-loading" /><Skeleton className="h-3 w-24" /></>}
      {got.state === "error" && (
        <p className="text-sm text-muted-foreground" data-testid="glance-error">
          Could not load.{" "}
          <button type="button" className="underline" onClick={got.retry} data-testid="glance-retry">Try again</button>
        </p>
      )}
      {got.state === "ok" && (
        <>
          <p className="text-2xl font-semibold tabular-nums" data-testid="glance-value">{got.v.value}</p>
          <p className="text-xs text-muted-foreground" data-testid="glance-detail">{got.v.detail}</p>
        </>
      )}
    </CardContent>
  );
  const lead = tile.tab && got.state === "ok";
  return (
    <Card className={cn("min-w-0", lead && "transition-colors hover:bg-accent/40 focus-within:ring-2 focus-within:ring-ring")}
      data-testid="glance-tile" data-tile={tile.id}>
      {lead ? (
        <Link to={`/app/projects/${encodeURIComponent(d.id)}?tab=${tile.tab}`} className="block rounded-[inherit] focus:outline-none"
          aria-label={`${tile.label}: ${got.v.value} ${got.v.detail}. Open the ${tile.label} tab`} data-testid="glance-link">
          {body}
        </Link>
      ) : body}
    </Card>
  );
}

export function Glance({ d }: { d: ProjectDetail }) {
  const tiles = glanceTiles(d);
  if (tiles.length === 0) return null;
  return (
    <section aria-label="At a glance" className="grid grid-cols-2 gap-3 md:grid-cols-4" data-testid="project-glance">
      {tiles.map((t) => <Tile key={t.id} d={d} tile={t} />)}
    </section>
  );
}

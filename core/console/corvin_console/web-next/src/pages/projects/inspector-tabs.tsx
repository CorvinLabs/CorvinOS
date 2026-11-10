/**
 * The inspector's tab bodies (T-0359). Each shows real data for this project or an explained empty state; a tab
 * whose feature is off shows FeatureOff with the requirements and a button to switch the feature on.
 * Nothing here writes to the project except through the feature switch, which the server judges.
 */
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { getTaskItems } from "@/lib/api/task-tracking";
import { getHealth, getStructure, setFeatures, type ProjectDetail, type StructureEntry } from "@/lib/api/projects";
import { featureLabel } from "./encodings";
import { explain } from "./wizard";
import { useAuth } from "@/lib/auth";

function Explained({ children, testId }: { children: React.ReactNode; testId: string }) {
  return <p className="text-sm text-muted-foreground" data-testid={testId}>{children}</p>;
}

/** A tab whose feature is off: what it needs, and the switch. */
export function FeatureOff({ d, feature, onNotice }: { d: ProjectDetail; feature: string; onNotice?: (msg: string) => void }) {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();
  const row = d.catalogue?.features?.find((f) => f.id === feature);
  const requires = (row?.requires ?? []).filter((r) => r !== "core").map(featureLabel);
  const mut = useMutation({
    mutationFn: () => setFeatures(d.id, { set: { [feature]: true }, accept_dependencies: true }, csrf),
    onSuccess: (r) => {
      // the response is the only place the warnings exist (a GET of the project has none): keep them for the next view
      const warn = (r.warnings ?? []).filter(Boolean);
      if (warn.length) onNotice?.(warn.join(" "));
      void qc.invalidateQueries({ queryKey: ["projects", "detail", d.id] });
    },
  });
  return (
    <div className="space-y-3 rounded-lg border p-4" data-testid="feature-off" data-feature={feature}>
      <p className="font-medium">Feature off</p>
      <p className="text-sm text-muted-foreground">{featureLabel(feature)} is switched off for this project. Its files are not touched either way.</p>
      {requires.length > 0 && (
        <p className="text-xs text-muted-foreground" data-testid="feature-off-requires">Switching it on also adds: {requires.join(", ")}.</p>
      )}
      <Button size="sm" onClick={() => mut.mutate()} disabled={mut.isPending} data-testid="feature-off-enable">
        Switch {featureLabel(feature)} on
      </Button>
      {mut.error && <p role="alert" className="text-sm text-red-700 dark:text-red-300" data-testid="feature-off-error">{explain(mut.error)}</p>}
    </div>
  );
}

export function StructureTab({ id }: { id: string }) {
  const q = useQuery({ queryKey: ["projects", "structure", id], queryFn: ({ signal }) => getStructure(id, signal), refetchInterval: 60_000 });
  if (q.isLoading) return <p className="text-sm text-muted-foreground">Reading the folder…</p>;
  if (!q.data) return <p role="alert" className="text-sm text-red-700 dark:text-red-300" data-testid="structure-error">The folder could not be read.</p>;
  const entries: StructureEntry[] = q.data.entries;
  return (
    <section className="space-y-3" data-testid="structure-tab">
      <p className="text-xs text-muted-foreground">Read-only: the entries below are what is in the project folder now (two levels; .git and virtual environments are left out).</p>
      {entries.length === 0 ? <Explained testId="structure-empty">The folder has no entries.</Explained> : (
        <ul className="max-h-96 overflow-auto rounded-md border text-sm" data-testid="structure-list">
          {entries.map((e) => (
            <li key={e.path} data-testid="structure-entry" data-kind={e.kind} className="flex justify-between gap-3 border-b px-3 py-1 last:border-b-0">
              <span className="truncate font-mono text-xs">{e.kind === "dir" ? `${e.path}/` : e.path}</span>
              <span className="shrink-0 text-xs text-muted-foreground">{e.kind === "file" && e.size !== null ? `${e.size} B` : e.kind}</span>
            </li>
          ))}
        </ul>
      )}
      {q.data.truncated && <p className="text-xs text-muted-foreground" data-testid="structure-truncated">The list stops at {entries.length} entries.</p>}
    </section>
  );
}

export function DocsTab({ id }: { id: string }) {
  const q = useQuery({ queryKey: ["projects", "structure", id], queryFn: ({ signal }) => getStructure(id, signal), refetchInterval: 60_000 });
  const counts = q.data?.kb_counts ?? {};
  const rows = Object.entries(counts);
  const total = rows.reduce((n, [, v]) => n + v, 0);
  if (q.isLoading) return <p className="text-sm text-muted-foreground">Counting the knowledge base…</p>;
  if (!q.isLoading && !q.data) return <p role="alert" className="text-sm text-red-700 dark:text-red-300" data-testid="structure-error">The folder could not be read.</p>;
  return (
    <section className="space-y-3" data-testid="docs-tab">
      {total === 0 ? (
        <Explained testId="docs-empty">The knowledge base has no entries yet. Create the first idea or decision with the commands in .corvin/local/kb-commands.md.</Explained>
      ) : (
        <table className="w-full text-sm" data-testid="docs-counts">
          <tbody>
            {rows.map(([k, v]) => (
              <tr key={k} data-testid="docs-count-row" data-kind={k} className="border-b last:border-b-0">
                <td className="py-1 font-mono text-xs">{k}</td><td className="py-1 text-right tabular-nums">{v}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export function KnowledgeTab({ id }: { id: string }) {
  const q = useQuery({ queryKey: ["projects", "structure", id], queryFn: ({ signal }) => getStructure(id, signal), refetchInterval: 60_000 });
  const total = Object.values(q.data?.kb_counts ?? {}).reduce((n, v) => n + v, 0);
  if (q.isLoading) return <p className="text-sm text-muted-foreground">Counting the knowledge base…</p>;
  if (!q.isLoading && !q.data) return <p role="alert" className="text-sm text-red-700 dark:text-red-300" data-testid="structure-error">The folder could not be read.</p>;
  return (
    <section className="space-y-3" data-testid="knowledge-tab">
      <p className="text-sm">{total} entries in this project's knowledge base.</p>
      <Button asChild size="sm" variant="outline">
        <Link to={`/app/corvin-knowledge?scope=project:${encodeURIComponent(id)}`} data-testid="knowledge-open">Open this project's graph</Link>
      </Button>
      <p className="text-xs text-muted-foreground">The graph shows the last committed state of the project's knowledge base.</p>
    </section>
  );
}

export function TasksTab({ id }: { id: string }) {
  const q = useQuery({
    queryKey: ["task-tracking", "items", `project:${id}`],
    queryFn: ({ signal }) => getTaskItems(signal, false, `project:${id}`),
  });
  const items = q.data?.items ?? [];
  return (
    <section className="space-y-3" data-testid="tasks-tab">
      {q.isLoading && <p className="text-sm text-muted-foreground">Loading tasks…</p>}
      {q.error && <p role="alert" className="text-sm text-red-700 dark:text-red-300" data-testid="tasks-error">The tasks could not be loaded.</p>}
      {q.data && items.length === 0 && <Explained testId="tasks-empty">No tasks for this project yet. A new epic's tasks appear here after the board's next sync.</Explained>}
      {items.length > 0 && (
        <ul className="divide-y rounded-md border text-sm" data-testid="tasks-list">
          {items.map((it) => (
            <li key={it.id} data-testid="tasks-row" className="flex justify-between gap-3 px-3 py-2">
              <span className="truncate">{it.title}</span>
              <span className="shrink-0 text-xs text-muted-foreground">{it.status}</span>
            </li>
          ))}
        </ul>
      )}
      <Button asChild size="sm" variant="outline">
        <Link to={`/app/initiatives?scope=project:${encodeURIComponent(id)}`} data-testid="tasks-open">Open on the Tasks board</Link>
      </Button>
    </section>
  );
}

export function VideosTab({ id }: { id: string }) {
  const q = useQuery({ queryKey: ["projects", "structure", id], queryFn: ({ signal }) => getStructure(id, signal), refetchInterval: 60_000 });
  const rows = q.data?.videos ?? [];
  if (q.isLoading) return <p className="text-sm text-muted-foreground">Reading the videos…</p>;
  if (!q.isLoading && !q.data) return <p role="alert" className="text-sm text-red-700 dark:text-red-300" data-testid="structure-error">The folder could not be read.</p>;
  return (
    <section className="space-y-3" data-testid="videos-tab">
      {rows.length === 0 ? (
        <Explained testId="videos-empty">No videos yet. Create one from Overview; it appears here with its languages and whether its folder is there.</Explained>
      ) : (
        <ul className="divide-y rounded-md border text-sm" data-testid="videos-list">
          {rows.map((v) => (
            <li key={v.name} data-testid="video-row" className="flex flex-wrap justify-between gap-2 px-3 py-2">
              <span className="font-medium">{v.name}</span>
              <span className="break-words text-xs text-muted-foreground">DE {v.de ? "yes" : "-"} · EN {v.en ? "yes" : "-"} · {v.folder_exists ? "folder present" : "folder missing"}{v.state ? ` · ${v.state}` : ""}</span>
            </li>
          ))}
        </ul>
      )}
      {q.data?.videos_root && <p className="break-all text-xs text-muted-foreground font-mono" data-testid="videos-root">{q.data.videos_root}</p>}
    </section>
  );
}

export function DirectoryTab({ d }: { d: ProjectDetail }) {
  const peers = d.directory_peers ?? [];
  return (
    <section className="space-y-3" data-testid="directory-tab">
      {peers.length === 0 ? (
        <Explained testId="directory-empty">No peer is allowed for this project yet. Peers are the ones paired in the Agent Hub; a project only narrows that list and never sends anything by itself.</Explained>
      ) : (
        <ul className="divide-y rounded-md border text-sm" data-testid="directory-list">
          {peers.map((p) => <li key={p} data-testid="directory-peer" className="px-3 py-2 font-mono text-xs">{p}</li>)}
        </ul>
      )}
    </section>
  );
}

export function ComputeTab({ id }: { id: string }) {
  const q = useQuery({ queryKey: ["projects", "health", id], queryFn: ({ signal }) => getHealth(id, signal), refetchInterval: false });
  const worker = q.data?.checks.find((c) => c.id === "compute_worker");
  return (
    <section className="space-y-3" data-testid="compute-tab">
      {q.error ? (
        <p role="alert" className="text-sm text-red-700 dark:text-red-300" data-testid="compute-error">The compute worker could not be checked.</p>
      ) : q.isLoading ? (
        <p className="text-sm text-muted-foreground">Checking the compute worker…</p>
      ) : worker ? (
        <p className="text-sm" data-testid="compute-worker">{worker.detail}</p>
      ) : (
        <Explained testId="compute-no-check">The compute worker is checked in Overview health when agentic compute is on.</Explained>
      )}
      <Explained testId="compute-runs-note">Compute runs are not linked to a project yet, so the runs themselves are listed on the Compute page for the whole tenant.</Explained>
      <Button asChild size="sm" variant="outline"><Link to="/app/compute">Open Compute</Link></Button>
    </section>
  );
}

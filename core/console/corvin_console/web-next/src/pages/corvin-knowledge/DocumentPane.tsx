import { useEffect, useMemo, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle, ArrowLeft, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Markdown } from "@/components/markdown";
import { api, ApiError } from "@/lib/api/client";
import { NODE_HREF_PREFIX, linkifyMarkdown, type LinkIndex } from "./link-resolver";

export interface DocRef { id: string; label: string; title: string; type: string; relation: string }
export interface KnowledgeDoc {
  entity: { id: string; label: string; type: string; title: string; status: string; realization: string };
  frontmatter: Record<string, unknown>;
  markdown: string;
  outgoing: DocRef[];
  incoming: DocRef[];
}

function group(refs: DocRef[]): [string, DocRef[]][] {
  const m = new Map<string, DocRef[]>();
  for (const r of refs) (m.get(r.relation) ?? m.set(r.relation, []).get(r.relation)!).push(r);
  return [...m.entries()];
}

function RefList({ title, refs, onOpen, testid }: { title: string; refs: DocRef[]; onOpen: (k: string) => void; testid: string }) {
  if (refs.length === 0) return null;
  return (
    <section className="space-y-1" data-testid={testid}>
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{title}</h3>
      {group(refs).map(([rel, items]) => (
        <div key={rel} className="text-sm">
          <span className="mr-2 text-xs text-muted-foreground">{rel.replace(/_/g, " ")}</span>
          {items.map((r) => (
            <button key={`${rel}-${r.id}`} type="button" onClick={() => onOpen(r.id)} title={r.title}
              className="mr-2 inline text-accent underline-offset-2 hover:underline">{r.label}</button>
          ))}
        </div>
      ))}
    </section>
  );
}

interface Props {
  nodeKey: string | null;
  index: LinkIndex;
  basePath: string;
  onOpen: (key: string) => void;
  onBack: (() => void) | null;
}

/** Reads one node's Markdown (GET /doc/{key}) and renders it with ids turned into links. */
export function DocumentPane({ nodeKey, index, basePath, onOpen, onBack }: Props) {
  const top = useRef<HTMLDivElement>(null);
  const q = useQuery({
    queryKey: ["corvin-knowledge", "doc", nodeKey],
    queryFn: ({ signal }) => api<KnowledgeDoc>(`${basePath}/doc/${encodeURIComponent(nodeKey ?? "")}`, { signal }),
    enabled: !!nodeKey,
    retry: false,
    staleTime: 30_000,
  });
  useEffect(() => { top.current?.scrollTo({ top: 0 }); }, [nodeKey]);
  const body = useMemo(
    () => (q.data ? linkifyMarkdown(q.data.markdown, index, q.data.entity.id) : ""),
    [q.data, index],
  );
  const internal = useMemo(
    () => ({ prefix: NODE_HREF_PREFIX, onNavigate: (rest: string) => { try { onOpen(decodeURIComponent(rest)); } catch { /* malformed href */ } } }),
    [onOpen],
  );

  let content: React.ReactNode;
  if (!nodeKey) content = <p className="text-sm text-muted-foreground">Select a node in the graph to read its document.</p>;
  else if (q.isLoading) content = <div className="py-16 flex justify-center"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>;
  else if (q.isError) {
    const status = q.error instanceof ApiError ? q.error.status : 0;
    content = (
      <div className="flex items-start gap-2 text-sm text-destructive" data-testid="knowledge-doc-error">
        <AlertCircle size={18} className="mt-0.5 shrink-0" />
        {status === 413 ? "This document is too large to display." : status === 404 ? "This document is not available in the configured repository." : status === 409 ? "Several documents share this id." : "The document could not be loaded."}
      </div>
    );
  } else if (q.data) {
    const d = q.data;
    content = (
      <>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline">{d.entity.type}</Badge>
          <Badge variant="secondary">{d.entity.status}</Badge>
          {d.entity.realization && d.entity.realization !== "n/a" && <Badge variant="outline">{d.entity.realization}</Badge>}
          <span className="font-mono text-xs text-muted-foreground" data-testid="knowledge-doc-id">{d.entity.label}</span>
        </div>
        <div data-testid="knowledge-doc-body">
          {body.trim() ? <Markdown text={body} internalHref={internal} /> : <p className="text-sm font-semibold">{d.entity.title}</p>}
        </div>
        <RefList title="Links" refs={d.outgoing} onOpen={onOpen} testid="knowledge-doc-outgoing" />
        <RefList title="Linked from" refs={d.incoming} onOpen={onOpen} testid="knowledge-doc-incoming" />
      </>
    );
  }

  return (
    <div ref={top} className="h-[620px] overflow-y-auto rounded-lg border border-border bg-card p-4 space-y-3" data-testid="knowledge-doc-pane">
      {onBack && <Button size="sm" variant="ghost" onClick={onBack}><ArrowLeft className="h-4 w-4" /> Back</Button>}
      {content}
    </div>
  );
}

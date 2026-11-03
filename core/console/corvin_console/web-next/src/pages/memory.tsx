import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookOpen,
  Loader2,
  MessageSquare,
  Plus,
  RefreshCw,
  Search,
  StickyNote,
  FileText,
  Trash2,
  X,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  addMemoryNote,
  deleteMemoryEntry,
  getMemoryEntry,
  getMemoryIndex,
  searchMemory,
  type MemoryFileSummary,
  type MemoryNoteType,
  type MemorySource,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

// The caller's OWN tenant memory (ADR A-10): conversation turns indexed from
// the tenant's chats (PII-redacted), notes added here, and the tenant's memory
// files. Never the host developer's ~/.claude memory (S-1a), never sample data.

type Filter = "all" | "conversation" | "note" | "files";

const SOURCE_LABEL: Record<MemorySource, string> = {
  conversation: "Conversation",
  note: "Note",
  topic: "Topic",
  harvest: "Worker harvest",
  session: "Session",
  forge: "Forge",
};

const NOTE_TYPES: { value: MemoryNoteType; label: string }[] = [
  { value: "project", label: "Project" },
  { value: "user", label: "About me" },
  { value: "feedback", label: "Feedback" },
  { value: "reference", label: "Reference" },
];

function fmtTime(ts: number | null | undefined): string {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString();
}

function matchesFilter(f: MemoryFileSummary, filter: Filter): boolean {
  if (filter === "all") return true;
  if (filter === "conversation") return f.source === "conversation";
  if (filter === "note") return f.source === "note";
  return f.source !== "conversation" && f.source !== "note";
}

function SourceIcon({ source, className }: { source: MemorySource; className?: string }) {
  if (source === "conversation") return <MessageSquare className={className} />;
  if (source === "note") return <StickyNote className={className} />;
  return <FileText className={className} />;
}

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = React.useState(value);
  React.useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function MemoryPage() {
  const { session } = useAuth();
  const qc = useQueryClient();
  const [filter, setFilter] = React.useState<Filter>("all");
  const [query, setQuery] = React.useState("");
  const [selected, setSelected] = React.useState<string | null>(null);
  const [addOpen, setAddOpen] = React.useState(false);
  const debounced = useDebounced(query.trim(), 300);

  const indexQ = useQuery({
    queryKey: ["memory-index"],
    queryFn: ({ signal }) => getMemoryIndex(signal),
    refetchInterval: 30_000,
  });

  const searchQ = useQuery({
    queryKey: ["memory-search", debounced],
    queryFn: ({ signal }) => searchMemory(debounced, signal),
    enabled: debounced.length > 0,
  });

  const entryQ = useQuery({
    queryKey: ["memory-entry", selected],
    queryFn: ({ signal }) => getMemoryEntry(selected!, signal),
    enabled: !!selected,
  });

  const deleteM = useMutation({
    mutationFn: (name: string) => deleteMemoryEntry(name, session!.csrf_token),
    onSuccess: () => {
      setSelected(null);
      qc.invalidateQueries({ queryKey: ["memory-index"] });
      qc.invalidateQueries({ queryKey: ["memory-search"] });
    },
  });

  const searching = debounced.length > 0;
  const list: MemoryFileSummary[] = (
    searching ? searchQ.data?.results ?? [] : indexQ.data?.files ?? []
  ).filter((f) => matchesFilter(f, filter));
  const sources = indexQ.data?.sources ?? {};
  const fileCount =
    (sources.topic ?? 0) + (sources.harvest ?? 0) + (sources.session ?? 0) + (sources.forge ?? 0);

  return (
    <div className="flex flex-col gap-6" data-testid="memory-page">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Memory</h1>
          <p className="text-sm text-muted-foreground">
            Persistent memory for this workspace
            {indexQ.data?.tenant_id ? <> ({indexQ.data.tenant_id})</> : null}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={() => setAddOpen(true)} data-testid="memory-add-btn">
            <Plus className="mr-1 h-4 w-4" />
            Add memory
          </Button>
          <Button
            size="icon"
            variant="ghost"
            className="h-8 w-8 text-muted-foreground"
            aria-label="Refresh"
            onClick={() => {
              indexQ.refetch();
              if (searching) searchQ.refetch();
            }}
            disabled={indexQ.isFetching}
          >
            <RefreshCw className={cn("h-4 w-4", indexQ.isFetching && "animate-spin")} />
          </Button>
        </div>
      </div>

      {indexQ.isLoading && <Skeleton className="h-40 w-full rounded-lg" />}

      {indexQ.isError && (
        <p className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          Memory is unavailable right now.
        </p>
      )}

      {indexQ.data && (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatCard label="Total entries" value={indexQ.data.count} />
            <StatCard
              label="Conversation turns"
              value={sources.conversation ?? 0}
              hint={
                indexQ.data.recall?.chats
                  ? `${indexQ.data.recall.chats} chat${indexQ.data.recall.chats === 1 ? "" : "s"}`
                  : undefined
              }
            />
            <StatCard label="Notes" value={sources.note ?? 0} />
            <StatCard label="Memory files" value={fileCount} />
          </div>

          {indexQ.data.reason && (
            <p className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
              {indexQ.data.reason}
            </p>
          )}

          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <div className="relative flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search memory (conversations and notes)…"
                className="pl-9 pr-9"
                data-testid="memory-search-input"
                aria-label="Search memory"
              />
              {query && (
                <button
                  type="button"
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                  onClick={() => setQuery("")}
                  aria-label="Clear search"
                >
                  <X className="h-4 w-4" />
                </button>
              )}
            </div>
            <div className="flex gap-1" role="tablist" aria-label="Filter memory">
              {(["all", "conversation", "note", "files"] as Filter[]).map((f) => (
                <Button
                  key={f}
                  size="sm"
                  variant={filter === f ? "secondary" : "ghost"}
                  role="tab"
                  aria-selected={filter === f}
                  onClick={() => setFilter(f)}
                  className="h-8 px-3 text-xs"
                >
                  {f === "all" ? "All" : f === "conversation" ? "Conversations" : f === "note" ? "Notes" : "Files"}
                </Button>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
            <div className="flex flex-col gap-2" data-testid="memory-list">
              {searching && searchQ.isFetching && <Skeleton className="h-16 w-full" />}
              {!indexQ.data.present && !searching && (
                <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border py-16 text-center">
                  <BookOpen className="mb-3 h-8 w-8 text-muted-foreground/40" />
                  <p className="text-sm font-medium">No memory yet</p>
                  <p className="mt-1 max-w-sm text-xs text-muted-foreground">
                    Memory fills as you chat — every answered turn is indexed here (personal data
                    redacted). You can also add a note yourself.
                  </p>
                </div>
              )}
              {(indexQ.data.present || searching) && list.length === 0 && !searchQ.isFetching && (
                <p className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-xs text-muted-foreground">
                  {searching ? `Nothing in memory matches “${debounced}”.` : "No entries in this view."}
                </p>
              )}
              {list.map((f) => (
                <button
                  key={f.name}
                  type="button"
                  onClick={() => setSelected(f.name)}
                  data-testid={`memory-item-${f.name}`}
                  className={cn(
                    "rounded-lg border border-border bg-card px-3 py-2.5 text-left transition-colors hover:border-accent/40",
                    selected === f.name && "border-accent/60 bg-accent/5",
                  )}
                >
                  <div className="flex items-start gap-2">
                    <SourceIcon source={f.source} className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">{f.title}</div>
                      <div className="line-clamp-2 text-xs text-muted-foreground">
                        {"snippet" in f && typeof f.snippet === "string" ? f.snippet : f.description}
                      </div>
                      <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] text-muted-foreground">
                        <Badge variant="outline" className="px-1.5 py-0 text-[10px]">
                          {SOURCE_LABEL[f.source] ?? f.source}
                        </Badge>
                        {f.source !== "conversation" && f.type && (
                          <span className="font-mono">{f.type}</span>
                        )}
                        <span>{fmtTime(f.modified)}</span>
                      </div>
                    </div>
                  </div>
                </button>
              ))}
              {!searching && indexQ.data.truncated && filter !== "note" && filter !== "files" && (
                <p className="text-center text-[11px] text-muted-foreground">
                  Showing the newest conversation turns — search to reach older ones.
                </p>
              )}
            </div>

            <div>
              {!selected && (
                <div className="hidden h-full min-h-40 items-center justify-center rounded-lg border border-dashed border-border text-xs text-muted-foreground lg:flex">
                  Select an entry to read it.
                </div>
              )}
              {selected && (
                <Card data-testid="memory-detail">
                  <CardContent className="flex flex-col gap-3 p-4">
                    {entryQ.isLoading && <Skeleton className="h-32 w-full" />}
                    {entryQ.isError && (
                      <p className="text-xs text-destructive">This entry is no longer available.</p>
                    )}
                    {entryQ.data && (
                      <>
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <h2 className="break-words text-base font-semibold">{entryQ.data.title}</h2>
                            <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                              <Badge variant="outline" className="px-1.5 py-0 text-[10px]">
                                {SOURCE_LABEL[entryQ.data.source] ?? entryQ.data.source}
                              </Badge>
                              <span>{fmtTime(entryQ.data.modified)}</span>
                              {entryQ.data.origin && <span className="font-mono">{entryQ.data.origin}</span>}
                            </div>
                          </div>
                          <div className="flex shrink-0 items-center gap-1">
                            {entryQ.data.deletable && (
                              <Button
                                variant="ghost"
                                size="sm"
                                className="h-7 px-2 text-destructive hover:bg-destructive/10 hover:text-destructive"
                                disabled={deleteM.isPending}
                                data-testid="memory-delete-btn"
                                onClick={() => {
                                  if (window.confirm("Delete this memory entry? This cannot be undone.")) {
                                    deleteM.mutate(entryQ.data!.name);
                                  }
                                }}
                              >
                                {deleteM.isPending ? (
                                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                ) : (
                                  <Trash2 className="h-3.5 w-3.5" />
                                )}
                                <span className="ml-1 text-xs">Delete</span>
                              </Button>
                            )}
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-7 w-7 text-muted-foreground"
                              aria-label="Close"
                              onClick={() => setSelected(null)}
                            >
                              <X className="h-4 w-4" />
                            </Button>
                          </div>
                        </div>
                        {deleteM.isError && (
                          <p className="text-xs text-destructive">
                            {deleteM.error instanceof Error ? deleteM.error.message : "Delete failed."}
                          </p>
                        )}
                        {entryQ.data.source === "conversation" ? (
                          <div className="flex flex-col gap-2 text-sm">
                            <div className="rounded-md bg-muted/40 px-3 py-2">
                              <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">You</div>
                              <p className="whitespace-pre-wrap break-words">{entryQ.data.user_text}</p>
                            </div>
                            <div className="rounded-md border border-border px-3 py-2">
                              <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">Assistant</div>
                              <p className="whitespace-pre-wrap break-words">{entryQ.data.assistant_text}</p>
                            </div>
                            {entryQ.data.redacted_classes && entryQ.data.redacted_classes.length > 0 && (
                              <p className="text-[11px] text-muted-foreground">
                                Redacted before storage: {entryQ.data.redacted_classes.join(", ")}
                              </p>
                            )}
                          </div>
                        ) : (
                          <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted/40 p-3 font-mono text-xs leading-relaxed">
                            {entryQ.data.content}
                          </pre>
                        )}
                        {!entryQ.data.deletable && (
                          <p className="text-[11px] text-muted-foreground">
                            Read-only: managed by its session or the forge.
                          </p>
                        )}
                      </>
                    )}
                  </CardContent>
                </Card>
              )}
            </div>
          </div>
        </>
      )}

      <AddMemoryDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        csrf={session?.csrf_token ?? ""}
        onAdded={(name) => {
          qc.invalidateQueries({ queryKey: ["memory-index"] });
          qc.invalidateQueries({ queryKey: ["memory-search"] });
          setSelected(name);
        }}
      />
    </div>
  );
}

function StatCard({ label, value, hint }: { label: string; value: number; hint?: string }) {
  return (
    <Card>
      <CardContent className="p-3">
        <div className="text-[11px] text-muted-foreground">{label}</div>
        <div className="text-lg font-semibold tabular-nums">{value}</div>
        {hint && <div className="text-[10px] text-muted-foreground">{hint}</div>}
      </CardContent>
    </Card>
  );
}

function AddMemoryDialog({
  open,
  onOpenChange,
  csrf,
  onAdded,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  csrf: string;
  onAdded: (name: string) => void;
}) {
  const [title, setTitle] = React.useState("");
  const [content, setContent] = React.useState("");
  const [type, setType] = React.useState<MemoryNoteType>("project");
  const [error, setError] = React.useState<string | null>(null);

  const reset = () => {
    setTitle("");
    setContent("");
    setType("project");
    setError(null);
  };

  const addM = useMutation({
    mutationFn: () => addMemoryNote({ title: title.trim(), content: content.trim(), type }, csrf),
    onSuccess: (entry) => {
      onAdded(entry.name);
      onOpenChange(false);
      reset();
    },
    onError: (e) => setError(e instanceof Error ? e.message : String(e)),
  });

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) reset();
        onOpenChange(v);
      }}
    >
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Add memory</DialogTitle>
          <DialogDescription>
            Saved to this workspace's own memory store — other workspaces never see it.
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4 pt-1"
          data-testid="memory-add-form"
          onSubmit={(e) => {
            e.preventDefault();
            setError(null);
            if (!title.trim() || !content.trim()) {
              setError("Title and content are required.");
              return;
            }
            addM.mutate();
          }}
        >
          <div>
            <Label htmlFor="memory-title" className="mb-1.5 block text-xs">Title</Label>
            <Input
              id="memory-title"
              value={title}
              maxLength={200}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Deployment rules"
              data-testid="memory-title-input"
            />
          </div>
          <div>
            <Label htmlFor="memory-type" className="mb-1.5 block text-xs">Kind</Label>
            <Select id="memory-type" value={type} onChange={(e) => setType(e.target.value as MemoryNoteType)}>
              {NOTE_TYPES.map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </Select>
          </div>
          <div>
            <Label htmlFor="memory-content" className="mb-1.5 block text-xs">Content</Label>
            <Textarea
              id="memory-content"
              value={content}
              maxLength={20000}
              onChange={(e) => setContent(e.target.value)}
              placeholder="What should be remembered?"
              data-testid="memory-content-input"
            />
          </div>
          {error && <p className="text-xs text-destructive">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={addM.isPending || !csrf} data-testid="memory-save-btn">
              {addM.isPending && <Loader2 className="mr-1 h-4 w-4 animate-spin" />}
              Save
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

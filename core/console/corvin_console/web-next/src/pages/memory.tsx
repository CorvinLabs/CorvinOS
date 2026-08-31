import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookOpen,
  Edit3,
  FileText,
  Loader2,
  RefreshCw,
  Save,
  Tag,
  Trash2,
  X,
} from "lucide-react";
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
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { ReauthDialog } from "@/components/reauth-dialog";
import {
  deleteMemoryFile,
  getMemoryFile,
  getMemoryIndex,
  putMemoryFile,
  type MemoryFileSummary,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn, formatDate } from "@/lib/utils";

// ── Type badge ────────────────────────────────────────────────────────────

const TYPE_COLORS: Record<string, string> = {
  index:     "bg-slate-500/15 text-slate-600 dark:text-slate-400",
  user:      "bg-blue-500/15 text-blue-600 dark:text-blue-400",
  feedback:  "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  project:   "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  reference: "bg-violet-500/15 text-violet-600 dark:text-violet-400",
  other:     "bg-muted text-muted-foreground",
};

function TypeBadge({ type }: { type: string }) {
  return (
    <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide", TYPE_COLORS[type] ?? TYPE_COLORS.other)}>
      {type}
    </span>
  );
}

// ── File card ─────────────────────────────────────────────────────────────

function MemoryCard({
  file,
  active,
  onClick,
}: {
  file: MemoryFileSummary;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <Card
      onClick={onClick}
      className={cn(
        "cursor-pointer transition-colors hover:border-accent/50",
        active && "border-accent/70 bg-accent/5",
      )}
    >
      <CardContent className="flex flex-col gap-1.5 p-4">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <span className="truncate font-mono text-xs font-medium">{file.name}</span>
          </div>
          <TypeBadge type={file.type} />
        </div>
        {file.description && (
          <p className="line-clamp-2 text-xs text-muted-foreground">{file.description}</p>
        )}
        <div className="flex items-center gap-2 text-[10px] text-muted-foreground/60">
          {file.size_bytes != null && <span>{(file.size_bytes / 1024).toFixed(1)} KB</span>}
          {file.modified != null && (
            <>
              <span>·</span>
              <span>{formatDate(file.modified)}</span>
            </>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// ── Editor panel ──────────────────────────────────────────────────────────

function EditorPanel({
  name,
  csrf,
  onClose,
}: {
  name: string;
  csrf: string;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const fileQ = useQuery({
    queryKey: ["memory-file", name],
    queryFn: ({ signal }) => getMemoryFile(name, signal),
  });

  const [draft, setDraft] = React.useState<string | null>(null);
  const [reauthOpen, setReauthOpen] = React.useState(false);
  const [deleteOpen, setDeleteOpen] = React.useState(false);
  const [saved, setSaved] = React.useState(false);

  React.useEffect(() => {
    if (fileQ.data && draft === null) setDraft(fileQ.data.body);
  }, [fileQ.data, draft]);

  const saveMut = useMutation({
    mutationFn: (body: string) => putMemoryFile(name, body, csrf),
    onSuccess: () => {
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
      void qc.invalidateQueries({ queryKey: ["memory-index"] });
      void qc.invalidateQueries({ queryKey: ["memory-file", name] });
    },
  });

  const deleteMut = useMutation({
    mutationFn: () => deleteMemoryFile(name, csrf),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["memory-index"] });
      onClose();
    },
  });

  const isProtected = name === "MEMORY.md";
  const isDirty = draft !== null && draft !== (fileQ.data?.body ?? "");

  if (fileQ.isLoading) {
    return (
      <div className="flex flex-col gap-3 p-4">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-80 w-full" />
      </div>
    );
  }

  if (fileQ.isError) {
    return (
      <div className="p-4 text-sm text-destructive">Failed to load file.</div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* Toolbar */}
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm font-medium">{name}</span>
          {fileQ.data && <TypeBadge type={fileQ.data.type} />}
        </div>
        <div className="flex items-center gap-1.5">
          {!isProtected && (
            <Button
              size="sm"
              variant="ghost"
              className="h-7 gap-1.5 text-destructive hover:text-destructive"
              onClick={() => setDeleteOpen(true)}
              disabled={deleteMut.isPending}
            >
              <Trash2 className="h-3.5 w-3.5" /> Delete
            </Button>
          )}
          <Button
            size="sm"
            variant={saved ? "outline" : "accent"}
            className="h-7 gap-1.5"
            disabled={!isDirty || saveMut.isPending}
            onClick={() => setReauthOpen(true)}
          >
            {saveMut.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : saved ? (
              <>✓ Saved</>
            ) : (
              <><Save className="h-3.5 w-3.5" /> Save</>
            )}
          </Button>
          <Button size="icon" variant="ghost" className="h-7 w-7" onClick={onClose}>
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {/* Editor */}
      <div className="flex-1 p-4">
        <Textarea
          value={draft ?? ""}
          onChange={(e) => setDraft(e.target.value)}
          className="h-full min-h-[400px] resize-none font-mono text-xs leading-relaxed"
          spellCheck={false}
        />
      </div>

      {/* Save re-auth */}
      <ReauthDialog
        open={reauthOpen}
        onOpenChange={setReauthOpen}
        title="Save memory file"
        description={`Overwrite "${name}" with your changes?`}
        onConfirm={async (_token) => {
          if (draft === null) return;
          await saveMut.mutateAsync(draft);
        }}
      />

      {/* Delete confirm */}
      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Delete memory file?</DialogTitle>
            <DialogDescription>
              This permanently removes <span className="font-mono">{name}</span> from the memory store.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDeleteOpen(false)}>Cancel</Button>
            <Button
              variant="destructive"
              disabled={deleteMut.isPending}
              onClick={() => deleteMut.mutate()}
            >
              {deleteMut.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ── Filter tabs ───────────────────────────────────────────────────────────

const TYPES = ["all", "user", "feedback", "project", "reference", "index", "other"] as const;
type Filter = (typeof TYPES)[number];

// ── Main page ─────────────────────────────────────────────────────────────

export function MemoryPage() {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";

  const indexQ = useQuery({
    queryKey: ["memory-index"],
    queryFn: ({ signal }) => getMemoryIndex(signal),
    refetchInterval: 30_000,
  });

  const [activeFile, setActiveFile] = React.useState<string | null>(null);
  const [filter, setFilter] = React.useState<Filter>("all");

  const files = React.useMemo(() => indexQ.data?.files ?? [], [indexQ.data?.files]);
  const filtered = filter === "all" ? files : files.filter((f) => f.type === filter);

  const counts = React.useMemo(() => {
    const c: Record<string, number> = { all: files.length };
    for (const f of files) c[f.type] = (c[f.type] ?? 0) + 1;
    return c;
  }, [files]);

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Memory</h1>
          <p className="text-sm text-muted-foreground">
            Persistent memory store — {indexQ.data?.count ?? 0} file{indexQ.data?.count !== 1 ? "s" : ""} in{" "}
            <span className="font-mono text-xs">{indexQ.data?.memory_dir ?? "…"}</span>
          </p>
        </div>
        <Button
          size="icon"
          variant="ghost"
          className="h-8 w-8 text-muted-foreground"
          onClick={() => indexQ.refetch()}
          disabled={indexQ.isFetching}
        >
          <RefreshCw className={cn("h-4 w-4", indexQ.isFetching && "animate-spin")} />
        </Button>
      </div>

      {/* Empty state — memory dir not present */}
      {indexQ.data && !indexQ.data.present && (
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border py-16 text-center">
          <BookOpen className="mb-3 h-8 w-8 text-muted-foreground/40" />
          <p className="text-sm font-medium">No memory files yet</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Memory is created automatically as Claude Code works in this project.
          </p>
        </div>
      )}

      {/* Content */}
      {indexQ.data?.present && (
        <div className="flex gap-4">
          {/* Left: list */}
          <div className="flex w-72 shrink-0 flex-col gap-3">
            {/* Filter chips */}
            <div className="flex flex-wrap gap-1.5">
              {TYPES.filter((t) => t === "all" || (counts[t] ?? 0) > 0).map((t) => (
                <button
                  key={t}
                  onClick={() => setFilter(t)}
                  className={cn(
                    "flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium transition-colors",
                    filter === t
                      ? "bg-accent text-accent-foreground"
                      : "bg-muted text-muted-foreground hover:bg-muted/80",
                  )}
                >
                  <Tag className="h-2.5 w-2.5" />
                  {t}
                  {counts[t] != null && (
                    <span className="ml-0.5 opacity-70">{counts[t]}</span>
                  )}
                </button>
              ))}
            </div>

            {/* File list */}
            <div className="flex flex-col gap-2">
              {indexQ.isLoading
                ? Array.from({ length: 4 }, (_, i) => (
                    <Skeleton key={i} className="h-20 w-full rounded-lg" />
                  ))
                : filtered.map((f) => (
                    <MemoryCard
                      key={f.name}
                      file={f}
                      active={activeFile === f.name}
                      onClick={() => setActiveFile(f.name === activeFile ? null : f.name)}
                    />
                  ))}
              {!indexQ.isLoading && filtered.length === 0 && (
                <p className="py-4 text-center text-xs text-muted-foreground">
                  No {filter} files.
                </p>
              )}
            </div>
          </div>

          {/* Right: editor */}
          <div className="flex-1 rounded-lg border border-border bg-card min-h-[500px]">
            {activeFile ? (
              <EditorPanel
                key={activeFile}
                name={activeFile}
                csrf={csrf}
                onClose={() => setActiveFile(null)}
              />
            ) : (
              <div className="flex h-full flex-col items-center justify-center gap-2 py-20 text-center">
                <Edit3 className="h-7 w-7 text-muted-foreground/30" />
                <p className="text-sm text-muted-foreground">Select a memory file to view or edit</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  ChevronDown,
  ChevronRight,
  Download,
  File as FileIcon,
  FileText,
  Folder,
  FolderOpen,
  FolderPlus,
  Image,
  Lock,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import {
  createDir,
  deleteFile,
  fileDownloadUrl,
  getFileContent,
  listFilesTree,
  uploadFile,
  type FileEntry,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// ── Helpers ────────────────────────────────────────────────────────────

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function fmtDate(ts: number | null): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleDateString("de-DE", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
}

function EntryIcon({ entry }: { entry: FileEntry }) {
  if (entry.is_dir) {
    if (entry.access === "read")
      return <Lock className="h-4 w-4 shrink-0 text-amber-500" />;
    return <Folder className="h-4 w-4 shrink-0 text-blue-500" />;
  }
  const ext = entry.name.split(".").pop()?.toLowerCase() ?? "";
  if (["png", "jpg", "jpeg", "gif", "svg", "webp", "bmp"].includes(ext))
    return <Image className="h-4 w-4 shrink-0 text-purple-500" />;
  if (["md", "txt", "py", "ts", "tsx", "js", "json", "yaml", "yml", "log", "sh"].includes(ext))
    return <FileText className="h-4 w-4 shrink-0 text-emerald-500" />;
  return <FileIcon className="h-4 w-4 shrink-0 text-muted-foreground" />;
}

// ── Tree node (lazy-loading sidebar) ──────────────────────────────────

interface TreeNodeProps {
  entry: FileEntry;
  indent: number;
  selected: string;
  onSelect: (path: string) => void;
}

function TreeNode({ entry, indent, selected, onSelect }: TreeNodeProps) {
  const [open, setOpen] = React.useState(entry.rel_path === "files");

  const { data, isFetching } = useQuery({
    queryKey: ["files-tree-node", entry.rel_path],
    queryFn: ({ signal }) => listFilesTree(entry.rel_path, 1, signal),
    enabled: open && entry.is_dir,
    staleTime: 15_000,
  });

  const subdirs = (data?.children ?? entry.children ?? []).filter(
    (c) => c.is_dir && c.access !== "none",
  );

  const isActive = selected === entry.rel_path;

  return (
    <div>
      <button
        style={{ paddingLeft: `${indent * 12 + 8}px` }}
        className={cn(
          "flex w-full items-center gap-1.5 rounded px-2 py-1.5 text-left text-sm transition-colors",
          "hover:bg-muted",
          isActive ? "bg-accent/15 text-foreground font-medium" : "text-muted-foreground",
        )}
        onClick={() => {
          setOpen((v) => !v);
          onSelect(entry.rel_path);
        }}
      >
        <span className="shrink-0 text-muted-foreground/60">
          {open ? (
            <ChevronDown className="h-3 w-3" />
          ) : (
            <ChevronRight className="h-3 w-3" />
          )}
        </span>
        {entry.access === "read" ? (
          <Lock className="h-3.5 w-3.5 shrink-0 text-amber-500" />
        ) : open ? (
          <FolderOpen className="h-3.5 w-3.5 shrink-0 text-blue-500" />
        ) : (
          <Folder className="h-3.5 w-3.5 shrink-0 text-blue-500" />
        )}
        <span className="truncate">{entry.name}</span>
        {entry.access === "read" && (
          <Badge variant="outline" className="ml-auto shrink-0 px-1 py-0 text-[10px]">
            ro
          </Badge>
        )}
      </button>

      {open && (
        <div>
          {isFetching && subdirs.length === 0 && (
            <div
              style={{ paddingLeft: `${indent * 12 + 28}px` }}
              className="py-1 text-xs text-muted-foreground"
            >
              …
            </div>
          )}
          {subdirs.map((child) => (
            <TreeNode
              key={child.rel_path}
              entry={child}
              indent={indent + 1}
              selected={selected}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ── File preview overlay ───────────────────────────────────────────────

function PreviewOverlay({ path, onClose }: { path: string; onClose: () => void }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["file-content", path],
    queryFn: ({ signal }) => getFileContent(path, signal),
  });

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 p-4 backdrop-blur"
      onClick={onClose}
    >
      <div
        className="relative flex max-h-[82vh] w-full max-w-3xl flex-col overflow-hidden rounded-xl border bg-card shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex shrink-0 items-center gap-2 border-b px-4 py-3">
          <span className="min-w-0 flex-1 truncate text-sm font-medium">
            {path.split("/").pop()}
          </span>
          <Button asChild variant="outline" size="sm" className="shrink-0">
            <a href={fileDownloadUrl(path)} download onClick={(e) => e.stopPropagation()}>
              <Download className="mr-1 h-3.5 w-3.5" /> Download
            </a>
          </Button>
          <Button variant="ghost" size="icon" className="shrink-0" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>

        {/* Body */}
        <div className="min-h-0 flex-1 overflow-auto p-4">
          {isLoading && (
            <div className="py-8 text-center text-sm text-muted-foreground">Loading preview…</div>
          )}
          {error && (
            <div className="flex items-center gap-2 text-sm text-destructive">
              <AlertCircle className="h-4 w-4" /> Preview not available
            </div>
          )}
          {data?.kind === "image" && data.content_b64 && (
            <img
              src={`data:${data.mime};base64,${data.content_b64}`}
              alt={data.name}
              className="mx-auto max-w-full rounded"
            />
          )}
          {data?.kind === "text" && (
            <pre className="whitespace-pre-wrap break-all font-mono text-xs text-foreground/80">
              {data.content}
            </pre>
          )}
          {data?.kind === "binary" && (
            <div className="flex flex-col items-center gap-4 py-10 text-sm text-muted-foreground">
              <FileIcon className="h-12 w-12 opacity-20" />
              <span>
                Binary file · {fmtBytes(data.size_bytes)} · {data.mime}
              </span>
              <Button asChild variant="outline" size="sm">
                <a href={fileDownloadUrl(path)} download>
                  <Download className="mr-1 h-3.5 w-3.5" /> Download
                </a>
              </Button>
            </div>
          )}
          {data?.truncated && (
            <div className="mt-3 flex items-center gap-1.5 text-xs text-amber-600">
              <AlertCircle className="h-3.5 w-3.5" /> Preview was truncated
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Main page ──────────────────────────────────────────────────────────

export function FilesPage() {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();

  // Start at the tenant ROOT (not the empty files/ cloud zone) so the user
  // immediately sees ALL the data Corvin keeps for them — sessions/, browser/,
  // compute/, global/, files/ — for data transparency (GDPR Art. 15). Secret
  // material (keys/, secrets.enc, BYOK PEMs, audit chain) stays hidden by the
  // backend access policy.
  const [selectedDir, setSelectedDir] = React.useState("");
  const [previewPath, setPreviewPath] = React.useState<string | null>(null);
  const [dragging, setDragging] = React.useState(false);
  const [showMkdir, setShowMkdir] = React.useState(false);
  const [newDirName, setNewDirName] = React.useState("");
  const [uploadError, setUploadError] = React.useState<string | null>(null);
  const fileInputRef = React.useRef<HTMLInputElement>(null);

  // Root tree for the sidebar (depth=2, stale for 30 s)
  const rootTree = useQuery({
    queryKey: ["files-tree", "root"],
    queryFn: ({ signal }) => listFilesTree("", 2, signal),
    staleTime: 30_000,
  });

  // Current directory listing
  const dirTree = useQuery({
    queryKey: ["files-tree-dir", selectedDir],
    queryFn: ({ signal }) => listFilesTree(selectedDir, 1, signal),
    staleTime: 10_000,
  });

  const currentEntries = dirTree.data?.children ?? [];
  const currentAccess = dirTree.data?.access ?? "full";

  const invalidateCurrent = () => {
    void qc.invalidateQueries({ queryKey: ["files-tree-dir", selectedDir] });
    void qc.invalidateQueries({ queryKey: ["files-tree", "root"] });
    void qc.invalidateQueries({ queryKey: ["files-tree-node", selectedDir] });
  };

  // Upload
  const uploadMut = useMutation({
    mutationFn: async (files: FileList) => {
      for (const f of Array.from(files)) {
        await uploadFile(selectedDir, f, csrf);
      }
    },
    onSuccess: () => {
      setUploadError(null);
      invalidateCurrent();
    },
    onError: (e) => setUploadError(e instanceof Error ? e.message : "Upload failed"),
  });

  // Delete
  const deleteMut = useMutation({
    mutationFn: (path: string) => deleteFile(path, csrf),
    onSuccess: invalidateCurrent,
  });

  // Mkdir
  const mkdirMut = useMutation({
    mutationFn: (name: string) => createDir(`${selectedDir}/${name}`.replace(/\/+/g, "/"), csrf),
    onSuccess: () => {
      setNewDirName("");
      setShowMkdir(false);
      invalidateCurrent();
    },
  });

  // Breadcrumb
  const crumbParts = selectedDir ? selectedDir.split("/").filter(Boolean) : [];
  const breadcrumbs = [
    { label: "~", path: "" },
    ...crumbParts.map((p, i) => ({ label: p, path: crumbParts.slice(0, i + 1).join("/") })),
  ];

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragging(false);
    if (currentAccess !== "full") return;
    if (e.dataTransfer.files.length) uploadMut.mutate(e.dataTransfer.files);
  }

  const topDirs = (rootTree.data?.children ?? []).filter(
    (e) => e.is_dir && e.access !== "none",
  );

  return (
    <div className="flex h-[calc(100vh-8rem)] gap-0 overflow-hidden rounded-xl border bg-card">
      {/* ── Sidebar ─────────────────────────────────────────── */}
      <aside className="flex w-52 shrink-0 flex-col border-r">
        <div className="flex-1 overflow-y-auto">
          <div className="px-3 py-3 text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">
            Directories
          </div>
          {rootTree.isLoading && (
            <div className="px-4 py-2 text-xs text-muted-foreground">Loading…</div>
          )}
          {topDirs.map((entry) => (
            <TreeNode
              key={entry.rel_path}
              entry={entry}
              indent={0}
              selected={selectedDir}
              onSelect={setSelectedDir}
            />
          ))}
        </div>
        {/* Storage quota bar */}
        {rootTree.data?.quota && (() => {
          const q = rootTree.data.quota;
          const usedMB = (q.used_bytes / 1024 / 1024).toFixed(1);
          const limitGB = (q.limit_bytes / 1024 / 1024 / 1024).toFixed(0);
          const pct = Math.min(q.used_pct, 100);
          const color = pct >= 90 ? "bg-red-500" : pct >= 70 ? "bg-amber-500" : "bg-primary";
          return (
            <div className="border-t px-3 py-3">
              <div className="mb-1.5 flex items-center justify-between text-[11px] text-muted-foreground">
                <span>Storage</span>
                <span>{usedMB} MB / {limitGB} GB</span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                <div
                  className={`h-full rounded-full transition-all ${color}`}
                  style={{ width: `${pct}%` }}
                />
              </div>
              {pct >= 90 && (
                <p className="mt-1 text-[10px] text-red-400">Storage almost full</p>
              )}
            </div>
          );
        })()}
      </aside>

      {/* ── Content panel ───────────────────────────────────── */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {/* Toolbar */}
        <div className="flex shrink-0 items-center gap-2 border-b px-4 py-2.5">
          {/* Breadcrumb */}
          <div className="flex min-w-0 flex-1 items-center gap-0.5 text-sm">
            {breadcrumbs.map((crumb, i) => (
              <React.Fragment key={crumb.path}>
                {i > 0 && <span className="text-muted-foreground/40">/</span>}
                <button
                  className={cn(
                    "rounded px-1.5 py-0.5 transition-colors",
                    crumb.path === selectedDir
                      ? "font-medium text-foreground"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground",
                  )}
                  onClick={() => setSelectedDir(crumb.path)}
                >
                  {crumb.label}
                </button>
              </React.Fragment>
            ))}
          </div>

          {/* Action buttons */}
          {currentAccess === "full" ? (
            <div className="flex shrink-0 items-center gap-2">
              <Button variant="outline" size="sm" onClick={() => setShowMkdir((v) => !v)}>
                <FolderPlus className="mr-1 h-3.5 w-3.5" /> New folder
              </Button>
              <Button
                size="sm"
                disabled={uploadMut.isPending}
                onClick={() => fileInputRef.current?.click()}
              >
                <Upload className="mr-1 h-3.5 w-3.5" />
                {uploadMut.isPending ? "…" : "Upload"}
              </Button>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={(e) => e.target.files && uploadMut.mutate(e.target.files)}
              />
            </div>
          ) : currentAccess === "read" ? (
            <Badge variant="secondary" className="shrink-0">
              <Lock className="mr-1 h-3 w-3" /> Read-only
            </Badge>
          ) : null}
        </div>

        {/* Inline mkdir form */}
        {showMkdir && (
          <div className="flex shrink-0 items-center gap-2 border-b bg-muted/30 px-4 py-2">
            <input
              autoFocus
              className="flex-1 rounded border border-border bg-background px-2 py-1 text-sm outline-none focus:ring-1 focus:ring-accent"
              placeholder="Folder name…"
              value={newDirName}
              onChange={(e) => setNewDirName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && newDirName.trim()) mkdirMut.mutate(newDirName.trim());
                if (e.key === "Escape") {
                  setShowMkdir(false);
                  setNewDirName("");
                }
              }}
            />
            <Button
              size="sm"
              disabled={!newDirName.trim() || mkdirMut.isPending}
              onClick={() => newDirName.trim() && mkdirMut.mutate(newDirName.trim())}
            >
              Create
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setShowMkdir(false);
                setNewDirName("");
              }}
            >
              Cancel
            </Button>
          </div>
        )}

        {/* Root data-transparency note (GDPR Art. 15) */}
        {selectedDir === "" && (
          <div className="shrink-0 border-b bg-muted/20 px-4 py-2 text-xs text-muted-foreground">
            This is your tenant folder — every piece of data Corvin stores for
            you. Keys, secrets and the audit log are hidden for security.
          </div>
        )}

        {/* Upload error banner */}
        {uploadError && (
          <div className="flex shrink-0 items-center gap-2 border-b bg-destructive/5 px-4 py-2 text-sm text-destructive">
            <AlertCircle className="h-4 w-4 shrink-0" />
            <span className="flex-1">{uploadError}</span>
            <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => setUploadError(null)}>
              <X className="h-3.5 w-3.5" />
            </Button>
          </div>
        )}

        {/* Drop zone + file table */}
        <div
          className={cn(
            "relative flex-1 overflow-y-auto transition-colors",
            dragging && currentAccess === "full" && "bg-accent/8 ring-2 ring-inset ring-accent",
          )}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={handleDrop}
        >
          {dirTree.isLoading && (
            <div className="py-10 text-center text-sm text-muted-foreground">Loading…</div>
          )}

          {!dirTree.isLoading && currentEntries.length === 0 && (
            <div className="flex flex-col items-center justify-center gap-3 py-20 text-muted-foreground">
              {currentAccess === "full" ? (
                <>
                  <Upload className="h-12 w-12 opacity-20" />
                  <span className="text-sm">Folder is empty — drop files here or upload</span>
                </>
              ) : (
                <>
                  <Folder className="h-12 w-12 opacity-20" />
                  <span className="text-sm">Empty</span>
                </>
              )}
            </div>
          )}

          {currentEntries.length > 0 && (
            <table className="w-full text-sm">
              <thead className="sticky top-0 z-10 border-b bg-card">
                <tr className="text-xs text-muted-foreground">
                  <th className="px-4 py-2 text-left font-medium">Name</th>
                  <th className="w-24 px-4 py-2 text-right font-medium">Size</th>
                  <th className="w-28 px-4 py-2 text-right font-medium">Modified</th>
                  <th className="w-16 px-2 py-2" />
                </tr>
              </thead>
              <tbody>
                {currentEntries.map((entry) => (
                  <tr
                    key={entry.rel_path}
                    className={cn(
                      "group border-b border-border/40 transition-colors hover:bg-muted/40",
                      (entry.is_dir || !entry.is_dir) && "cursor-pointer",
                    )}
                    onClick={() => {
                      if (entry.is_dir) setSelectedDir(entry.rel_path);
                      else setPreviewPath(entry.rel_path);
                    }}
                  >
                    <td className="px-4 py-2.5">
                      <div className="flex min-w-0 items-center gap-2">
                        <EntryIcon entry={entry} />
                        <span
                          className={cn(
                            "truncate",
                            entry.access === "read" && "text-muted-foreground",
                          )}
                        >
                          {entry.name}
                        </span>
                        {entry.access === "read" && (
                          <Badge
                            variant="outline"
                            className="ml-1 shrink-0 px-1 py-0 text-[10px]"
                          >
                            ro
                          </Badge>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-2.5 text-right text-muted-foreground">
                      {entry.is_dir ? "—" : fmtBytes(entry.size_bytes)}
                    </td>
                    <td className="px-4 py-2.5 text-right text-muted-foreground">
                      {fmtDate(entry.mtime)}
                    </td>
                    <td className="px-2 py-2.5">
                      <div className="flex items-center justify-end gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                        {!entry.is_dir && (
                          <Button
                            asChild
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7"
                            title="Download"
                            onClick={(e) => e.stopPropagation()}
                          >
                            <a
                              href={fileDownloadUrl(entry.rel_path)}
                              download
                              onClick={(e) => e.stopPropagation()}
                            >
                              <Download className="h-3.5 w-3.5" />
                            </a>
                          </Button>
                        )}
                        {entry.access === "full" && (
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7 text-destructive hover:text-destructive"
                            title="Delete"
                            disabled={deleteMut.isPending}
                            onClick={(e) => {
                              e.stopPropagation();
                              if (
                                window.confirm(`"${entry.name}" really delete?`)
                              ) {
                                deleteMut.mutate(entry.rel_path);
                              }
                            }}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {/* Drop overlay */}
          {dragging && currentAccess === "full" && (
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
              <div className="rounded-xl bg-accent/20 px-10 py-5 text-lg font-semibold text-accent">
                Drop files here
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Preview overlay */}
      {previewPath && (
        <PreviewOverlay path={previewPath} onClose={() => setPreviewPath(null)} />
      )}
    </div>
  );
}

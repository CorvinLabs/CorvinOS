/**
 * Extensions page — ADR-0142 M5 (Layer Extension API).
 *
 * Two sections:
 *   1. CORE LAYERS — the immutable security baseline (corvin.*), protected by
 *      ADR-0141. Listed with a lock icon and NO action buttons.
 *   2. YOUR EXTENSIONS — user-managed layers (ext.<vendor>.*). Enable / Disable
 *      / Remove / Details actions plus an "Add Extension" install action.
 *
 * The deny-wins model means extensions can only make CorvinOS stricter, never
 * weaker; the UI reflects that core layers cannot be touched from here.
 */
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Blocks, CheckCircle2, Info, Loader2, Lock, PauseCircle, PlayCircle,
  Plus, Puzzle, RefreshCw, ShieldCheck, Trash2, XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useAuth } from "@/lib/auth";
import {
  getExtension,
  installExtension,
  listExtensions,
  removeExtension,
  setExtensionEnabled,
  validateExtensionManifest,
  type CoreLayer,
  type ExtensionList,
  type ExtensionManifest,
  type ExtensionValidateResult,
} from "@/lib/api";

// ── Core layers section ─────────────────────────────────────────────────────

function CoreLayerRow({ layer }: { layer: CoreLayer }) {
  return (
    <div
      data-testid={`core-layer-${layer.name}`}
      className="flex items-center gap-3 rounded-lg border bg-muted/30 px-4 py-2.5"
    >
      <Lock className="h-4 w-4 shrink-0 text-muted-foreground" />
      <CheckCircle2 className="h-4 w-4 shrink-0 text-green-500" />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-mono text-sm font-medium">{layer.name}</span>
          <Badge variant="outline" className="text-xs">v{layer.version}</Badge>
        </div>
        <p className="text-xs text-muted-foreground mt-0.5 truncate">{layer.description}</p>
      </div>
      <Badge variant="secondary" className="shrink-0 text-xs">immutable</Badge>
    </div>
  );
}

// ── Extension card ──────────────────────────────────────────────────────────

function ExtensionCard({
  ext,
  csrf,
  onDetails,
}: {
  ext: ExtensionManifest;
  csrf: string;
  onDetails: (name: string) => void;
}) {
  const qc = useQueryClient();
  const [error, setError] = React.useState<string | null>(null);

  const toggleMut = useMutation({
    mutationFn: (enable: boolean) => setExtensionEnabled(ext.name, enable, csrf),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["extensions"] }),
    onError: (e: Error) => setError(e.message),
  });
  const removeMut = useMutation({
    mutationFn: () => removeExtension(ext.name, csrf),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["extensions"] }),
    onError: (e: Error) => setError(e.message),
  });

  const events = [...new Set(ext.hooks.map((h) => h.event))];

  return (
    <Card data-testid={`ext-card-${ext.name}`}>
      <CardContent className="px-4 py-3">
        <div className="flex items-start gap-3">
          {ext.enabled
            ? <CheckCircle2 className="h-5 w-5 shrink-0 text-green-500 mt-0.5" />
            : <PauseCircle className="h-5 w-5 shrink-0 text-muted-foreground mt-0.5" />}
          <div className="flex-1 min-w-0 space-y-1">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-mono text-sm font-medium">{ext.name}</span>
              <Badge variant="outline" className="text-xs">v{ext.version}</Badge>
              <Badge variant="secondary" className="text-xs">scope: {ext.scope}</Badge>
              {!ext.enabled && <Badge variant="outline" className="text-xs">disabled</Badge>}
            </div>
            {ext.description && (
              <p className="text-xs text-muted-foreground truncate">{ext.description}</p>
            )}
            <p className="text-xs text-muted-foreground">
              {events.length > 0 ? events.join(" · ") : "no hooks"}
            </p>
            {error && (
              <p className="text-xs text-destructive flex items-center gap-1">
                <XCircle className="h-3.5 w-3.5" /> {error}
              </p>
            )}
          </div>
          <div className="flex items-center gap-1 shrink-0">
            {ext.enabled ? (
              <Button
                variant="outline"
                size="sm"
                data-testid={`ext-disable-${ext.name}`}
                onClick={() => { setError(null); toggleMut.mutate(false); }}
                disabled={toggleMut.isPending}
              >
                {toggleMut.isPending
                  ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  : <PauseCircle className="h-3.5 w-3.5" />}
                <span className="ml-1">Disable</span>
              </Button>
            ) : (
              <Button
                variant="outline"
                size="sm"
                data-testid={`ext-enable-${ext.name}`}
                onClick={() => { setError(null); toggleMut.mutate(true); }}
                disabled={toggleMut.isPending}
              >
                {toggleMut.isPending
                  ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  : <PlayCircle className="h-3.5 w-3.5" />}
                <span className="ml-1">Enable</span>
              </Button>
            )}
            <Button
              variant="ghost"
              size="sm"
              data-testid={`ext-details-${ext.name}`}
              onClick={() => onDetails(ext.name)}
            >
              Details
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 text-destructive hover:text-destructive"
              data-testid={`ext-remove-${ext.name}`}
              onClick={() => {
                if (confirm(`Remove extension "${ext.name}"?`)) {
                  setError(null);
                  removeMut.mutate();
                }
              }}
              disabled={removeMut.isPending}
            >
              {removeMut.isPending
                ? <Loader2 className="h-4 w-4 animate-spin" />
                : <Trash2 className="h-4 w-4" />}
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ── Add Extension dialog ────────────────────────────────────────────────────

function AddExtensionDialog({
  open,
  onClose,
  csrf,
}: {
  open: boolean;
  onClose: () => void;
  csrf: string;
}) {
  const qc = useQueryClient();
  const [source, setSource] = React.useState("");
  const [enable, setEnable] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  function reset() {
    setSource("");
    setEnable(false);
    setError(null);
  }

  const installMut = useMutation({
    mutationFn: () => installExtension(source.trim(), csrf, { enable }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["extensions"] });
      reset();
      onClose();
    },
    onError: (e: Error) => setError(e.message),
  });

  function handleClose() {
    reset();
    onClose();
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && handleClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Add Extension</DialogTitle>
          <DialogDescription>
            Install a layer extension from an unpacked directory on the server.
            Extensions are installed disabled by default — enable them after review.
          </DialogDescription>
        </DialogHeader>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!source.trim()) return;
            setError(null);
            installMut.mutate();
          }}
          className="space-y-4"
        >
          <div>
            <label className="text-xs font-medium mb-1 block">Source directory path *</label>
            <Input
              data-testid="ext-source-input"
              placeholder="/path/to/myext/  (contains layer.yaml)"
              value={source}
              onChange={(e) => setSource(e.target.value)}
              required
            />
            <p className="text-xs text-muted-foreground mt-0.5">
              The directory must contain a <code>layer.yaml</code>. Tarball / GitHub-URL
              install is planned but not yet available.
            </p>
          </div>

          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              data-testid="ext-enable-checkbox"
              checked={enable}
              onChange={(e) => setEnable(e.target.checked)}
            />
            Enable immediately after install
          </label>

          <div className="rounded border bg-muted/20 px-3 py-2 text-xs text-muted-foreground flex items-start gap-1.5">
            <ShieldCheck className="h-3.5 w-3.5 shrink-0 mt-0.5 text-green-500" />
            <span>
              Extensions follow the deny-wins model: they can add restrictions but
              never override a core deny. The <code>corvin.*</code> namespace is reserved.
            </span>
          </div>

          {error && (
            <p className="text-sm text-destructive rounded border border-destructive/30 bg-destructive/10 px-3 py-2">
              {error}
            </p>
          )}

          <div className="flex justify-end gap-2 pt-1">
            <Button type="button" variant="outline" onClick={handleClose}>Cancel</Button>
            <Button type="submit" data-testid="ext-install-btn" disabled={installMut.isPending || !source.trim()}>
              {installMut.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Install
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ── Detail dialog ───────────────────────────────────────────────────────────

function DetailDialog({
  name,
  open,
  onClose,
}: {
  name: string | null;
  open: boolean;
  onClose: () => void;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["extension", name],
    queryFn: ({ signal }) => getExtension(name!, signal),
    enabled: open && !!name,
  });

  if (!name) return null;

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-mono">{name}</DialogTitle>
          {data && (
            <DialogDescription>
              v{data.version}{data.description ? ` — ${data.description}` : ""}
            </DialogDescription>
          )}
        </DialogHeader>

        {isLoading && (
          <div className="space-y-2">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-3/4" />
          </div>
        )}

        {data && (
          <div className="space-y-4 text-sm">
            <div className="grid grid-cols-2 gap-2 rounded-lg border bg-muted/40 px-3 py-2 text-xs">
              <div className="flex justify-between col-span-2">
                <span className="text-muted-foreground">Scope</span>
                <span>{data.scope}</span>
              </div>
              <div className="flex justify-between col-span-2">
                <span className="text-muted-foreground">Status</span>
                <span>{data.enabled ? "enabled" : "disabled"}</span>
              </div>
              {data.author && (
                <div className="flex justify-between col-span-2">
                  <span className="text-muted-foreground">Author</span>
                  <span className="truncate">{data.author}</span>
                </div>
              )}
              {data.license && (
                <div className="flex justify-between col-span-2">
                  <span className="text-muted-foreground">License</span>
                  <span>{data.license}</span>
                </div>
              )}
            </div>

            {data.hooks.length > 0 && (
              <div>
                <p className="text-xs font-medium text-muted-foreground mb-1">Hooks</p>
                <div className="space-y-1">
                  {data.hooks.map((h, i) => (
                    <div key={i} className="flex items-center justify-between rounded border px-2 py-1 text-xs">
                      <span className="font-mono">{h.event}</span>
                      <span className="text-muted-foreground">priority {h.priority}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {data.requires.length > 0 && (
              <div>
                <p className="text-xs font-medium text-muted-foreground mb-1">Requires</p>
                <div className="flex flex-wrap gap-1.5">
                  {data.requires.map((r) => (
                    <code key={r} className="text-xs rounded border bg-muted px-2 py-0.5">{r}</code>
                  ))}
                </div>
              </div>
            )}

            {data.provides.length > 0 && (
              <div>
                <p className="text-xs font-medium text-muted-foreground mb-1">Provides</p>
                <div className="flex flex-wrap gap-1.5">
                  {data.provides.map((p) => (
                    <code key={p.name} className="text-xs rounded border bg-muted px-2 py-0.5">
                      {p.name}{p.version ? ` ${p.version}` : ""}
                    </code>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

// ── Main page ───────────────────────────────────────────────────────────────

export function ExtensionsPage() {
  const { session } = useAuth();
  const csrf = (session as { csrf_token?: string } | null)?.csrf_token ?? "";
  const qc = useQueryClient();

  const [showAdd, setShowAdd] = React.useState(false);
  const [detailName, setDetailName] = React.useState<string | null>(null);

  const { data, isLoading } = useQuery<ExtensionList>({
    queryKey: ["extensions"],
    queryFn: ({ signal }) => listExtensions(signal),
  });

  const core = data?.core ?? [];
  const exts = data?.extensions ?? [];

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Puzzle className="h-6 w-6" />
            Extensions
          </h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Add custom layers — hooks, filters, routing logic — without touching the
            core. Extensions can only make CorvinOS stricter, never weaker.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="icon" onClick={() => qc.invalidateQueries({ queryKey: ["extensions"] })}>
            <RefreshCw className="h-4 w-4" />
          </Button>
          <Button data-testid="add-extension-btn" onClick={() => setShowAdd(true)}>
            <Plus className="mr-2 h-4 w-4" />
            Add Extension
          </Button>
        </div>
      </div>

      {isLoading && (
        <div className="space-y-2">
          {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-14 w-full" />)}
        </div>
      )}

      {/* Core layers */}
      {!isLoading && (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <Lock className="h-4 w-4 text-muted-foreground" />
            <h2 className="text-lg font-semibold">
              Core Layers <span className="text-muted-foreground font-normal">({core.length})</span>
            </h2>
            <Badge variant="secondary" className="text-xs">immutable — core-protected</Badge>
          </div>
          <div className="space-y-2">
            {core.map((layer) => (
              <CoreLayerRow key={layer.name} layer={layer} />
            ))}
          </div>
        </div>
      )}

      {/* Extensions */}
      {!isLoading && (
        <div className="space-y-3 pt-2">
          <div className="flex items-center gap-2">
            <Blocks className="h-4 w-4 text-muted-foreground" />
            <h2 className="text-lg font-semibold">
              Your Extensions <span className="text-muted-foreground font-normal">({exts.length})</span>
            </h2>
          </div>

          {exts.length === 0 ? (
            <Card>
              <CardContent className="flex flex-col items-center justify-center py-10 text-center">
                <Puzzle className="h-7 w-7 text-muted-foreground mb-3" />
                <p className="font-medium text-sm">No extensions installed yet.</p>
                <p className="text-xs text-muted-foreground mt-1 max-w-sm">
                  Install a layer extension from a directory containing a layer.yaml manifest.
                </p>
                <Button className="mt-4" onClick={() => setShowAdd(true)}>
                  <Plus className="mr-2 h-4 w-4" />
                  Add your first extension
                </Button>
              </CardContent>
            </Card>
          ) : (
            <div className="space-y-2">
              {exts.map((ext) => (
                <ExtensionCard key={ext.name} ext={ext} csrf={csrf} onDetails={setDetailName} />
              ))}
            </div>
          )}

          <div className="rounded border bg-muted/20 px-3 py-2 text-xs text-muted-foreground flex items-start gap-1.5">
            <Info className="h-3.5 w-3.5 shrink-0 mt-0.5" />
            <span>
              Manage extensions from the CLI too: <code>corvin-layer list</code>,{" "}
              <code>corvin-layer add &lt;dir&gt;</code>, <code>corvin-layer enable &lt;name&gt;</code>.
            </span>
          </div>
        </div>
      )}

      <AddExtensionDialog open={showAdd} onClose={() => setShowAdd(false)} csrf={csrf} />
      <DetailDialog name={detailName} open={!!detailName} onClose={() => setDetailName(null)} />
    </div>
  );
}

// Keep the validate client referenced so tree-shaking does not warn on an
// unused import while the inline validator UI is a future enhancement.
export type { ExtensionValidateResult };
void validateExtensionManifest;

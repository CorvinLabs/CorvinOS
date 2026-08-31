import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUpFromLine,
  ChevronRight,
  Hammer,
  Loader2,
  PlayCircle,
  Plus,
  Search,
  Shield,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { ReauthDialog } from "@/components/reauth-dialog";
import { EmptyState } from "@/components/EmptyState";
import {
  createManualTool,
  getForgeTool,
  getLicenseInfo,
  listForgeTools,
  listManualTools,
  previewManualTool,
  promoteForgeTool,
  type ForgeToolSummary,
  type LicenseInfo,
  type ManualTool,
  type PromoteTarget,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatDate } from "@/lib/utils";

const PROMOTE_FLOW: Record<string, PromoteTarget | null> = {
  // scope_source → next target (or null if already at user)
  // Order: session → session-default (tenant-root) → user
  session: "project",
  "session-default": "user",
  user: null,
};

function nextPromoteTarget(scopeSource: string): PromoteTarget | null {
  if (scopeSource.startsWith("session:")) return "project";
  return PROMOTE_FLOW[scopeSource] ?? null;
}

// ── Manual-tool names: lowercase alphanumeric, dots, and underscores ──────────
const TOOL_NAME_RE = /^[a-z0-9][a-z0-9._]*$/;

function CreateToolDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const { session } = useAuth();
  const qc = useQueryClient();

  const [toolName, setToolName] = React.useState("");
  const [toolDesc, setToolDesc] = React.useState("");
  const [toolImpl, setToolImpl] = React.useState("");
  const [previewInputs, setPreviewInputs] = React.useState("{}");
  const [previewResult, setPreviewResult] = React.useState<{
    exit_code: number;
    stdout: string;
    stderr: string;
  } | null>(null);
  const [nameError, setNameError] = React.useState<string | null>(null);
  const [inputsError, setInputsError] = React.useState<string | null>(null);

  // Reset state when dialog closes
  React.useEffect(() => {
    if (!open) {
      setToolName("");
      setToolDesc("");
      setToolImpl("");
      setPreviewInputs("{}");
      setPreviewResult(null);
      setNameError(null);
      setInputsError(null);
    }
  }, [open]);

  const createMutation = useMutation({
    mutationFn: () =>
      createManualTool(toolName, toolDesc, toolImpl, session!.csrf_token),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["tools"] });
      await qc.invalidateQueries({ queryKey: ["forge"] });
      onOpenChange(false);
    },
  });

  const previewMutation = useMutation({
    mutationFn: () => {
      let parsed: Record<string, unknown>;
      try {
        parsed = JSON.parse(previewInputs) as Record<string, unknown>;
      } catch {
        throw new Error("Sample Inputs is not valid JSON");
      }
      return previewManualTool(toolName, parsed, session!.csrf_token);
    },
    onSuccess: (data) => {
      setPreviewResult({ exit_code: data.exit_code, stdout: data.stdout, stderr: data.stderr });
      setInputsError(null);
    },
    onError: (e: Error) => {
      if (e.message.includes("JSON")) setInputsError(e.message);
    },
  });

  function validateName(v: string): string | null {
    if (!v) return "Tool name is required";
    if (!TOOL_NAME_RE.test(v)) return "Lowercase alphanumeric with . or _ only";
    return null;
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const err = validateName(toolName);
    if (err) { setNameError(err); return; }
    if (!toolDesc.trim()) return;
    createMutation.mutate();
  }

  const canSave = toolName.trim() && toolDesc.trim() && toolImpl.trim() && !nameError;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Hammer className="h-5 w-5 text-accent" />
            Create Manual Tool
          </DialogTitle>
          <DialogDescription>
            Define a sandboxed Python tool that your AI can call.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit}>
          <Tabs defaultValue="edit" className="mt-2">
            <TabsList className="mb-4">
              <TabsTrigger value="edit">Edit</TabsTrigger>
              <TabsTrigger value="preview">Preview</TabsTrigger>
            </TabsList>

            {/* ── Edit Tab ─────────────────────────────────────────────────── */}
            <TabsContent value="edit" className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="tool-name-input">
                  Tool Name <span className="text-destructive">*</span>
                </Label>
                <Input
                  id="tool-name-input"
                  data-testid="tool-name-input"
                  value={toolName}
                  onChange={(e) => {
                    setToolName(e.target.value);
                    setNameError(validateName(e.target.value));
                  }}
                  placeholder="my_tool.v1"
                  required
                  aria-describedby={nameError ? "tool-name-error" : undefined}
                />
                {nameError && (
                  <p id="tool-name-error" className="text-xs text-destructive">{nameError}</p>
                )}
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="tool-description-input">
                  Description <span className="text-destructive">*</span>
                </Label>
                <Input
                  id="tool-description-input"
                  data-testid="tool-description-input"
                  value={toolDesc}
                  onChange={(e) => setToolDesc(e.target.value)}
                  placeholder="What this tool does, in one sentence"
                  required
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="tool-impl-input">Implementation</Label>
                <Textarea
                  id="tool-impl-input"
                  data-testid="tool-impl-input"
                  value={toolImpl}
                  onChange={(e) => setToolImpl(e.target.value)}
                  className="min-h-[300px] font-mono text-sm"
                  placeholder={"# Python code\n# inputs dict is available as `inputs`\nresult = inputs.get('text', '').upper()\nprint(result)"}
                  spellCheck={false}
                />
              </div>
            </TabsContent>

            {/* ── Preview Tab ───────────────────────────────────────────────── */}
            <TabsContent value="preview" className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="tool-preview-inputs">Sample Inputs (JSON)</Label>
                <Textarea
                  id="tool-preview-inputs"
                  data-testid="tool-preview-inputs"
                  value={previewInputs}
                  onChange={(e) => {
                    setPreviewInputs(e.target.value);
                    setInputsError(null);
                  }}
                  className="min-h-[120px] font-mono text-sm"
                  placeholder='{"text": "hello"}'
                  spellCheck={false}
                />
                {inputsError && (
                  <p className="text-xs text-destructive">{inputsError}</p>
                )}
              </div>

              <Button
                type="button"
                data-testid="run-preview-btn"
                variant="outline"
                size="sm"
                disabled={!toolName.trim() || !toolImpl.trim() || previewMutation.isPending}
                onClick={() => previewMutation.mutate()}
              >
                {previewMutation.isPending ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <PlayCircle className="mr-1.5 h-3.5 w-3.5" />
                )}
                Run Preview
              </Button>

              {previewMutation.isError && !inputsError && (
                <p className="text-xs text-destructive">
                  {(previewMutation.error as Error).message}
                </p>
              )}

              {previewResult && (
                <div className="space-y-2">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-medium text-muted-foreground">Exit code</span>
                    <Badge
                      variant={previewResult.exit_code === 0 ? "ok" : "danger"}
                      className="text-[10px]"
                    >
                      {previewResult.exit_code}
                    </Badge>
                  </div>
                  {previewResult.stdout && (
                    <div className="space-y-1">
                      <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                        stdout
                      </p>
                      <pre className="max-h-40 overflow-y-auto rounded-md border border-border/60 bg-muted/40 p-2.5 font-mono text-[11px] leading-relaxed whitespace-pre-wrap">
                        {previewResult.stdout}
                      </pre>
                    </div>
                  )}
                  {previewResult.stderr && (
                    <div className="space-y-1">
                      <p className="text-[10px] font-medium uppercase tracking-wide text-destructive/70">
                        stderr
                      </p>
                      <pre className="max-h-40 overflow-y-auto rounded-md border border-destructive/30 bg-destructive/5 p-2.5 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-destructive">
                        {previewResult.stderr}
                      </pre>
                    </div>
                  )}
                </div>
              )}
            </TabsContent>
          </Tabs>

          {/* ── Footer actions ──────────────────────────────────────────────── */}
          <div className="mt-6 flex justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              data-testid="create-tool-submit"
              disabled={!canSave || createMutation.isPending}
            >
              {createMutation.isPending && (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              )}
              Save Tool
            </Button>
          </div>

          {createMutation.isError && (
            <p className="mt-2 text-right text-xs text-destructive">
              {(createMutation.error as Error).message}
            </p>
          )}
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function ForgePage() {
  const { session } = useAuth();
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["forge", "tools"],
    queryFn: ({ signal }) => listForgeTools(signal),
  });

  const manualList = useQuery({
    queryKey: ["tools", "manual"],
    queryFn: ({ signal }) => listManualTools(signal),
  });

  const licQ = useQuery<LicenseInfo>({
    queryKey: ["license", "info"],
    queryFn: ({ signal }) => getLicenseInfo(signal),
    staleTime: 60_000,
  });

  const [search, setSearch] = React.useState("");
  const [selected, setSelected] = React.useState<string | null>(null);
  const [reauthOpen, setReauthOpen] = React.useState(false);
  const [pending, setPending] = React.useState<{ name: string; to: PromoteTarget } | null>(null);
  const [toast, setToast] = React.useState<{ kind: "ok" | "err"; msg: string } | null>(null);
  const [createOpen, setCreateOpen] = React.useState(false);

  // Build a set of manual tool names for badge lookup
  const manualToolNames = React.useMemo(
    () => new Set((manualList.data?.tools ?? []).map((t: ManualTool) => t.name)),
    [manualList.data],
  );

  const filtered = React.useMemo(() => {
    const all = list.data?.tools ?? [];
    if (!search) return all;
    const needle = search.toLowerCase();
    return all.filter(
      (t) =>
        t.name.toLowerCase().includes(needle) ||
        t.description.toLowerCase().includes(needle),
    );
  }, [list.data, search]);

  const promoteMutation = useMutation({
    mutationFn: async ({ name, to }: { name: string; to: PromoteTarget }) =>
      promoteForgeTool(name, to, session!.csrf_token),
    onSuccess: async (_data, vars) => {
      setToast({ kind: "ok", msg: `Promoted ${vars.name} → ${vars.to}` });
      await qc.invalidateQueries({ queryKey: ["forge"] });
    },
    onError: (e: Error) => {
      setToast({ kind: "err", msg: e.message });
      throw e;
    },
  });

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-serif text-3xl font-light tracking-tight">Tools</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Custom tools your AI can run, sandboxed and scoped by workspace.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Badge variant="outline" className="text-xs">
            {list.data ? `${list.data.count} tool${list.data.count === 1 ? "" : "s"}` : "—"}
          </Badge>
          <Button
            size="sm"
            data-testid="new-tool-btn"
            onClick={() => setCreateOpen(true)}
          >
            <Plus className="mr-1.5 h-4 w-4" />
            New Tool
          </Button>
        </div>
      </div>

      {/* License context strip */}
      {licQ.data && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-card/50 px-3 py-2 text-xs">
          <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            Licence
          </span>
          <Badge variant={licQ.data.loaded ? "ok" : "secondary"} className="text-[10px]">
            {licQ.data.tier}
          </Badge>
          {licQ.data.expires_at && (
            <span className="text-muted-foreground">
              · Expires {new Date(licQ.data.expires_at * 1000).toLocaleDateString()}
            </span>
          )}
          {Object.entries(licQ.data.features)
            .filter(([, v]) => v)
            .map(([k]) => (
              <Badge key={k} variant="outline" className="text-[10px]">{k}</Badge>
            ))}
          {Object.entries(licQ.data.limits)
            .filter(([, v]) => typeof v === "number")
            .map(([k, v]) => (
              <span key={k} className="text-muted-foreground">
                {k}: <span className="font-medium text-foreground">{String(v)}</span>
              </span>
            ))}
        </div>
      )}

      <div className="relative max-w-md">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search by name or description…"
          className="pl-9"
        />
      </div>

      {list.isLoading && (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      )}

      {list.data && filtered.length === 0 && (
        search ? (
          <Card className="border-dashed">
            <CardContent className="py-12 text-center text-sm text-muted-foreground">
              No tools match your search.
            </CardContent>
          </Card>
        ) : (
          <EmptyState
            icon={Hammer}
            title="No tools yet"
            description="Forge turns repeated commands into schema-bound, sandboxed tools. Ask the assistant or use the CLI to create your first tool."
          />
        )
      )}

      <div className="space-y-2">
        {filtered.map((t) => (
          <ToolRow
            key={`${t.name}::${t.scope_source}`}
            tool={t}
            isManual={manualToolNames.has(t.name)}
            onOpen={() => setSelected(t.name)}
            onPromote={(to) => {
              setPending({ name: t.name, to });
              setReauthOpen(true);
            }}
            pendingFor={
              promoteMutation.isPending && pending?.name === t.name ? pending?.to : null
            }
          />
        ))}
      </div>

      {selected && (
        <ToolDetailDialog name={selected} open={!!selected} onClose={() => setSelected(null)} />
      )}

      <CreateToolDialog open={createOpen} onOpenChange={setCreateOpen} />

      {toast && (
        <div
          className={
            toast.kind === "ok"
              ? "fixed bottom-6 right-6 z-50 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-4 py-2 text-sm text-emerald-700 shadow-lg dark:text-emerald-300"
              : "fixed bottom-6 right-6 z-50 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-2 text-sm text-destructive shadow-lg"
          }
          onClick={() => setToast(null)}
        >
          {toast.msg}
        </div>
      )}

      <ReauthDialog
        open={reauthOpen}
        onOpenChange={setReauthOpen}
        title={pending ? `Promote ${pending.name} → ${pending.to}` : "Confirm"}
        description="Forge promotion widens a tool's reach. Confirm to proceed."
        onConfirm={async () => {
          if (pending) {
            await promoteMutation.mutateAsync({ name: pending.name, to: pending.to });
            setPending(null);
          }
        }}
      />
    </div>
  );
}

function ToolRow({
  tool,
  isManual,
  onOpen,
  onPromote,
  pendingFor,
}: {
  tool: ForgeToolSummary;
  isManual: boolean;
  onOpen: () => void;
  onPromote: (to: PromoteTarget) => void;
  pendingFor: PromoteTarget | null;
}) {
  const next = nextPromoteTarget(tool.scope_source);
  return (
    <Card className="transition-all hover:border-accent/40">
      <CardContent className="flex items-center gap-4 py-3">
        <button
          onClick={onOpen}
          className="flex min-w-0 flex-1 items-center gap-3 text-left focus:outline-none focus-visible:rounded-md focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Hammer className="h-5 w-5 shrink-0 text-accent" />
          <div className="min-w-0 flex-1 overflow-hidden">
            <div className="flex min-w-0 items-center gap-2">
              <span className="min-w-0 flex-1 truncate font-medium">{tool.name}</span>
              {isManual && (
                <Badge variant="secondary" className="shrink-0 text-[10px]">
                  manual
                </Badge>
              )}
              <Badge variant="outline" className="shrink-0 font-mono text-[10px]">
                {tool.runtime}
              </Badge>
              <Badge variant="secondary" className="shrink-0 font-mono text-[10px]">
                {tool.scope_source}
              </Badge>
              {tool.promoted && <Badge variant="ok" className="shrink-0">promoted</Badge>}
            </div>
            <p className="line-clamp-1 text-xs text-muted-foreground">
              {tool.description || "—"}
            </p>
            <div className="mt-1 flex items-center gap-3 text-[10px] text-muted-foreground">
              <span>{tool.param_count} param{tool.param_count === 1 ? "" : "s"}</span>
              <span>{tool.call_count} call{tool.call_count === 1 ? "" : "s"}</span>
              <span>{formatDate(tool.created_at)}</span>
              <span className="truncate font-mono">{tool.sha256.slice(0, 8)}</span>
            </div>
          </div>
          <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
        </button>
        {next && (
          <Button
            variant="outline"
            size="sm"
            onClick={(e) => {
              e.stopPropagation();
              onPromote(next);
            }}
            disabled={pendingFor !== null}
          >
            {pendingFor ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <ArrowUpFromLine className="h-3 w-3" />
            )}
            → {next}
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

function ToolDetailDialog({
  name,
  open,
  onClose,
}: {
  name: string;
  open: boolean;
  onClose: () => void;
}) {
  const q = useQuery({
    queryKey: ["forge", "tool", name],
    queryFn: ({ signal }) => getForgeTool(name, signal),
    enabled: open,
  });

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Hammer className="h-5 w-5 text-accent" />
            {name}
          </DialogTitle>
          <DialogDescription>
            Read-only inspection. Edits land via the persona that generated this tool.
          </DialogDescription>
        </DialogHeader>
        {q.isLoading && <Skeleton className="h-64 w-full" />}
        {q.data && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <Badge variant="secondary" className="font-mono">
                scope · {q.data.scope_source}
              </Badge>
              {q.data.registry_path && (
                <span className="truncate font-mono text-[10px] text-muted-foreground">
                  {q.data.registry_path}
                </span>
              )}
            </div>
            <Section title="Entry" icon={<PlayCircle className="h-4 w-4 text-muted-foreground" />}>
              <pre className="max-h-72 overflow-y-auto rounded-md border border-border/60 bg-muted/40 p-3 font-mono text-[11px] leading-relaxed">
                {JSON.stringify(q.data.entry, null, 2)}
              </pre>
            </Section>
            {q.data.impl_preview && (
              <Section title="Implementation" icon={<Shield className="h-4 w-4 text-muted-foreground" />}>
                <pre className="max-h-72 overflow-y-auto rounded-md border border-border/60 bg-muted/40 p-3 font-mono text-[11px] leading-relaxed">
                  {q.data.impl_preview}
                </pre>
              </Section>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function Section({
  title,
  icon,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-2 flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
        {icon}
        {title}
      </div>
      {children}
    </div>
  );
}

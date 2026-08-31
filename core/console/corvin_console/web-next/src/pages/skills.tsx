import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUpFromLine,
  BookOpen,
  ChevronRight,
  Loader2,
  Plus,
  Search,
  ShieldAlert,
  Star,
  Trash2,
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
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ReauthDialog } from "@/components/reauth-dialog";
import {
  createManualSkill,
  deleteManualSkill,
  getLicenseInfo,
  getSkill,
  listSkills,
  promoteSkill,
  type LicenseInfo,
  type PromoteTarget,
  type SkillSummary,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatDate } from "@/lib/utils";
import { SkillCreatorPanel } from "@/components/SkillCreatorPanel";

// SkillForge promotion gates per CLAUDE.md § Layer 7:
//   task → session : ≥ 1 positive grade
//   session → project : ≥ 3 grades, mean ≥ 0.5
//   project → user : force = true required (explicit operator decision)
function nextPromoteTarget(scopeSource: string): { to: PromoteTarget; needsForce: boolean } | null {
  if (scopeSource.startsWith("session:") || scopeSource === "task") {
    return { to: "session", needsForce: false };
  }
  if (scopeSource === "session" || scopeSource === "session-default") {
    return { to: "project", needsForce: false };
  }
  if (scopeSource === "project") {
    return { to: "user", needsForce: true };
  }
  return null;
}

export function SkillsPage() {
  const { session } = useAuth();
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["skills", "list"],
    queryFn: ({ signal }) => listSkills(signal),
  });

  const licQ = useQuery<LicenseInfo>({
    queryKey: ["license", "info"],
    queryFn: ({ signal }) => getLicenseInfo(signal),
    staleTime: 60_000,
  });

  const [search, setSearch] = React.useState("");
  const [selected, setSelected] = React.useState<string | null>(null);
  const [reauthOpen, setReauthOpen] = React.useState(false);
  const [pending, setPending] = React.useState<{ name: string; to: PromoteTarget; force: boolean } | null>(null);
  const [toast, setToast] = React.useState<{ kind: "ok" | "err"; msg: string } | null>(null);

  // New Skill dialog state
  const [isDialogOpen, setIsDialogOpen] = React.useState(false);
  const [newSkillName, setNewSkillName] = React.useState("");
  const [newSkillBody, setNewSkillBody] = React.useState("");
  const [newSkillError, setNewSkillError] = React.useState<string | null>(null);

  const filtered = React.useMemo(() => {
    const all = list.data?.skills ?? [];
    if (!search) return all;
    const needle = search.toLowerCase();
    return all.filter(
      (s) =>
        s.name.toLowerCase().includes(needle) ||
        (s.description ?? "").toLowerCase().includes(needle),
    );
  }, [list.data, search]);

  const promoteMutation = useMutation({
    mutationFn: async ({ name, to, force }: { name: string; to: PromoteTarget; force: boolean }) =>
      promoteSkill(name, to, session!.csrf_token, force),
    onSuccess: async (_data, vars) => {
      setToast({ kind: "ok", msg: `Promoted ${vars.name} → ${vars.to}` });
      await qc.invalidateQueries({ queryKey: ["skills"] });
    },
    onError: (e: Error) => {
      setToast({ kind: "err", msg: e.message });
      throw e;
    },
  });

  const createMutation = useMutation({
    mutationFn: async ({ name, body }: { name: string; body: string }) =>
      createManualSkill(name, body, session!.csrf_token),
    onSuccess: async (_data, vars) => {
      setToast({ kind: "ok", msg: `Created skill "${vars.name}"` });
      await qc.invalidateQueries({ queryKey: ["skills"] });
      setIsDialogOpen(false);
      setNewSkillName("");
      setNewSkillBody("");
      setNewSkillError(null);
    },
    onError: (e: Error) => {
      setNewSkillError(e.message);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (name: string) =>
      deleteManualSkill(name, session!.csrf_token),
    onSuccess: async (_data, name) => {
      setToast({ kind: "ok", msg: `Deleted skill "${name}"` });
      await qc.invalidateQueries({ queryKey: ["skills"] });
    },
    onError: (e: Error) => {
      setToast({ kind: "err", msg: e.message });
    },
  });

  function handleCreateSubmit(e: React.FormEvent) {
    e.preventDefault();
    setNewSkillError(null);
    if (!newSkillName.trim()) {
      setNewSkillError("Skill name is required.");
      return;
    }
    if (!newSkillBody.trim()) {
      setNewSkillError("Skill body is required.");
      return;
    }
    createMutation.mutate({ name: newSkillName.trim(), body: newSkillBody });
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <SkillCreatorPanel />

      <hr className="my-2" />

      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-serif text-3xl font-light tracking-tight">Skills</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Reusable instructions your AI saves and improves over time.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Badge variant="outline" className="text-xs">
            {list.data ? `${list.data.count} skill${list.data.count === 1 ? "" : "s"}` : "—"}
          </Badge>
          <Button
            size="sm"
            onClick={() => {
              setNewSkillName("");
              setNewSkillBody("");
              setNewSkillError(null);
              setIsDialogOpen(true);
            }}
            data-testid="new-skill-btn"
          >
            <Plus className="mr-1 h-4 w-4" />
            New Skill
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
        <Card className="border-dashed">
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            No skills yet. Skills are created automatically during conversations and appear here when one is ready.
          </CardContent>
        </Card>
      )}

      <div className="space-y-2">
        {filtered.map((s) => (
          <SkillRow
            key={`${s.name}::${s.scope_source}`}
            skill={s}
            onOpen={() => setSelected(s.name)}
            onPromote={(to, force) => {
              setPending({ name: s.name, to, force });
              setReauthOpen(true);
            }}
            onDelete={(name) => deleteMutation.mutate(name)}
            pendingFor={
              promoteMutation.isPending && pending?.name === s.name ? pending?.to : null
            }
            deleteLoading={deleteMutation.isPending && deleteMutation.variables === s.name}
          />
        ))}
      </div>

      {selected && (
        <SkillDetailDialog name={selected} open={!!selected} onClose={() => setSelected(null)} />
      )}

      {/* Create Manual Skill dialog */}
      <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>Create Manual Skill</DialogTitle>
            <DialogDescription>
              Write a Markdown skill body. It will be injected as a system-prompt snippet into
              future conversations. Name must be lowercase alphanumeric with <code>_</code> or{" "}
              <code>-</code>.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleCreateSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="skill-name">Skill Name</Label>
              <Input
                id="skill-name"
                data-testid="skill-name-input"
                placeholder="my-skill-name"
                value={newSkillName}
                onChange={(e) => setNewSkillName(e.target.value)}
                autoComplete="off"
                spellCheck={false}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="skill-body">Skill Body</Label>
              <Textarea
                id="skill-body"
                data-testid="skill-body-input"
                className="min-h-[300px] font-mono text-[13px]"
                placeholder={"Write your skill in Markdown...\n\nThis text is injected as a system-prompt snippet."}
                value={newSkillBody}
                onChange={(e) => setNewSkillBody(e.target.value)}
                spellCheck={false}
              />
            </div>
            {newSkillError && (
              <p className="text-sm text-destructive">{newSkillError}</p>
            )}
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setIsDialogOpen(false)}
                disabled={createMutation.isPending}
              >
                Cancel
              </Button>
              <Button
                type="submit"
                data-testid="create-skill-submit"
                disabled={createMutation.isPending}
              >
                {createMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Create Skill
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

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
        title={
          pending
            ? `Promote ${pending.name} → ${pending.to}${pending.force ? " (force)" : ""}`
            : "Confirm"
        }
        description={
          pending?.force
            ? "Project→user is an explicit operator decision. Confirm to proceed."
            : "Promotion is gated by quality score. Confirm to proceed."
        }
        onConfirm={async () => {
          if (pending) {
            await promoteMutation.mutateAsync({ name: pending.name, to: pending.to, force: pending.force });
            setPending(null);
          }
        }}
      />
    </div>
  );
}

function SkillRow({
  skill,
  onOpen,
  onPromote,
  onDelete,
  pendingFor,
  deleteLoading,
}: {
  skill: SkillSummary;
  onOpen: () => void;
  onPromote: (to: PromoteTarget, force: boolean) => void;
  onDelete: (name: string) => void;
  pendingFor: PromoteTarget | null;
  deleteLoading: boolean;
}) {
  const next = nextPromoteTarget(skill.scope_source);
  const isManual = (skill as SkillSummary & { origin?: string }).origin === "manual";
  return (
    <Card className="transition-all hover:border-accent/40">
      <CardContent className="flex items-center gap-4 py-3">
        <button
          onClick={onOpen}
          className="flex min-w-0 flex-1 items-center gap-3 text-left focus:outline-none focus-visible:rounded-md focus-visible:ring-2 focus-visible:ring-ring"
        >
          <BookOpen className="h-5 w-5 shrink-0 text-accent" />
          <div className="min-w-0 flex-1 overflow-hidden">
            <div className="flex min-w-0 items-center gap-2">
              <span className="min-w-0 flex-1 truncate font-medium">{skill.name}</span>
              {isManual && (
                <Badge variant="outline" className="shrink-0 text-[10px]">
                  Manual
                </Badge>
              )}
              {skill.type && (
                <Badge variant="outline" className="shrink-0 font-mono text-[10px]">
                  {skill.type}
                </Badge>
              )}
              <Badge variant="secondary" className="shrink-0 font-mono text-[10px]">
                {skill.scope_source}
              </Badge>
              {skill.mean_score !== null && (
                <Badge variant={skill.mean_score >= 0.5 ? "ok" : "warn"} className="shrink-0">
                  <Star className="mr-1 h-3 w-3" />
                  {skill.mean_score.toFixed(2)}
                </Badge>
              )}
            </div>
            <p className="line-clamp-1 text-xs text-muted-foreground">
              {skill.description || "—"}
            </p>
            <div className="mt-1 flex items-center gap-3 text-[10px] text-muted-foreground">
              <span>{skill.grade_count} grade{skill.grade_count === 1 ? "" : "s"}</span>
              <span>{formatDate(skill.created_at)}</span>
              <span className="truncate font-mono">{skill.sha256.slice(0, 8)}</span>
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
              onPromote(next.to, next.needsForce);
            }}
            disabled={pendingFor !== null}
          >
            {pendingFor ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : next.needsForce ? (
              <ShieldAlert className="h-3 w-3" />
            ) : (
              <ArrowUpFromLine className="h-3 w-3" />
            )}
            → {next.to}
            {next.needsForce && <span className="ml-1 text-[10px]">force</span>}
          </Button>
        )}
        {isManual && (
          <Button
            variant="ghost"
            size="sm"
            className="text-destructive hover:text-destructive"
            onClick={(e) => {
              e.stopPropagation();
              onDelete(skill.name);
            }}
            disabled={deleteLoading}
            data-testid={`delete-skill-${skill.name}`}
          >
            {deleteLoading ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Trash2 className="h-3 w-3" />
            )}
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

function SkillDetailDialog({
  name,
  open,
  onClose,
}: {
  name: string;
  open: boolean;
  onClose: () => void;
}) {
  const q = useQuery({
    queryKey: ["skills", "detail", name],
    queryFn: ({ signal }) => getSkill(name, signal),
    enabled: open,
  });

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <BookOpen className="h-5 w-5 text-accent" />
            {name}
          </DialogTitle>
          <DialogDescription>
            Skill body (SKILL.md) + grade history. Skills are read-only here; edits land via the
            persona that authored them.
          </DialogDescription>
        </DialogHeader>
        {q.isLoading && <Skeleton className="h-64 w-full" />}
        {q.data && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <Badge variant="secondary" className="font-mono">
                scope · {q.data.scope_source}
              </Badge>
              <span className="truncate font-mono text-[10px] text-muted-foreground">
                {q.data.skill_dir}
              </span>
            </div>
            {q.data.body_preview && (
              <pre className="max-h-80 overflow-y-auto rounded-md border border-border/60 bg-muted/40 p-3 font-mono text-[11px] leading-relaxed">
                {q.data.body_preview}
              </pre>
            )}
            <details className="rounded-md border border-border/60 bg-muted/30 px-3 py-2 text-xs">
              <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                Raw meta.json
              </summary>
              <pre className="mt-2 max-h-60 overflow-y-auto font-mono text-[11px]">
                {JSON.stringify(q.data.meta, null, 2)}
              </pre>
            </details>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

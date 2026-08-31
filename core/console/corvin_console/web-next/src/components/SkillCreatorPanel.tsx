/**
 * Skill-Creator UI Panel — generate, inspect, refine and delete skills.
 *
 * Rendered on /console/app/skills (ADR-0405).
 *
 * The panel owns the full lifecycle of a GENERATED skill, because that is
 * where an operator's loop actually runs: describe → watch the phases →
 * read what came out → change it → keep or delete it. Earlier versions
 * stopped after "watch the phases" and left View and Delete as buttons with
 * no endpoint behind them.
 *
 * Reachability is surfaced everywhere a skill is listed: a registered skill
 * with no grade sits below skill_inject's eligibility gate and is never
 * injected into a turn, so "registered" and "usable" are not the same thing
 * and the UI must not imply they are.
 */

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  Brain,
  CheckCircle,
  Copy,
  Cpu,
  Loader2,
  Pencil,
  Search,
  Trash2,
  X,
} from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  deleteGeneratedSkill,
  getGeneratedSkill,
  getSkillRunStatus,
  listGeneratedSkills,
  startSkillGeneration,
  type GeneratedSkillDetail,
  type GeneratedSkillSummary,
  type SkillRunStatus,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";

/**
 * The five phases SkillCreatorOrchestrator.create_skill reports. The backend
 * also serves this order in every status payload; the local copy is only the
 * fallback for the very first render, before a run exists.
 */
const PHASE_LABELS: Record<string, string> = {
  planning: "📋 Planning",
  validation: "🔍 Validation",
  ldd_iteration: "🔁 LDD Iteration",
  review: "🎯 Adversarial Review",
  promotion: "🚀 Promotion",
};

const DEFAULT_PHASES = ["planning", "validation", "ldd_iteration", "review", "promotion"];

const ENGINE_LABELS: Record<string, string> = {
  claude_code: "Claude subscription (Claude Code CLI)",
  api: "Anthropic API key",
  local: "Local templates (no engine)",
};

type Toast = { kind: "ok" | "err"; msg: string };

export const SkillCreatorPanel: React.FC = () => {
  const { session } = useAuth();
  const qc = useQueryClient();

  const [userRequest, setUserRequest] = useState("");
  const [runId, setRunId] = useState<string | null>(null);
  const [refineTarget, setRefineTarget] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<Toast | null>(null);
  const [search, setSearch] = useState("");
  const [viewing, setViewing] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  // ── Data ────────────────────────────────────────────────────────────────
  const skills = useQuery({
    queryKey: ["skill-creator", "skills"],
    queryFn: ({ signal }) => listGeneratedSkills(signal),
  });

  const run = useQuery<SkillRunStatus>({
    queryKey: ["skill-creator", "run", runId],
    queryFn: ({ signal }) => getSkillRunStatus(runId!, signal),
    enabled: !!runId,
    // Poll only while the run is live. A finished run keeps its last payload
    // on screen without hammering the endpoint forever.
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "success" || status === "failed" ? false : 1000;
    },
  });

  const detail = useQuery<GeneratedSkillDetail>({
    queryKey: ["skill-creator", "skill", viewing],
    queryFn: ({ signal }) => getGeneratedSkill(viewing!, signal),
    enabled: !!viewing,
  });

  const isRunning = run.data ? run.data.status === "running" || run.data.status === "pending" : !!runId;

  // Refresh the list once a run finishes successfully — the new or refined
  // skill has to appear without the operator reloading the page.
  useEffect(() => {
    if (run.data?.status === "success") {
      void qc.invalidateQueries({ queryKey: ["skill-creator", "skills"] });
      void qc.invalidateQueries({ queryKey: ["skill-creator", "skill"] });
      void qc.invalidateQueries({ queryKey: ["skills"] });
    }
  }, [run.data?.status, qc]);

  // ── Mutations ───────────────────────────────────────────────────────────
  const generate = useMutation({
    mutationFn: async ({ request, base }: { request: string; base: string | null }) =>
      startSkillGeneration(request, base),
    onSuccess: (data) => {
      setRunId(data.run_id);
      setUserRequest("");
      setError(null);
    },
    onError: (e: Error) => setError(e.message),
  });

  const remove = useMutation({
    mutationFn: async (name: string) => deleteGeneratedSkill(name, session!.csrf_token),
    onSuccess: async (_data, name) => {
      setToast({ kind: "ok", msg: `Deleted "${name}"` });
      if (viewing === name) setViewing(null);
      if (refineTarget === name) setRefineTarget(null);
      await qc.invalidateQueries({ queryKey: ["skill-creator", "skills"] });
      await qc.invalidateQueries({ queryKey: ["skills"] });
    },
    onError: (e: Error) => setToast({ kind: "err", msg: e.message }),
    onSettled: () => setConfirmDelete(null),
  });

  // ── Derived ─────────────────────────────────────────────────────────────
  const filtered = useMemo(() => {
    const all = skills.data?.skills ?? [];
    if (!search.trim()) return all;
    const needle = search.toLowerCase();
    return all.filter(
      (s) =>
        s.name.toLowerCase().includes(needle) ||
        (s.description ?? "").toLowerCase().includes(needle),
    );
  }, [skills.data, search]);

  const phases = run.data?.phases ?? DEFAULT_PHASES;

  const startRefine = useCallback((name: string) => {
    setRefineTarget(name);
    setError(null);
    // Scroll the composer into view: the list can be long, and a refine
    // banner nobody sees looks like a button that did nothing.
    document.getElementById("skill-request")?.scrollIntoView({ block: "center" });
    document.getElementById("skill-request")?.focus();
  }, []);

  const submit = () => {
    if (!userRequest.trim()) {
      setError(
        refineTarget
          ? "Describe the change you want made to this skill"
          : "Please enter a skill description",
      );
      return;
    }
    generate.mutate({ request: userRequest.trim(), base: refineTarget });
  };

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 4000);
    return () => clearTimeout(t);
  }, [toast]);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <Brain className="h-5 w-5 text-accent" />
          <CardTitle className="text-base">Skill Creator</CardTitle>
        </div>
        <CardDescription>
          Generate reusable skills through 5-phase LDD orchestration (Planning, Validation,
          LDD Iteration, Adversarial Review, Promotion) — on your Claude subscription via the
          Claude Code engine, no API key required.
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-6">
        {/* ── Composer ──────────────────────────────────────────────────── */}
        <div className="space-y-3">
          {refineTarget && (
            <div
              className="flex items-center justify-between gap-2 rounded-md border border-accent/40 bg-accent/5 p-2 text-xs"
              data-testid="refine-banner"
            >
              <div className="flex min-w-0 items-center gap-2">
                <Pencil className="h-3.5 w-3.5 shrink-0 text-accent" />
                <span className="truncate">
                  Refining <code className="font-mono">{refineTarget}</code> — it will be
                  replaced in place.
                </span>
              </div>
              <Button
                size="sm"
                variant="ghost"
                className="h-6 shrink-0 px-2 text-xs"
                onClick={() => setRefineTarget(null)}
                data-testid="cancel-refine"
              >
                <X className="mr-1 h-3 w-3" />
                Cancel
              </Button>
            </div>
          )}

          <Label htmlFor="skill-request" className="text-sm font-medium">
            {refineTarget ? "What should change?" : "What skill do you want to create?"}
          </Label>
          <Textarea
            id="skill-request"
            data-testid="skill-request"
            placeholder={
              refineTarget
                ? "e.g., 'also report duplicate keys as warnings' or 'kürze die Anleitung auf 5 Schritte'"
                : "e.g., 'Create a skill that validates JSON files and reports errors' or 'erzeuge einen Skill für CSV-Analyse'"
            }
            value={userRequest}
            onChange={(e) => setUserRequest(e.target.value)}
            disabled={isRunning}
            rows={3}
            className="font-mono text-xs"
          />
          <Button
            onClick={submit}
            disabled={isRunning || generate.isPending || !userRequest.trim()}
            className="w-full"
            data-testid="submit-generation"
          >
            {(isRunning || generate.isPending) && (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            )}
            {isRunning
              ? refineTarget
                ? "Refining Skill..."
                : "Generating Skill..."
              : refineTarget
                ? "Refine Skill"
                : "Generate Skill"}
          </Button>
          <p className="text-[10px] text-muted-foreground">
            A run takes several minutes and is charged to your Claude subscription.
          </p>
        </div>

        {/* ── Error ─────────────────────────────────────────────────────── */}
        {error && (
          <div className="flex items-start justify-between gap-3 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
            <div className="flex items-start gap-3">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{error}</span>
            </div>
            <Button size="sm" variant="outline" onClick={() => setError(null)} className="shrink-0">
              Dismiss
            </Button>
          </div>
        )}

        {/* ── Run progress ──────────────────────────────────────────────── */}
        {run.data && (
          <div className="space-y-3 rounded-md border border-accent/40 bg-accent/5 p-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-medium">
                {run.data.base_skill ? "Refinement Progress" : "Generation Progress"}
              </h3>
              <Badge variant="outline" className="font-mono text-xs">
                {run.data.status.toUpperCase()}
              </Badge>
            </div>

            <div className="space-y-2 text-xs text-muted-foreground">
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-[10px]">Run: {run.data.run_id.slice(0, 12)}...</span>
                <span className="text-sm font-medium">
                  {PHASE_LABELS[run.data.phase] || run.data.phase}
                </span>
              </div>
              {run.data.engine && (
                <div className="flex items-center gap-2">
                  <Cpu className="h-3 w-3 shrink-0" />
                  <span data-testid="engine-label">
                    Engine: {ENGINE_LABELS[run.data.engine] || run.data.engine}
                  </span>
                </div>
              )}
              {run.data.base_skill && (
                <div className="flex items-center gap-2">
                  <Pencil className="h-3 w-3 shrink-0" />
                  <span data-testid="run-base-skill">
                    Refining: <code className="font-mono">{run.data.base_skill}</code>
                  </span>
                </div>
              )}
            </div>

            <ol className="flex flex-wrap gap-1" data-testid="phase-stepper">
              {phases.map((phase, idx) => {
                const activeIdx = phases.indexOf(run.data!.phase);
                const done = run.data!.status === "success" || (activeIdx > -1 && idx < activeIdx);
                const active = idx === activeIdx && run.data!.status !== "failed";
                const failed = idx === activeIdx && run.data!.status === "failed";
                return (
                  <li
                    key={phase}
                    data-phase={phase}
                    data-state={failed ? "failed" : active ? "active" : done ? "done" : "pending"}
                    className={
                      "rounded-sm border px-1.5 py-0.5 text-[10px] " +
                      (failed
                        ? "border-destructive/50 bg-destructive/10 text-destructive"
                        : active
                          ? "border-accent bg-accent/15 text-foreground"
                          : done
                            ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                            : "border-border/60 text-muted-foreground")
                    }
                  >
                    {PHASE_LABELS[phase] || phase}
                  </li>
                );
              })}
            </ol>

            <div className="h-2 w-full overflow-hidden rounded-full border border-border/60 bg-muted/30">
              <div
                className="h-full bg-accent transition-all"
                style={{ width: `${Math.min(run.data.progress, 100)}%` }}
              />
            </div>

            <p className="text-xs text-muted-foreground">{run.data.message}</p>

            {run.data.status === "success" && run.data.skill && (
              <SkillResult
                skill={run.data.skill}
                onView={() => setViewing(run.data!.skill!.name)}
                onRefine={() => startRefine(run.data!.skill!.name)}
              />
            )}

            {run.data.status === "failed" && (
              <div
                className="space-y-1 rounded-sm border border-destructive/40 bg-destructive/5 p-2 text-xs text-destructive"
                data-testid="generation-error"
              >
                <div>❌ {run.data.message}</div>
                {run.data.error && run.data.error !== run.data.message && (
                  <details>
                    <summary className="cursor-pointer opacity-80">Engine detail</summary>
                    <pre className="mt-1 whitespace-pre-wrap break-all font-mono text-[10px] opacity-90">
                      {run.data.error}
                    </pre>
                  </details>
                )}
              </div>
            )}
          </div>
        )}

        {/* ── Skill library ─────────────────────────────────────────────── */}
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-2">
            <h3 className="flex items-center gap-2 text-sm font-medium">
              Generated Skills
              <Badge variant="secondary" className="h-5 text-[10px]">
                {skills.data?.count ?? 0}
              </Badge>
              {skills.data && skills.data.count > skills.data.injectable_count && (
                <Badge
                  variant="outline"
                  className="h-5 text-[10px]"
                  title="Skills with no grade sit below the injection gate and are never used"
                >
                  {skills.data.count - skills.data.injectable_count} inert
                </Badge>
              )}
            </h3>
            {(skills.data?.count ?? 0) > 4 && (
              <div className="relative w-48">
                <Search className="absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Filter…"
                  className="h-7 pl-7 text-xs"
                  data-testid="skill-filter"
                />
              </div>
            )}
          </div>

          {skills.isLoading ? (
            <p className="text-xs text-muted-foreground">Loading skills…</p>
          ) : skills.isError ? (
            <p className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive">
              Could not load skills: {(skills.error as Error).message}
            </p>
          ) : filtered.length === 0 ? (
            <p className="rounded-md border border-border/60 bg-muted/30 p-3 text-xs text-muted-foreground">
              {search
                ? `No skill matches "${search}".`
                : "No skills generated yet. Create your first skill above!"}
            </p>
          ) : (
            <div className="max-h-72 space-y-2 overflow-y-auto">
              {filtered.map((skill) => (
                <SkillRow
                  key={skill.name}
                  skill={skill}
                  onView={() => setViewing(skill.name)}
                  onRefine={() => startRefine(skill.name)}
                  onDelete={() => setConfirmDelete(skill.name)}
                  busy={remove.isPending && remove.variables === skill.name}
                  disabled={isRunning}
                />
              ))}
            </div>
          )}
        </div>

        {/* ── Viewer ────────────────────────────────────────────────────── */}
        {viewing && (
          <SkillViewer
            name={viewing}
            detail={detail.data}
            loading={detail.isLoading}
            error={detail.isError ? (detail.error as Error).message : null}
            onClose={() => setViewing(null)}
            onRefine={() => startRefine(viewing)}
            onCopy={() => {
              const body = detail.data?.body ?? "";
              void navigator.clipboard?.writeText(body).then(
                () => setToast({ kind: "ok", msg: "Skill body copied" }),
                () => setToast({ kind: "err", msg: "Clipboard unavailable" }),
              );
            }}
          />
        )}
      </CardContent>

      {/* ── Delete confirmation ───────────────────────────────────────────
          Deletion removes the skill from the registry, disk and the engine
          plugin slot; it is not undoable from here, so it asks first. */}
      <Dialog open={!!confirmDelete} onOpenChange={(open) => !open && setConfirmDelete(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Delete skill?</DialogTitle>
            <DialogDescription>
              <code className="font-mono">{confirmDelete}</code> will be removed from the
              registry, from disk and from the engine plugin slot. This cannot be undone —
              you would have to generate it again.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmDelete(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => confirmDelete && remove.mutate(confirmDelete)}
              disabled={remove.isPending}
              data-testid="confirm-delete"
            >
              {remove.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {toast && (
        <div
          data-testid="skill-toast"
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
    </Card>
  );
};

// ── Sub-components ──────────────────────────────────────────────────────────

function UsableBadge({ skill }: { skill: { injectable: boolean; n_grades: number; mean_score: number } }) {
  return (
    <Badge
      variant={skill.injectable ? "secondary" : "outline"}
      className="h-5 shrink-0 text-[9px]"
      title={
        skill.injectable
          ? `graded ${skill.n_grades}× (mean ${skill.mean_score}) — eligible for injection`
          : "no grade yet — below the injection gate, will not be used in a turn"
      }
    >
      {skill.injectable ? "usable" : "inert"}
    </Badge>
  );
}

function SkillRow({
  skill,
  onView,
  onRefine,
  onDelete,
  busy,
  disabled,
}: {
  skill: GeneratedSkillSummary;
  onView: () => void;
  onRefine: () => void;
  onDelete: () => void;
  busy: boolean;
  disabled: boolean;
}) {
  return (
    <div
      className="flex items-center justify-between gap-2 rounded-md border border-border/60 bg-card/40 p-2.5 transition-colors hover:border-accent/40"
      data-testid="skill-row"
      data-skill={skill.name}
    >
      <button
        onClick={onView}
        className="min-w-0 flex-1 text-left focus:outline-none focus-visible:rounded-md focus-visible:ring-2 focus-visible:ring-ring"
      >
        <div className="font-mono text-xs font-medium">{skill.name}</div>
        <div className="truncate text-[10px] text-muted-foreground">{skill.description || "—"}</div>
      </button>
      <div className="flex shrink-0 items-center gap-1">
        <UsableBadge skill={skill} />
        <Button size="sm" variant="outline" className="h-6 px-2 text-xs" onClick={onView}>
          View
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="h-6 px-2 text-xs"
          onClick={onRefine}
          disabled={disabled}
          title="Modify this skill with a new generation round"
          data-testid={`refine-${skill.name}`}
        >
          <Pencil className="mr-1 h-3 w-3" />
          Refine
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="h-6 px-2 text-xs text-destructive hover:text-destructive"
          onClick={onDelete}
          disabled={busy}
          data-testid={`delete-${skill.name}`}
          aria-label={`Delete ${skill.name}`}
        >
          {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
        </Button>
      </div>
    </div>
  );
}

function SkillResult({
  skill,
  onView,
  onRefine,
}: {
  skill: NonNullable<SkillRunStatus["skill"]>;
  onView: () => void;
  onRefine: () => void;
}) {
  return (
    <div className="mt-3 space-y-2 rounded-sm border border-emerald-500/40 bg-emerald-500/5 p-2">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-emerald-700 dark:text-emerald-300">
          <CheckCircle className="h-4 w-4" />
          <span className="text-sm font-medium">Skill ready</span>
        </div>
        <div className="flex items-center gap-1">
          <Button size="sm" variant="outline" className="h-6 px-2 text-xs" onClick={onView}>
            View
          </Button>
          <Button size="sm" variant="outline" className="h-6 px-2 text-xs" onClick={onRefine}>
            <Pencil className="mr-1 h-3 w-3" />
            Refine
          </Button>
        </div>
      </div>

      <div className="space-y-1 text-xs">
        <Field label="Name">
          <code className="text-foreground">{skill.name}</code>
        </Field>
        <Field label="Purpose">
          <span className="text-foreground">{skill.purpose}</span>
        </Field>
        <Field label="Scope">
          <Badge variant="secondary" className="h-5 text-[10px]">
            {skill.scope}
          </Badge>
        </Field>
        <Field label="Quality">
          <Badge className="h-5 text-[10px]">{Math.round((skill.quality ?? 0) * 100)}%</Badge>
        </Field>
        <Field label="Iterations">
          <span className="text-foreground" data-testid="skill-iterations">
            {skill.iterations ?? "—"}
          </span>
        </Field>
        <Field label="Usable">
          <Badge
            variant={skill.injectable ? "default" : "outline"}
            className="h-5 text-[10px]"
            data-testid="skill-injectable"
          >
            {skill.injectable ? "registered + graded" : "not injectable"}
          </Badge>
        </Field>
      </div>

      {/* Why the score is what it is. Without this a low quality number
          arrives with no way to act on it. */}
      {skill.findings && skill.findings.length > 0 && (
        <details className="mt-2" data-testid="review-findings">
          <summary className="cursor-pointer text-xs text-muted-foreground">
            {skill.findings.length} review finding(s) — why this score
          </summary>
          <ul className="mt-1 space-y-1">
            {skill.findings.map((f, i) => (
              <li key={i} className="text-[11px] leading-snug">
                <Badge variant="outline" className="mr-1 h-4 text-[9px]">
                  {f.verdict}
                </Badge>
                <span className="font-mono text-muted-foreground">{f.dimension}:</span>{" "}
                <span className="text-foreground">{f.summary}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-2">
      <span className="font-mono text-muted-foreground">{label}:</span>
      {children}
    </div>
  );
}

function SkillViewer({
  name,
  detail,
  loading,
  error,
  onClose,
  onRefine,
  onCopy,
}: {
  name: string;
  detail?: GeneratedSkillDetail;
  loading: boolean;
  error: string | null;
  onClose: () => void;
  onRefine: () => void;
  onCopy: () => void;
}) {
  return (
    <div
      className="space-y-2 rounded-md border border-border/60 bg-card/40 p-3"
      data-testid="skill-viewer"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="font-mono text-xs font-medium">{name}</div>
          {detail && (
            <div className="text-[10px] text-muted-foreground">
              {detail.type} · {detail.scope} ·{" "}
              {detail.injectable
                ? `graded ${detail.n_grades}× (mean ${detail.mean_score})`
                : "no grade — below the injection gate"}
            </div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button size="sm" variant="outline" className="h-6 px-2 text-xs" onClick={onCopy}>
            <Copy className="mr-1 h-3 w-3" />
            Copy
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="h-6 px-2 text-xs"
            onClick={onRefine}
            data-testid="viewer-refine"
          >
            <Pencil className="mr-1 h-3 w-3" />
            Refine
          </Button>
          <Button size="sm" variant="outline" className="h-6 px-2 text-xs" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>

      {loading ? (
        <p className="text-xs text-muted-foreground">Loading skill…</p>
      ) : error ? (
        <p className="rounded-sm border border-destructive/40 bg-destructive/5 p-2 text-xs text-destructive">
          {error}
        </p>
      ) : (
        <>
          <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-sm border border-border/60 bg-muted/30 p-2 font-mono text-[11px] leading-snug">
            {detail?.body}
          </pre>
          {detail?.grades && detail.grades.length > 0 && (
            <details>
              <summary className="cursor-pointer text-[10px] text-muted-foreground">
                {detail.grades.length} grade(s)
              </summary>
              <ul className="mt-1 space-y-0.5">
                {detail.grades.map((g, i) => (
                  <li key={i} className="text-[10px] text-muted-foreground">
                    <span className="font-mono">{g.score.toFixed(2)}</span>
                    {g.notes ? ` — ${g.notes}` : ""}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </div>
  );
}

export default SkillCreatorPanel;

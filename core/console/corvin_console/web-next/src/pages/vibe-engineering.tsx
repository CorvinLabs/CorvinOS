/**
 * Vibe Engineering — Context Pipeline (read-only observability, ADR-0275/0278).
 *
 * Compact, overview-first: each turn is a single row of small clickable stage
 * pills (no more wide cards running off-screen). Click a stage → a window with a
 * star-graph of that stage (the stage at the centre, its sources radial with
 * relevance-scored edges) so the user can VISUALLY follow how the context is
 * built. A separate button opens the full injected brief + audit integrity.
 */
import { useEffect, useState } from "react";
import {
  Loader2, AlertCircle, Brain, Network, Sparkles, Workflow, X, ShieldCheck,
  Hash, FileText, ChevronRight, Cpu, Wrench, BookOpen, Code, Layers,
} from "lucide-react";
import { Card, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  useTraces, useBrief, useAssembly, useForged, usePipeline, useSavePipeline,
  useStageGrades, useGradeStage,
  type Turn, type Stage, type Assembly,
} from "@/adapters/vibe";

// Single source of truth for domain types is the adapter (ADR-0353 P0).
// (domain types come from the adapter)

const STAGE_META: Record<string, {
  icon: React.ComponentType<{ className?: string }>; label: string; short: string;
  effect?: "pure" | "egress" | "forge";
}> = {
  memory: { icon: Brain, label: "Memory Lookup", short: "Memory", effect: "pure" },
  graph: { icon: Network, label: "Graph Traversal", short: "Graph", effect: "pure" },
  skill: { icon: Sparkles, label: "Skill Injection", short: "Skill", effect: "pure" },
  approach_synthesis: { icon: Workflow, label: "Approach Synthesis", short: "Approach", effect: "pure" },
  blocker_id: { icon: AlertCircle, label: "Blocker ID", short: "Blocker", effect: "pure" },
  llm_synthesis: { icon: Cpu, label: "LLM Synthesis", short: "LLM", effect: "egress" },
  toolforge: { icon: Wrench, label: "ToolForge", short: "ToolForge", effect: "forge" },
  skillforge: { icon: BookOpen, label: "SkillForge", short: "SkillForge", effect: "forge" },
};
const ORDER = ["memory", "graph", "skill", "llm_synthesis", "toolforge",
  "skillforge", "approach_synthesis", "blocker_id"];
function effectBadge(effect?: string): { txt: string; cls: string } | null {
  if (effect === "egress") return { txt: "egress", cls: "bg-orange-500/15 text-orange-300 border-orange-500/30" };
  if (effect === "forge") return { txt: "forge", cls: "bg-violet-500/15 text-violet-300 border-violet-500/30" };
  return null;
}

function dotColor(status: string): string {
  if (status === "ok") return "bg-emerald-500";
  if (status === "not_run") return "bg-slate-500/50";
  if (status === "failed") return "bg-red-500";
  return "bg-amber-500";
}
function tierColor(tier?: string): string {
  if (tier === "high") return "#10b981";
  if (tier === "medium") return "#f59e0b";
  return "#64748b";
}

/* ── compact pipeline bar: one row of small clickable pills ─────────────── */
function StagePill({ stage, onClick }: { stage: Stage; onClick: () => void }) {
  const meta = STAGE_META[stage.stage] ?? { icon: Workflow, label: stage.stage, short: stage.stage, effect: "pure" as const };
  const Icon = meta.icon;
  const n = stage.sources?.length ?? 0;
  const notRun = stage.status === "not_run";
  const eff = effectBadge(meta.effect);
  return (
    <button
      onClick={onClick}
      title={`${meta.label} — ${stage.status}${n ? `, ${n} sources` : ""}`}
      className={
        "group flex items-center gap-2 rounded-lg border px-3 py-2 text-left transition-colors " +
        (notRun
          ? "border-border bg-muted/20 opacity-60 hover:opacity-100"
          : "border-border bg-card hover:border-accent-foreground/50")
      }
    >
      <span className={"h-2 w-2 shrink-0 rounded-full " + dotColor(stage.status)} />
      <Icon className="h-4 w-4 shrink-0 text-accent-foreground" />
      <span className="text-sm font-medium">{meta.short}</span>
      {eff && <span className={"rounded border px-1 text-[10px] font-semibold " + eff.cls}>{eff.txt}</span>}
      {!notRun && (
        <span className="text-xs text-muted-foreground tabular-nums">{n}</span>
      )}
    </button>
  );
}

function PipelineBar({ turn, onStage }: { turn: Turn; onStage: (s: Stage) => void }) {
  const map = new Map(turn.stages.map((s) => [s.stage, s]));
  const ordered = ORDER.map((k) => map.get(k)).filter(Boolean) as Stage[];
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {ordered.map((s, i) => (
        <div key={s.stage} className="flex items-center gap-1.5">
          <StagePill stage={s} onClick={() => onStage(s)} />
          {i < ordered.length - 1 && (
            <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground/60" />
          )}
        </div>
      ))}
    </div>
  );
}

/* ── star graph of one stage (SVG): stage at centre, sources radial ─────── */
function StageGraph({ stage }: { stage: Stage }) {
  const meta = STAGE_META[stage.stage] ?? { label: stage.stage, short: stage.stage };
  const srcs = (stage.sources ?? []).slice(0, 8);
  const W = 520, H = 360, cx = W / 2, cy = H / 2, R = 128;
  const col = tierColor(stage.confidence_tier);

  if (stage.status === "not_run") {
    return <p className="text-sm text-muted-foreground italic py-8 text-center">
      This stage did not run this turn ({stage.reason || "not reached"}).</p>;
  }
  if (srcs.length === 0) {
    return <p className="text-sm text-muted-foreground italic py-8 text-center">
      Stage ran with no matching sources.</p>;
  }

  const pts = srcs.map((s, i) => {
    const a = -Math.PI / 2 + (2 * Math.PI * i) / srcs.length;
    return { s, x: cx + R * Math.cos(a), y: cy + R * Math.sin(a) };
  });

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto">
      {/* edges */}
      {pts.map((p, i) => {
        const mx = (cx + p.x) / 2, my = (cy + p.y) / 2;
        return (
          <g key={"e" + i}>
            <line x1={cx} y1={cy} x2={p.x} y2={p.y} stroke={col}
                  strokeWidth={0.8 + (p.s.score ?? 0) * 2.6} strokeOpacity={0.45} />
            <rect x={mx - 15} y={my - 9} width={30} height={16} rx={4}
                  className="fill-background" stroke={col} strokeOpacity={0.5} />
            <text x={mx} y={my + 3} textAnchor="middle"
                  className="fill-muted-foreground" fontSize={10}>
              {(p.s.score ?? 0).toFixed(2)}
            </text>
          </g>
        );
      })}
      {/* source nodes */}
      {pts.map((p, i) => {
        const label = p.s.id.length > 22 ? p.s.id.slice(0, 21) + "…" : p.s.id;
        const w = Math.max(70, label.length * 6.2);
        return (
          <g key={"n" + i}>
            <rect x={p.x - w / 2} y={p.y - 13} width={w} height={26} rx={7}
                  className="fill-card" stroke={col} strokeOpacity={0.7} />
            <text x={p.x} y={p.y + 4} textAnchor="middle"
                  className="fill-foreground" fontSize={11}>{label}</text>
          </g>
        );
      })}
      {/* centre */}
      <circle cx={cx} cy={cy} r={44} className="fill-accent/15" stroke={col} strokeWidth={2} />
      <text x={cx} y={cy - 2} textAnchor="middle" className="fill-foreground" fontSize={12} fontWeight={600}>
        {meta.short}
      </text>
      <text x={cx} y={cy + 14} textAnchor="middle" className="fill-muted-foreground" fontSize={10}>
        {srcs.length} src
      </text>
    </svg>
  );
}

/* ── modal shells ──────────────────────────────────────────────────────── */
function Modal({ title, subtitle, onClose, children }: {
  title: string; subtitle?: string; onClose: () => void; children: React.ReactNode;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="fixed inset-0 bg-background/70 backdrop-blur-sm" />
      <div className="relative z-10 w-full max-w-2xl max-h-[85vh] overflow-y-auto rounded-xl border border-border bg-card p-6 shadow-xl"
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between mb-4">
          <div>
            <h2 className="text-lg font-bold">{title}</h2>
            {subtitle && <p className="text-xs text-muted-foreground">{subtitle}</p>}
          </div>
          <button onClick={onClose} className="rounded-md p-1 hover:bg-muted" aria-label="Close">
            <X className="h-5 w-5" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

/* ── operator stage grading (G3): the missing UI for the CEL grade store ─────────
 * The confidence tier was always visible but the accrued grades were not, and there
 * was no way to add one. Only an operator grade (grader="operator") is promoting —
 * this is the human-in-the-loop that governs stage default-eligibility (ADR-0285). */
function StageGradePanel({ stageId }: { stageId: string }) {
  const q = useStageGrades();
  const grade = useGradeStage();
  const g = q.data?.grades?.[stageId];
  const buttons: Array<{ label: string; score: number; cls: string }> = [
    { label: "👎", score: 0.0, cls: "hover:border-red-500/60 hover:bg-red-900/20" },
    { label: "😐", score: 0.5, cls: "hover:border-amber-500/60 hover:bg-amber-900/20" },
    { label: "👍", score: 1.0, cls: "hover:border-emerald-500/60 hover:bg-emerald-900/20" },
  ];
  return (
    <div className="mt-4 rounded-lg border border-border bg-background/50 p-3">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-semibold">Stage-Vertrauen (Operator-Grade)</h3>
        {g && (
          <span className="text-xs text-muted-foreground tabular-nums">
            {g.n_grades} Grade{g.n_grades === 1 ? "" : "s"} · ⌀ {g.mean_score.toFixed(2)}
            {g.promoting != null ? ` · ${g.promoting} promotend` : ""}
          </span>
        )}
      </div>
      <div className="flex items-center gap-2">
        {buttons.map((b) => (
          <button key={b.label} disabled={grade.isPending}
            onClick={() => grade.mutate({ stage: stageId, score: b.score })}
            className={`rounded-md border border-border px-3 py-1 text-lg transition disabled:opacity-40 ${b.cls}`}
            title={`Grade ${b.score.toFixed(1)}`}>
            {b.label}
          </button>
        ))}
        {grade.isPending && <span className="text-xs text-muted-foreground">speichere…</span>}
        {grade.isError && <span className="text-xs text-red-400">Fehler beim Speichern</span>}
      </div>
      <p className="mt-2 text-[11px] text-muted-foreground">
        Nur Operator-Grades zählen für die Default-Eignung einer Stage. Die Auto-Grades
        des Outcome-Loops (G4) sind rein beratend.
      </p>
    </div>
  );
}

function StageModal({ stage, onClose }: { stage: Stage; onClose: () => void }) {
  const meta = STAGE_META[stage.stage] ?? { label: stage.stage };
  return (
    <Modal title={meta.label}
           subtitle={`status ${stage.status}` +
             (stage.confidence_tier ? ` · confidence ${stage.confidence_tier}` : "") +
             (stage.duration_ms != null ? ` · ${Math.round(stage.duration_ms)} ms` : "")}
           onClose={onClose}>
      <div className="flex flex-wrap gap-1.5 mb-3">
        <Badge variant={stage.status === "ok" ? "ok" : stage.status === "not_run" ? "secondary" : "danger"}>
          {stage.status}
        </Badge>
        {stage.confidence_tier && <Badge variant="secondary">{stage.confidence_tier}</Badge>}
      </div>
      <div className="rounded-lg border border-border bg-background/50 p-2">
        <StageGraph stage={stage} />
      </div>
      <StageGradePanel stageId={stage.stage} />
      {(stage.sources?.length ?? 0) > 0 && (
        <div className="mt-4">
          <h3 className="text-sm font-semibold mb-2">Sources (relevance)</h3>
          <ul className="space-y-1">
            {stage.sources!.map((s, i) => (
              <li key={i} className="flex justify-between gap-2 text-sm">
                <span className="truncate font-mono text-xs" title={s.id}>{s.id}</span>
                <span className="tabular-nums text-muted-foreground">{(s.score ?? 0).toFixed(2)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Modal>
  );
}

function BriefModal({ turn, onClose }: { turn: Turn; onClose: () => void }) {
  const q = useBrief(turn.brief_sha256);
  const brief = q.data?.found ? (q.data.text ?? null) : null;
  const state: "loading" | "ok" | "erased" | "none" =
    !turn.brief_sha256 ? "none"
      : q.isLoading ? "loading"
        : q.data?.found ? "ok" : "erased";
  return (
    <Modal title="Injected context brief" subtitle={turn.turn_id} onClose={onClose}>
      <div className="mb-4 rounded-lg border border-border bg-muted/30 p-3">
        <div className="flex items-center gap-2 mb-1 text-sm font-medium">
          <ShieldCheck className="h-4 w-4 text-emerald-600 dark:text-emerald-400" /> Audit record — hash-chained
        </div>
        <div className="flex items-center gap-1 text-xs text-muted-foreground font-mono break-all">
          <Hash className="h-3 w-3 shrink-0" /> {turn.hash ?? "—"}
        </div>
      </div>
      <div className="rounded-lg border border-border bg-background p-3 text-sm">
        {state === "loading" && <span className="flex items-center gap-2 text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /> loading…</span>}
        {state === "ok" && <pre className="whitespace-pre-wrap font-mono text-xs leading-relaxed">{brief}</pre>}
        {state === "erased" && <span className="text-muted-foreground italic">Brief text was lawfully erased (GDPR Art. 17).</span>}
        {state === "none" && <span className="text-muted-foreground italic">No brief produced this turn (plain context).</span>}
      </div>
    </Modal>
  );
}

/* ── Glass Box: the exact prompt the worker engine received, split by origin ──
 * v1 honest annotation (see docs/implementation/vibe-engineering-glassbox-plan.md,
 * Review F2): final_prompt = base_system_prompt + "\n\n" + cel_text. The CEL block is
 * the ONLY boundary cleanly locatable from existing data — so we colour the base
 * prompt vs the injected CEL context distinctly and list the retrieval sections as a
 * legend. We deliberately do NOT fake per-stage spans inside cel_text (not derivable). */
function GlassBoxPrompt({ asm }: { asm: Assembly }) {
  const full = asm.final_prompt || "";
  const cel = (asm.cel_text || "").trim();
  // Split at the CEL block if it is present as a suffix of the final prompt.
  let base = full, injected = "";
  if (cel && full.includes(cel)) {
    const idx = full.lastIndexOf(cel);
    base = full.slice(0, idx).replace(/\n+$/, "");
    injected = full.slice(idx);
  }
  const sections = asm.sections ?? [];
  if (!full) return <pre className="rounded-lg border border-slate-700/40 bg-slate-900/20 p-3 font-mono text-xs text-slate-400">(leer — dieser Turn hat keinen persistierten Prompt)</pre>;
  return (
    <div className="space-y-2">
      {sections.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {sections.map((s, i) => (
            <span key={i} className="rounded-full border border-violet-700/40 bg-violet-900/20 px-2 py-0.5 text-[10px] font-medium text-violet-200" title={s.kind}>
              {s.label}{Array.isArray(s.items) && s.items.length ? ` · ${s.items.length}` : ""}
            </span>
          ))}
        </div>
      )}
      <div>
        <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-slate-500">Basis-System-Prompt</div>
        <pre className="max-h-[30vh] overflow-auto whitespace-pre-wrap rounded-lg border border-slate-700/40 bg-slate-900/30 p-3 font-mono text-xs leading-relaxed text-slate-300">{base || "(kein Basis-Teil)"}</pre>
      </div>
      {injected && (
        <div>
          <div className="mb-1 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-emerald-400">
            <Layers className="h-3 w-3" /> CEL-Kontext (eingespeist)
          </div>
          <pre className="max-h-[30vh] overflow-auto whitespace-pre-wrap rounded-lg border border-emerald-700/50 bg-emerald-900/15 p-3 font-mono text-xs leading-relaxed text-emerald-50">{injected}</pre>
        </div>
      )}
    </div>
  );
}

/* ── turn card: header + one compact pipeline row ──────────────────────── */
/* ── prompt inspector: bausteine → final prompt + forged tool/skill code ── */
function PromptInspectorModal({ turn, onClose }: { turn: Turn; onClose: () => void }) {
  const [openCode, setOpenCode] = useState<string | null>(null);
  const asmQ = useAssembly(turn.turn_id);
  const forgedQ = useForged(turn.turn_id);
  const asm = asmQ.data?.found ? asmQ.data : null;
  const forged = forgedQ.data?.found
    ? { tools: forgedQ.data.tools ?? [], skills: forgedQ.data.skills ?? [] } : null;
  const state: "loading" | "ok" | "none" =
    asmQ.isLoading ? "loading" : asm ? "ok" : "none";

  return (
    <Modal title="Prompt Inspector" subtitle={turn.turn_id} onClose={onClose}>
      {state === "loading" && <div className="flex items-center gap-2 text-sm text-slate-400"><Loader2 className="h-4 w-4 animate-spin" /> lädt…</div>}
      {state === "none" && <div className="text-sm text-slate-400">Kein Assembly-Record für diesen Turn (passiver Turn ohne aktives Brain, oder GDPR-gelöscht).</div>}
      {state === "ok" && asm && (
        <div className="space-y-5">
          <div>
            <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-400"><Layers className="h-3.5 w-3.5" /> Bausteine</div>
            <div className="space-y-2">
              {(asm.sections || []).map((s, i) => (
                <div key={i} className="rounded-lg border border-slate-700/60 bg-slate-800/40 p-3">
                  <div className="mb-1 text-xs font-semibold text-slate-300">{s.label}</div>
                  {s.text
                    ? <pre className="whitespace-pre-wrap font-mono text-xs leading-relaxed text-slate-200">{s.text}</pre>
                    : <ul className="list-disc pl-4 text-xs text-slate-300">{(s.items || []).map((it, j) => <li key={j}>{it}</li>)}</ul>}
                </div>
              ))}
              {(asm.sections || []).length === 0 && <div className="text-xs text-slate-500">(keine Bausteine — leerer Brief)</div>}
            </div>
          </div>
          {forged && (forged.tools.length > 0 || forged.skills.length > 0) && (
            <div>
              <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-400"><Code className="h-3.5 w-3.5" /> Erzeugte Tools & Skills — Code</div>
              <div className="space-y-2">
                {forged.tools.map((t) => (
                  <div key={t.name} className="rounded-lg border border-violet-700/40 bg-violet-900/10">
                    <button onClick={() => setOpenCode(openCode === t.name ? null : t.name)} className="flex w-full items-center gap-2 p-2 text-left text-xs">
                      <Wrench className="h-3.5 w-3.5 shrink-0 text-violet-300" />
                      <span className="font-mono text-violet-200">{t.name}</span>
                      <span className="truncate opacity-60">{t.description}</span>
                      <ChevronRight className={`ml-auto h-3.5 w-3.5 shrink-0 transition ${openCode === t.name ? "rotate-90" : ""}`} />
                    </button>
                    {openCode === t.name && <pre className="max-h-[30vh] overflow-auto border-t border-violet-700/30 p-3 font-mono text-xs leading-relaxed text-slate-200">{t.code || "(kein Code auf Platte)"}</pre>}
                  </div>
                ))}
                {forged.skills.map((s) => (
                  <div key={s.skill_id} className="rounded-lg border border-violet-700/40 bg-violet-900/10">
                    <button onClick={() => setOpenCode(openCode === s.skill_id ? null : s.skill_id)} className="flex w-full items-center gap-2 p-2 text-left text-xs">
                      <BookOpen className="h-3.5 w-3.5 shrink-0 text-violet-300" />
                      <span className="font-mono text-violet-200">{s.skill_id}</span>
                      <ChevronRight className={`ml-auto h-3.5 w-3.5 shrink-0 transition ${openCode === s.skill_id ? "rotate-90" : ""}`} />
                    </button>
                    {openCode === s.skill_id && <pre className="max-h-[30vh] overflow-auto whitespace-pre-wrap border-t border-violet-700/30 p-3 font-mono text-xs leading-relaxed text-slate-200">{s.body}</pre>}
                  </div>
                ))}
              </div>
            </div>
          )}
          <div>
            <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-400"><FileText className="h-3.5 w-3.5" /> Finaler Prompt → Worker-Engine</div>
            <GlassBoxPrompt asm={asm} />
          </div>
        </div>
      )}
    </Modal>
  );
}

interface PaletteStage { id: string; requires: string[]; effect: string; trust: string }
interface PipeEntry { stage: string; config?: Record<string, any> }

const EFFECT_HINT: Record<string, string> = {
  pure: "local reads → text; runs pre-gate, free",
  egress: "sends context to an LLM — needs 'allow egress', gated + billed",
  forge: "forges tools/skills for the worker — runs post-gate",
};

function PipelineEditorModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [current, setCurrent] = useState<PipeEntry[]>([]);
  const [palette, setPalette] = useState<PaletteStage[]>([]);
  const [def, setDef] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Whether the egress/forge stages below can actually RUN. They are deferred to
  // the post-gate phase, which only the ACTIVE brain executes — with the
  // `vibe_engineering_active` flag off they sit permanently "deferred". Without
  // this the editor showed a pipeline that looked armed and silently was not.
  const [activeOn, setActiveOn] = useState(true);

  const pipeQ = usePipeline();
  const saveMut = useSavePipeline();
  useEffect(() => {
    if (pipeQ.data) {
      setCurrent(pipeQ.data.current || []); setPalette(pipeQ.data.palette || []);
      setDef(pipeQ.data.default || []); setActiveOn(pipeQ.data.active_enabled !== false);
      setLoading(false);
    } else if (pipeQ.isError) { setErr("failed to load pipeline"); setLoading(false); }
  }, [pipeQ.data, pipeQ.isError]);

  const meta = (id: string) => palette.find((p) => p.id === id);
  const move = (i: number, d: number) => {
    const a = [...current]; const j = i + d;
    if (j < 0 || j >= a.length) return;
    [a[i], a[j]] = [a[j], a[i]]; setCurrent(a);
  };
  const remove = (i: number) => setCurrent(current.filter((_, x) => x !== i));
  const add = (id: string) => setCurrent([...current, { stage: id }]);
  const setCfg = (i: number, k: string, v: any) => {
    const a = [...current]; a[i] = { ...a[i], config: { ...(a[i].config || {}), [k]: v } };
    setCurrent(a);
  };

  const save = async () => {
    setSaving(true); setErr(null);
    try {
      await saveMut.mutateAsync(current);
      onSaved(); onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setSaving(false); }
  };

  const inPipe = new Set(current.map((c) => c.stage));
  const addable = palette.filter((p) => !inPipe.has(p.id));

  return (
    <Modal title="Configure Context Pipeline"
           subtitle="reorder, toggle, tune per-stage — saved to your tenant, validated (DAG + memory root)"
           onClose={onClose}>
      {loading ? (
        <div className="py-8 flex justify-center"><Loader2 className="h-6 w-6 animate-spin" /></div>
      ) : (
        <>
          {!activeOn && current.some((e) => {
            const m = palette.find((p) => p.id === e.stage);
            return m && m.effect !== "pure";
          }) && (
            <div className="mb-4 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs">
              This pipeline contains egress/forge stages, but the <b>ACTIVE brain</b>{" "}
              feature flag (<code>vibe_engineering_active</code>) is off — those stages
              are recorded <code>deferred</code> and never run. Enable it under
              Settings → Features to arm them.
            </div>
          )}
          <div className="space-y-2 mb-4">
            {current.map((e, i) => {
              const m = meta(e.stage);
              const eff = m?.effect || "pure";
              return (
                <div key={i} className="rounded-lg border border-border bg-card p-3">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2 min-w-0">
                      <span className="text-xs text-muted-foreground tabular-nums">{i + 1}</span>
                      <span className="font-medium text-sm truncate">{e.stage}</span>
                      <Badge variant={eff === "pure" ? "secondary" : eff === "egress" ? "warn" : "danger"}>{eff}</Badge>
                    </div>
                    <div className="flex items-center gap-1 shrink-0">
                      <button onClick={() => move(i, -1)} className="rounded px-1.5 hover:bg-muted" title="up">↑</button>
                      <button onClick={() => move(i, 1)} className="rounded px-1.5 hover:bg-muted" title="down">↓</button>
                      <button onClick={() => remove(i)} className="rounded p-1 hover:bg-muted" title="remove"><X className="h-4 w-4" /></button>
                    </div>
                  </div>
                  <p className="text-xs text-muted-foreground mt-1">{EFFECT_HINT[eff]}</p>
                  {e.stage === "llm_synthesis" && (
                    <div className="mt-2 space-y-1 text-xs">
                      <label className="flex items-center gap-2">
                        <input type="checkbox" checked={!!e.config?.egress_ok}
                               onChange={(ev) => setCfg(i, "egress_ok", ev.target.checked)} />
                        allow egress (send context to an LLM)
                      </label>
                      <input className="w-full rounded border border-border bg-background px-2 py-1"
                             placeholder="model (default claude-haiku-4-5)"
                             value={e.config?.model || ""}
                             onChange={(ev) => setCfg(i, "model", ev.target.value)} />
                    </div>
                  )}
                  {e.stage === "toolforge" && (
                    <label className="mt-2 flex items-center gap-2 text-xs">
                      <input type="checkbox" checked={!!e.config?.allow_llm_impl}
                             onChange={(ev) => setCfg(i, "allow_llm_impl", ev.target.checked)} />
                      allow LLM-authored tool code (AST-checked; default: safe template only)
                    </label>
                  )}
                </div>
              );
            })}
          </div>
          {addable.length > 0 && (
            <div className="mb-4">
              <p className="text-xs text-muted-foreground mb-1">Add a stage:</p>
              <div className="flex flex-wrap gap-1.5">
                {addable.map((p) => (
                  <button key={p.id} onClick={() => add(p.id)}
                          className="rounded-md border border-border px-2 py-1 text-xs hover:border-accent-foreground/50">
                    + {p.id} <span className="opacity-60">({p.effect})</span>
                  </button>
                ))}
              </div>
            </div>
          )}
          {err && <p className="text-destructive text-xs mb-2">⚠ {err}</p>}
          <div className="flex gap-2">
            <button onClick={save} disabled={saving}
                    className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-foreground disabled:opacity-50">
              {saving ? "Saving…" : "Save pipeline"}
            </button>
            <button onClick={() => setCurrent(def.map((s) => ({ stage: s })))}
                    className="rounded-md border border-border px-4 py-2 text-sm">
              Reset to default
            </button>
          </div>
        </>
      )}
    </Modal>
  );
}

function TurnCard({ turn, onStage, onBrief, onInspect }: {
  turn: Turn; onStage: (s: Stage) => void; onBrief: () => void; onInspect: () => void;
}) {
  const when = turn.ts ? new Date(turn.ts * 1000).toLocaleString() : "";
  return (
    <Card className="mb-3">
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div className="min-w-0">
            <CardTitle className="text-sm truncate">{turn.turn_id}</CardTitle>
            <CardDescription className="text-xs">
              {when} · top score {turn.top_score ?? 0}
            </CardDescription>
          </div>
          <div className="flex items-center gap-2">
            {turn.degraded && <Badge variant="warn">degraded</Badge>}
            <button onClick={onInspect}
                    className="flex items-center gap-1 rounded-md border border-emerald-600/50 px-2 py-1 text-xs text-emerald-300 hover:border-emerald-400">
              <Layers className="h-3 w-3" /> Inspect
            </button>
            <button onClick={onBrief}
                    className="flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs hover:border-accent-foreground/50">
              <FileText className="h-3 w-3" /> Brief
            </button>
          </div>
        </div>
      </CardHeader>
      <div className="px-6 pb-4">
        {turn.stages.length === 0
          ? <p className="text-sm text-muted-foreground">No enrichment stages ran (plain context).</p>
          : <PipelineBar turn={turn} onStage={onStage} />}
      </div>
    </Card>
  );
}

export default function VibeEngineeringPage() {
  const q = useTraces(50);
  const data = q.data ?? null;
  const loading = q.isLoading;
  const error = q.error instanceof Error ? q.error.message : q.error ? String(q.error) : null;
  const [stage, setStage] = useState<Stage | null>(null);
  const [briefTurn, setBriefTurn] = useState<Turn | null>(null);
  const [inspectTurn, setInspectTurn] = useState<Turn | null>(null);
  const [showEditor, setShowEditor] = useState(false);

  if (loading && !data) {
    return <div className="flex items-center justify-center min-h-screen">
      <div className="flex flex-col items-center gap-4">
        <Loader2 className="h-8 w-8 animate-spin text-accent-foreground" />
        <p className="text-muted-foreground">Loading Context Pipeline…</p>
      </div></div>;
  }
  if (error) {
    return <div className="flex items-center justify-center min-h-screen">
      <div className="flex flex-col items-center gap-4 text-destructive">
        <AlertCircle className="h-8 w-8" /><p className="text-muted-foreground">Error: {error}</p>
      </div></div>;
  }

  const sessions = data?.sessions ?? [];
  const totalTurns = sessions.reduce((n, s) => n + s.turns.length, 0);

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <div className="flex items-center justify-between gap-3 mb-1">
        <div className="flex items-center gap-3">
          <Workflow className="h-6 w-6 text-accent-foreground" />
          <h1 className="text-2xl font-bold">Context Pipeline</h1>
        </div>
        <button onClick={() => setShowEditor(true)}
                className="rounded-md border border-border px-3 py-1.5 text-sm hover:border-accent-foreground/50">
          ⚙ Configure
        </button>
      </div>
      <p className="text-muted-foreground mb-6">
        Each turn as a compact pipeline: memory → graph → skill → approach →
        blocker. Click a stage to see its source graph; click Brief for the exact
        injected context + audit integrity.
      </p>

      {sessions.length === 0 ? (
        <Card><CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-accent-foreground" /> No context-engineered turns yet
          </CardTitle>
          <CardDescription>
            Turn on <span className="font-mono">vibe_engineering</span> under
            Settings → Features. Every enriched turn then appears here.
          </CardDescription>
        </CardHeader></Card>
      ) : (
        <>
          <p className="text-sm text-muted-foreground mb-4">
            {totalTurns} enriched turn{totalTurns === 1 ? "" : "s"} across{" "}
            {sessions.length} session{sessions.length === 1 ? "" : "s"}
          </p>
          {sessions.map((sg) => (
            <div key={sg.session} className="mb-8">
              <h2 className="text-sm font-semibold text-muted-foreground mb-3 font-mono">{sg.session}</h2>
              {sg.turns.map((t) => (
                <TurnCard key={t.turn_id + (t.ts ?? "")} turn={t}
                          onStage={setStage} onBrief={() => setBriefTurn(t)}
                          onInspect={() => setInspectTurn(t)} />
              ))}
            </div>
          ))}
        </>
      )}

      {stage && <StageModal stage={stage} onClose={() => setStage(null)} />}
      {briefTurn && <BriefModal turn={briefTurn} onClose={() => setBriefTurn(null)} />}
      {inspectTurn && <PromptInspectorModal turn={inspectTurn} onClose={() => setInspectTurn(null)} />}
      {showEditor && <PipelineEditorModal onClose={() => setShowEditor(false)}
                                          onSaved={() => window.location.reload()} />}
    </div>
  );
}

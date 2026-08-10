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
  Hash, FileText, ChevronRight,
} from "lucide-react";
import { Card, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

interface Source { id: string; score: number }
interface Stage {
  stage: string;
  status: string;
  duration_ms?: number | null;
  confidence_tier?: string;
  sources?: Source[];
  reason?: string;
}
interface Turn {
  turn_id: string;
  ts?: number;
  hash?: string;
  degraded?: string | null;
  top_score?: number;
  brief_sha256?: string | null;
  stages: Stage[];
}
interface SessionGroup { session: string; turns: Turn[] }
interface TracesResponse { tenant_id: string; sessions: SessionGroup[]; available: boolean }

const STAGE_META: Record<string, {
  icon: React.ComponentType<{ className?: string }>; label: string; short: string;
}> = {
  memory: { icon: Brain, label: "Memory Lookup", short: "Memory" },
  graph: { icon: Network, label: "Graph Traversal", short: "Graph" },
  skill: { icon: Sparkles, label: "Skill Injection", short: "Skill" },
  approach_synthesis: { icon: Workflow, label: "Approach Synthesis", short: "Approach" },
  blocker_id: { icon: AlertCircle, label: "Blocker ID", short: "Blocker" },
};
const ORDER = ["memory", "graph", "skill", "approach_synthesis", "blocker_id"];

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
  const meta = STAGE_META[stage.stage] ?? { icon: Workflow, label: stage.stage, short: stage.stage };
  const Icon = meta.icon;
  const n = stage.sources?.length ?? 0;
  const notRun = stage.status === "not_run";
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
                  strokeWidth={0.8 + p.s.score * 2.6} strokeOpacity={0.45} />
            <rect x={mx - 15} y={my - 9} width={30} height={16} rx={4}
                  className="fill-background" stroke={col} strokeOpacity={0.5} />
            <text x={mx} y={my + 3} textAnchor="middle"
                  className="fill-muted-foreground" fontSize={10}>
              {p.s.score.toFixed(2)}
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
      {(stage.sources?.length ?? 0) > 0 && (
        <div className="mt-4">
          <h3 className="text-sm font-semibold mb-2">Sources (relevance)</h3>
          <ul className="space-y-1">
            {stage.sources!.map((s, i) => (
              <li key={i} className="flex justify-between gap-2 text-sm">
                <span className="truncate font-mono text-xs" title={s.id}>{s.id}</span>
                <span className="tabular-nums text-muted-foreground">{s.score.toFixed(2)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Modal>
  );
}

function BriefModal({ turn, onClose }: { turn: Turn; onClose: () => void }) {
  const [brief, setBrief] = useState<string | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "erased" | "none">("loading");
  useEffect(() => {
    if (!turn.brief_sha256) { setState("none"); return; }
    let alive = true;
    fetch(`/v1/console/vibe-engineering/explain/${turn.brief_sha256}`)
      .then((r) => r.json())
      .then((d) => { if (!alive) return; d.found ? (setBrief(d.text), setState("ok")) : setState("erased"); })
      .catch(() => alive && setState("erased"));
    return () => { alive = false; };
  }, [turn.brief_sha256]);
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

/* ── turn card: header + one compact pipeline row ──────────────────────── */
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
  const [csrf, setCsrf] = useState("");

  useEffect(() => {
    let alive = true;
    fetch("/v1/console/vibe-engineering/pipeline").then((r) => r.json()).then((d) => {
      if (!alive) return;
      setCurrent(d.current || []); setPalette(d.palette || []);
      setDef(d.default || []); setLoading(false);
    }).catch(() => { if (alive) { setErr("failed to load pipeline"); setLoading(false); } });
    fetch("/v1/console/auth/whoami").then((r) => r.json())
      .then((d) => alive && setCsrf(d.csrf_token || "")).catch(() => {});
    return () => { alive = false; };
  }, []);

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
      const r = await fetch("/v1/console/vibe-engineering/pipeline", {
        method: "PUT",
        headers: { "content-type": "application/json", "x-csrf-token": csrf },
        body: JSON.stringify({ pipeline: current }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        const errs = d.detail?.errors || d.detail || d;
        throw new Error(Array.isArray(errs) ? errs.join("; ") : JSON.stringify(errs));
      }
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

function TurnCard({ turn, onStage, onBrief }: {
  turn: Turn; onStage: (s: Stage) => void; onBrief: () => void;
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
  const [data, setData] = useState<TracesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [stage, setStage] = useState<Stage | null>(null);
  const [briefTurn, setBriefTurn] = useState<Turn | null>(null);
  const [showEditor, setShowEditor] = useState(false);

  useEffect(() => {
    let alive = true;
    const run = async () => {
      try {
        const res = await fetch("/v1/console/vibe-engineering/traces?limit=50");
        if (!res.ok) throw new Error(`Failed to load (${res.status})`);
        const body = (await res.json()) as TracesResponse;
        if (alive) { setData(body); setError(null); }
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      } finally { if (alive) setLoading(false); }
    };
    run();
    const id = setInterval(run, 15000);
    return () => { alive = false; clearInterval(id); };
  }, []);

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
                          onStage={setStage} onBrief={() => setBriefTurn(t)} />
              ))}
            </div>
          ))}
        </>
      )}

      {stage && <StageModal stage={stage} onClose={() => setStage(null)} />}
      {briefTurn && <BriefModal turn={briefTurn} onClose={() => setBriefTurn(null)} />}
      {showEditor && <PipelineEditorModal onClose={() => setShowEditor(false)}
                                          onSaved={() => window.location.reload()} />}
    </div>
  );
}

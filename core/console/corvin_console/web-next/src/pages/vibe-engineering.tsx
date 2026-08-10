/**
 * Vibe Engineering — Context Pipeline (read-only observability, ADR-0275/0278).
 *
 * Reads the durable Layer-A Decision Record (per-turn: stage chain, per-source
 * scores, chain hash, brief_sha256). Click a turn to open a detail drawer with
 * the exact injected brief text (Layer B via /explain), the causal per-source
 * scores, and the audit hash + tamper-evidence.
 */
import { useEffect, useState } from "react";
import {
  Loader2, AlertCircle, Brain, Network, Sparkles, ArrowRight, Workflow,
  Clock, FileText, X, ShieldCheck, Hash,
} from "lucide-react";
import {
  Card, CardHeader, CardTitle, CardDescription, CardContent,
} from "@/components/ui/card";
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
  session_id?: string;
  ts?: number;
  hash?: string;
  prev_hash?: string;
  degraded?: string | null;
  top_score?: number;
  stages_ok?: number;
  brief_sha256?: string | null;
  brief_bytes?: number;
  stages: Stage[];
}
interface SessionGroup { session: string; turns: Turn[] }
interface TracesResponse {
  tenant_id: string;
  sessions: SessionGroup[];
  available: boolean;
}

const STAGE_META: Record<
  string, { icon: React.ComponentType<{ className?: string }>; label: string }
> = {
  memory: { icon: Brain, label: "Memory Lookup" },
  graph: { icon: Network, label: "Graph Traversal" },
  skill: { icon: Sparkles, label: "Skill Injection" },
  approach_synthesis: { icon: Workflow, label: "Approach Synthesis" },
  blocker_id: { icon: AlertCircle, label: "Blocker ID" },
};

function confidenceVariant(t?: string): "ok" | "warn" | "secondary" {
  if (t === "high") return "ok";
  if (t === "medium") return "warn";
  return "secondary";
}
function statusVariant(s: string): "ok" | "warn" | "danger" | "secondary" {
  if (s === "ok") return "ok";
  if (s === "not_run") return "secondary";
  return "danger";
}

function StageNode({ stage }: { stage: Stage }) {
  const meta = STAGE_META[stage.stage] ?? { icon: Workflow, label: stage.stage };
  const Icon = meta.icon;
  const notRun = stage.status === "not_run";
  const failed = stage.status !== "ok" && !notRun;
  const sources = stage.sources ?? [];
  return (
    <div
      className={
        "flex-1 min-w-[150px] rounded-lg border p-3 " +
        (failed ? "border-destructive/40 bg-destructive/5"
          : notRun ? "border-border bg-muted/30 opacity-70"
          : "border-border bg-card")
      }
    >
      <div className="flex items-center gap-2 mb-2">
        <Icon className={"h-4 w-4 " + (failed ? "text-destructive" : "text-accent-foreground")} />
        <span className="text-sm font-medium">{meta.label}</span>
      </div>
      <div className="flex flex-wrap gap-1.5 mb-2">
        <Badge variant={statusVariant(stage.status)}>{stage.status}</Badge>
        {stage.confidence_tier && (
          <Badge variant={confidenceVariant(stage.confidence_tier)}>{stage.confidence_tier}</Badge>
        )}
      </div>
      {!notRun && (
        <div className="flex flex-col gap-1 text-xs text-muted-foreground">
          <span className="flex items-center gap-1">
            <FileText className="h-3 w-3" /> {sources.length} source{sources.length === 1 ? "" : "s"}
          </span>
          {sources.slice(0, 3).map((s, i) => (
            <div key={i} className="flex justify-between gap-2 ml-4">
              <span className="truncate" title={s.id}>{s.id}</span>
              <span className="tabular-nums opacity-70">{s.score.toFixed(2)}</span>
            </div>
          ))}
          {stage.duration_ms != null && (
            <span className="flex items-center gap-1">
              <Clock className="h-3 w-3" /> {Math.round(stage.duration_ms)} ms
            </span>
          )}
        </div>
      )}
      {stage.reason && notRun && (
        <p className="text-xs text-muted-foreground italic">{stage.reason}</p>
      )}
    </div>
  );
}

function StageChain({ stages }: { stages: Stage[] }) {
  const shown = stages.filter((s) => s.stage in STAGE_META);
  return (
    <div className="flex flex-col md:flex-row items-stretch gap-2 md:gap-1">
      {shown.map((s, i) => (
        <div key={i} className="flex items-center gap-2 md:gap-1 md:flex-1">
          <StageNode stage={s} />
          {i < shown.length - 1 && (
            <ArrowRight className="h-5 w-5 shrink-0 text-muted-foreground rotate-90 md:rotate-0" />
          )}
        </div>
      ))}
    </div>
  );
}

function TurnCard({ turn, onOpen }: { turn: Turn; onOpen: () => void }) {
  const when = turn.ts ? new Date(turn.ts * 1000).toLocaleString() : "";
  return (
    <Card className="mb-4 cursor-pointer transition-colors hover:border-accent-foreground/40"
          onClick={onOpen} role="button" tabIndex={0}
          onKeyDown={(e) => (e.key === "Enter" ? onOpen() : null)}>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div className="min-w-0">
            <CardTitle className="text-base truncate">{turn.turn_id}</CardTitle>
            <CardDescription className="text-xs">
              {when} · top score {turn.top_score ?? 0} · click for detail
            </CardDescription>
          </div>
          {turn.degraded && <Badge variant="warn">degraded: {turn.degraded}</Badge>}
        </div>
      </CardHeader>
      <CardContent>
        {turn.stages.length === 0
          ? <p className="text-sm text-muted-foreground">No enrichment stages ran (plain context).</p>
          : <StageChain stages={turn.stages} />}
      </CardContent>
    </Card>
  );
}

function DetailDrawer({ turn, onClose }: { turn: Turn; onClose: () => void }) {
  const [brief, setBrief] = useState<string | null>(null);
  const [briefState, setBriefState] = useState<"loading" | "ok" | "erased" | "none">("loading");

  useEffect(() => {
    if (!turn.brief_sha256) { setBriefState("none"); return; }
    let alive = true;
    fetch(`/v1/console/vibe-engineering/explain/${turn.brief_sha256}`)
      .then((r) => r.json())
      .then((d) => {
        if (!alive) return;
        if (d.found) { setBrief(d.text); setBriefState("ok"); }
        else setBriefState("erased");
      })
      .catch(() => alive && setBriefState("erased"));
    return () => { alive = false; };
  }, [turn.brief_sha256]);

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="fixed inset-0 bg-background/70 backdrop-blur-sm" />
      <div className="relative z-10 h-full w-full max-w-xl overflow-y-auto border-l border-border bg-card p-6 shadow-xl"
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between mb-4">
          <div>
            <h2 className="text-lg font-bold">{turn.turn_id}</h2>
            <p className="text-xs text-muted-foreground">
              {turn.ts ? new Date(turn.ts * 1000).toLocaleString() : ""}
            </p>
          </div>
          <button onClick={onClose} className="rounded-md p-1 hover:bg-muted" aria-label="Close">
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Integrity — hash-chained audit record (ADR-0278) */}
        <div className="mb-5 rounded-lg border border-border bg-muted/30 p-3">
          <div className="flex items-center gap-2 mb-1 text-sm font-medium">
            <ShieldCheck className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
            Audit record — hash-chained
          </div>
          <div className="flex items-center gap-1 text-xs text-muted-foreground font-mono break-all">
            <Hash className="h-3 w-3 shrink-0" /> {turn.hash ?? "—"}
          </div>
          {turn.brief_sha256 && (
            <div className="text-xs text-muted-foreground font-mono break-all mt-1">
              brief sha256: {turn.brief_sha256}
            </div>
          )}
        </div>

        {/* Injected brief — Layer B */}
        <h3 className="text-sm font-semibold mb-2">Injected context brief</h3>
        <div className="mb-5 rounded-lg border border-border bg-background p-3 text-sm">
          {briefState === "loading" && (
            <span className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> loading…
            </span>
          )}
          {briefState === "ok" && (
            <pre className="whitespace-pre-wrap font-mono text-xs leading-relaxed">{brief}</pre>
          )}
          {briefState === "erased" && (
            <span className="text-muted-foreground italic">
              Brief text was lawfully erased (GDPR Art. 17) — the hash in the audit
              record now resolves to nothing, which is itself honest evidence.
            </span>
          )}
          {briefState === "none" && (
            <span className="text-muted-foreground italic">
              No brief was produced this turn (plain context / degraded).
            </span>
          )}
        </div>

        {/* Full stage chain with per-source scores */}
        <h3 className="text-sm font-semibold mb-2">Pipeline stages</h3>
        <StageChain stages={turn.stages} />
      </div>
    </div>
  );
}

export default function VibeEngineeringPage() {
  const [data, setData] = useState<TracesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Turn | null>(null);

  useEffect(() => {
    let alive = true;
    const fetchData = async () => {
      try {
        const res = await fetch("/v1/console/vibe-engineering/traces?limit=50");
        if (!res.ok) throw new Error(`Failed to load (${res.status})`);
        const body = (await res.json()) as TracesResponse;
        if (alive) { setData(body); setError(null); }
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (alive) setLoading(false);
      }
    };
    fetchData();
    const id = setInterval(fetchData, 15000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  if (loading && !data) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="flex flex-col items-center gap-4">
          <Loader2 className="h-8 w-8 animate-spin text-accent-foreground" />
          <p className="text-muted-foreground">Loading Context Pipeline…</p>
        </div>
      </div>
    );
  }
  if (error) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="flex flex-col items-center gap-4 text-destructive">
          <AlertCircle className="h-8 w-8" />
          <p className="text-muted-foreground">Error: {error}</p>
        </div>
      </div>
    );
  }

  const sessions = data?.sessions ?? [];
  const totalTurns = sessions.reduce((n, s) => n + s.turns.length, 0);

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <div className="flex items-center gap-3 mb-1">
        <Workflow className="h-6 w-6 text-accent-foreground" />
        <h1 className="text-2xl font-bold">Context Pipeline</h1>
      </div>
      <p className="text-muted-foreground mb-6">
        How the Context Engineering Layer enriches each turn — memory → graph →
        skill — before your agent sees it. From the durable, hash-chained audit
        record. Click a turn for the injected brief + integrity detail.
      </p>

      {sessions.length === 0 ? (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Sparkles className="h-5 w-5 text-accent-foreground" />
              No context-engineered turns yet
            </CardTitle>
            <CardDescription>
              Turn on <span className="font-mono">vibe_engineering</span> under
              Settings → Features. Every enriched turn then appears here with its
              pipeline, per-source scores and audit record.
            </CardDescription>
          </CardHeader>
        </Card>
      ) : (
        <>
          <p className="text-sm text-muted-foreground mb-4">
            {totalTurns} enriched turn{totalTurns === 1 ? "" : "s"} across{" "}
            {sessions.length} session{sessions.length === 1 ? "" : "s"}
          </p>
          {sessions.map((sg) => (
            <div key={sg.session} className="mb-8">
              <h2 className="text-sm font-semibold text-muted-foreground mb-3 font-mono">
                {sg.session}
              </h2>
              {sg.turns.map((t) => (
                <TurnCard key={t.turn_id + (t.ts ?? "")} turn={t} onOpen={() => setSelected(t)} />
              ))}
            </div>
          ))}
        </>
      )}

      {selected && <DetailDrawer turn={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}

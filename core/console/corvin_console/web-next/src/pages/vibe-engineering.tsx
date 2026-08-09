/**
 * Vibe Engineering — Context Pipeline (read-only observability, ADR-0275).
 *
 * Renders the per-turn CEL traces persisted per session: for every context-
 * engineered turn, the memory -> graph -> skill stage chain with status,
 * confidence tier, sources and token cost. Empty-state doubles as onboarding
 * (turn the `vibe_engineering` flag on under Settings -> Features).
 */
import { useEffect, useState } from "react";
import {
  Loader2,
  AlertCircle,
  Brain,
  Network,
  Sparkles,
  ArrowRight,
  Workflow,
  Clock,
  FileText,
  Zap,
} from "lucide-react";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

interface Source {
  id: string;
  score: number;
}
interface Stage {
  stage: string;
  status: string;
  duration_ms?: number | null;
  confidence_tier?: string;
  sources?: Source[];
  tokens_in?: number | null;
  tokens_out?: number | null;
  error?: string;
}
interface TraceBody {
  task_preview?: string;
  stages: Stage[];
  degraded?: string;
}
interface TraceRec {
  turn_id: string;
  ts: number;
  trace: TraceBody;
}
interface SessionGroup {
  session: string;
  path: string;
  traces: TraceRec[];
}
interface TracesResponse {
  tenant_id: string;
  sessions: SessionGroup[];
  available: boolean;
}

const STAGE_META: Record<
  string,
  { icon: React.ComponentType<{ className?: string }>; label: string }
> = {
  memory: { icon: Brain, label: "Memory Lookup" },
  graph: { icon: Network, label: "Graph Traversal" },
  skill: { icon: Sparkles, label: "Skill Injection" },
};

function confidenceVariant(tier?: string): "ok" | "warn" | "secondary" {
  if (tier === "high") return "ok";
  if (tier === "medium") return "warn";
  return "secondary";
}

function StageNode({ stage }: { stage: Stage }) {
  const meta = STAGE_META[stage.stage] ?? { icon: Workflow, label: stage.stage };
  const Icon = meta.icon;
  const failed = stage.status !== "ok";
  const sources = stage.sources ?? [];
  const srcCount = sources.length;
  return (
    <div
      className={
        "flex-1 min-w-[150px] rounded-lg border p-3 " +
        (failed
          ? "border-destructive/40 bg-destructive/5"
          : "border-border bg-card")
      }
    >
      <div className="flex items-center gap-2 mb-2">
        <Icon
          className={
            "h-4 w-4 " + (failed ? "text-destructive" : "text-accent-foreground")
          }
        />
        <span className="text-sm font-medium">{meta.label}</span>
      </div>
      <div className="flex flex-wrap gap-1.5 mb-2">
        <Badge variant={failed ? "danger" : "ok"}>{stage.status}</Badge>
        {stage.confidence_tier && (
          <Badge variant={confidenceVariant(stage.confidence_tier)}>
            {stage.confidence_tier}
          </Badge>
        )}
      </div>
      <div className="flex flex-col gap-1 text-xs text-muted-foreground">
        <span className="flex items-center gap-1">
          <FileText className="h-3 w-3" /> {srcCount} source{srcCount === 1 ? "" : "s"}
        </span>
        {srcCount > 0 && (
          <ul className="ml-4 space-y-0.5">
            {sources.slice(0, 3).map((s, i) => (
              <li key={i} className="flex justify-between gap-2">
                <span className="truncate" title={`${s.id} — relevance ${s.score}`}>
                  {s.id}
                </span>
                <span className="tabular-nums opacity-70">{s.score.toFixed(2)}</span>
              </li>
            ))}
          </ul>
        )}
        {(stage.tokens_in != null || stage.tokens_out != null) && (
          <span className="flex items-center gap-1">
            <Zap className="h-3 w-3" /> {stage.tokens_in ?? 0} in / {stage.tokens_out ?? 0} out
          </span>
        )}
        {stage.duration_ms != null && (
          <span className="flex items-center gap-1">
            <Clock className="h-3 w-3" /> {Math.round(stage.duration_ms)} ms
          </span>
        )}
      </div>
      {stage.error && (
        <p className="mt-2 text-xs text-destructive truncate" title={stage.error}>
          {stage.error}
        </p>
      )}
    </div>
  );
}

function TraceCard({ rec }: { rec: TraceRec }) {
  const t = rec.trace;
  const when = rec.ts ? new Date(rec.ts * 1000).toLocaleString() : "";
  return (
    <Card className="mb-4">
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div className="min-w-0">
            <CardTitle className="text-base truncate">
              {t.task_preview || rec.turn_id}
            </CardTitle>
            <CardDescription className="text-xs">
              {rec.turn_id} · {when}
            </CardDescription>
          </div>
          {t.degraded && (
            <Badge variant="warn" title="Turn ran on plain context">
              degraded: {t.degraded}
            </Badge>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {t.stages.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No enrichment stages ran (plain context served).
          </p>
        ) : (
          <div className="flex flex-col md:flex-row items-stretch gap-2 md:gap-1">
            {t.stages.map((s, i) => (
              <div key={i} className="flex items-center gap-2 md:gap-1 md:flex-1">
                <StageNode stage={s} />
                {i < t.stages.length - 1 && (
                  <ArrowRight className="h-5 w-5 shrink-0 text-muted-foreground rotate-90 md:rotate-0" />
                )}
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default function VibeEngineeringPage() {
  const [data, setData] = useState<TracesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const fetchData = async () => {
      try {
        const res = await fetch("/v1/console/vibe-engineering/traces?limit=20");
        if (!res.ok) throw new Error(`Failed to load traces (${res.status})`);
        const body = (await res.json()) as TracesResponse;
        if (alive) {
          setData(body);
          setError(null);
        }
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (alive) setLoading(false);
      }
    };
    fetchData();
    const id = setInterval(fetchData, 15000);
    return () => {
      alive = false;
      clearInterval(id);
    };
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
  const totalTurns = sessions.reduce((n, s) => n + s.traces.length, 0);

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <div className="flex items-center gap-3 mb-1">
        <Workflow className="h-6 w-6 text-accent-foreground" />
        <h1 className="text-2xl font-bold">Context Pipeline</h1>
      </div>
      <p className="text-muted-foreground mb-6">
        How the Context Engineering Layer enriches each turn — memory → graph →
        skill — before your agent ever sees it. Read-only, updates every 15 s.
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
              Settings → Features. From then on, every enriched turn shows its
              pipeline here — which memories, decisions and skills shaped the
              context, and what each stage cost.
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
            <div key={sg.path} className="mb-8">
              <h2 className="text-sm font-semibold text-muted-foreground mb-3 font-mono">
                {sg.session}
              </h2>
              {sg.traces.map((rec) => (
                <TraceCard key={rec.turn_id + rec.ts} rec={rec} />
              ))}
            </div>
          ))}
        </>
      )}
    </div>
  );
}

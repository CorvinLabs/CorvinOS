/**
 * Vibe Engineering — Overview (G2, replaces the redundant Vibe Inspector).
 *
 * The Vibe Inspector was a read-only subset of the Context Pipeline page (same
 * /traces data), adding only aggregate counters. G2 removes it and moves that unique
 * value here, plus a mental-model explainer of the CEL flow so a new operator can
 * understand "what happens to my turn" before drilling into the trace.
 */
import { useTraces } from "@/adapters/vibe";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { ArrowRight, Workflow, Boxes, Network } from "lucide-react";
import { Link } from "react-router-dom";

const STAGES = [
  { id: "memory", label: "Memory", note: "Was weiß ich schon über dich/das Projekt?" },
  { id: "graph", label: "Graph", note: "Verknüpfte ADRs & Entscheidungen." },
  { id: "skill", label: "Skill", note: "Passende gelernte Fertigkeiten." },
  { id: "approach_synthesis", label: "Approach", note: "Lösungsweg skizzieren." },
  { id: "blocker_id", label: "Blocker", note: "Was könnte schiefgehen?" },
  { id: "llm_synthesis", label: "LLM-Synth", note: "Aktiv: LLM verdichtet (opt-in)." },
  { id: "toolforge", label: "ToolForge", note: "Aktiv: Werkzeug bauen (opt-in)." },
  { id: "skillforge", label: "SkillForge", note: "Aktiv: Skill lernen (opt-in)." },
];

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-border bg-background/50 p-4">
      <div className="text-2xl font-semibold tabular-nums">{value}</div>
      <div className="text-xs text-muted-foreground">{label}</div>
    </div>
  );
}

export default function VibeOverviewPage() {
  const { data } = useTraces(200);
  const sessions = data?.sessions ?? [];
  const turns = sessions.flatMap((s) => s.turns ?? []);
  const avg = turns.length
    ? turns.reduce((a, t) => a + (t.top_score ?? 0), 0) / turns.length
    : 0;
  const degraded = turns.filter((t) => t.degraded).length;

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-1">
      <div>
        <h1 className="text-xl font-semibold">Vibe Engineering — Überblick</h1>
        <p className="text-sm text-muted-foreground">
          Wie dein Turn zum Kontext für die Worker-Engine wird — und was das System dabei lernt.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Turns erfasst" value={turns.length} />
        <Stat label="Sessions" value={sessions.length} />
        <Stat label="⌀ Top-Score" value={avg.toFixed(2)} />
        <Stat label="Degradiert" value={degraded} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Der Kontext-Fluss (CEL)</CardTitle>
          <CardDescription>
            Jeder Turn läuft durch die Context Engineering Layer, bevor er die Engine erreicht.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-stretch gap-2">
            {STAGES.map((s, i) => (
              <div key={s.id} className="flex items-center gap-2">
                <div className="w-28 rounded-lg border border-border bg-background/50 p-2">
                  <div className="text-xs font-semibold">{s.label}</div>
                  <div className="mt-0.5 text-[10px] leading-tight text-muted-foreground">{s.note}</div>
                </div>
                {i < STAGES.length - 1 && <ArrowRight className="h-3 w-3 shrink-0 text-muted-foreground" />}
              </div>
            ))}
          </div>
          <div className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
            <ArrowRight className="h-3 w-3" /> Assembly → <span className="font-medium text-foreground">Worker-Engine</span> → Outcome → Learning
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-3 sm:grid-cols-3">
        <Link to="/app/vibe-engineering" className="rounded-lg border border-border p-4 transition hover:border-primary/50">
          <Workflow className="mb-2 h-5 w-5 text-primary" />
          <div className="text-sm font-semibold">Context Pipeline</div>
          <div className="text-xs text-muted-foreground">Pro Turn: Stages, Glass-Box-Prompt, Grades.</div>
        </Link>
        <Link to="/app/learning" className="rounded-lg border border-border p-4 transition hover:border-primary/50">
          <Boxes className="mb-2 h-5 w-5 text-primary" />
          <div className="text-sm font-semibold">TreeOfThoughts</div>
          <div className="text-xs text-muted-foreground">Gelernte Muster & Confidence.</div>
        </Link>
        <Link to="/app/multi-instance" className="rounded-lg border border-border p-4 transition hover:border-primary/50">
          <Network className="mb-2 h-5 w-5 text-primary" />
          <div className="text-sm font-semibold">Cross-Device Learning</div>
          <div className="text-xs text-muted-foreground">Geteilter Lernzustand über Geräte.</div>
        </Link>
      </div>
    </div>
  );
}

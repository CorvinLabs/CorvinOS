/**
 * Session Explorer — task history across the tenant's context-engineered turns.
 *
 * Source is the durable Decision Record (GET /traces, Layer A of ADR-0278):
 * hash-chained, nothing ages out, one entry per turn with its per-stage result.
 * Drill-down uses the same Layer-B endpoints the old Context Pipeline page
 * used — /explain/{brief_sha256} for the injected brief, /prompt/{turn} for the
 * assembled sections and /forged/{turn} for tools + skills forged in that turn —
 * so no reachable functionality was lost when that page was retired.
 */
import { useMemo, useState } from "react";
import {
  Loader2, AlertCircle, History, ChevronRight, ChevronDown, Hash,
  FileText, Wrench, ShieldCheck,
} from "lucide-react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  useTraces, useBrief, useAssembly, useForged, type Turn,
} from "@/adapters/vibe";

type Selection = { session: string; turn: Turn } | null;

function fmtTs(ts?: number): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString();
}

function turnBadge(t: Turn) {
  const failed = t.stages.filter((s) => s.status === "failed").length;
  if (t.degraded) return <Badge variant="danger" className="text-xs">degraded</Badge>;
  if (failed > 0) return <Badge variant="danger" className="text-xs">{failed} failed</Badge>;
  return <Badge variant="ok" className="text-xs">ok</Badge>;
}

function TurnDetail({ session, turn }: { session: string; turn: Turn }) {
  const [tab, setTab] = useState<"brief" | "prompt" | "forged">("brief");
  const brief = useBrief(tab === "brief" ? turn.brief_sha256 : null);
  const asm = useAssembly(tab === "prompt" ? turn.turn_id : null, session);
  const forged = useForged(tab === "forged" ? turn.turn_id : null, session);

  return (
    <div className="space-y-3 rounded-lg border border-border bg-muted/30 p-3">
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
        <span className="font-mono text-foreground">{turn.turn_id}</span>
        <span>{fmtTs(turn.ts)}</span>
        {turn.top_score !== undefined && (
          <span>top score <span className="font-mono">{turn.top_score.toFixed(2)}</span></span>
        )}
        {turn.hash && (
          <span className="flex items-center gap-1">
            <Hash className="h-3 w-3" />
            <code className="text-[10px]">{turn.hash.slice(0, 16)}…</code>
          </span>
        )}
      </div>

      <div className="flex flex-wrap gap-1">
        {turn.stages.map((s, i) => (
          <Badge
            key={`${s.stage}-${i}`}
            variant={s.status === "ok" ? "ok" : s.status === "not_run" ? "secondary" : "danger"}
            className="text-xs"
            title={`${s.status}${s.duration_ms != null ? ` · ${s.duration_ms}ms` : ""}${s.reason ? ` · ${s.reason}` : ""}`}
          >
            {s.stage}
            {s.sources?.length ? ` (${s.sources.length})` : ""}
          </Badge>
        ))}
      </div>

      <div className="flex gap-1 border-b border-border">
        {([
          ["brief", "Injected brief", ShieldCheck],
          ["prompt", "Assembly", FileText],
          ["forged", "Forged", Wrench],
        ] as const).map(([id, label, Icon]) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`flex items-center gap-1 px-2 py-1 text-xs transition-colors ${
              tab === id
                ? "border-b-2 border-primary text-primary"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            <Icon className="h-3 w-3" /> {label}
          </button>
        ))}
      </div>

      {tab === "brief" && (
        <div className="text-xs">
          {!turn.brief_sha256 && (
            <p className="italic text-muted-foreground">This turn recorded no brief.</p>
          )}
          {brief.isLoading && <Loader2 className="h-4 w-4 animate-spin" />}
          {brief.data?.found && (
            <pre className="max-h-72 overflow-auto whitespace-pre-wrap font-mono leading-relaxed">
              {brief.data.text}
            </pre>
          )}
          {brief.data && !brief.data.found && (
            <p className="italic text-muted-foreground">
              Brief unavailable{brief.data.reason ? `: ${brief.data.reason}` : " (lawfully erased)"}.
            </p>
          )}
        </div>
      )}

      {tab === "prompt" && (
        <div className="text-xs">
          {asm.isLoading && <Loader2 className="h-4 w-4 animate-spin" />}
          {asm.data?.found ? (
            <div className="space-y-2">
              {(asm.data.sections ?? []).map((s, i) => (
                <div key={i}>
                  <p className="font-medium">{s.label} <span className="text-muted-foreground">({s.kind})</span></p>
                  {s.text && (
                    <pre className="max-h-40 overflow-auto whitespace-pre-wrap font-mono text-[11px]">{s.text}</pre>
                  )}
                  {s.items && (
                    <ul className="list-inside list-disc text-[11px] text-muted-foreground">
                      {s.items.map((it, j) => <li key={j}>{it}</li>)}
                    </ul>
                  )}
                </div>
              ))}
            </div>
          ) : asm.data ? (
            <p className="italic text-muted-foreground">
              No assembly recorded{asm.data.reason ? `: ${asm.data.reason}` : ""}.
            </p>
          ) : null}
        </div>
      )}

      {tab === "forged" && (
        <div className="space-y-2 text-xs">
          {forged.isLoading && <Loader2 className="h-4 w-4 animate-spin" />}
          {forged.data?.found ? (
            <>
              {(forged.data.tools ?? []).length === 0 && (forged.data.skills ?? []).length === 0 && (
                <p className="italic text-muted-foreground">Nothing forged in this turn.</p>
              )}
              {(forged.data.tools ?? []).map((t) => (
                <div key={t.name} className="rounded border border-border p-2">
                  <p className="font-mono font-medium">{t.name}</p>
                  {t.description && <p className="text-muted-foreground">{t.description}</p>}
                  <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap font-mono text-[10px]">{t.code}</pre>
                </div>
              ))}
              {(forged.data.skills ?? []).map((s) => (
                <div key={s.skill_id} className="rounded border border-border p-2">
                  <p className="font-mono font-medium">{s.skill_id}</p>
                  <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap text-[10px]">{s.body}</pre>
                </div>
              ))}
            </>
          ) : forged.data ? (
            <p className="italic text-muted-foreground">Nothing forged in this turn.</p>
          ) : null}
        </div>
      )}
    </div>
  );
}

export function SessionExplorer() {
  const traces = useTraces(200);
  const [open, setOpen] = useState<string | null>(null);
  const [sel, setSel] = useState<Selection>(null);

  const sessions = traces.data?.sessions ?? [];
  const totals = useMemo(() => {
    const turns = sessions.flatMap((s) => s.turns);
    return {
      sessions: sessions.length,
      turns: turns.length,
      degraded: turns.filter((t) => t.degraded).length,
    };
  }, [sessions]);

  if (traces.isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (traces.isError) {
    return (
      <div className="flex items-start gap-2 rounded-lg border border-red-500/50 bg-red-500/5 p-4">
        <AlertCircle className="mt-0.5 h-4 w-4 text-red-500" />
        <p className="text-sm text-red-700 dark:text-red-400">
          Traces unavailable: {String(traces.error)}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6" data-testid="session-explorer">
      <header className="space-y-1">
        <h1 className="flex items-center gap-2 text-xl font-semibold">
          <History className="h-5 w-5" /> Session Explorer
        </h1>
        <p className="text-sm text-muted-foreground">
          {totals.sessions} session{totals.sessions === 1 ? "" : "s"} ·{" "}
          {totals.turns} recorded turn{totals.turns === 1 ? "" : "s"} ·{" "}
          {totals.degraded} degraded. Hash-chained, nothing ages out.
        </p>
      </header>

      {sessions.length === 0 && (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            No context-engineered turn has been recorded yet for this tenant.
          </CardContent>
        </Card>
      )}

      {sessions.map((s) => {
        const isOpen = open === s.session;
        const degraded = s.turns.filter((t) => t.degraded).length;
        return (
          <Card key={s.session}>
            <CardHeader
              className="cursor-pointer pb-3 hover:bg-muted/40"
              onClick={() => setOpen(isOpen ? null : s.session)}
            >
              <div className="flex items-center justify-between gap-2">
                <CardTitle className="flex items-center gap-2 text-sm">
                  {isOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                  <span className="font-mono">{s.session}</span>
                </CardTitle>
                <div className="flex items-center gap-2">
                  <Badge variant="secondary" className="text-xs">{s.turns.length} turns</Badge>
                  {degraded > 0 && <Badge variant="danger" className="text-xs">{degraded} degraded</Badge>}
                  <span className="text-xs text-muted-foreground">{fmtTs(s.turns[0]?.ts)}</span>
                </div>
              </div>
            </CardHeader>
            {isOpen && (
              <CardContent className="space-y-2">
                {s.turns.map((t) => {
                  const selected = sel?.turn.turn_id === t.turn_id && sel?.session === s.session;
                  return (
                    <div key={`${s.session}-${t.turn_id}`} className="space-y-2">
                      <Button
                        variant={selected ? "outline" : "ghost"}
                        className="h-auto w-full justify-between px-2 py-1.5 text-xs"
                        onClick={() => setSel(selected ? null : { session: s.session, turn: t })}
                      >
                        <span className="flex items-center gap-2">
                          <span className="font-mono">{t.turn_id}</span>
                          <span className="text-muted-foreground">{fmtTs(t.ts)}</span>
                        </span>
                        <span className="flex items-center gap-2">
                          <span className="text-muted-foreground">{t.stages.length} stages</span>
                          {turnBadge(t)}
                        </span>
                      </Button>
                      {selected && <TurnDetail session={s.session} turn={t} />}
                    </div>
                  );
                })}
              </CardContent>
            )}
          </Card>
        );
      })}
    </div>
  );
}

export default SessionExplorer;

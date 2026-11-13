/**
 * Collapsible cost / token-efficiency dashboard for one chat (ADR-2324).
 *
 * Closed by default and fetches nothing while closed. Opening it loads
 * `GET /chat/sessions/{sid}/efficiency`, which derives every figure from the
 * persisted turn log; it reloads when a reply finishes streaming so the numbers
 * follow the conversation. Missing figures render as an em dash with the reason
 * in the notes - never as 0.
 */
import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Gauge, Loader2 } from "lucide-react";
import { getChatEfficiency, type ChatEfficiency } from "@/lib/api/chat";
import { buildNotes, buildTiles, costBars, fmtCompact, fmtInt, fmtUsd } from "@/lib/chat-efficiency";

export interface ChatEfficiencyPanelProps {
  sid: string;
  /** True while a reply is streaming; the panel refreshes on the falling edge. */
  streaming: boolean;
}

const queryKey = (sid: string) => ["chat-efficiency", sid] as const;

export function ChatEfficiencyPanel({ sid, streaming }: ChatEfficiencyPanelProps) {
  const [open, setOpen] = React.useState(false);
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: queryKey(sid),
    queryFn: ({ signal }) => getChatEfficiency(sid, signal),
    enabled: open,
    staleTime: 0,
    retry: 0,
  });

  // Reload once when a reply ends - its usage is persisted just before `done`.
  const wasStreaming = React.useRef(streaming);
  React.useEffect(() => {
    if (wasStreaming.current && !streaming && open) {
      void qc.invalidateQueries({ queryKey: queryKey(sid) });
    }
    wasStreaming.current = streaming;
  }, [streaming, open, sid, qc]);

  const data = q.data;
  const bodyId = `chat-efficiency-body-${sid}`;

  return (
    <section
      className="border-b border-border/60 bg-background/60 px-4 md:px-6"
      data-testid="chat-efficiency"
      aria-label="Cost and efficiency"
    >
      <div className="mx-auto w-full max-w-4xl">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls={bodyId}
          data-testid="chat-efficiency-toggle"
          className="flex w-full items-center gap-2 py-1.5 text-xs text-muted-foreground hover:text-foreground"
        >
          {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          <Gauge className="h-3.5 w-3.5" />
          <span className="font-medium">Cost &amp; efficiency</span>
          {open && data && data.coverage.turns > 0 && (
            <span className="ml-auto tabular-nums" data-testid="chat-efficiency-summary">
              {fmtUsd(data.cost.total_usd)} · {fmtCompact(data.tokens.output)} output tokens
            </span>
          )}
        </button>

        {open && (
          <div id={bodyId} className="max-h-[40vh] space-y-3 overflow-y-auto pb-3" data-testid="chat-efficiency-body">
            {q.isLoading && (
              <p className="flex items-center gap-2 text-xs text-muted-foreground" role="status">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading cost figures…
              </p>
            )}
            {q.isError && (
              <div className="flex items-center gap-3 text-xs" role="alert" data-testid="chat-efficiency-error">
                <span className="text-destructive">Cost figures could not be loaded.</span>
                <button type="button" className="underline" onClick={() => void q.refetch()}>Retry</button>
              </div>
            )}
            {data && data.coverage.turns === 0 && (
              <p className="text-xs text-muted-foreground" data-testid="chat-efficiency-empty">
                No replies yet - cost figures appear after the first answer.
              </p>
            )}
            {data && data.coverage.turns > 0 && <Dashboard data={data} />}
          </div>
        )}
      </div>
    </section>
  );
}

function Dashboard({ data }: { data: ChatEfficiency }) {
  const tiles = buildTiles(data);
  const notes = buildNotes(data);
  const bars = costBars(data);
  const t = data.tokens;
  return (
    <>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4" data-testid="chat-efficiency-tiles">
        {tiles.map((tile) => (
          <div
            key={tile.id}
            title={tile.help}
            data-testid={`eff-${tile.id}`}
            className="rounded border border-border/60 bg-muted/30 px-3 py-2"
          >
            <div className="text-[11px] leading-tight text-muted-foreground">{tile.label}</div>
            <div className="text-base font-semibold tabular-nums" data-testid={`eff-${tile.id}-value`}>{tile.value}</div>
            {tile.sub && <div className="text-[11px] text-muted-foreground tabular-nums">{tile.sub}</div>}
          </div>
        ))}
      </div>

      <div className="text-[11px] text-muted-foreground tabular-nums" data-testid="chat-efficiency-tokens">
        Tokens: {fmtInt(t.input)} input · {fmtInt(t.output)} output · {fmtInt(t.cache_read)} cache read · {fmtInt(t.cache_write)} cache write
      </div>

      {bars.length > 0 && (
        <figure className="space-y-1" data-testid="chat-efficiency-chart">
          <figcaption className="text-[11px] text-muted-foreground">
            Cost per turn (last {bars.length} priced turn{bars.length === 1 ? "" : "s"}, newest right)
          </figcaption>
          <div className="flex h-12 items-end gap-0.5" role="img"
            aria-label={`Cost per turn, ${bars.length} turns, highest ${fmtUsd(Math.max(...bars.map((b) => b.cost)))}`}>
            {bars.map((b, i) => (
              <div
                key={`${b.ts ?? "x"}-${i}`}
                className="w-2 shrink-0 rounded-sm"
                style={{ height: b.height, background: "var(--viz-role-os)" }}
                title={`${fmtUsd(b.cost)}${b.model ? ` · ${b.model}` : ""}`}
              />
            ))}
          </div>
        </figure>
      )}

      {data.models.length > 1 && (
        <table className="w-full text-[11px]" data-testid="chat-efficiency-models">
          <thead className="text-left text-muted-foreground">
            <tr><th className="font-normal">Model</th><th className="text-right font-normal">Turns</th><th className="text-right font-normal">Cost</th></tr>
          </thead>
          <tbody className="tabular-nums">
            {data.models.map((m) => (
              <tr key={m.model}>
                <td className="truncate pr-2">{m.model}</td>
                <td className="text-right">{fmtInt(m.turns)}</td>
                <td className="text-right">{fmtUsd(m.cost_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {notes.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-4 text-[11px] text-muted-foreground" data-testid="chat-efficiency-notes">
          {notes.map((n) => <li key={n}>{n}</li>)}
        </ul>
      )}
      <p className="text-[11px] text-muted-foreground/80">{data.price_basis}</p>
    </>
  );
}

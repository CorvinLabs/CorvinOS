/**
 * DualTrackAuditPanel — ADR-0118 Dual-Track Audit Chain View.
 *
 * Renders OS and Worker audit events as two vertical swimlanes
 * connected by delegation-bridge arrows. Data is reconstructed
 * client-side from audit.jsonl via the /chain-dual-track endpoint.
 */
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import {
  getChainDualTrack,
  type ChainAuditEvent,
  type ChainDelegationGroup,
  type ChainDualTrackPayload,
} from "@/lib/api";
import { AlertCircle, GitBranch, Loader2, Shield, ShieldCheck, ShieldAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

// ── colour helpers ─────────────────────────────────────────────────────────

const EVENT_COLOR: Record<string, string> = {
  // OS turn lifecycle
  "os_turn.started":    "indigo",
  "os_turn.tool_called": "cyan",
  "os_turn.completed":  "indigo",
  "os_turn.error":      "red",
  "delegation.started": "orange",
  "delegation.ended":   "orange",
  "delegation.error":   "red",
  // Bridge / A2A OS-side
  "A2A.envelope_sent":        "orange",
  "A2A.response_received":    "orange",
  // Worker execution
  "A2A.envelope_received":    "orange",
  "A2A.engine_spawned":       "emerald",
  "A2A.result_filtered":      "emerald",
  "A2A.response_signed":      "orange",
  "A2A.request_rejected":     "red",
  // NBAC security
  "A2A.chain_dna_verified":    "amber",
  "A2A.chain_dna_mismatch":    "red",
  "A2A.chain_dna_genesis_absent": "yellow",
  "chain.genesis":             "amber",
};

const COLOR_CLASSES: Record<string, { border: string; bg: string; text: string; dot: string }> = {
  indigo: {
    border: "border-indigo-400/70 dark:border-indigo-500/70",
    bg:     "bg-indigo-50/70 dark:bg-indigo-950/60",
    text:   "text-indigo-700 dark:text-indigo-300",
    dot:    "bg-indigo-100 border-indigo-400 dark:bg-indigo-950 dark:border-indigo-500",
  },
  emerald: {
    border: "border-emerald-400/70 dark:border-emerald-500/70",
    bg:     "bg-emerald-50/70 dark:bg-emerald-950/60",
    text:   "text-emerald-700 dark:text-emerald-300",
    dot:    "bg-emerald-100 border-emerald-400 dark:bg-emerald-950 dark:border-emerald-500",
  },
  orange: {
    border: "border-orange-400/80 dark:border-orange-500/80",
    bg:     "bg-orange-50/70 dark:bg-orange-950/50",
    text:   "text-orange-700 dark:text-orange-300",
    dot:    "bg-orange-100 border-orange-400 dark:bg-orange-950 dark:border-orange-500",
  },
  amber: {
    border: "border-amber-400/70 dark:border-amber-500/70",
    bg:     "bg-amber-50/70 dark:bg-amber-950/50",
    text:   "text-amber-700 dark:text-amber-300",
    dot:    "bg-amber-100 border-amber-400 dark:bg-amber-950 dark:border-amber-500",
  },
  red: {
    border: "border-red-400/70 dark:border-red-500/70",
    bg:     "bg-red-50/70 dark:bg-red-950/50",
    text:   "text-red-700 dark:text-red-300",
    dot:    "bg-red-100 border-red-400 dark:bg-red-950 dark:border-red-500",
  },
  yellow: {
    border: "border-yellow-400/70 dark:border-yellow-500/70",
    bg:     "bg-yellow-50/70 dark:bg-yellow-950/50",
    text:   "text-yellow-700 dark:text-yellow-300",
    dot:    "bg-yellow-100 border-yellow-400 dark:bg-yellow-950 dark:border-yellow-500",
  },
  cyan: {
    border: "border-cyan-400/60 dark:border-cyan-500/60",
    bg:     "bg-cyan-50/60 dark:bg-cyan-950/40",
    text:   "text-cyan-700 dark:text-cyan-300",
    dot:    "bg-cyan-100 border-cyan-400 dark:bg-cyan-950 dark:border-cyan-500",
  },
  default: {
    border: "border-slate-300 dark:border-slate-600",
    bg:     "bg-slate-50 dark:bg-slate-900",
    text:   "text-slate-600 dark:text-slate-400",
    dot:    "bg-slate-100 border-slate-400 dark:bg-slate-900 dark:border-slate-500",
  },
};

function eventColors(eventType: string) {
  const key = EVENT_COLOR[eventType] ?? "default";
  return COLOR_CLASSES[key] ?? COLOR_CLASSES.default;
}

// ── sub-components ─────────────────────────────────────────────────────────

interface EventBlockProps {
  ev: ChainAuditEvent;
  side: "os" | "worker";
  last?: boolean;
}

function EventBlock({ ev, last }: EventBlockProps) {
  const c = eventColors(ev.event_type);
  const [expanded, setExpanded] = React.useState(false);
  const hasDetails = Object.keys(ev.details).length > 0;

  return (
    <div className="flex gap-1.5 min-h-0">
      {/* chain link column */}
      <div className="flex flex-col items-center w-3 flex-shrink-0 pt-1">
        <div className={cn("w-2.5 h-2.5 rounded-full border-2 flex-shrink-0", c.dot)} />
        {!last && <div className="w-px flex-1 bg-slate-200 dark:bg-slate-700 mt-0.5" />}
      </div>
      {/* event card */}
      <div
        className={cn(
          "flex-1 rounded border px-2 py-1 mb-1 min-w-0 cursor-pointer select-none",
          c.border, c.bg,
        )}
        onClick={() => hasDetails && setExpanded(v => !v)}
      >
        <div className="flex items-center gap-1.5 min-w-0">
          <span className="font-mono text-[9px] text-cyan-600 flex-shrink-0">
            {ev.hash_prefix}…
          </span>
          <span className={cn("font-semibold text-[10px] truncate", c.text)}>
            {ev.event_type}
          </span>
          {ev.severity !== "INFO" && (
            <Badge variant="outline" className={cn(
              "text-[8px] px-1 py-0 flex-shrink-0 ml-auto",
              ev.severity === "CRITICAL" ? "border-red-500 text-red-400" : "border-yellow-500 text-yellow-400"
            )}>
              {ev.severity}
            </Badge>
          )}
        </div>
        {/* key detail inline — surface the most relevant fields per event so the
            row is self-describing without expanding. Tool calls in particular
            were rendering blank because tool_name/seq were never shown. */}
        {(() => {
          const d = ev.details as Record<string, unknown>;
          const bits: React.ReactNode[] = [];
          if (d.tool_name != null) {
            bits.push(
              <span key="tool" className="mr-2 font-medium">
                {d.seq != null && <span className="opacity-50">{String(d.seq)}. </span>}
                {String(d.tool_name)}
              </span>,
            );
          }
          if (d.engine_id != null) bits.push(<span key="eng" className="mr-2">engine: {String(d.engine_id)}</span>);
          if (d.engine != null && d.engine_id == null) bits.push(<span key="eng2" className="mr-2">engine: {String(d.engine)}</span>);
          if (d.persona != null && d.tool_name == null) bits.push(<span key="pers" className="mr-2">{String(d.persona)}</span>);
          if (d.status != null) bits.push(<span key="st" className="mr-2">status: {String(d.status)}</span>);
          if (d.tools_called != null) bits.push(<span key="tc" className="mr-2">{String(d.tools_called)} tools</span>);
          if (d.exit_code != null) bits.push(<span key="rc" className="mr-2">rc {String(d.exit_code)}</span>);
          if (d.filter_pass_count != null || d.filter_reject_count != null)
            bits.push(<span key="filt" className="mr-2">filter {String(d.filter_pass_count ?? 0)}✓/{String(d.filter_reject_count ?? 0)}✗</span>);
          if (d.duration_ms != null) bits.push(<span key="dur">{Number(d.duration_ms).toFixed(0)}ms</span>);
          if (bits.length === 0) return null;
          return <div className="text-[9px] text-muted-foreground mt-0.5 truncate">{bits}</div>;
        })()}
        {/* expanded details */}
        {expanded && hasDetails && (
          <pre className="mt-1 text-[8px] text-muted-foreground whitespace-pre-wrap break-all">
            {JSON.stringify(ev.details, null, 2)}
          </pre>
        )}
      </div>
    </div>
  );
}

// ── delegation group ───────────────────────────────────────────────────────

interface DelegationRowProps {
  grp: ChainDelegationGroup;
  index: number;
  chainVerified: boolean;
}

function DelegationRow({ grp, index, chainVerified }: DelegationRowProps) {
  // Audit trails must read as complete: only collapse genuinely long worker
  // lanes, and still show a generous prefix (8) rather than a token 2.
  const COLLAPSE_OVER = 12;
  const COLLAPSED_SHOWN = 8;
  const [collapsed, setCollapsed] = React.useState(grp.worker_events.length > COLLAPSE_OVER);

  const workerEventsShown = collapsed ? grp.worker_events.slice(0, COLLAPSED_SHOWN) : grp.worker_events;

  const NbacIcon = grp.genesis_match === true
    ? ShieldCheck
    : grp.genesis_match === false
    ? ShieldAlert
    : Shield;

  const nbacClass = grp.genesis_match === true
    ? "text-amber-600 dark:text-amber-400"
    : grp.genesis_match === false
    ? "text-red-500 dark:text-red-400"
    : "text-muted-foreground/50";

  return (
    <div className="mb-4">
      {/* delegation header */}
      <div className="flex items-center gap-2 mb-2 px-1">
        <div className="w-4 h-px bg-border dark:bg-slate-700" />
        <span className="text-[9px] text-muted-foreground font-mono flex-shrink-0">
          #{index + 1}
        </span>
        <Badge variant="outline" className="text-[9px] px-1.5 py-0 border-orange-400 text-orange-600 dark:border-orange-700 dark:text-orange-400 font-mono flex-shrink-0">
          {grp.delegation_id.slice(0, 12)}…
        </Badge>
        <Badge variant="outline" className="text-[9px] px-1.5 py-0 border-emerald-400 text-emerald-600 dark:border-emerald-800 dark:text-emerald-500 flex-shrink-0">
          {grp.engine}
        </Badge>
        <NbacIcon size={11} className={cn("flex-shrink-0 ml-auto", nbacClass)} />
        {grp.genesis_match === true && (
          <span
            className="text-[8px] text-amber-600 flex-shrink-0"
            title={!chainVerified ? "Hash-chain verification failed — this reading is not trustworthy" : undefined}
          >
            chain DNA {chainVerified ? "✓" : "✓ (unverified chain)"}
          </span>
        )}
        {grp.genesis_match === false && (
          <span className="text-[8px] text-red-500 flex-shrink-0">chain DNA ✗</span>
        )}
        <div className="flex-1 h-px bg-border" />
      </div>

      {/* dual-lane grid */}
      <div className="grid gap-0" style={{ gridTemplateColumns: "1fr 60px 1fr" }}>
        {/* OS lane */}
        <div className="border-r border-border pr-2">
          <div className="text-[8px] font-semibold text-indigo-600 uppercase tracking-wider mb-1 pl-3">
            OS · ClaudeCodeEngine
          </div>
          {grp.os_events.length === 0 ? (
            <div className="text-[9px] text-muted-foreground/50 italic pl-3">no OS events</div>
          ) : (
            grp.os_events.map((ev, i) => (
              <EventBlock key={`${ev.ts}-${ev.event_type}-${i}`} ev={ev} side="os" last={i === grp.os_events.length - 1} />
            ))
          )}
        </div>

        {/* Bridge column */}
        <div className="flex flex-col items-center justify-around py-2 gap-2">
          {/* request arrow */}
          {grp.os_events.some(e => e.event_type === "A2A.envelope_sent") && (
            <div className="flex flex-col items-center gap-0.5">
              <div className="w-full h-px bg-orange-700/70" />
              <svg width="16" height="8" viewBox="0 0 16 8" className="fill-orange-600">
                <polygon points="8,0 16,4 8,8" />
              </svg>
              <span className="text-[7px] text-orange-700 rotate-90 whitespace-nowrap" style={{ writingMode: "vertical-rl" }}>
                →
              </span>
            </div>
          )}
          {/* response arrow */}
          {grp.os_events.some(e => e.event_type === "A2A.response_received") && (
            <div className="flex flex-col items-center gap-0.5">
              <svg width="16" height="8" viewBox="0 0 16 8" className="fill-orange-600">
                <polygon points="8,0 0,4 8,8" />
              </svg>
              <div className="w-full h-px bg-orange-700/70" />
            </div>
          )}
        </div>

        {/* Worker lane */}
        <div className="pl-2">
          <div className="text-[8px] font-semibold text-emerald-700 uppercase tracking-wider mb-1 pl-3">
            Worker · {grp.engine !== "unknown" ? grp.engine : "Engine"}
          </div>
          {grp.worker_events.length === 0 ? (
            <div className="text-[9px] text-muted-foreground/50 italic pl-3">no worker events</div>
          ) : (
            <>
              {workerEventsShown.map((ev, i) => (
                <EventBlock
                  key={`${ev.ts}-${ev.event_type}-${i}`}
                  ev={ev}
                  side="worker"
                  last={!collapsed && i === grp.worker_events.length - 1}
                />
              ))}
              {collapsed && grp.worker_events.length > 2 && (
                <button
                  onClick={() => setCollapsed(false)}
                  className="text-[9px] text-muted-foreground hover:text-foreground pl-4 mt-1"
                >
                  + {grp.worker_events.length - 2} more events…
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ── genesis badge ──────────────────────────────────────────────────────────

function GenesisBadge({ genesis }: { genesis: ChainDualTrackPayload["genesis"] }) {
  if (!genesis) return null;
  return (
    <div className="flex items-center gap-2 px-3 py-1.5 bg-amber-50/80 dark:bg-amber-950/40 border border-amber-300/60 dark:border-amber-700/40 rounded text-[9px]">
      <Shield size={11} className="text-amber-600 dark:text-amber-500 flex-shrink-0" />
      <span className="font-semibold text-amber-700 dark:text-amber-400">chain.genesis</span>
      <span className="text-muted-foreground font-mono">{genesis.hash_prefix}…</span>
      <span className="text-muted-foreground">·</span>
      <span className="text-amber-600">{genesis.network_id || "unknown"}</span>
      {genesis.instance_id && (
        <>
          <span className="text-muted-foreground">·</span>
          <span className="text-muted-foreground font-mono truncate max-w-[100px]">{genesis.instance_id}</span>
        </>
      )}
      <Badge variant="outline" className="ml-auto text-[8px] px-1 border-amber-400 dark:border-amber-700 text-amber-600">
        NBAC
      </Badge>
    </div>
  );
}

// ── main component ─────────────────────────────────────────────────────────

interface DualTrackAuditPanelProps {
  sid: string;
}

export function DualTrackAuditPanel({ sid }: DualTrackAuditPanelProps) {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey:  ["chain-dual-track", sid],
    queryFn:   ({ signal }) => getChainDualTrack(sid, signal),
    refetchInterval: 4000,
    staleTime: 2000,
  });

  if (isLoading) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 h-40 text-slate-500">
        <Loader2 size={20} className="animate-spin" />
        <span className="text-sm">Loading chain events…</span>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 h-40 text-red-400">
        <AlertCircle size={20} />
        <span className="text-sm">{String(error)}</span>
        <button onClick={() => refetch()} className="text-xs text-slate-400 underline">retry</button>
      </div>
    );
  }

  if (!data) return null;

  const hasDelegations = data.delegations.length > 0;
  const hasOsOnly = data.os_only_events.length > 0;

  return (
    <div className="flex flex-col h-full min-h-0 overflow-hidden bg-card dark:bg-slate-950">
      {/* header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border flex-shrink-0">
        <GitBranch size={13} className="text-muted-foreground" />
        <span className="text-xs font-semibold text-foreground/80">Dual-Track · OS + Worker</span>
        {hasDelegations && (
          <Badge variant="outline" className="text-[9px] px-1.5 border-border text-muted-foreground">
            {data.delegations.length} delegation{data.delegations.length !== 1 ? "s" : ""}
          </Badge>
        )}
        <Badge
          variant="outline"
          className={cn(
            "text-[9px] px-1.5 font-mono",
            data.chain_verified
              ? "border-green-700 text-green-500"
              : "border-red-700 text-red-500",
          )}
          title={
            data.chain_verified
              ? "Hash-chain verified — genesis DNA readings below are trustworthy"
              : "Hash-chain verification FAILED — genesis DNA readings below cannot be trusted"
          }
        >
          Chain: {data.chain_verified ? "verified" : "broken"}
        </Badge>
        <span className="ml-auto text-[9px] text-muted-foreground/40 font-mono">Dual-Track</span>
      </div>

      {/* scrollable body */}
      <div className="flex-1 overflow-y-auto min-h-0 p-3 space-y-3">
        {/* genesis block */}
        <GenesisBadge genesis={data.genesis} />

        {/* OS-only events (turn lifecycle without delegation) */}
        {hasOsOnly && (
          <div className="space-y-0.5">
            <div className="text-[8px] font-semibold text-muted-foreground/60 uppercase tracking-wider px-1 mb-1">
              OS Events (no delegation)
            </div>
            {data.os_only_events.map((ev, i) => (
              <EventBlock key={`${ev.ts}-${ev.event_type}-${i}`} ev={ev} side="os" last={i === data.os_only_events.length - 1} />
            ))}
          </div>
        )}

        {/* delegation groups */}
        {hasDelegations ? (
          data.delegations.map((grp, i) => (
            <DelegationRow key={grp.delegation_id} grp={grp} index={i} chainVerified={data.chain_verified} />
          ))
        ) : (
          <div className="flex flex-col items-center justify-center gap-2 py-12 text-muted-foreground/50">
            <GitBranch size={28} strokeWidth={1} />
            <p className="text-sm">No delegation events in this session yet.</p>
            <p className="text-xs text-muted-foreground/30">
              Delegation events appear when the OS spawns a worker engine (e.g. /engine hermes).
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

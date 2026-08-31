/**
 * WdatAuditPanel — Live WDAT Audit Trail graph for chat sessions (ADR-0109).
 *
 * Live mode: polls every 2 s while the run's result.json is absent (is_active).
 * New nodes and edges are merged incrementally — viewport and positions of
 * existing nodes are preserved so the graph doesn't jump on each poll.
 * Only the first load triggers fitView.
 *
 * Clicking a node opens a detail panel; clicking the canvas deselects.
 */
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import ReactFlow, {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  ReactFlowProvider,
  Handle,
  Position,
  type NodeProps,
  type Node,
  type Edge,
  type ReactFlowInstance,
} from "reactflow";
import "reactflow/dist/style.css";
import { AlertCircle, GitBranch, Loader2, ShieldCheck, Wrench } from "lucide-react";
import {
  listSessionWdatRuns,
  getSessionWdatGraph,
  fetchWorkerTrace,
  listSessionOsTurns,
  listSessionEngineSpans,
  type EngineSpan,
  fetchExecutionLog,
  getOsEngineSetting,
  getSessionDebugLog,
  getSessionAnomalies,
  repairSession,
  type AnomalyItem,
  type WdatRunSummary,
  type WdatGraphPayload,
  type WdatNodeData,
  type WorkerToolCall,
  type OsTurn,
  type ExecLogEntry,
} from "@/lib/api";
import { chatDebugLog } from "@/lib/chat-registry";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

// ── Theme detection ───────────────────────────────────────────────────────────
function useIsDark(): boolean {
  const [isDark, setIsDark] = React.useState(
    () => document.documentElement.getAttribute("data-theme") === "dark",
  );
  React.useEffect(() => {
    const obs = new MutationObserver(() => {
      setIsDark(document.documentElement.getAttribute("data-theme") === "dark");
    });
    obs.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme", "class"],
    });
    return () => obs.disconnect();
  }, []);
  return isDark;
}

// ── Invisible handle ──────────────────────────────────────────────────────────
const H: React.CSSProperties = { opacity: 0, pointerEvents: "none" };

// ── Manager decision node (amber diamond) ─────────────────────────────────────
function WdatManagerNode({ data, selected }: NodeProps) {
  const [l1 = "Iter", l2 = ""] = String(data.label ?? "Iter").split("\n");
  const isDark = useIsDark();
  const subtitleColor = isDark ? "#c9d1d9" : "#374151";
  return (
    <div style={{ width: 130, height: 100, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <Handle type="target" position={Position.Top} style={H} />
      <div style={{
        width: 90, height: 90,
        background: selected ? "rgba(255,214,0,0.28)" : "rgba(255,214,0,0.12)",
        clipPath: "polygon(50% 0%,100% 50%,50% 100%,0% 50%)",
        display: "flex", alignItems: "center", justifyContent: "center",
        filter: selected
          ? "drop-shadow(0 0 14px rgba(255,214,0,0.8))"
          : "drop-shadow(0 0 6px rgba(255,214,0,0.3))",
        transition: "filter 0.15s, background 0.15s",
      }}>
        <div style={{ textAlign: "center", lineHeight: 1.3, padding: "0 12px" }}>
          <div style={{ fontSize: 10, fontFamily: "monospace", fontWeight: 700, color: "#FFD600" }}>{l1}</div>
          {l2 && <div style={{ fontSize: 9, fontFamily: "monospace", color: subtitleColor, opacity: 0.9 }}>{l2}</div>}
        </div>
      </div>
      <Handle type="source" position={Position.Bottom} style={H} />
    </div>
  );
}

// ── Worker node (pill, colour by status, tool-count badge) ───────────────────
function WdatWorkerNode({ data, selected }: NodeProps) {
  const [l1 = "?", l2 = ""] = String(data.label ?? "?").split("\n");
  const color: string = data.color ?? "#6e7681";
  const conf: number | null = data.confidence ?? null;
  const isNew: boolean = data._isNew ?? false;
  const toolCount: number = (data as { tool_count?: number }).tool_count ?? 0;
  const isDark = useIsDark();
  const subtitleColor = isDark ? "#8b949e" : "#4b5563";
  return (
    <div style={{
      position: "relative",
      width: 140, height: 64,
      background: `${color}18`,
      border: `2px solid ${selected ? color : color + "88"}`,
      borderRadius: 32,
      display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
      fontFamily: "monospace", gap: 1,
      boxShadow: isNew
        ? `0 0 20px ${color}99`
        : selected ? `0 0 14px ${color}55` : "none",
      transition: "all 0.3s",
      overflow: "hidden",
    }}>
      <Handle type="target" position={Position.Top} style={H} />
      <div style={{ fontSize: 10, fontWeight: 700, color, lineHeight: 1 }}>{l1}</div>
      {l2 && <div style={{ fontSize: 9, color: subtitleColor, lineHeight: 1 }}>{l2}</div>}
      {toolCount > 0 && (
        <div style={{
          position: "absolute", top: 4, right: 8,
          background: `${color}33`, border: `1px solid ${color}88`,
          borderRadius: 8, padding: "0 4px",
          fontSize: 7, fontWeight: 700, color, lineHeight: "12px",
        }}>
          {toolCount} tools
        </div>
      )}
      {conf != null && (
        <div style={{
          position: "absolute", bottom: 0, left: 0,
          width: `${Math.round(conf * 100)}%`,
          height: 3, background: color, opacity: 0.7,
          transition: "width 0.4s",
        }} />
      )}
      <Handle type="source" position={Position.Bottom} style={H} />
    </div>
  );
}

// ── Engine attestation node (wdat_engine) — sits between Worker and Tool-calls ─
function WdatEngineNode({ data, selected }: NodeProps) {
  const model    = String(data.model_id ?? data.label ?? "?");
  const engineId = String(data.engine_id ?? "");
  const color: string = (data.color as string) ?? "#60a5fa";
  const dur  = data.duration_ms != null ? `${data.duration_ms} ms` : null;
  const tok  = data.tokens_used != null ? `${data.tokens_used} tok` : null;

  const ENGINE_LABELS: Record<string, string> = {
    claude_code: "CC",
    hermes:      "HM",
    codex_cli:   "CX",
    opencode:    "OC",
    copilot:     "CP",
  };
  const badge = ENGINE_LABELS[engineId] ?? "??";

  return (
    <div style={{
      width: 110, minHeight: 34,
      background: selected ? `${color}22` : `${color}0d`,
      border: `1.5px solid ${selected ? color : color + "55"}`,
      borderRadius: 6,
      display: "flex", alignItems: "center", justifyContent: "center",
      fontFamily: "monospace",
      boxShadow: selected ? `0 0 10px ${color}55` : "none",
      transition: "all 0.15s",
      cursor: "pointer",
      padding: "3px 6px",
      gap: 4,
    }}>
      <Handle type="target" position={Position.Top} style={H} />
      {/* Engine type badge */}
      <span style={{
        fontSize: 8, fontWeight: 800, color, letterSpacing: "0.04em",
        background: `${color}22`, borderRadius: 3, padding: "1px 3px",
        flexShrink: 0,
      }}>{badge}</span>
      <div style={{ display: "flex", flexDirection: "column", flex: 1, minWidth: 0 }}>
        <span style={{ fontSize: 8, fontWeight: 700, color, lineHeight: 1.2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          {model.length > 18 ? model.slice(0, 16) + "…" : model}
        </span>
        {(dur || tok) && (
          <span style={{ fontSize: 7, color: "#6e7681", lineHeight: 1.2 }}>
            {[dur, tok].filter(Boolean).join(" · ")}
          </span>
        )}
      </div>
      <Handle type="source" position={Position.Bottom} style={H} />
    </div>
  );
}

// ── Tool-call node (wider pill — seq# + tool name, green=allow / red=deny) ──────
function WdatToolNode({ data, selected }: NodeProps) {
  const isDeny  = data.decision === "deny";
  const color   = isDeny ? "#FF1744" : "#00E676";
  const seq     = data.seq != null ? String(data.seq) : null;
  const toolStr = String(data.label ?? "?").slice(0, 10);
  return (
    <div style={{
      width: 80, height: 28,
      background: `${color}18`,
      border: `1.5px solid ${selected ? color : color + "66"}`,
      borderRadius: 14,
      display: "flex", alignItems: "center", justifyContent: "center", gap: 3,
      fontFamily: "monospace", padding: "0 6px",
      boxShadow: selected ? `0 0 8px ${color}55` : "none",
      transition: "box-shadow 0.15s",
      cursor: "pointer",
    }}>
      <Handle type="target" position={Position.Top} style={H} />
      {seq && (
        <span style={{ fontSize: 7, color: `${color}99`, fontWeight: 700, flexShrink: 0 }}>
          {seq}.
        </span>
      )}
      <span style={{ fontSize: 8, color, fontWeight: 600, letterSpacing: "0.02em", lineHeight: 1,
                     overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {isDeny ? "✕ " : "✓ "}{toolStr}
      </span>
      <Handle type="source" position={Position.Bottom} style={H} />
    </div>
  );
}

const WDAT_NODE_TYPES = {
  wdat_manager: WdatManagerNode,
  wdat_worker:  WdatWorkerNode,
  wdat_engine:  WdatEngineNode,
  wdat_tool:    WdatToolNode,
};

// ── EU AI Act compliance badge ────────────────────────────────────────────────
function ComplianceBadge({ label, value }: { label: string; value?: string }) {
  const ok = value && value !== "no_workers_found";
  return (
    <span className={cn(
      "inline-flex items-center gap-1 rounded px-2 py-0.5 text-[10px] font-mono whitespace-nowrap",
      ok
        ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"
        : "bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-500",
    )}>
      <ShieldCheck className="h-3 w-3 shrink-0" />
      {label}: {value ?? "—"}
    </span>
  );
}

// ── Node detail panel ─────────────────────────────────────────────────────────
function NodeDetail({ data }: { data: WdatNodeData }) {
  const rows: [string, string][] = [];
  if (data.iteration   != null) rows.push(["Iteration",       String(data.iteration)]);
  if (data.decision_type)       rows.push(["Decision",        data.decision_type]);
  if (data.n_subtasks  != null) rows.push(["Subtasks",        String(data.n_subtasks)]);
  if (data.model_id)            rows.push(["Mgr model",       data.model_id]);
  if (data.decision_hash)       rows.push(["Decision hash",   data.decision_hash + "…"]);
  if (data.spawn_nonce)         rows.push(["Nonce",           data.spawn_nonce + "…"]);
  if (data.worker_id)           rows.push(["Worker ID",       data.worker_id.slice(0, 20)]);
  if (data.status)              rows.push(["Status",          data.status]);
  if (data.confidence  != null) rows.push(["Confidence",      `${(data.confidence * 100).toFixed(0)}%`]);
  if (data.depth       != null) rows.push(["Depth",           String(data.depth)]);
  if (data.duration_ms != null) rows.push(["Duration",        `${data.duration_ms} ms`]);
  if (data.tokens_used != null) rows.push(["Tokens",          String(data.tokens_used)]);
  if ((data as { tool_count?: number }).tool_count != null)
                                rows.push(["Tool calls",       String((data as { tool_count?: number }).tool_count)]);
  if (data.instruction_hash)    rows.push(["Instr. hash",     data.instruction_hash + "…"]);
  if (data.output_hash)         rows.push(["Output hash",     data.output_hash + "…"]);
  const att = data.engine_attestation;
  if (att?.model_id)            rows.push(["Engine model",    att.model_id]);
  if (att?.locality)            rows.push(["Locality",        att.locality]);
  // wdat_engine fields (identified by presence of engine_id at top level)
  if (data.engine_id)           rows.push(["Engine type",    data.engine_id]);
  if (data.locality && !data.engine_attestation)
                                rows.push(["Locality",        data.locality]);
  // wdat_tool fields
  if (data.decision != null)    rows.push(["Decision",        data.decision === "deny" ? "DENIED (path-gate)" : "allowed"]);
  if (data.seq      != null)    rows.push(["Seq",             String(data.seq)]);

  return (
    <div className="flex flex-col gap-1">
      {rows.map(([k, v]) => (
        <div key={k} className="flex items-baseline justify-between gap-2 text-[11px]">
          <span className="text-muted-foreground shrink-0">{k}</span>
          <span className="font-mono text-foreground truncate">{v}</span>
        </div>
      ))}
    </div>
  );
}

// ── ADR-0109 M6 — Worker engine trace panel ───────────────────────────────────
function WorkerTracePanel({
  sid,
  runId,
  workerId,
  isActive,
}: {
  sid: string;
  runId: string;
  workerId: string;
  isActive: boolean;
}) {
  const traceQ = useQuery({
    queryKey: ["wdat-trace", sid, runId, workerId],
    queryFn: ({ signal }) => fetchWorkerTrace(sid, runId, workerId, signal),
    staleTime: 0,
    refetchInterval: isActive ? 2_000 : false,
  });

  if (traceQ.isPending) {
    return (
      <div className="flex items-center gap-1 text-muted-foreground text-[11px] mt-2">
        <Loader2 className="h-3 w-3 animate-spin" />
        <span>Loading trace…</span>
      </div>
    );
  }
  if (traceQ.isError || !traceQ.data) {
    return (
      <div className="text-[11px] text-muted-foreground mt-2 opacity-60">
        Trace unavailable
      </div>
    );
  }
  const { tool_calls, summary } = traceQ.data;
  if (tool_calls.length === 0) {
    return (
      <div className="text-[11px] text-muted-foreground mt-2 opacity-60">
        No tool calls recorded
      </div>
    );
  }
  return (
    <div className="mt-3 border-t border-border pt-2">
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
        Engine Trace
      </p>
      <div className="flex flex-col gap-0.5">
        {tool_calls.map((tc: WorkerToolCall) => (
          <div
            key={tc.seq}
            className="flex items-center gap-1.5 text-[11px]"
          >
            <span
              className={cn(
                "h-1.5 w-1.5 rounded-full shrink-0",
                tc.decision === "deny" ? "bg-destructive" : "bg-emerald-500",
              )}
            />
            <span className="font-mono text-foreground truncate flex-1">
              {tc.tool}
            </span>
            <span
              className={cn(
                "shrink-0 text-[10px]",
                tc.decision === "deny" ? "text-destructive" : "text-muted-foreground",
              )}
            >
              {tc.decision === "deny" ? "denied" : "✓"}
            </span>
          </div>
        ))}
      </div>
      <p className="mt-1 text-[10px] text-muted-foreground">
        {summary.total_calls} calls
        {summary.denied_calls > 0 && (
          <span className="text-destructive"> · {summary.denied_calls} denied</span>
        )}
      </p>
    </div>
  );
}

// ── Inner graph with live merge logic ─────────────────────────────────────────
function WdatGraphInner({
  payload,
  isActive,
  onNodeClick,
}: {
  payload: WdatGraphPayload;
  isActive: boolean;
  onNodeClick: (data: WdatNodeData | null) => void;
}) {
  const isDark = useIsDark();
  const [nodes, setNodes, onNodesChange] = useNodesState([] as Node[]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([] as Edge[]);
  const rfRef = React.useRef<ReactFlowInstance | null>(null);
  const initializedRef = React.useRef(false);
  // Track which node/edge IDs we've already rendered
  const knownNodes = React.useRef(new Set<string>());
  const knownEdges = React.useRef(new Set<string>());

  const meta = payload.meta;

  React.useEffect(() => {
    const incoming = payload.nodes as Node[];
    const incomingEdges = payload.edges as Edge[];

    if (!initializedRef.current) {
      // First load — full replace + fitView
      setNodes(incoming);
      setEdges(incomingEdges);
      incoming.forEach((n) => knownNodes.current.add(n.id));
      incomingEdges.forEach((e) => knownEdges.current.add(e.id));
      initializedRef.current = true;
      // Defer fitView until ReactFlow has mounted
      setTimeout(() => rfRef.current?.fitView({ padding: 0.25 }), 50);
      return;
    }

    // Subsequent polls — never remove nodes, never change positions (the
    // viewport must stay stable), but DO refresh the data of existing
    // nodes: a worker that was rendered as "running" must turn green when
    // the backend reports success. Only-add merging froze live runs.
    const incomingById = new Map(incoming.map((n) => [n.id, n]));
    const incomingEdgeById = new Map(incomingEdges.map((e) => [e.id, e]));
    const newNodes = incoming.filter((n) => !knownNodes.current.has(n.id));
    const newEdges = incomingEdges.filter((e) => !knownEdges.current.has(e.id));

    // Mark new nodes with _isNew flash, then clear after 1.5 s
    const flashNodes = newNodes.map((n) => ({ ...n, data: { ...n.data, _isNew: true } }));

    setNodes((prev) => [
      ...prev.map((p) => {
        const inc = incomingById.get(p.id);
        if (!inc) return p;
        const prevIsNew = (p.data as WdatNodeData & { _isNew?: boolean })._isNew;
        // Keep the node object (position, selection) — replace only data.
        return { ...p, data: { ...(inc.data as WdatNodeData), _isNew: prevIsNew } };
      }),
      ...flashNodes,
    ]);
    setEdges((prev) => [
      ...prev.map((p) => incomingEdgeById.get(p.id) ?? p),
      ...newEdges,
    ]);

    flashNodes.forEach((n) => knownNodes.current.add(n.id));
    newEdges.forEach((e) => knownEdges.current.add(e.id));

    if (newNodes.length > 0) {
      // Remove _isNew flag after animation
      setTimeout(() => {
        setNodes((prev) =>
          prev.map((n) =>
            (n.data as WdatNodeData & { _isNew?: boolean })._isNew
              ? { ...n, data: { ...n.data, _isNew: false } }
              : n,
          ),
        );
      }, 1500);
    }
  }, [payload, setNodes, setEdges]);

  return (
    <div className="flex h-full flex-col">
      {/* Compliance + live header */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border bg-background/80 px-3 py-2 shrink-0">
        <Badge
          variant="outline"
          className={cn(
            "font-mono text-[10px]",
            meta.chain_integrity === "verified"
              ? "border-green-700 text-green-400"
              : meta.chain_integrity === "broken"
                ? "border-red-700 text-red-400"
                : meta.chain_integrity === "unavailable"
                  ? "border-amber-700 text-amber-400"
                  : "border-zinc-600 text-zinc-400",
          )}
          title={
            meta.chain_integrity === "broken"
              ? "Hash-chain verification found tampered or broken entries in the audit log"
              : meta.chain_integrity === "unavailable"
                ? "Hash-chain verification could not be performed"
                : undefined
          }
        >
          Chain: {meta.chain_integrity}
        </Badge>
        <span className="text-[10px] text-muted-foreground whitespace-nowrap">
          {meta.total_manager_decisions} decisions · {meta.total_workers} workers
        </span>
        {isActive && (
          <span className="inline-flex items-center gap-1 rounded bg-blue-100 dark:bg-blue-900/30 px-2 py-0.5 text-[10px] font-mono text-blue-700 dark:text-blue-400">
            <span className="h-2 w-2 rounded-full bg-blue-500 dark:bg-blue-400 animate-pulse" />
            Live
          </span>
        )}
        <div className="ml-auto flex flex-wrap gap-1">
          <ComplianceBadge label="Art.9"  value={meta.eu_ai_act.art_9_risk_management} />
          <ComplianceBadge label="Art.13" value={meta.eu_ai_act.art_13_transparency} />
          <ComplianceBadge label="Art.14" value={meta.eu_ai_act.art_14_human_oversight} />
        </div>
      </div>

      <div className="flex-1 min-h-0" style={{ background: isDark ? "#0d1117" : "#f8f7f3" }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          nodeTypes={WDAT_NODE_TYPES}
          onInit={(instance) => {
            rfRef.current = instance;
            if (initializedRef.current) {
              setTimeout(() => instance.fitView({ padding: 0.25 }), 50);
            }
          }}
          onNodeClick={(_evt, node) => onNodeClick(node.data as WdatNodeData)}
          onPaneClick={() => onNodeClick(null)}
          fitView={false}
          minZoom={0.1}
          maxZoom={2.5}
          proOptions={{ hideAttribution: true }}
          attributionPosition="bottom-left"
        >
          <Background variant={BackgroundVariant.Dots} color={isDark ? "#21262d" : "#ddd8ce"} gap={20} />
          <Controls />
          <MiniMap
            nodeColor={(n) => (n.data as WdatNodeData).color ?? "#FFD600"}
            style={{ background: isDark ? "#161b22" : "#ede9e0" }}
          />
        </ReactFlow>
      </div>
    </div>
  );
}

// ── Task navigation — chips (2–3 runs) or collapsible rail (4+ runs) ─────────
function RunChip({
  run, isSelected, onSelect,
}: { run: WdatRunSummary; isSelected: boolean; onSelect: () => void }) {
  const statusColor =
    run.status === "success" ? "#00E676"
    : run.status === "failed" ? "#FF1744"
    : run.is_active ? "#FFD600"
    : "#6e7681";
  return (
    <button
      onClick={onSelect}
      title={`${run.run_id} — ${run.workflow_id || "unnamed"} [${run.status}]`}
      style={{
        display: "inline-flex", alignItems: "center", gap: 4,
        padding: "2px 8px",
        background: isSelected ? `${statusColor}22` : "transparent",
        border: `1.5px solid ${isSelected ? statusColor : statusColor + "55"}`,
        borderRadius: 14,
        fontFamily: "monospace", fontSize: 10,
        color: isSelected ? statusColor : `${statusColor}aa`,
        cursor: "pointer",
        transition: "all 0.15s",
        whiteSpace: "nowrap",
      }}
    >
      {run.is_active && <span style={{ fontSize: 8 }}>▶</span>}
      <span style={{ fontWeight: 700 }}>{run.run_id.slice(0, 8)}</span>
      {run.total_workers > 0 && (
        <span style={{ opacity: 0.7 }}>{run.total_workers}w</span>
      )}
      {run.duration_s != null && run.duration_s > 0 && (
        <span style={{ opacity: 0.7 }}>{run.duration_s.toFixed(1)}s</span>
      )}
    </button>
  );
}

function RunSelector({
  runs,
  selected,
  onSelect,
}: {
  runs: WdatRunSummary[];
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const [railOpen, setRailOpen] = React.useState(false);
  if (runs.length === 0) return null;

  // ≤3 runs: inline chips — always visible, no scrolling needed
  if (runs.length <= 3) {
    return (
      <div className="flex items-center gap-2 border-b border-border bg-background/90 px-3 py-2 shrink-0 flex-wrap">
        <GitBranch className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        {runs.map((r) => (
          <RunChip key={r.run_id} run={r} isSelected={r.run_id === selected} onSelect={() => onSelect(r.run_id)} />
        ))}
      </div>
    );
  }

  // ≥4 runs: collapsible rail — header shows selected run + toggle
  const selectedRun = runs.find((r) => r.run_id === selected);
  return (
    <div className="border-b border-border bg-background/90 shrink-0">
      <div
        className="flex items-center gap-2 px-3 py-2 cursor-pointer select-none"
        onClick={() => setRailOpen((o) => !o)}
      >
        <GitBranch className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        {selectedRun ? (
          <RunChip run={selectedRun} isSelected={true} onSelect={() => {}} />
        ) : (
          <span className="text-[11px] font-mono text-muted-foreground">Select run…</span>
        )}
        <span className="ml-auto text-[10px] text-muted-foreground font-mono">
          {runs.length} runs {railOpen ? "▲" : "▼"}
        </span>
      </div>
      {railOpen && (
        <div className="flex flex-wrap gap-2 px-3 pb-2">
          {runs.map((r) => (
            <RunChip
              key={r.run_id} run={r} isSelected={r.run_id === selected}
              onSelect={() => { onSelect(r.run_id); setRailOpen(false); }}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ── OS-Turn activity list (non-ACS interactions) ─────────────────────────────

// Strip the -YYYYMMDD release suffix from a model id for display.
const MODEL_DATE_SUFFIX = /-\d{8}$/;
const trimModelId = (m: string): string => m.replace(MODEL_DATE_SUFFIX, "");

function OsTurnRow({ turn }: { turn: OsTurn }) {
  const dur = turn.duration_ms > 0 ? `${(turn.duration_ms / 1000).toFixed(1)}s` : "—";
  const statusColor = !turn.completed
    ? "text-yellow-400"
    : turn.exit_code !== 0
    ? "text-destructive"
    : "text-emerald-400";
  const label = turn.completed
    ? (turn.exit_code !== 0 ? "error" : "ok")
    : "running…";
  return (
    <div className="border-b border-border px-3 py-2 text-xs">
      <div className="flex items-center gap-2">
        <span className={`font-mono font-semibold ${statusColor}`}>{label}</span>
        <span className="text-muted-foreground">{turn.persona}</span>
        {turn.model && (
          <span className="rounded bg-muted px-1 py-0.5 font-mono text-[10px] text-muted-foreground">
            {trimModelId(turn.model)}
          </span>
        )}
        <span className="ml-auto text-muted-foreground">{dur}</span>
      </div>
      {turn.tools.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-1">
          {turn.tools.map((t, i) => (
            <span key={i} className="rounded bg-muted px-1 py-0.5 font-mono text-[10px]">
              {t.seq ? <span className="opacity-50 mr-0.5">{t.seq}.</span> : null}
              {t.name}
            </span>
          ))}
        </div>
      )}
      <div className="mt-0.5 font-mono text-[10px] text-muted-foreground/60">
        {turn.turn_id} · {turn.started_at.slice(11, 19)}
      </div>
    </div>
  );
}

// ── OS-Turn graph — same visual language as the ACS graph, built from the
//    os_turn.* chain events (one engine pill per turn, tool chips below).
//    Positions are oldest-left so that new turns always appear at the RIGHT
//    end and existing node positions are never displaced between polls.
//    fitView is called once on init; subsequent polls only append new columns.
function OsTurnGraph({ turns }: { turns: OsTurn[] }) {
  const isDark = useIsDark();
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const rfRef = React.useRef<ReactFlowInstance | null>(null);
  // True once fitView has been scheduled (set immediately so re-renders don't
  // schedule a second call; the actual call is deferred 50 ms).
  const didFitRef = React.useRef(false);
  // Tracks which turn/tool node IDs have already been rendered.
  const knownNodes = React.useRef(new Set<string>());
  const knownEdges = React.useRef(new Set<string>());
  // Tracks the rightmost x-edge of the last rendered column so new columns
  // are appended without re-iterating the whole turns array.
  const nextXRef = React.useRef(0);

  React.useEffect(() => {
    const COL_GAP = 28;
    const TOOL_W = 58;
    const PILL_W = 140;

    // Build node/edge objects for every turn in oldest-first order, but only
    // collect the ones not yet in knownNodes — new turns always appear at the
    // right end, so we process them in the same oldest-first pass and start
    // their x-offset from the last recorded right edge.
    const appendNodes: Node[] = [];
    const appendEdges: Edge[] = [];

    const orderedTurns = [...turns].reverse(); // oldest-first

    for (const t of orderedTurns) {
      const turnNodeId = `turn_${t.turn_id}`;
      const colWidth = Math.max(PILL_W, t.tools.length * TOOL_W);

      // Compute updated node data regardless — existing turns may have changed
      // state (running → completed).
      const color = !t.completed ? "#FFD600" : t.exit_code !== 0 ? "#FF1744" : "#00E676";
      const dur = t.duration_ms > 0 ? ` · ${(t.duration_ms / 1000).toFixed(1)}s` : "";
      // OS-engine model (ADR-0112 split: OS = adaptive Haiku/Sonnet,
      // workers inherit user/tenant model) — shown so the two graph tabs
      // make the model difference auditable at a glance.
      const model = t.model ? trimModelId(t.model) : "";
      const sub = !t.completed
        ? `${t.persona} · running…`
        : t.exit_code !== 0
        ? `${t.persona} · rc ${t.exit_code}`
        : `${t.persona}${dur}`;

      if (!knownNodes.current.has(turnNodeId)) {
        // New column — append at the current right edge.
        const x = nextXRef.current;
        appendNodes.push({
          id: turnNodeId,
          type: "wdat_worker",
          position: { x: x + (colWidth - PILL_W) / 2, y: 0 },
          data: {
            label: `OS · ${model || (t.completed ? "done" : "running…")}\n${sub}`,
            color,
            turn_id: t.turn_id,
            started_at: t.started_at,
          },
        });
        t.tools.forEach((tool, i) => {
          const toolId = `tool_${t.turn_id}_${i}`;
          appendNodes.push({
            id: toolId,
            type: "wdat_tool",
            position: { x: x + i * TOOL_W + (colWidth - t.tools.length * TOOL_W + 6) / 2, y: 110 },
            data: { label: tool.name, decision: "allow" },
          });
          const edgeId = `e_${toolId}`;
          if (!knownEdges.current.has(edgeId)) {
            appendEdges.push({
              id: edgeId,
              source: turnNodeId,
              target: toolId,
              style: { stroke: "#3f4651", strokeWidth: 1.2 },
            });
            knownEdges.current.add(edgeId);
          }
        });
        knownNodes.current.add(turnNodeId);
        nextXRef.current = x + colWidth + COL_GAP;
      }
      // For existing turn nodes, refresh data in place (e.g. running → done).
      // Tool nodes are static once created, so only the turn pill needs a data
      // update.
    }

    const isFirstLoad = !didFitRef.current;

    if (isFirstLoad && appendNodes.length > 0) {
      // First load: full replace + schedule fitView.
      setNodes(appendNodes);
      setEdges(appendEdges);
      // Mark immediately so subsequent renders don't re-schedule.
      didFitRef.current = true;
      // Defer until ReactFlow has mounted and measured.
      setTimeout(() => rfRef.current?.fitView({ padding: 0.2 }), 50);
    } else if (appendNodes.length > 0) {
      // Incremental append: keep existing nodes, add new ones at the right.
      setNodes((prev) => {
        // Also refresh data on existing turn pills (state changes like running→done).
        const updatedById = new Map<string, { label: string; color: string }>();
        for (const t of orderedTurns) {
          const color2 = !t.completed ? "#FFD600" : t.exit_code !== 0 ? "#FF1744" : "#00E676";
          const dur2 = t.duration_ms > 0 ? ` · ${(t.duration_ms / 1000).toFixed(1)}s` : "";
          const model2 = t.model ? trimModelId(t.model) : "";
          const sub2 = !t.completed
            ? `${t.persona} · running…`
            : t.exit_code !== 0
            ? `${t.persona} · rc ${t.exit_code}`
            : `${t.persona}${dur2}`;
          updatedById.set(`turn_${t.turn_id}`, {
            label: `OS · ${model2 || (t.completed ? "done" : "running…")}\n${sub2}`,
            color: color2,
          });
        }
        return [
          ...prev.map((p) => {
            const upd = updatedById.get(p.id);
            if (!upd) return p;
            return { ...p, data: { ...p.data, ...upd } };
          }),
          ...appendNodes,
        ];
      });
      setEdges((prev) => [...prev, ...appendEdges]);
      // Expand the viewport to reveal new columns without resetting user zoom
      // on the existing nodes.
      setTimeout(() => rfRef.current?.fitView({ padding: 0.2 }), 50);
    } else if (appendNodes.length === 0 && knownNodes.current.size > 0) {
      // No new nodes, but existing turn pills may have updated state (e.g.
      // a running turn just completed). Refresh data without touching positions.
      setNodes((prev) => {
        const updatedById = new Map<string, { label: string; color: string }>();
        for (const t of orderedTurns) {
          const color2 = !t.completed ? "#FFD600" : t.exit_code !== 0 ? "#FF1744" : "#00E676";
          const dur2 = t.duration_ms > 0 ? ` · ${(t.duration_ms / 1000).toFixed(1)}s` : "";
          const model2 = t.model ? trimModelId(t.model) : "";
          const sub2 = !t.completed
            ? `${t.persona} · running…`
            : t.exit_code !== 0
            ? `${t.persona} · rc ${t.exit_code}`
            : `${t.persona}${dur2}`;
          updatedById.set(`turn_${t.turn_id}`, {
            label: `OS · ${model2 || (t.completed ? "done" : "running…")}\n${sub2}`,
            color: color2,
          });
        }
        return prev.map((p) => {
          const upd = updatedById.get(p.id);
          if (!upd) return p;
          return { ...p, data: { ...p.data, ...upd } };
        });
      });
    }
  }, [turns, setNodes, setEdges]);

  return (
    <ReactFlowProvider>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={WDAT_NODE_TYPES}
        onInit={(inst) => {
          rfRef.current = inst;
          // If the effect ran before ReactFlow mounted (rfRef was null at
          // effect time), fitView was not called — call it now.
          if (didFitRef.current) {
            setTimeout(() => inst.fitView({ padding: 0.2 }), 50);
          }
        }}
        fitView={false}
        minZoom={0.2}
        proOptions={{ hideAttribution: true }}
        style={{ background: isDark ? "#0d1117" : "#f8f7f3" }}
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1} color={isDark ? "#21262d" : "#ddd8ce"} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </ReactFlowProvider>
  );
}

function OsTurnPanel({ sid }: { sid: string }) {
  const [osView, setOsView] = React.useState<"graph" | "list">("graph");
  const q = useQuery({
    queryKey: ["os-turns", sid],
    queryFn: ({ signal }) => listSessionOsTurns(sid, signal),
    staleTime: 0,
    // Accelerate polling to 2 s while any turn is still running; otherwise
    // poll at 5 s so new turns appear automatically without burning requests.
    refetchInterval: (query) => {
      const turns = query.state.data?.turns ?? [];
      return turns.some((t) => !t.completed) ? 2_000 : 5_000;
    },
  });

  if (q.isPending) {
    return (
      <div className="flex h-full items-center justify-center gap-2 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        <span className="text-sm">Loading OS turns…</span>
      </div>
    );
  }
  const turns = q.data?.turns ?? [];
  const hasRunning = turns.some((t) => !t.completed);
  if (turns.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground">
        <ShieldCheck className="h-8 w-8 opacity-30" />
        <p className="text-sm">No OS-turn activity yet</p>
        <p className="text-xs opacity-60">Every user message creates an audit entry here</p>
      </div>
    );
  }
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center border-b border-border bg-muted/30 px-3 py-1.5 text-xs font-semibold text-muted-foreground">
        <span>OS-Turn Audit (EU AI Act Art. 12) — {turns.length} turns · graph oldest→newest, list newest first</span>
        {hasRunning && (
          <span className="ml-2 inline-flex items-center gap-1 rounded bg-blue-100 dark:bg-blue-900/30 px-2 py-0.5 text-[10px] font-mono text-blue-700 dark:text-blue-400">
            <span className="h-2 w-2 rounded-full bg-blue-400 animate-pulse" />
            Live
          </span>
        )}
        <div className="ml-auto flex gap-1">
          {(["graph", "list"] as const).map((v) => (
            <button
              key={v}
              onClick={() => setOsView(v)}
              className={cn(
                "rounded px-2 py-0.5 text-[10px] font-medium transition-colors",
                osView === v ? "bg-primary text-primary-foreground" : "hover:text-foreground",
              )}
            >
              {v === "graph" ? "Graph" : "List"}
            </button>
          ))}
        </div>
      </div>
      {osView === "graph" ? (
        <div className="min-h-0 flex-1">
          <OsTurnGraph turns={turns} />
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto">
          {turns.map((t) => <OsTurnRow key={t.turn_id} turn={t} />)}
        </div>
      )}
    </div>
  );
}

// ── Execution Log — flat chronological OS + ACS event stream ─────────────────
function roleLabel(et: string): string {
  if (et === "os_turn.started")  return "OS turn started";
  if (et === "os_turn.tool_called") return "tool called";
  if (et === "os_turn.completed") return "OS turn done";
  if (et === "acs.run_start")     return "ACS run started";
  if (et === "acs.manager_call")  return "manager call";
  if (et === "acs.manager_decided") return "manager decided";
  if (et === "acs.worker_spawned") return "worker spawned";
  if (et === "acs.worker_start")  return "worker started";
  if (et === "acs.engine_started") return "engine started";
  if (et === "acs.engine_completed") return "engine done";
  if (et === "acs.worker_traced") return "worker traced";
  if (et === "acs.gate_chain_evaluated") return "gate chain";
  if (et === "acs.workflow_complete") return "run complete";
  return et;
}

function ExecLogRow({ entry }: { entry: ExecLogEntry }) {
  const d = entry.details;
  const model = (d.model_id || d.model || "").replace(/^claude-/, "").replace(/-20\d{6}$/, "");
  const tags: string[] = [];
  if (model) tags.push(model);
  if (d.worker_id) tags.push(d.worker_id);
  if (d.tool_name) tags.push(`#${d.seq ?? "?"} ${d.tool_name}`);
  if (d.decision_type) tags.push(d.decision_type);
  if (d.duration_ms != null && d.duration_ms > 0) tags.push(`${(d.duration_ms / 1000).toFixed(1)}s`);
  if (d.tokens_used != null && d.tokens_used > 0) tags.push(`${d.tokens_used}tok`);
  if (d.status) tags.push(d.status);
  if (d.workers_spawned != null) tags.push(`${d.workers_spawned} workers`);
  if (d.confidence != null) tags.push(`conf ${d.confidence.toFixed(2)}`);

  const roleColor = entry.role === "os"
    ? "bg-indigo-50/80 border-indigo-300/60 text-indigo-700 dark:bg-indigo-950/60 dark:border-indigo-700/40 dark:text-indigo-300"
    : "bg-emerald-50/80 border-emerald-300/60 text-emerald-700 dark:bg-emerald-950/60 dark:border-emerald-800/40 dark:text-emerald-300";

  return (
    <div className="flex items-center gap-2 border-b border-border/40 px-3 py-1 text-[11px] hover:bg-muted/20 font-mono">
      <span className="w-16 shrink-0 text-muted-foreground/60">{entry.ts_iso.slice(11, 19)}</span>
      <span className={`shrink-0 rounded border px-1.5 py-0.5 text-[9px] font-semibold ${roleColor}`}>
        {entry.role.toUpperCase()}
      </span>
      <span className="min-w-0 flex-1 truncate text-foreground/80">{roleLabel(entry.event_type)}</span>
      <div className="flex shrink-0 flex-wrap gap-1">
        {tags.map((tag, i) => (
          <span key={i} className="rounded bg-muted/60 px-1 py-0.5 text-[10px] text-muted-foreground">
            {tag}
          </span>
        ))}
      </div>
    </div>
  );
}

function ExecLogPanel({ sid, isActive }: { sid: string; isActive: boolean }) {
  const q = useQuery({
    queryKey: ["exec-log", sid],
    queryFn: ({ signal }) => fetchExecutionLog(sid, signal),
    staleTime: 0,
    // 4 s while any run is active; 15 s idle so late-arriving OS-turn
    // events still appear without requiring a manual tab-switch.
    refetchInterval: isActive ? 4_000 : 15_000,
  });

  // Auto-scroll to bottom whenever new entries arrive so the user sees the
  // latest events without manually scrolling.
  const scrollRef = React.useRef<HTMLDivElement | null>(null);
  const entries = q.data?.entries ?? [];
  React.useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [entries.length]);

  if (q.isPending) {
    return (
      <div className="flex h-full items-center justify-center gap-2 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        <span className="text-sm">Loading execution log…</span>
      </div>
    );
  }
  if (entries.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground">
        <GitBranch className="h-8 w-8 opacity-30" />
        <p className="text-sm">No execution events yet</p>
        <p className="text-xs opacity-60">OS turns and ACS runs appear here in real time</p>
      </div>
    );
  }
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center border-b border-border bg-muted/30 px-3 py-1.5 text-xs font-semibold text-muted-foreground">
        <span>Execution Log — {entries.length} events, oldest first</span>
        <span className="ml-auto opacity-60">metadata only · GDPR Art. 5</span>
      </div>
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
        {entries.map((e, i) => <ExecLogRow key={i} entry={e} />)}
      </div>
    </div>
  );
}

// ── ACS empty state — context-aware guidance ──────────────────────────────────
function AcsEmptyState({ onViewOs }: { onViewOs: () => void }) {
  const q = useQuery({
    queryKey: ["engine-settings"],
    queryFn: ({ signal }) => getOsEngineSetting(signal),
    staleTime: 30_000,
  });

  const delegationEnabled = q.data?.delegation_enabled ?? false;
  const workerEngineSet = !!q.data?.default_worker_engine;

  let headline = "No worker-engine (ACS) runs in this session";
  let detail = "This chat ran on the OS engine only.";
  let hint: React.ReactNode = null;

  if (!q.isPending) {
    if (!workerEngineSet) {
      detail = "Configure a Worker Engine in Engine Settings to enable delegation runs.";
      hint = (
        <a
          href="/settings/engine"
          className="mt-1 rounded border border-border px-2.5 py-1 text-xs font-medium text-foreground hover:bg-muted"
        >
          Open Engine Settings →
        </a>
      );
    } else if (!delegationEnabled) {
      detail = "A Worker Engine is configured but delegation is disabled.";
      hint = (
        <a
          href="/settings/engine"
          className="mt-1 rounded border border-border px-2.5 py-1 text-xs font-medium text-foreground hover:bg-muted"
        >
          Enable delegation in Engine Settings →
        </a>
      );
    } else {
      detail = "No delegation runs yet — delegation is enabled.";
      hint = (
        <p className="text-xs opacity-50">
          Tip: prefix a message with <span className="font-mono">/delegate</span> to force a worker run.
        </p>
      );
    }
  }

  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-2 text-muted-foreground">
      <GitBranch className="h-8 w-8 opacity-30" data-testid="acs-empty-icon" />
      <p className="text-sm">{headline}</p>
      <p className="text-xs opacity-60">{detail}</p>
      {hint}
      <button
        onClick={onViewOs}
        className="mt-2 rounded border border-border px-2.5 py-1 text-xs font-medium text-foreground hover:bg-muted"
      >
        View OS-engine graph →
      </button>
    </div>
  );
}

// ── Engine Spans (ADR-0171) — engine-agnostic span list ───────────────────────
type AuditView = "acs" | "os" | "spans" | "log" | "debug";

function EngineSpanRow({ span }: { span: EngineSpan }) {
  const model = (span.model_id || "").replace(/^claude-/, "").replace(/-20\d{6}$/, "");
  const roleColor = span.role === "os"
    ? "bg-indigo-50/80 border-indigo-300/60 text-indigo-700 dark:bg-indigo-950/60 dark:border-indigo-700/40 dark:text-indigo-300"
    : span.role === "manager"
    ? "bg-amber-50/80 border-amber-300/60 text-amber-700 dark:bg-amber-950/60 dark:border-amber-800/40 dark:text-amber-300"
    : "bg-emerald-50/80 border-emerald-300/60 text-emerald-700 dark:bg-emerald-950/60 dark:border-emerald-800/40 dark:text-emerald-300";
  const statusColor = !span.completed
    ? "text-sky-600 dark:text-sky-400"
    : span.status === "error"
    ? "text-destructive"
    : "text-muted-foreground";
  const tags: string[] = [];
  if (model) tags.push(model);
  if (span.duration_ms != null && span.duration_ms > 0) tags.push(`${(span.duration_ms / 1000).toFixed(1)}s`);
  if (span.tokens_used != null && span.tokens_used > 0) tags.push(`${span.tokens_used}tok`);
  if (span.tool_call_count != null && span.tool_call_count > 0) tags.push(`${span.tool_call_count} tools`);

  return (
    <div className="flex items-center gap-2 border-b border-border/40 px-3 py-1 text-[11px] hover:bg-muted/20 font-mono">
      <span className={`shrink-0 rounded border px-1.5 py-0.5 text-[9px] font-semibold ${roleColor}`}>
        {(span.role || "?").toUpperCase()}
      </span>
      {/* engine_id is the load-bearing field: ANY engine shows here, ACS or not */}
      <span className="min-w-0 flex-1 truncate text-foreground/90">{span.engine_id || "unknown-engine"}</span>
      <div className="flex shrink-0 flex-wrap gap-1">
        {tags.map((tag, i) => (
          <span key={i} className="rounded bg-muted/60 px-1 py-0.5 text-[10px] text-muted-foreground">{tag}</span>
        ))}
      </div>
      <span className={`shrink-0 text-[10px] ${statusColor}`}>
        {span.completed ? (span.status || "ok") : "running…"}
      </span>
    </div>
  );
}

function EngineSpansPanel({ sid, isActive }: { sid: string; isActive: boolean }) {
  const q = useQuery({
    queryKey: ["engine-spans", sid],
    queryFn: ({ signal }) => listSessionEngineSpans(sid, undefined, signal),
    staleTime: 0,
    refetchInterval: isActive ? 3_000 : 15_000,
  });

  if (q.isPending) {
    return (
      <div className="flex h-full items-center justify-center gap-2 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        <span className="text-sm">Loading engine spans…</span>
      </div>
    );
  }
  if (q.isError) {
    // On an AUDIT surface a fetch failure must NOT masquerade as "no spans" —
    // surface the error so an empty trail is never mistaken for a clean one.
    return (
      <div className="flex h-full items-center justify-center gap-2 text-destructive">
        <AlertCircle className="h-4 w-4" />
        <span className="text-sm">Error loading engine spans</span>
      </div>
    );
  }
  const spans = q.data?.spans ?? [];
  const engines = q.data?.engines ?? [];
  if (spans.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground">
        <GitBranch className="h-8 w-8 opacity-30" />
        <p className="text-sm">No engine spans yet</p>
        <p className="text-xs opacity-60">Every engine invocation (OS or worker, any engine) appears here</p>
      </div>
    );
  }
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center border-b border-border bg-muted/30 px-3 py-1.5 text-xs font-semibold text-muted-foreground">
        <span>Engine Spans — {spans.length} invocation{spans.length === 1 ? "" : "s"}</span>
        {engines.length > 0 && (
          <span className="ml-2 truncate opacity-70">· {engines.join(", ")}</span>
        )}
        <span className="ml-auto opacity-60">metadata only · GDPR Art. 5</span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {spans.map((s) => <EngineSpanRow key={s.span_id} span={s} />)}
      </div>
    </div>
  );
}

// ── Debug Log Panel ────────────────────────────────────────────────────────────
// Shows structured debug events from <workdir>/chat_debug.jsonl (backend)
// merged with in-memory frontend WS events. Refreshes every 4 s while open.
const EVENT_COLOR: Record<string, string> = {
  "turn.start":           "text-sky-400",
  "turn.done":            "text-emerald-400",
  "delegation.decision":  "text-violet-400",
  "delegation.skipped":   "text-orange-400",
  "acs.run.start":        "text-violet-300",
  "acs.run.done":         "text-emerald-300",
  "stream.error":         "text-red-400",
  "stream.done":          "text-green-400",
  "ws.open":              "text-sky-300",
  "ws.close":             "text-yellow-400",
  "ws.error":             "text-red-500",
  "msg.send":             "text-blue-300",
  "msg.cancel":           "text-orange-300",
};

const ANOMALY_SEVERITY_COLOR: Record<string, string> = {
  CRITICAL: "bg-red-600 text-white",
  HIGH:     "bg-orange-500 text-white",
  MEDIUM:   "bg-yellow-500 text-black",
  LOW:      "bg-slate-500 text-white",
};

function AnomalyPanel({ sid }: { sid: string }) {
  const [repairing, setRepairing] = React.useState(false);
  const [repairMsg, setRepairMsg] = React.useState<string | null>(null);

  const q = useQuery({
    queryKey: ["aco-anomalies", sid],
    queryFn: ({ signal }) => getSessionAnomalies(sid, signal),
    staleTime: 10_000,
    refetchInterval: 30_000,
  });

  const handleRepair = React.useCallback(async () => {
    setRepairing(true);
    setRepairMsg(null);
    try {
      const result = await repairSession(sid, false);
      if (result.convergence_reached) {
        setRepairMsg(`✓ Converged — delta_loss=${result.delta_loss}, 0 CRITICAL/HIGH remaining`);
      } else {
        const after = result.after.critical + result.after.high;
        setRepairMsg(
          `Repair applied (delta_loss=${result.delta_loss}) — ${after} CRITICAL/HIGH still open`
        );
      }
      await q.refetch();
    } catch {
      setRepairMsg("Repair failed — check server logs");
    } finally {
      setRepairing(false);
    }
  }, [sid, q]);

  if (q.isPending) return (
    <div className="flex items-center gap-2 px-3 py-2 text-xs text-muted-foreground">
      <Loader2 className="h-3 w-3 animate-spin" /> Scanning for anomalies…
    </div>
  );

  const data = q.data;
  if (!data || data.total === 0) return (
    <div className="flex items-center gap-2 px-3 py-2 text-xs text-emerald-500">
      <ShieldCheck className="h-3 w-3" />
      {repairMsg
        ? <span className="text-emerald-400">{repairMsg}</span>
        : <span>No anomalies detected</span>
      }
      <button className="ml-auto text-muted-foreground hover:text-foreground" onClick={() => q.refetch()}>↺</button>
    </div>
  );

  const hasActionable = (data.critical + data.high) > 0;

  return (
    <div className="border-b border-border bg-muted/5 shrink-0">
      <div className="flex items-center gap-2 px-3 py-1.5">
        <AlertCircle className="h-3 w-3 text-orange-400 shrink-0" />
        <span className="text-xs font-semibold text-orange-400">{data.total} anomal{data.total === 1 ? "y" : "ies"}</span>
        <span className="flex gap-1 ml-1">
          {data.critical > 0 && <span className="px-1 rounded text-[10px] bg-red-600 text-white">{data.critical} CRIT</span>}
          {data.high > 0 && <span className="px-1 rounded text-[10px] bg-orange-500 text-white">{data.high} HIGH</span>}
          {data.medium > 0 && <span className="px-1 rounded text-[10px] bg-yellow-500 text-black">{data.medium} MED</span>}
        </span>
        <div className="ml-auto flex items-center gap-2">
          {hasActionable && (
            <button
              onClick={handleRepair}
              disabled={repairing}
              title="Layer 5 Auto-Repair: annotate repaired anomalies so future scans converge"
              className={cn(
                "flex items-center gap-1 rounded px-2 py-0.5 text-[10px] font-medium transition-colors",
                repairing
                  ? "opacity-50 cursor-not-allowed bg-muted text-muted-foreground"
                  : "bg-orange-500/15 text-orange-400 hover:bg-orange-500/30 border border-orange-500/30",
              )}
            >
              {repairing
                ? <Loader2 className="h-2.5 w-2.5 animate-spin" />
                : <Wrench className="h-2.5 w-2.5" />
              }
              {repairing ? "Repairing…" : "Auto-Repair"}
            </button>
          )}
          <button className="text-xs text-muted-foreground hover:text-foreground" onClick={() => q.refetch()}>↺</button>
        </div>
      </div>
      {repairMsg && (
        <div className={cn(
          "px-3 pb-1.5 text-[11px] font-mono",
          repairMsg.startsWith("✓") ? "text-emerald-400" : "text-orange-300",
        )}>
          {repairMsg}
        </div>
      )}
      <div className="px-3 pb-2 space-y-1 max-h-36 overflow-y-auto">
        {data.anomalies.slice(0, 5).map((a: AnomalyItem, i: number) => (
          <div key={i} className="flex gap-2 text-[11px]">
            <span className={`px-1 rounded shrink-0 ${ANOMALY_SEVERITY_COLOR[a.severity] ?? "bg-slate-600 text-white"}`}>
              {a.severity}
            </span>
            <span className="text-slate-300 truncate" title={a.message}>{a.anomaly_class}: {a.message.slice(0, 80)}</span>
          </div>
        ))}
        {data.total > 5 && (
          <div className="text-[10px] text-muted-foreground">+{data.total - 5} more…</div>
        )}
      </div>
    </div>
  );
}

function DebugLogPanel({ sid }: { sid: string }) {
  const q = useQuery({
    queryKey: ["debug-log", sid],
    queryFn: ({ signal }) => getSessionDebugLog(sid, signal),
    staleTime: 2_000,
    refetchInterval: 4_000,
  });

  // Merge backend events with frontend in-memory log
  const feEvents = React.useMemo(() => chatDebugLog(sid), [sid, q.dataUpdatedAt]);
  const beEvents: object[] = q.data?.events ?? [];
  // Backend events are the source of truth; frontend adds WS-side events
  // not visible on the server (ws.open, ws.close, msg.send, stream.*)
  const all = React.useMemo(() => {
    // Deduplicate by event+ts — backend may echo turn.start which is also in FE
    const beKeys = new Set(beEvents.map((e) => {
      const r = e as Record<string, unknown>;
      return `${r.ts}|${r.event}`;
    }));
    const feOnly = feEvents.filter((e) => {
      const r = e as Record<string, unknown>;
      return !beKeys.has(`${r.ts}|${r.event}`);
    });
    return [...beEvents, ...feOnly].sort((a, b) => {
      const at = (a as Record<string, unknown>).ts as string ?? "";
      const bt = (b as Record<string, unknown>).ts as string ?? "";
      return at < bt ? -1 : at > bt ? 1 : 0;
    });
  }, [beEvents, feEvents]);

  const listRef = React.useRef<HTMLDivElement>(null);
  // Auto-scroll to bottom on new events
  React.useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [all.length]);

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Anomaly panel — live ACO Layer 3 scan */}
      <AnomalyPanel sid={sid} />
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-border bg-muted/10 shrink-0">
        <span className="text-xs font-mono text-muted-foreground">
          Debug Log — {all.length} events
        </span>
        {q.isFetching && <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />}
        <button
          className="ml-auto text-xs text-muted-foreground hover:text-foreground"
          onClick={() => q.refetch()}
          title="Refresh"
        >↺</button>
      </div>
      <div
        ref={listRef}
        className="flex-1 overflow-y-auto font-mono text-[11px] leading-relaxed px-2 py-1 space-y-px"
      >
        {all.length === 0 && (
          <p className="text-muted-foreground text-center mt-8 text-xs">
            No debug events yet — send a message to start.
          </p>
        )}
        {all.map((evt, i) => {
          const r = evt as Record<string, unknown>;
          const ev = String(r.event ?? "?");
          const ts = String(r.ts ?? "").replace("T", " ").replace("Z", "");
          const color = EVENT_COLOR[ev] ?? "text-slate-400";
          // Build compact field display (exclude ts/event/sid)
          const fields = Object.entries(r)
            .filter(([k]) => !["ts", "event", "sid", "chat_key", "channel"].includes(k))
            .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
            .join("  ");
          return (
            <div key={i} className="flex gap-2 min-w-0">
              <span className="text-slate-600 shrink-0 select-none">{ts.slice(11)}</span>
              <span className={`${color} shrink-0 font-semibold`}>{ev}</span>
              <span className="text-slate-500 truncate">{fields}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Main exported component ───────────────────────────────────────────────────
export function WdatAuditPanel({ sid }: { sid: string }) {
  const [selectedRun, setSelectedRun] = React.useState<string | null>(null);
  const [detailData, setDetailData] = React.useState<WdatNodeData | null>(null);
  // "acs" | "os" | "log" — toggle between ACS graph, OS-turn audit, execution log.
  // DEFAULT = "os": every chat produces OS-turn events (the OS-engine graph), so
  // the panel always opens on a view that has data. When the session has ACS
  // workflow runs (worker-engine delegation) we auto-switch to "acs" below —
  // unless the operator has manually picked a view. This fixes the old default
  // of "acs", which showed an empty "No ACS runs" screen for normal chats and
  // hid the OS-engine graph two clicks deep.
  const [view, setView] = React.useState<AuditView>("os");
  // True once the operator clicks a view tab — suppresses auto-switching so we
  // never yank the view out from under a deliberate choice.
  const userPickedViewRef = React.useRef(false);
  const pickView = React.useCallback((v: AuditView) => {
    userPickedViewRef.current = true;
    setView(v);
  }, []);

  // Poll run list every 5 s always (not just when active) — new runs appear
  // in the list as soon as they start, without requiring a manual navigation.
  const runsQ = useQuery({
    queryKey: ["wdat-runs", sid],
    queryFn: ({ signal }) => listSessionWdatRuns(sid, signal),
    staleTime: 2_000,
    refetchInterval: (query) => {
      const data = query.state.data;
      // Fast poll (2 s) while a run is active; otherwise slow poll (5 s) so
      // new runs discovered automatically without burning unnecessary requests.
      return data?.runs.some((r) => r.is_active) ? 2_000 : 5_000;
    },
  });

  // Auto-select first run on initial load
  React.useEffect(() => {
    if (runsQ.data && runsQ.data.runs.length > 0 && !selectedRun) {
      setSelectedRun(runsQ.data.runs[0].run_id);
    }
  }, [runsQ.data, selectedRun]);

  const runs = runsQ.data?.runs ?? [];
  const selectedMeta = runs.find((r) => r.run_id === selectedRun);
  const isActive = selectedMeta?.is_active ?? false;

  // Auto-switch to the ACS (worker-engine) graph the first time delegation runs
  // appear — but only if the operator hasn't manually chosen a view. Normal
  // chats (no runs) stay on the OS-engine graph.
  React.useEffect(() => {
    if (userPickedViewRef.current) return;
    if (runs.length > 0) setView("acs");
  }, [runs.length]);

  // Poll graph every 2 s while active
  const graphQ = useQuery({
    queryKey: ["wdat-graph", sid, selectedRun],
    queryFn: ({ signal }) => getSessionWdatGraph(sid, selectedRun!, signal),
    enabled: !!selectedRun && view === "acs",
    staleTime: 0,
    refetchInterval: isActive ? 2_000 : false,
  });

  // When the run flips active → finished, polling stops — but the LAST
  // poll may predate the final manager iteration / worker results. Fetch
  // the terminal state exactly once so the graph doesn't freeze one step
  // before completion.
  const graphRefetch = graphQ.refetch;
  const prevActiveRef = React.useRef(isActive);
  React.useEffect(() => {
    if (prevActiveRef.current && !isActive) {
      void graphRefetch();
    }
    prevActiveRef.current = isActive;
  }, [isActive, graphRefetch]);

  // ── View toggle header ──────────────────────────────────────────────────────
  const VIEW_LABELS: Record<AuditView, string> = {
    acs:   "ACS Workflow Graph",
    os:    "OS-Turn Audit",
    spans: "Engine Spans",
    log:   "Execution Log",
    debug: "Debug Log",
  };
  const ViewToggle = (
    <div className="flex border-b border-border bg-muted/20">
      {(["acs", "os", "spans", "log", "debug"] as const).map((v) => (
        <button
          key={v}
          onClick={() => pickView(v)}
          className={cn(
            "flex-1 py-1.5 text-xs font-medium transition-colors",
            view === v
              ? "border-b-2 border-primary text-foreground"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {VIEW_LABELS[v]}
        </button>
      ))}
    </div>
  );

  // ── OS-Turn view ────────────────────────────────────────────────────────────
  if (view === "os") {
    return (
      <div className="flex h-full flex-col">
        {ViewToggle}
        <OsTurnPanel sid={sid} />
      </div>
    );
  }

  // ── Engine Spans view (ADR-0171, engine-agnostic) ────────────────────────────
  if (view === "spans") {
    return (
      <div className="flex h-full flex-col">
        {ViewToggle}
        <EngineSpansPanel sid={sid} isActive={isActive || runs.some((r) => r.is_active)} />
      </div>
    );
  }

  // ── Debug Log view ──────────────────────────────────────────────────────────
  if (view === "debug") {
    return (
      <div className="flex h-full flex-col">
        {ViewToggle}
        <DebugLogPanel sid={sid} />
      </div>
    );
  }

  // ── Execution Log view ──────────────────────────────────────────────────────
  if (view === "log") {
    return (
      <div className="flex h-full flex-col">
        {ViewToggle}
        {/* isActive = true while an ACS run or OS turn is ongoing so
            the log keeps polling at 4 s; false when everything is idle. */}
        <ExecLogPanel sid={sid} isActive={isActive || runs.some((r) => r.is_active)} />
      </div>
    );
  }

  // ── ACS view — loading / error / empty states ───────────────────────────────
  if (runsQ.isPending) {
    return (
      <div className="flex h-full flex-col">
        {ViewToggle}
        <div className="flex flex-1 items-center justify-center gap-2 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          <span className="text-sm">Loading audit trail…</span>
        </div>
      </div>
    );
  }
  if (runsQ.isError) {
    return (
      <div className="flex h-full flex-col">
        {ViewToggle}
        <div className="flex flex-1 items-center justify-center gap-2 text-destructive">
          <AlertCircle className="h-4 w-4" />
          <span className="text-sm">Error loading audit trail</span>
        </div>
      </div>
    );
  }
  if (runs.length === 0) {
    return (
      <div className="flex h-full flex-col">
        {ViewToggle}
        <AcsEmptyState onViewOs={() => pickView("os")} />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {ViewToggle}
      <RunSelector
        runs={runs}
        selected={selectedRun}
        onSelect={(id) => { setSelectedRun(id); setDetailData(null); }}
      />

      <div className="relative flex min-h-0 flex-1">
        {/* Graph area */}
        <div className={cn("flex-1 min-h-0", detailData ? "pr-60" : "")}>
          {graphQ.isPending && selectedRun && !graphQ.data && (
            <div className="flex h-full items-center justify-center gap-2 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              <span className="text-sm">Building graph…</span>
            </div>
          )}
          {graphQ.isError && (
            <div className="flex h-full items-center justify-center gap-2 text-destructive">
              <AlertCircle className="h-4 w-4" />
              <span className="text-sm">Graph unavailable</span>
            </div>
          )}
          {graphQ.data && (
            <ReactFlowProvider>
              <WdatGraphInner
                // Key changes only when switching runs, not on live polls
                key={selectedRun ?? "none"}
                payload={graphQ.data}
                isActive={isActive}
                onNodeClick={setDetailData}
              />
            </ReactFlowProvider>
          )}
        </div>

        {/* Node detail panel — wider when a worker node (has trace) is selected */}
        {detailData && (
          <div className={cn(
            "absolute right-0 top-0 bottom-0 border-l border-border bg-background/95 overflow-y-auto p-3 text-xs z-10",
            detailData.worker_id ? "w-72" : "w-60",
          )}>
            <div className="mb-2 flex items-center justify-between">
              <span className="font-semibold text-foreground">Node Detail</span>
              <button
                className="text-muted-foreground hover:text-foreground"
                onClick={() => setDetailData(null)}
                aria-label="Close"
              >
                ✕
              </button>
            </div>
            <NodeDetail data={detailData} />
            {/* ADR-0109 M6: inline engine trace for worker nodes */}
            {detailData.worker_id && selectedRun && (
              <WorkerTracePanel
                sid={sid}
                runId={selectedRun}
                workerId={detailData.worker_id}
                isActive={isActive}
              />
            )}
          </div>
        )}
      </div>
    </div>
  );
}

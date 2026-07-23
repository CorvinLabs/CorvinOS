/**
 * ComputeGraphView — React Flow graph for L25 and ACS runs.
 *
 * AWP-style column layout (mirrors AWP graph_builder.py):
 *   Task Root (diamond) at top → Manager (star) below →
 *   Iterations stacked vertically in left column →
 *   Workers fanning out horizontally to the right per iteration →
 *   Completion node at bottom.
 *
 * Custom node components match AWP visual language (diamond, star, box, circle).
 * ADR-0107 M1a (L25) + M1b (ACS).
 */
import * as React from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import ReactFlow, {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  Handle,
  Position,
  MarkerType,
  type NodeProps,
  type Node,
  type Edge,
  type ReactFlowInstance,
} from "reactflow";
import "reactflow/dist/style.css";
import { Loader2, AlertCircle } from "lucide-react";
import {
  getComputeRunGraph,
  getACSRunGraph,
  getTdeRunGraph,
  type L25GraphPayload,
  type ACSGraphPayload,
  type TDEGraphPayload,
} from "@/lib/api";

// ── Semantic color constants (CSS-variable-backed where possible) ─────────
// Replace raw GitHub dark-palette hex values with named constants so the
// graph remains readable when the app switches between light and dark themes.
const COLORS = {
  task:            "#40C4FF",
  manager:         "#E040FB",
  iteration:       "#FFD600",
  success:         "#00E676",
  successAlt:      "#69F0AE",
  failed:          "#FF1744",
  warning:         "#FF6D00",
  muted:           "var(--muted-foreground)",
  textPrimary:     "var(--foreground)",
  bg:              "var(--background)",
  card:            "var(--card)",
  border:          "var(--border)",
} as const;

// ── Node dimensions per group (width × height in px) ─────────────────────

const NODE_W: Record<string, number> = {
  task:        90,
  manager:     140,
  decision:    140,
  worker:      120,
  sub_manager: 130,
  sub_worker:  110,
  completion:  130,
  l34_block:   110,
};
const NODE_H: Record<string, number> = {
  task:        90,
  manager:     64,
  decision:    60,
  worker:      56,
  sub_manager: 64,
  sub_worker:  50,
  completion:  52,
  l34_block:   50,
};

// Invisible handle style — edges connect but dots aren't visible
const H_STYLE: React.CSSProperties = { opacity: 0, pointerEvents: "none" };

// ── Custom node components ────────────────────────────────────────────────

function TaskRootNode({ data }: NodeProps) {
  const [l1 = "Task", l2 = ""] = String(data.label ?? "Task").split("\n");
  return (
    <div style={{ width: NODE_W.task, height: NODE_H.task, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <Handle type="source" position={Position.Bottom} style={H_STYLE} />
      <div style={{
        width: 74, height: 74,
        background: COLORS.task,
        clipPath: "polygon(50% 0%,100% 50%,50% 100%,0% 50%)",
        display: "flex", alignItems: "center", justifyContent: "center",
        filter: "drop-shadow(0 0 10px rgba(64,196,255,0.5))",
      }}>
        <div style={{ textAlign: "center", lineHeight: 1.25 }}>
          <div style={{ fontSize: 9, fontFamily: "monospace", fontWeight: 700, color: COLORS.bg }}>{l1}</div>
          {l2 && <div style={{ fontSize: 8, fontFamily: "monospace", color: COLORS.bg, opacity: 0.75 }}>{l2}</div>}
        </div>
      </div>
    </div>
  );
}

function ManagerNode({ data }: NodeProps) {
  const [l1 = "Manager", l2 = ""] = String(data.label ?? "Manager").split("\n");
  // TDE's manager node (engine selection) carries a confidence-derived color
  // per-node (see _acs_confidence_color in compute.py); ACS always sends the
  // same fixed purple, so defaulting to COLORS.manager keeps that unchanged.
  const color: string = (data.color as string) ?? COLORS.manager;
  return (
    <div style={{
      width: NODE_W.manager, height: NODE_H.manager,
      background: `${color}1f`,
      border: `2px solid ${color}`, borderRadius: 10,
      display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
      fontFamily: "monospace", gap: 2,
      boxShadow: `0 0 14px ${color}38`,
    }}>
      <Handle type="target" position={Position.Top} style={H_STYLE} />
      <div style={{ fontSize: 12, fontWeight: 700, color }}>★ {l1}</div>
      {l2 && <div style={{ fontSize: 9, color: COLORS.muted }}>{l2}</div>}
      <Handle type="source" position={Position.Bottom} style={H_STYLE} />
    </div>
  );
}

// TDE-only: L34 pre-scan/step block node — a red warning triangle inserted
// into the chain wherever the L34 data-classification gate refused delegation
// outright (compute.py's "l34_block" group; ACS never emits this group).
function L34BlockNode({ data }: NodeProps) {
  const [l1 = "L34 Blocked", l2 = ""] = String(data.label ?? "L34 Blocked").split("\n");
  return (
    <div style={{ width: NODE_W.l34_block, height: NODE_H.l34_block, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <Handle type="target" position={Position.Top} style={H_STYLE} />
      <div style={{
        width: 46, height: 40,
        background: `${COLORS.failed}20`,
        border: `2px solid ${COLORS.failed}`,
        clipPath: "polygon(50% 0%, 0% 100%, 100% 100%)",
        display: "flex", alignItems: "center", justifyContent: "center",
      }}>
        <div style={{ textAlign: "center", marginTop: 8 }}>
          <div style={{ fontSize: 7, fontFamily: "monospace", fontWeight: 700, color: COLORS.failed }}>{l1}</div>
        </div>
      </div>
      {l2 && <div style={{ fontSize: 8, fontFamily: "monospace", color: COLORS.failed, marginTop: 2 }}>{l2}</div>}
      <Handle type="source" position={Position.Bottom} style={H_STYLE} />
    </div>
  );
}

// Loss colour: red (high loss) → orange → yellow → green (low loss).
function _lossColor(loss: number): string {
  if (loss >= 0.7) return COLORS.failed;
  if (loss >= 0.5) return COLORS.warning;
  if (loss >= 0.3) return COLORS.iteration;
  if (loss >= 0.1) return COLORS.successAlt;
  return COLORS.success;
}

// Confidence colour derived from _lossColor so both always agree on the same data point.
function _confColor(confidence: number | null | undefined): string | undefined {
  if (confidence == null) return undefined;
  return _lossColor(1 - confidence);
}

// Loss bar shown at the bottom of worker/sub-manager/sub-worker nodes.
function LossBar({ loss }: { loss: number | null | undefined }) {
  if (loss == null) return null;
  return (
    <div style={{
      position: "absolute", bottom: 0, left: 0,
      width: `${Math.min(1, loss) * 100}%`,
      height: 3, background: _lossColor(loss),
    }} />
  );
}

function IterationNode({ data }: NodeProps) {
  const [l1 = "Iter", l2 = "", l3 = ""] = String(data.label ?? "Iter").split("\n");
  // l25 runs flag their single best iteration (matches the green "best iter"
  // marker already used in LossCurvePanel) — everything else stays the
  // regular gold iteration styling. TDE's decision nodes instead carry a
  // per-node color (red=blocked, green=delegate, gray=local) — respected
  // here when present and the node isn't flagged "best".
  const isBest = Boolean(data.isBest);
  const color: string = isBest ? COLORS.success : ((data.color as string) ?? COLORS.iteration);
  return (
    <div style={{
      width: NODE_W.decision, height: NODE_H.decision,
      background: `${color}1f`,
      border: `${isBest ? 3 : 2}px solid ${color}`,
      borderRadius: 8,
      display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
      fontFamily: "monospace", gap: 1,
    }}>
      <Handle type="target" position={Position.Top} style={H_STYLE} />
      <div style={{ fontSize: 11, fontWeight: 700, color }}>{l1}</div>
      {l2 && <div style={{ fontSize: 9, color: COLORS.textPrimary }}>{l2}</div>}
      {l3 && <div style={{ fontSize: 9, color: COLORS.muted }}>{l3}</div>}
      {/* Named handles so edges target the correct side */}
      <Handle id="right"  type="source" position={Position.Right}  style={H_STYLE} />
      <Handle id="bottom" type="source" position={Position.Bottom} style={H_STYLE} />
    </div>
  );
}

function WorkerNode({ data }: NodeProps) {
  const [l1 = "worker", l2 = ""] = String(data.label ?? "worker").split("\n");
  const color: string = data.color ?? COLORS.muted;
  return (
    <div style={{
      position: "relative",
      width: NODE_W.worker, height: NODE_H.worker,
      background: color + "20",
      border: `2px solid ${color}`,
      borderRadius: 28,
      display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
      fontFamily: "monospace", gap: 1,
      overflow: "hidden",
    }}>
      {/* Left=target (receives chain from iter or prev worker), Right=source (passes to next worker) */}
      <Handle id="left"  type="target" position={Position.Left}  style={H_STYLE} />
      <Handle id="right" type="source" position={Position.Right} style={H_STYLE} />
      <div style={{
        fontSize: 9, fontWeight: 700, color,
        maxWidth: 100, overflow: "hidden", textOverflow: "ellipsis",
        whiteSpace: "nowrap", textAlign: "center",
      }}>{l1}</div>
      {l2 && <div style={{ fontSize: 9, color: COLORS.muted }}>{l2}</div>}
      <LossBar loss={data.loss as number | undefined} />
    </div>
  );
}

function SubManagerNode({ data }: NodeProps) {
  const [l1 = "sub-mgr", l2 = "", l3 = ""] = String(data.label ?? "sub-mgr").split("\n");
  const color: string = data.color ?? COLORS.manager;
  return (
    <div style={{
      position: "relative",
      width: NODE_W.sub_manager, height: NODE_H.sub_manager,
      background: color + "20",
      border: `2.5px dashed ${color}`,
      borderRadius: 28,
      display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
      fontFamily: "monospace", gap: 1,
      overflow: "hidden",
    }}>
      <Handle id="left"  type="target" position={Position.Left}  style={H_STYLE} />
      <Handle id="right" type="source" position={Position.Right} style={H_STYLE} />
      <Handle id="bottom_sub" type="source" position={Position.Bottom} style={H_STYLE} />
      <div style={{ fontSize: 9, fontWeight: 700, color, maxWidth: 110,
        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", textAlign: "center" }}>
        ★ {l1}
      </div>
      {l2 && <div style={{ fontSize: 8, color: COLORS.textPrimary }}>{l2}</div>}
      {l3 && <div style={{ fontSize: 8, color: color }}>{l3}</div>}
      <LossBar loss={data.loss as number | undefined} />
    </div>
  );
}

function SubWorkerNode({ data }: NodeProps) {
  const [l1 = "sw", l2 = ""] = String(data.label ?? "sw").split("\n");
  const color: string = data.color ?? COLORS.manager;
  return (
    <div style={{
      position: "relative",
      width: NODE_W.sub_worker, height: NODE_H.sub_worker,
      background: color + "18",
      border: `1.5px solid ${color}`,
      borderRadius: 22,
      display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
      fontFamily: "monospace", gap: 1,
      opacity: 0.92,
      overflow: "hidden",
    }}>
      <Handle id="top"   type="target" position={Position.Top}   style={H_STYLE} />
      <Handle id="left"  type="target" position={Position.Left}  style={H_STYLE} />
      <Handle id="right" type="source" position={Position.Right} style={H_STYLE} />
      <div style={{ fontSize: 8, fontWeight: 700, color,
        maxWidth: 96, overflow: "hidden", textOverflow: "ellipsis",
        whiteSpace: "nowrap", textAlign: "center" }}>{l1}</div>
      {l2 && <div style={{ fontSize: 8, color: COLORS.muted }}>{l2}</div>}
      <LossBar loss={data.loss as number | undefined} />
    </div>
  );
}

function CompletionNode({ data }: NodeProps) {
  const [l1 = "Result", l2 = "", l3 = ""] = String(data.label ?? "Result").split("\n");
  const color: string = data.color ?? COLORS.success;
  return (
    <div style={{
      width: NODE_W.completion, height: NODE_H.completion,
      background: color + "20",
      border: `2px solid ${color}`,
      borderRadius: 8,
      display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
      fontFamily: "monospace", gap: 1,
      boxShadow: `0 0 12px ${color}44`,
    }}>
      <Handle type="target" position={Position.Top} style={H_STYLE} />
      <div style={{ fontSize: 11, fontWeight: 700, color }}>{l1}</div>
      {l2 && <div style={{ fontSize: 9, color: COLORS.textPrimary }}>{l2}</div>}
      {l3 && <div style={{ fontSize: 9, color: COLORS.muted }}>{l3}</div>}
    </div>
  );
}

// NODE_TYPES must be defined outside the component to avoid re-renders.
const NODE_TYPES = {
  task:        TaskRootNode,
  manager:     ManagerNode,
  decision:    IterationNode,
  worker:      WorkerNode,
  sub_manager: SubManagerNode,
  sub_worker:  SubWorkerNode,
  completion:  CompletionNode,
  l34_block:   L34BlockNode,
};

// ── AWP-style custom layout ───────────────────────────────────────────────
//
// Mirrors AWP's graph_builder.py column approach:
//   Row 0: Task Root   (top, centered above iteration column)
//   Row 1: Manager     (below Task Root)
//   Row N: Iteration N (vertically stacked below Manager)
//   Row N, col ≥1: Workers for Iteration N (spread horizontally to the right)
//   Last row: Completion (below last iteration)
//
// This keeps the graph manageable regardless of worker count:
// width = max_workers_per_iter × X_WORKER, height = n_iters × Y_ROW.

const X_WORKER = 165;   // horizontal gap between worker columns
const Y_ROW    = 130;   // vertical gap between rows
const ITER_X   = 0;     // x-center of the iteration/root/manager column
const W_START  = 220;   // x-offset from ITER_X to first worker column

interface RawNode {
  id: string;
  group: string;
  color?: string;
  // Decision / iteration fields
  iter_num?: number;
  decision?: string;
  confidence?: number;
  loss?: number;
  budget_pct?: number;
  workers_count?: number;
  // Worker / sub_worker / sub_manager fields
  worker_name?: string;
  iteration?: string | number;
  parent_worker_id?: string;
  status?: string;
  depth?: number;
  sub_workers_spawned?: number;
  // Task / completion fields
  run_id?: string;
  workflow_id?: string;
  run_status?: string;
  total_iters?: number;
  workers_spawned?: number;
  duration_s?: number;
  quality_score?: number;
  // Manager fields
  engine?: string;
  max_loops?: number;
  // TDE-only fields (ADR-0214 audit-graph endpoint — see compute.py
  // _build_tde_audit_graph). step_num/step_action/delegate/l34_blocked/
  // reason_code live on "decision" nodes; success/duration_ms/loss_pct/
  // measured on "worker" nodes; scope/reason_code on "l34_block"; task_type/
  // complexity/override/trivial on "manager"; n_events/wall_time_s on
  // "task"; step_count/delegated_count/local_count on "completion".
  step_num?: number | null;
  step_action?: string;
  delegate?: boolean;
  l34_blocked?: boolean;
  reason_code?: string;
  scope?: string;
  task_type?: string;
  complexity?: string;
  override?: boolean;
  trivial?: boolean;
  success?: boolean;
  duration_ms?: number;
  loss_pct?: number | null;
  measured?: boolean;
  n_events?: number;
  wall_time_s?: number | null;
  step_count?: number;
  delegated_count?: number;
  local_count?: number;
  [key: string]: unknown;
}

interface RawEdge {
  from?: string;
  to?: string;
  source?: string;
  target?: string;
  color?: string;
  width?: number;
  label?: string;
}

// The flat/l25 run graph endpoint (GET /compute/runs/{id}/graph) emits its
// own group vocabulary (run/strategy/iteration/best_iter) instead of the ACS
// endpoint's (task/manager/decision) — every downstream lookup in this file
// (byGroup categorisation, NODE_W/NODE_H sizing, the `type: n.group` used to
// pick a NODE_TYPES renderer) keys off the ACS vocabulary only, so l25 nodes
// silently fell through to React Flow's unstyled `react-flow__node-default`
// (a plain white box) and never received a layout position at all. Alias
// the l25 names onto their ACS equivalent before anything else touches them.
const _GROUP_ALIAS: Record<string, string> = {
  run: "task",
  strategy: "manager",
  iteration: "decision",
  best_iter: "decision",
};

function buildReactFlowGraph(
  rawNodes: RawNode[],
  rawEdges: RawEdge[],
): [Node[], Edge[], number] {
  rawNodes = rawNodes.map((n) =>
    n.group === "best_iter"
      ? { ...n, group: _GROUP_ALIAS[n.group], isBest: true }
      : { ...n, group: _GROUP_ALIAS[n.group] ?? n.group },
  );

  // Categorise nodes by group
  const byGroup: Record<string, RawNode[]> = {};
  rawNodes.forEach((n) => {
    (byGroup[n.group] ??= []).push(n);
  });

  // Sort iterations numerically by their id suffix (iter_001, iter_002 …)
  const iterations: RawNode[] = (byGroup["decision"] ?? []).sort((a: RawNode, b: RawNode) => {
    const na = parseInt(a.id.replace(/\D+/g, ""), 10) || 0;
    const nb = parseInt(b.id.replace(/\D+/g, ""), 10) || 0;
    return na - nb;
  });

  // Walk the full worker chain per iteration (iter→w0→w1→…) and sub-worker
  // chains inside sub-managers (sub-mgr→sw0→sw1→…).
  // Build four maps — two for the main chain, two for sub-worker chains.
  const iterFirstWorker: Record<string, string> = {};
  const workerNext: Record<string, string> = {};
  const subMgrFirstSW: Record<string, string> = {};   // sub-manager id → first sw id
  const swNext: Record<string, string> = {};           // sw id → next sw id

  rawEdges.forEach((e: RawEdge) => {
    const src = e.from ?? e.source;
    const tgt = e.to ?? e.target;
    if (src?.startsWith("iter_") && tgt?.startsWith("w_")) {
      iterFirstWorker[src] = tgt;
    } else if (src?.startsWith("w_") && tgt?.startsWith("w_")) {
      workerNext[src] = tgt;                           // main chain
    } else if (src?.startsWith("w_") && tgt?.startsWith("sw_")) {
      subMgrFirstSW[src] = tgt;                        // sub-manager → first sub-worker
    } else if (src?.startsWith("sw_") && tgt?.startsWith("sw_")) {
      swNext[src] = tgt;                               // sub-worker chain
    }
  });

  const workersByIter: Record<string, string[]> = {};
  for (const [iterId, firstW] of Object.entries(iterFirstWorker)) {
    const chain: string[] = [];
    let cur: string | undefined = firstW;
    const seen = new Set<string>();
    while (cur && !seen.has(cur)) {
      chain.push(cur);
      seen.add(cur);
      cur = workerNext[cur];
    }
    workersByIter[iterId] = chain;
  }

  const subWorkersByMgr: Record<string, string[]> = {};
  for (const [smId, firstSW] of Object.entries(subMgrFirstSW)) {
    const chain: string[] = [];
    let cur: string | undefined = firstSW;
    const seen = new Set<string>();
    while (cur && !seen.has(cur)) {
      chain.push(cur);
      seen.add(cur);
      cur = swNext[cur];
    }
    subWorkersByMgr[smId] = chain;
  }

  const SUB_Y_OFFSET = 90;  // sub-worker row sits this many px below its sub-manager
  // Extra vertical space added for rows that contain sub-workers so the sub-worker
  // nodes never overlap with nodes in the following iteration row.
  // Math: sub-worker bottom = rowY + SUB_Y_OFFSET + NODE_H.sub_worker/2 = rowY+115
  //       next row top      = nextRowY - NODE_H.worker/2 = nextRowY-28
  //       need gap ≥ 10px   → nextRowY > rowY + 153 → extra ≥ 23 → use 45 for comfort.
  // If NODE_H.sub_worker changes, recalculate this constant accordingly.
  const SUB_ROW_EXTRA = 45;

  const pos: Record<string, { x: number; y: number }> = {};

  // Task Root — top
  const taskRoot = (byGroup["task"] ?? [])[0];
  if (taskRoot) pos[taskRoot.id] = { x: ITER_X, y: 0 };

  // Manager — one row below Task Root
  const manager = (byGroup["manager"] ?? [])[0];
  if (manager) pos[manager.id] = { x: ITER_X, y: Y_ROW };

  // Dynamic row Y positions: accumulate extra height for iterations that have sub-workers
  // so sub-worker nodes never push into the next iteration row.
  const rowYs: number[] = [];
  let accumY = 2 * Y_ROW;
  for (let i = 0; i < iterations.length; i++) {
    rowYs.push(accumY);
    const hasSubW = (workersByIter[iterations[i].id] ?? []).some(
      (wId: string) => (subWorkersByMgr[wId]?.length ?? 0) > 0,
    );
    accumY += Y_ROW + (hasSubW ? SUB_ROW_EXTRA : 0);
  }
  const completionY = accumY;

  // Iterations + their workers + sub-worker rows
  iterations.forEach((iter: RawNode, i: number) => {
    const rowY = rowYs[i];
    pos[iter.id] = { x: ITER_X, y: rowY };
    (workersByIter[iter.id] ?? []).forEach((wId: string, j: number) => {
      const xPos = ITER_X + W_START + j * X_WORKER;
      pos[wId] = { x: xPos, y: rowY };
      // Position sub-workers in a row below this worker (if it's a sub-manager)
      (subWorkersByMgr[wId] ?? []).forEach((swId: string, k: number) => {
        pos[swId] = { x: xPos + k * X_WORKER, y: rowY + SUB_Y_OFFSET };
      });
    });
  });

  // Completion — below last iteration using accumulated Y
  const completion = (byGroup["completion"] ?? [])[0];
  if (completion) {
    pos[completion.id] = { x: ITER_X, y: completionY };
  }

  const nodes: Node[] = rawNodes.map((n: RawNode) => {
    const p = pos[n.id] ?? { x: 0, y: 0 };
    const w = NODE_W[n.group] ?? 120;
    const h = NODE_H[n.group] ?? 60;
    return {
      id: n.id,
      type: n.group,
      position: { x: p.x - w / 2, y: p.y - h / 2 },
      data: n,
    };
  });

  const edges: Edge[] = rawEdges.map((e: RawEdge, i: number) => {
    // Route edges to the correct named handles:
    //   iter→w / w→w:         Right → Left   (horizontal main-chain row)
    //   iter→iter / iter→*:   Bottom → Top   (vertical column)
    //   w→sw (sub-mgr entry): bottom_sub → top  (down into sub-row)
    //   sw→sw:                Right → Left   (horizontal sub-worker chain)
    const toWorker   = e.to?.startsWith("w_");
    const toSubWorker = e.to?.startsWith("sw_");
    const fromIter   = e.from?.startsWith("iter_");
    const fromSubMgr = e.from?.startsWith("w_") && toSubWorker;
    const swToSw     = e.from?.startsWith("sw_") && toSubWorker;
    return {
      id: `e${i}__${e.from}__${e.to}`,
      source: e.from ?? e.source ?? "",
      target: e.to ?? e.target ?? "",
      ...(toWorker                        ? { sourceHandle: "right",      targetHandle: "left" } : {}),
      ...(fromIter && !toWorker           ? { sourceHandle: "bottom" } : {}),
      ...(fromSubMgr                      ? { sourceHandle: "bottom_sub", targetHandle: "top"  } : {}),
      ...(swToSw                          ? { sourceHandle: "right",      targetHandle: "left" } : {}),
      type: "smoothstep",
      style: {
        stroke: e.color ?? COLORS.muted,
        strokeWidth: e.width ?? 1.5,
        opacity: 0.85,
      },
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color: e.color ?? COLORS.muted,
        width: 15,
        height: 15,
      },
      label: e.label,
      labelStyle: { fill: COLORS.muted, fontSize: 9, fontFamily: "monospace" },
      labelBgStyle: { fill: "transparent" },
      labelBgBorderRadius: 2,
    };
  });

  return [nodes, edges, completionY + Y_ROW] as const;
}

// ── TDE-specific layout (ADR-0214 audit-graph endpoint) ────────────────────
//
// The TDE turn is reconstructed from a hash-chained *event* trail, not from
// per-run artifact files, so its topology is a single linear chain (unlike
// ACS's fan of workers per iteration): task_root → [l34_prescan_block] →
// mgr_1 (engine_selected) → decision_1 → [worker_1] → decision_2 → …
// → completion. Each decision has AT MOST one worker (delegated OR local),
// which — per _build_tde_audit_graph — always branches off its own decision
// node (decision→decision edges chain independently of any worker), so a
// simple "one row per decision, worker offset to the right" layout mirrors
// the ACS column pattern without needing its id-prefix chain-walking.
function buildTdeReactFlowGraph(
  rawNodes: RawNode[],
  rawEdges: RawEdge[],
): [Node[], Edge[], number] {
  const groupById: Record<string, string> = {};
  rawNodes.forEach((n) => { groupById[n.id] = n.group; });

  // decision id -> its worker id (edges from a decision node to a worker node)
  const workerOfDecision: Record<string, string> = {};
  rawEdges.forEach((e) => {
    const src = e.from ?? e.source ?? "";
    const tgt = e.to ?? e.target ?? "";
    if (groupById[src] === "decision" && groupById[tgt] === "worker") {
      workerOfDecision[src] = tgt;
    }
  });

  const decisions = rawNodes
    .filter((n) => n.group === "decision")
    .sort((a, b) => {
      const sa = typeof a.step_num === "number" ? a.step_num : Number.MAX_SAFE_INTEGER;
      const sb = typeof b.step_num === "number" ? b.step_num : Number.MAX_SAFE_INTEGER;
      return sa - sb;
    });

  const pos: Record<string, { x: number; y: number }> = {};
  let row = 0;

  const taskRoot = rawNodes.find((n) => n.group === "task");
  if (taskRoot) pos[taskRoot.id] = { x: ITER_X, y: (row++) * Y_ROW };

  const l34Prescan = rawNodes.find((n) => n.group === "l34_block");
  if (l34Prescan) pos[l34Prescan.id] = { x: ITER_X, y: (row++) * Y_ROW };

  const manager = rawNodes.find((n) => n.group === "manager");
  if (manager) pos[manager.id] = { x: ITER_X, y: (row++) * Y_ROW };

  decisions.forEach((d) => {
    const y = row * Y_ROW;
    pos[d.id] = { x: ITER_X, y };
    const wId = workerOfDecision[d.id];
    if (wId) pos[wId] = { x: ITER_X + W_START, y };
    row++;
  });

  const completion = rawNodes.find((n) => n.group === "completion");
  if (completion) pos[completion.id] = { x: ITER_X, y: row * Y_ROW };
  const totalHeight = (row + 1) * Y_ROW;

  const nodes: Node[] = rawNodes.map((n) => {
    const p = pos[n.id] ?? { x: 0, y: 0 };
    const w = NODE_W[n.group] ?? 120;
    const h = NODE_H[n.group] ?? 60;
    return {
      id: n.id,
      type: n.group,
      position: { x: p.x - w / 2, y: p.y - h / 2 },
      data: n,
    };
  });

  const edges: Edge[] = rawEdges.map((e, i) => {
    const src = e.from ?? e.source ?? "";
    const tgt = e.to ?? e.target ?? "";
    const fromGroup = groupById[src];
    const toGroup = groupById[tgt];
    // decision → worker: horizontal branch (right → left).
    // decision → * (next decision, or completion when the last step had no
    // worker): vertical, via the decision's named "bottom" handle.
    // worker → * (completion, when the last step DID have a worker):
    // horizontal source via the worker's named "right" handle.
    // Everything else (task/l34_block/manager chain) uses the nodes'
    // unnamed default handles, same as the ACS builder above.
    const handles =
      toGroup === "worker" ? { sourceHandle: "right", targetHandle: "left" } :
      fromGroup === "decision" ? { sourceHandle: "bottom" } :
      fromGroup === "worker" ? { sourceHandle: "right" } :
      {};
    return {
      id: `e${i}__${src}__${tgt}`,
      source: src,
      target: tgt,
      ...handles,
      type: "smoothstep",
      style: { stroke: e.color ?? COLORS.muted, strokeWidth: e.width ?? 1.5, opacity: 0.85 },
      markerEnd: { type: MarkerType.ArrowClosed, color: e.color ?? COLORS.muted, width: 15, height: 15 },
      label: e.label,
      labelStyle: { fill: COLORS.muted, fontSize: 9, fontFamily: "monospace" },
      labelBgStyle: { fill: "transparent" },
      labelBgBorderRadius: 2,
    };
  });

  return [nodes, edges, totalHeight] as const;
}

// ── Hover Tooltip ─────────────────────────────────────────────────────────

function TooltipRow({ label, value, color }: { label: string; value: React.ReactNode; color?: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 16, lineHeight: "1.5" }}>
      <span style={{ color: COLORS.muted, flexShrink: 0 }}>{label}</span>
      <span style={{ color: color ?? COLORS.textPrimary, fontWeight: 600, textAlign: "right" }}>{value}</span>
    </div>
  );
}

function NodeTooltipPanel({ data, mode }: { data: RawNode; mode: "l25" | "acs" | "tde" }) {
  const g = data.group as string;
  const pct = (v: number | null | undefined) =>
    v != null ? `${(v * 100).toFixed(0)}%` : "—";
  const dur = (s: number | null | undefined) =>
    s != null ? `${s.toFixed(1)}s` : "—";
  const statusColor = (s: string | undefined) =>
    s === "success" || s === "ok" ? COLORS.success
    : s === "failed" || s === "error" ? COLORS.failed
    : COLORS.muted;

  const title: Record<string, string> =
    mode === "tde"
      ? {
          task:      "TDE Turn",
          l34_block: "L34 Gate — Blocked",
          manager:   "Engine Selection",
          decision:  `Step ${data.step_num ?? "?"}`,
          worker:    data.delegate ? "Delegated Worker" : "Local Worker",
          completion: "TDE Turn Result",
        }
      : {
          task:        "ACS Workflow Task",
          manager:     "Manager Agent",
          decision:    `Iteration ${data.iter_num ?? ""}`,
          worker:      data.worker_name ?? data.id,
          sub_manager: `Sub-Manager: ${data.worker_name ?? data.id}`,
          sub_worker:  `Sub-Worker: ${data.worker_name ?? data.id}`,
          completion:  "Run Result",
        };

  return (
    <div style={{
      background: COLORS.card,
      border: `1px solid ${COLORS.border}`,
      borderRadius: 8,
      padding: "10px 14px",
      minWidth: 220,
      maxWidth: 320,
      fontFamily: "monospace",
      fontSize: 11,
      boxShadow: "0 6px 24px rgba(0,0,0,0.6)",
      pointerEvents: "none",
    }}>
      <div style={{ fontWeight: 700, fontSize: 12, color: data.color ?? COLORS.textPrimary, marginBottom: 8 }}>
        {title[g] ?? g}
      </div>

      {mode !== "tde" && g === "task" && (<>
        <TooltipRow label="Run ID"      value={data.run_id ?? "—"} />
        <TooltipRow label="Workflow"    value={data.workflow_id ?? "—"} />
        <TooltipRow label="Status"      value={data.run_status ?? "—"} color={statusColor(data.run_status)} />
        <TooltipRow label="Iterations"  value={data.total_iters ?? "—"} />
        <TooltipRow label="Workers"     value={data.workers_spawned ?? "—"} />
        <TooltipRow label="Duration"    value={dur(data.duration_s)} />
      </>)}

      {mode !== "tde" && g === "manager" && (<>
        <TooltipRow label="Engine"     value={data.engine ?? "—"} />
        <TooltipRow label="Max loops"  value={data.max_loops ?? "—"} />
      </>)}

      {mode !== "tde" && g === "decision" && (<>
        <TooltipRow label="Decision"    value={data.decision ?? "—"} />
        <TooltipRow label="Confidence"  value={pct(data.confidence)} color={_confColor(data.confidence as number | undefined)} />
        {data.loss != null && (
          <TooltipRow label="Loss" value={(data.loss as number).toFixed(4)} color={_lossColor(data.loss as number)} />
        )}
        <TooltipRow label="Budget left" value={data.budget_pct != null ? `${data.budget_pct.toFixed(1)}%` : "—"} />
        <TooltipRow label="Workers"     value={data.workers_count ?? "—"} />
      </>)}

      {mode !== "tde" && (g === "worker" || g === "sub_worker") && (<>
        <TooltipRow label="Worker"      value={data.worker_name ?? data.id} />
        <TooltipRow label="Iteration"   value={data.iteration ?? "—"} />
        {data.parent_worker_id && <TooltipRow label="Sub-Mgr" value={data.parent_worker_id} />}
        <TooltipRow label="Status"      value={data.status ?? "—"} color={statusColor(data.status)} />
        <TooltipRow label="Confidence"  value={pct(data.confidence)} color={_confColor(data.confidence as number | undefined)} />
        {data.loss != null && (
          <TooltipRow label="Loss" value={(data.loss as number).toFixed(4)} color={_lossColor(data.loss as number)} />
        )}
        <TooltipRow label="Depth"       value={data.depth ?? "—"} />
      </>)}

      {mode !== "tde" && g === "sub_manager" && (<>
        <TooltipRow label="Worker"       value={data.worker_name ?? data.id} />
        <TooltipRow label="Iteration"    value={data.iteration ?? "—"} />
        <TooltipRow label="Status"       value={data.status ?? "—"} color={statusColor(data.status)} />
        <TooltipRow label="Confidence"   value={pct(data.confidence)} color={_confColor(data.confidence as number | undefined)} />
        {data.loss != null && (
          <TooltipRow label="Loss" value={(data.loss as number).toFixed(4)} color={_lossColor(data.loss as number)} />
        )}
        <TooltipRow label="Sub-Workers"  value={data.sub_workers_spawned ?? "—"} />
        <TooltipRow label="Depth"        value={data.depth ?? "—"} />
      </>)}

      {mode !== "tde" && g === "completion" && (<>
        <TooltipRow label="Status"      value={data.run_status ?? "—"} color={statusColor(data.run_status)} />
        <TooltipRow label="Iterations"  value={data.total_iters ?? "—"} />
        <TooltipRow label="Workers"     value={data.workers_spawned ?? "—"} />
        <TooltipRow label="Quality"     value={pct(data.quality_score)} color={
          data.quality_score != null && data.quality_score >= 0.8 ? COLORS.success : undefined
        } />
        <TooltipRow label="Wall time"   value={dur(data.duration_s)} />
      </>)}

      {/* ── TDE-mode rows (ADR-0214) ───────────────────────────────────── */}

      {mode === "tde" && g === "task" && (<>
        <TooltipRow label="Run ID"    value={data.run_id ?? "—"} />
        <TooltipRow label="Events"    value={data.n_events ?? "—"} />
        <TooltipRow label="Wall time" value={dur(data.wall_time_s as number | undefined)} />
      </>)}

      {mode === "tde" && g === "l34_block" && (<>
        <TooltipRow label="Scope"       value={data.scope ?? "—"} />
        <TooltipRow label="Reason code" value={data.reason_code ?? "—"} color={COLORS.failed} />
      </>)}

      {mode === "tde" && g === "manager" && (<>
        <TooltipRow label="Engine"     value={data.engine ?? "—"} />
        <TooltipRow label="Confidence" value={pct(data.confidence)} color={_confColor(data.confidence as number | undefined)} />
        <TooltipRow label="Task type"  value={data.task_type ?? "—"} />
        <TooltipRow label="Complexity" value={data.complexity ?? "—"} />
        {data.override != null && <TooltipRow label="Override" value={String(data.override)} />}
        {data.trivial != null && <TooltipRow label="Trivial" value={String(data.trivial)} />}
      </>)}

      {mode === "tde" && g === "decision" && (<>
        <TooltipRow label="Step action" value={data.step_action ?? "—"} />
        <TooltipRow label="Delegate"    value={data.delegate ? "yes" : "no"} color={data.delegate ? COLORS.success : COLORS.muted} />
        <TooltipRow label="L34 blocked" value={data.l34_blocked ? "yes" : "no"} color={data.l34_blocked ? COLORS.failed : COLORS.muted} />
        {data.reason_code != null && <TooltipRow label="Reason code" value={data.reason_code} />}
      </>)}

      {mode === "tde" && g === "worker" && (<>
        <TooltipRow label="Engine"     value={data.engine ?? "—"} />
        <TooltipRow label="Success"    value={data.success ? "yes" : "no"} color={data.success ? COLORS.success : COLORS.failed} />
        <TooltipRow label="Duration"   value={data.duration_ms != null ? `${data.duration_ms}ms` : "—"} />
        {data.loss_pct != null && (
          <TooltipRow label="Loss" value={(data.loss_pct as number).toFixed(4)} color={_lossColor(data.loss_pct as number)} />
        )}
        {data.measured != null && <TooltipRow label="Measured" value={String(data.measured)} />}
      </>)}

      {mode === "tde" && g === "completion" && (<>
        <TooltipRow label="Steps"     value={data.step_count ?? "—"} />
        <TooltipRow label="Delegated" value={data.delegated_count ?? "—"} />
        <TooltipRow label="Local"     value={data.local_count ?? "—"} />
      </>)}
    </div>
  );
}

// ── Loss Curve Chart ──────────────────────────────────────────────────────

interface LossPt { iter: number; loss: number; confidence: number; }

function LossCurvePanel({ lossCurve }: { lossCurve: LossPt[] }) {
  if (!lossCurve || lossCurve.length < 2) return null;

  const W = 520, H = 96, PL = 34, PR = 16, PT = 12, PB = 22;
  const plotW = W - PL - PR;
  const plotH = H - PT - PB;
  const minIter = lossCurve[0].iter;
  const maxIter = lossCurve[lossCurve.length - 1].iter;
  const maxLoss = Math.min(1.0, Math.max(...lossCurve.map(p => p.loss), 0.05) + 0.05);

  const xOf = (iter: number) =>
    PL + (maxIter === minIter ? plotW / 2 : ((iter - minIter) / (maxIter - minIter)) * plotW);
  const yOf = (loss: number) => PT + plotH * (1 - loss / maxLoss);

  const linePts = lossCurve.map(p => `${xOf(p.iter)},${yOf(p.loss)}`).join(" ");
  const first = lossCurve[0], last = lossCurve[lossCurve.length - 1];
  const areaPath =
    `M${xOf(first.iter)},${yOf(0)} ` +
    lossCurve.map(p => `L${xOf(p.iter)},${yOf(p.loss)}`).join(" ") +
    ` L${xOf(last.iter)},${yOf(0)} Z`;

  const yTicks = [0, 0.25, 0.5, 0.75, 1.0].filter(v => v <= maxLoss);
  const finalLoss = last.loss;
  const trend = finalLoss < first.loss ? "↓ converging" : finalLoss > first.loss ? "↑ diverging" : "→ flat";
  const trendColor = finalLoss < first.loss ? COLORS.success : finalLoss > first.loss ? COLORS.failed : COLORS.muted;

  return (
    <div style={{
      background: COLORS.card,
      border: `1px solid ${COLORS.border}`,
      borderRadius: 6,
      padding: "6px 10px 2px",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 2, fontFamily: "monospace" }}>
        <span style={{ fontSize: 10, color: COLORS.muted }}>
          Loss curve&nbsp;<span style={{ color: COLORS.muted }}>(1 − confidence per iteration)</span>
        </span>
        <span style={{ fontSize: 10, color: trendColor, marginLeft: "auto" }}>{trend}</span>
        <span style={{ fontSize: 10, color: _lossColor(finalLoss) }}>
          final&nbsp;{finalLoss.toFixed(3)}
        </span>
      </div>
      <svg width={W} height={H} style={{ display: "block", overflow: "visible" }}>
        {/* Y-axis grid + labels */}
        {yTicks.map(v => (
          <g key={v}>
            <line x1={PL} y1={yOf(v)} x2={W - PR} y2={yOf(v)}
              stroke={COLORS.border} strokeWidth={v === 0 ? 1.5 : 1} />
            <text x={PL - 4} y={yOf(v) + 3} textAnchor="end" fontSize={7} fill={COLORS.muted}>
              {v.toFixed(2)}
            </text>
          </g>
        ))}
        {/* Area fill */}
        <path d={areaPath} fill={`${COLORS.warning}12`} />
        {/* Line */}
        <polyline points={linePts} fill="none" stroke={COLORS.warning} strokeWidth={2} strokeLinejoin="round" />
        {/* Dots + labels */}
        {lossCurve.map(p => {
          const cx = xOf(p.iter), cy = yOf(p.loss);
          const dc = _lossColor(p.loss);
          return (
            <g key={p.iter}>
              <circle cx={cx} cy={cy} r={4} fill={dc} stroke={COLORS.bg} strokeWidth={1.5} />
              <text x={cx} y={cy - 6} textAnchor="middle" fontSize={7} fill={dc}>
                {p.loss.toFixed(2)}
              </text>
              <text x={cx} y={H - 5} textAnchor="middle" fontSize={7} fill={COLORS.muted}>
                {p.iter}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

// ── Legend ────────────────────────────────────────────────────────────────

function GraphLegend({ mode }: { mode: "l25" | "acs" | "tde" }) {
  const items =
    mode === "tde"
      ? [
          { color: COLORS.task, label: "Turn Root" },
          { color: COLORS.manager, label: "Engine Selection" },
          { color: "#00E676", label: "Delegated Step" },
          { color: "#8b949e", label: "Local Step" },
          { color: COLORS.failed, label: "L34 Blocked" },
        ]
      : [
          { color: COLORS.task, label: mode === "l25" ? "Run Root" : "Task Root" },
          { color: COLORS.manager, label: mode === "l25" ? "Strategy" : "Manager" },
          { color: COLORS.iteration, label: "Iteration" },
          { color: COLORS.success, label: "Worker (conf ≥0.8)" },
          { color: COLORS.muted, label: "Worker (partial)" },
          { color: COLORS.failed, label: "Failed" },
        ];
  return (
    <div className="flex flex-wrap items-center gap-3 text-[10px] text-muted-foreground font-mono pt-1">
      {items.map(({ color, label }) => (
        <span key={label} className="flex items-center gap-1">
          <span className="inline-block w-3 h-3 rounded-sm" style={{ background: color }} />
          {label}
        </span>
      ))}
    </div>
  );
}

// Hash-chain integrity banner — surfaces meta.chain_verified/chain_problems
// from the TDE audit-graph endpoint (ADR-0214). ACS/L25 graphs are built
// from on-disk artifact files, not the audit chain, so they carry no
// equivalent field and never render this.
function ChainIntegrityBanner({ verified, problems }: { verified: boolean; problems: Record<string, unknown>[] }) {
  return (
    <div
      className="flex items-center gap-2 rounded-md border px-3 py-1.5 text-xs font-mono"
      style={{
        borderColor: verified ? COLORS.success : COLORS.failed,
        background: `${verified ? COLORS.success : COLORS.failed}14`,
        color: verified ? COLORS.success : COLORS.failed,
      }}
    >
      {verified ? "✓ Audit hash-chain verified for this turn" : `✗ Audit hash-chain broken (${problems.length} problem${problems.length === 1 ? "" : "s"})`}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────

interface Props {
  mode: "l25" | "acs" | "tde";
  runId: string;
  pollMs?: number;
}

export function ComputeGraphView({ mode, runId, pollMs = 0 }: Props) {
  const graphQ = useQuery<L25GraphPayload | ACSGraphPayload | TDEGraphPayload, Error>({
    queryKey: ["compute-graph", mode, runId],
    queryFn: ({ signal }) =>
      mode === "l25"
        ? getComputeRunGraph(runId, { signal })
        : mode === "acs"
        ? getACSRunGraph(runId, { signal })
        : getTdeRunGraph(runId, { signal }),
    staleTime: pollMs > 0 ? pollMs / 2 : Infinity,
    refetchInterval: pollMs > 0 ? pollMs : false,
    retry: false,
  });

  const [rfNodes, setRfNodes, onNodesChange] = useNodesState([]);
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState([]);
  const rfRef = React.useRef<ReactFlowInstance | null>(null);
  // Tooltip uses viewport (clientX/Y) coords so it escapes overflow:hidden via portal.
  const [tooltip, setTooltip] = React.useState<{ data: RawNode; cx: number; cy: number } | null>(null);
  const [graphTotalHeight, setGraphTotalHeight] = React.useState<number>(0);

  React.useEffect(() => {
    if (!graphQ.data) return;
    const [nodes, edges, totalHeight] = mode === "tde"
      ? buildTdeReactFlowGraph(graphQ.data.nodes as unknown as RawNode[], graphQ.data.edges as unknown as RawEdge[])
      : buildReactFlowGraph(graphQ.data.nodes as unknown as RawNode[], graphQ.data.edges as unknown as RawEdge[]);
    setRfNodes(nodes);
    setRfEdges(edges);
    setGraphTotalHeight(totalHeight);
    setTimeout(() => rfRef.current?.fitView({ padding: 0.15, duration: 400 }), 150);
  }, [graphQ.data, mode, setRfNodes, setRfEdges]);

  const handleNodeEnter = React.useCallback((evt: React.MouseEvent, node: Node) => {
    setTooltip({ data: (node.data as RawNode), cx: evt.clientX, cy: evt.clientY });
  }, []);

  const handleNodeMove = React.useCallback((evt: React.MouseEvent) => {
    setTooltip(t => t ? { ...t, cx: evt.clientX, cy: evt.clientY } : null);
  }, []);

  const meta = graphQ.data?.meta;
  const nIters: number = (meta as Record<string, unknown>)?.n_iters as number ?? 4;
  // Use actual computed graph height so sub-worker rows don't get clipped.
  // Falls back to estimate before data loads.
  const graphHeight = graphTotalHeight > 0 ? graphTotalHeight : (nIters + 3) * Y_ROW;
  const canvasHeight = Math.max(480, Math.min(900, graphHeight));

  return (
    <div className="flex flex-col gap-2">
      {/* Stats bar */}
      {meta && mode === "tde" && (
        <div className="flex flex-wrap items-center gap-4 text-xs text-muted-foreground font-mono">
          <span>Steps: <span className="text-foreground font-semibold">{(meta as Record<string, unknown>).n_steps as number}</span></span>
          <span>Delegated: <span className="text-foreground font-semibold">{(meta as Record<string, unknown>).n_delegated as number}</span></span>
          <span>Local: <span className="text-foreground font-semibold">{(meta as Record<string, unknown>).n_local as number}</span></span>
          {(meta as Record<string, unknown>).engine != null && (
            <span>Engine: <span className="text-foreground font-semibold">{(meta as Record<string, unknown>).engine as string}</span></span>
          )}
          {(meta as Record<string, unknown>).loss_min != null && (meta as Record<string, unknown>).loss_max != null && (
            <span>Loss range: <span className="text-foreground font-semibold">
              {((meta as Record<string, unknown>).loss_min as number).toFixed(4)} – {((meta as Record<string, unknown>).loss_max as number).toFixed(4)}
            </span></span>
          )}
          {(meta as Record<string, unknown>).wall_time_s != null && (
            <span>Wall time: <span className="text-foreground font-semibold">{((meta as Record<string, unknown>).wall_time_s as number).toFixed(1)}s</span></span>
          )}
        </div>
      )}
      {meta && mode === "tde" && (
        <ChainIntegrityBanner
          verified={Boolean((meta as Record<string, unknown>).chain_verified)}
          problems={((meta as Record<string, unknown>).chain_problems as Record<string, unknown>[]) ?? []}
        />
      )}
      {meta && mode !== "tde" && (
        <div className="flex flex-wrap items-center gap-4 text-xs text-muted-foreground font-mono">
          {"n_iters" in meta && (
            <span>Iterations: <span className="text-foreground font-semibold">{meta.n_iters}</span></span>
          )}
          {"n_workers" in meta && (
            <span>Workers: <span className="text-foreground font-semibold">{meta.n_workers}</span></span>
          )}
          {"loss_min" in meta && meta.loss_min != null && meta.loss_max != null && (
            <span>Loss range: <span className="text-foreground font-semibold">{meta.loss_min.toFixed(4)} – {meta.loss_max.toFixed(4)}</span></span>
          )}
          {"quality_score" in meta && meta.quality_score != null && (
            <span>Quality: <span className="text-foreground font-semibold">{((meta.quality_score as number) * 100).toFixed(0)}%</span></span>
          )}
          <span className={
            (meta as Record<string, unknown>).state === "complete" || (meta as Record<string, unknown>).state === "success"
              ? "text-emerald-500"
              : (meta as Record<string, unknown>).state === "running"
              ? "text-sky-400"
              : "text-red-400"
          }>
            {(meta as Record<string, unknown>).state as string}
          </span>
        </div>
      )}

      {graphQ.isLoading && (
        <div className="flex items-center gap-2 text-xs text-muted-foreground py-4">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          Loading graph…
        </div>
      )}
      {graphQ.isError && (
        <div className="flex items-center gap-2 text-xs text-destructive py-2">
          <AlertCircle className="h-3.5 w-3.5" />
          {graphQ.error.message}
        </div>
      )}

      {graphQ.data && (
        <>
          {(((graphQ.data?.meta as Record<string, unknown>)?.loss_curve as LossPt[] | undefined)?.length ?? 0) >= 2 && (
            <LossCurvePanel lossCurve={(graphQ.data.meta as Record<string, unknown>).loss_curve as LossPt[]} />
          )}
          <div
            className="w-full rounded-md border border-border overflow-hidden"
            style={{ height: canvasHeight, background: COLORS.bg }}
          >
            <ReactFlow
              nodes={rfNodes}
              edges={rfEdges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              nodeTypes={NODE_TYPES}
              onInit={(instance) => { rfRef.current = instance; }}
              onNodeMouseEnter={handleNodeEnter}
              onNodeMouseMove={handleNodeMove}
              onNodeMouseLeave={() => setTooltip(null)}
              minZoom={0.05}
              maxZoom={2}
              proOptions={{ hideAttribution: true }}
            >
              <Background
                variant={BackgroundVariant.Dots}
                color={COLORS.border}
                gap={20}
                size={1}
              />
              <Controls
                showInteractive={false}
                style={{ background: COLORS.card, border: `1px solid ${COLORS.border}`, borderRadius: 6 }}
              />
              <MiniMap
                nodeColor={(n) => String((n.data as RawNode)?.color ?? COLORS.muted)}
                maskColor="rgba(0,0,0,0.45)"
                style={{ background: COLORS.card, border: `1px solid ${COLORS.border}`, borderRadius: 6 }}
              />
            </ReactFlow>

          </div>
          <GraphLegend mode={mode} />
        </>
      )}

      {/* Tooltip portal — rendered in document.body to escape overflow:hidden */}
      {tooltip && createPortal(
        <div style={{
          position: "fixed",
          left: Math.min(tooltip.cx + 16, window.innerWidth - 260),
          top: Math.max(8, tooltip.cy - 20),
          zIndex: 9999,
          pointerEvents: "none",
        }}>
          <NodeTooltipPanel data={tooltip.data} mode={mode} />
        </div>,
        document.body,
      )}
    </div>
  );
}

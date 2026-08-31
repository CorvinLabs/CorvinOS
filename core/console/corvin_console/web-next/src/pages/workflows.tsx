/**
 * Workflow Builder — ADR-0039 Phases 1-7.
 *
 * Pages exported:
 *   WorkflowsListPage   /app/workflows
 *   WorkflowEditorPage  /app/workflows/:wid
 *   WorkflowRunsPage    /app/workflows/:wid/runs
 *   WorkflowRunDetailPage /app/workflows/:wid/runs/:rid
 */
import * as React from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCCCEvents } from "../lib/ccc-bus";
import {
  AlertTriangle,
  ArrowLeft,
  BookOpen,
  Calendar,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Check,
  CheckCircle2,
  Clock,
  Copy,
  Image,
  Lightbulb,
  Loader2,
  Mic,
  MicOff,
  Package,
  Play,
  Plus,
  Square,
  ThumbsDown,
  ThumbsUp,
  Upload,
  Volume2,
  VolumeX,
  Workflow,
  X,
  Zap,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";
import {
  approveRunNode,
  rejectRunNode,
  createWorkflow,
  deleteWorkflow,
  explainWorkflow,
  getWorkflow,
  getRun,
  getWorkflowRunMedia,
  workflowMediaZipUrl,
  getWorkflowRunTables,
  getWorkflowRunTablePage,
  listRuns,
  listWorkflows,
  putWorkflowYaml,
  putWorkflowSchedule,
  deleteWorkflowSchedule,
  deleteRun,
  importWorkflow,
  getLicenseInfo,
  type ChatEntry,
  type GraphNode,
  type LicenseInfo,
  type RunMeta,
  type WorkflowMeta,
  type WorkflowRunEvent,
} from "@/lib/api";
import { MediaGallery, ImageCard, type MediaItem } from "@/components/media";
import { DataTable, TableCard, TableOverlay, type DataTableFetchParams, type TableEvent } from "@/components/table";

// ── Shared helpers ────────────────────────────────────────────────────────

const PHASE_LABELS: Record<string, string> = {
  discovering: "Discovering",
  structuring: "Structuring",
  detailing: "Detailing",
  ready: "Ready",
};

const PHASE_ORDER = ["discovering", "structuring", "detailing", "ready"];

function PhaseBreadcrumb({ phase }: { phase: string }) {
  const idx = PHASE_ORDER.indexOf(phase);
  return (
    <div className="flex items-center gap-1 text-xs">
      {PHASE_ORDER.map((p, i) => (
        <React.Fragment key={p}>
          <span
            className={cn(
              "rounded px-1.5 py-0.5 font-medium",
              i === idx
                ? "bg-accent/20 text-accent"
                : i < idx
                  ? "text-muted-foreground line-through"
                  : "text-muted-foreground/50",
            )}
          >
            {PHASE_LABELS[p]}
          </span>
          {i < PHASE_ORDER.length - 1 && (
            <ChevronRight className="h-3 w-3 text-muted-foreground/30" />
          )}
        </React.Fragment>
      ))}
    </div>
  );
}

function formatTs(ts: number | null | undefined): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDuration(started: number, finished: number | null): string {
  if (!finished) return "—";
  const secs = Math.round(finished - started);
  if (secs < 60) return `${secs}s`;
  return `${Math.floor(secs / 60)}m ${secs % 60}s`;
}

// ── SVG Canvas ───────────────────────────────────────────────────────────

interface NodeState {
  [nodeId: string]: "idle" | "running" | "ok" | "failed" | "waiting";
}

const NODE_TYPE_COLOR: Record<string, string> = {
  agent: "hsl(var(--accent))",
  fan_out: "hsl(var(--primary))",
  delegation_loop: "hsl(262 52% 60%)",
  http: "hsl(199 80% 55%)",
  condition: "hsl(38 90% 60%)",
  delay: "hsl(var(--muted-foreground))",
  trigger: "hsl(142 60% 50%)",
  deliver: "hsl(142 60% 44%)",  // emerald — delivery = "sent"
  approval: "hsl(38 85% 55%)",  // amber — waiting for human
  // ADR-0188 node types
  code: "hsl(199 80% 55%)",      // blue — deterministic, same family as http
  merge: "hsl(199 60% 45%)",     // muted blue — deterministic fan-in
  route: "hsl(38 90% 60%)",      // amber — branching, same family as condition
  answer: "hsl(142 60% 44%)",    // emerald — same family as deliver
  ask_human: "hsl(38 85% 55%)",  // amber — same family as approval
};

const NODE_TYPE_ICON: Record<string, string> = {
  delegation_loop: "⟳",
  fan_out: "⤆",
  deliver: "✉",
  approval: "👤",
  code: "λ",
  merge: "⑂",
  route: "⑃",
  answer: "💬",
  ask_human: "🙋",
  agent: "→",
};

function nodeColor(type: string, state: "idle" | "running" | "ok" | "failed" | "waiting"): string {
  if (state === "waiting") return "hsl(38 85% 65%)";
  if (state === "running") return "hsl(38 90% 55%)";
  if (state === "ok") return "hsl(142 60% 50%)";
  if (state === "failed") return "hsl(var(--destructive))";
  return NODE_TYPE_COLOR[type] ?? "hsl(var(--muted-foreground))";
}

// Groups nodes into visual "islands" for the organic layout: a node starts a
// new island at a root, a merge point (>1 dependency), or right after a
// branch point (its single parent has >1 children) — everything else joins
// its parent's island. A simple linear chain inside one branch (the common
// shape: route -> code -> deliver) ends up as one island — a clean, single
// horizontal pipeline segment, not a jittered blob.
function computeClusters(nodes: GraphNode[]): Map<string, string> {
  const idSet = new Set(nodes.map((n) => n.id));
  const parentsOf = new Map<string, string[]>(
    nodes.map((n) => [n.id, (n.depends_on ?? []).filter((d) => idSet.has(d))]),
  );
  const childCount = new Map<string, number>();
  for (const n of nodes) {
    for (const dep of n.depends_on ?? []) {
      if (idSet.has(dep)) childCount.set(dep, (childCount.get(dep) ?? 0) + 1);
    }
  }
  const cache = new Map<string, string>();
  function findCluster(id: string, seen: Set<string>): string {
    if (cache.has(id)) return cache.get(id)!;
    if (seen.has(id)) return id; // cycle guard
    seen.add(id);
    const parents = parentsOf.get(id) ?? [];
    if (parents.length !== 1) {
      cache.set(id, id); // root or merge point — starts its own island
      return id;
    }
    const [parent] = parents;
    if ((childCount.get(parent) ?? 0) > 1) {
      cache.set(id, id); // right after a branch point — starts a new island
      return id;
    }
    const c = findCluster(parent, seen);
    cache.set(id, c);
    return c;
  }
  const result = new Map<string, string>();
  nodes.forEach((n) => result.set(n.id, findCluster(n.id, new Set())));
  return result;
}

export type IslandBox = { id: string; x: number; y: number; w: number; h: number };

const LAYOUT_NODE_W = 160;
const LAYOUT_NODE_H = 56;
const LAYOUT_GAP_X = 40;
const LAYOUT_GAP_Y = 72;
// Vertical gap between two different islands/lanes — deliberately larger
// than the intra-island node gap, so each island reads as a clearly
// separated, calm group instead of one continuous dense grid.
const ISLAND_LANE_GAP = LAYOUT_NODE_H + LAYOUT_GAP_Y * 2;

// Swim-lane layout: every island (a straight chain of nodes, see
// computeClusters above) is assigned a horizontal lane. A chain that
// continues from a single upstream island inherits that island's lane
// whenever it's free, so one "main line" flows straight across the canvas;
// everything else (branches, merges, parallel chains) takes the next free
// lane. Nodes never move off their exact dependency-level column and never
// jitter within an island — the result is a small number of clean,
// deliberately separated horizontal "islands" instead of a jittered grid,
// matching how n8n/Zapier/Make lay out parallel branches.
function computeIslandLayout(nodes: GraphNode[]): {
  positions: Map<string, { x: number; y: number; level: number }>;
  islands: IslandBox[];
} {
  const idSet = new Set(nodes.map((n) => n.id));
  const depMap = new Map<string, string[]>(
    nodes.map((n) => [n.id, (n.depends_on ?? []).filter((d) => idSet.has(d))]),
  );
  const levels = new Map<string, number>();
  const visited = new Set<string>();
  function getLevel(id: string): number {
    if (levels.has(id)) return levels.get(id)!;
    if (visited.has(id)) return 0; // cycle guard
    visited.add(id);
    const deps = depMap.get(id) ?? [];
    const level = deps.length === 0 ? 0 : Math.max(...deps.map(getLevel)) + 1;
    levels.set(id, level);
    return level;
  }
  nodes.forEach((n) => getLevel(n.id));

  const clusters = computeClusters(nodes);
  const clusterMembers = new Map<string, string[]>();
  nodes.forEach((n) => {
    const c = clusters.get(n.id)!;
    const arr = clusterMembers.get(c) ?? [];
    arr.push(n.id);
    clusterMembers.set(c, arr);
  });
  for (const ids of clusterMembers.values()) {
    ids.sort((a, b) => levels.get(a)! - levels.get(b)!);
  }

  const islandList = Array.from(clusterMembers.entries())
    .map(([id, ids]) => ({
      id,
      ids,
      minLevel: Math.min(...ids.map((i) => levels.get(i)!)),
      maxLevel: Math.max(...ids.map((i) => levels.get(i)!)),
    }))
    .sort((a, b) => a.minLevel - b.minLevel);

  const laneOfIsland = new Map<string, number>();
  const laneOccupancy: { lane: number; minLevel: number; maxLevel: number }[] = [];
  function laneIsFree(lane: number, minL: number, maxL: number): boolean {
    return !laneOccupancy.some(
      (o) => o.lane === lane && !(maxL < o.minLevel || minL > o.maxLevel),
    );
  }

  for (const island of islandList) {
    const firstNode = island.ids[0];
    const parents = depMap.get(firstNode) ?? [];
    let lane: number | null = null;
    if (parents.length === 1) {
      const parentIsland = clusters.get(parents[0])!;
      const candidate = laneOfIsland.get(parentIsland);
      if (candidate !== undefined && laneIsFree(candidate, island.minLevel, island.maxLevel)) {
        lane = candidate; // continue the same lane as a single upstream chain
      }
    }
    if (lane === null) {
      lane = 0;
      while (!laneIsFree(lane, island.minLevel, island.maxLevel)) lane++;
    }
    laneOfIsland.set(island.id, lane);
    laneOccupancy.push({ lane, minLevel: island.minLevel, maxLevel: island.maxLevel });
  }

  const positions = new Map<string, { x: number; y: number; level: number }>();
  const islands: IslandBox[] = [];
  const PAD = 14;
  // A chain that sits dead-flat on one line reads as too rigid ("in Reihe").
  // Instead each island flows gently through three levels — up, centre,
  // down, centre, repeat — so a multi-node chain visibly spans multiple
  // heights while still returning to its lane's centre line periodically
  // (keeping lanes readable and non-overlapping). Single-node islands
  // (branch/merge points) stay exactly on the lane centre.
  const WAVE_AMPLITUDE = 46;
  for (const island of islandList) {
    const lane = laneOfIsland.get(island.id)!;
    const baseY = lane * ISLAND_LANE_GAP;
    let minY = Infinity;
    let maxY = -Infinity;
    island.ids.forEach((id, idx) => {
      const lvl = levels.get(id)!;
      const wave = island.ids.length > 1 ? Math.sin(idx * (Math.PI / 2)) * WAVE_AMPLITUDE : 0;
      const y = baseY + wave;
      positions.set(id, { x: lvl * (LAYOUT_NODE_W + LAYOUT_GAP_X), y, level: lvl });
      minY = Math.min(minY, y);
      maxY = Math.max(maxY, y);
    });
    if (island.ids.length > 1) {
      islands.push({
        id: island.id,
        x: island.minLevel * (LAYOUT_NODE_W + LAYOUT_GAP_X) - PAD,
        y: minY - PAD,
        w: (island.maxLevel - island.minLevel) * (LAYOUT_NODE_W + LAYOUT_GAP_X) + LAYOUT_NODE_W + PAD * 2,
        h: (maxY - minY) + LAYOUT_NODE_H + PAD * 2,
      });
    }
  }
  return { positions, islands };
}

function computeLayout(
  nodes: GraphNode[],
  organic = false,
): Map<string, { x: number; y: number; level: number }> {
  if (organic) return computeIslandLayout(nodes).positions;

  const idSet = new Set(nodes.map((n) => n.id));
  const depMap = new Map<string, string[]>(
    nodes.map((n) => [n.id, (n.depends_on ?? []).filter((d) => idSet.has(d))]),
  );
  const levels = new Map<string, number>();
  const visited = new Set<string>();

  function getLevel(id: string): number {
    if (levels.has(id)) return levels.get(id)!;
    if (visited.has(id)) return 0; // cycle guard
    visited.add(id);
    const deps = depMap.get(id) ?? [];
    const level = deps.length === 0 ? 0 : Math.max(...deps.map(getLevel)) + 1;
    levels.set(id, level);
    return level;
  }

  nodes.forEach((n) => getLevel(n.id));

  const byLevel = new Map<number, string[]>();
  for (const [id, lvl] of levels) {
    const arr = byLevel.get(lvl) ?? [];
    arr.push(id);
    byLevel.set(lvl, arr);
  }

  const positions = new Map<string, { x: number; y: number; level: number }>();
  for (const [lvl, ids] of byLevel) {
    ids.forEach((id, i) => {
      positions.set(id, {
        x: lvl * (LAYOUT_NODE_W + LAYOUT_GAP_X),
        y: i * (LAYOUT_NODE_H + LAYOUT_GAP_Y),
        level: lvl,
      });
    });
  }
  return positions;
}

function AwpCanvas({
  nodes,
  nodeStates = {},
  selectedId,
  onSelect,
  className,
  organic = false,
}: {
  nodes: GraphNode[];
  nodeStates?: NodeState;
  selectedId?: string | null;
  onSelect?: (id: string) => void;
  className?: string;
  organic?: boolean;
}) {
  const positions = React.useMemo(() => computeLayout(nodes, organic), [nodes, organic]);
  const islands = React.useMemo(
    () => (organic ? computeIslandLayout(nodes).islands : []),
    [nodes, organic],
  );

  const NODE_W = 168;
  const NODE_H = 64;
  const PAD = 24;

  const maxX = Math.max(0, ...Array.from(positions.values()).map((p) => p.x));
  const maxY = Math.max(0, ...Array.from(positions.values()).map((p) => p.y));
  const svgW = maxX + NODE_W + PAD * 2;
  const svgH = Math.max(200, maxY + NODE_H + PAD * 2);

  // ── Pan / Zoom state ───────────────────────────────────────────────────
  const [viewport, setViewport] = React.useState({ x: 0, y: 0, scale: 1 });
  const containerRef = React.useRef<HTMLDivElement>(null);
  const svgDimsRef = React.useRef({ w: svgW, h: svgH });
  svgDimsRef.current = { w: svgW, h: svgH };

  const isDragging = React.useRef(false);
  const dragMoved = React.useRef(false);
  const dragStart = React.useRef({ x: 0, y: 0, vpx: 0, vpy: 0 });

  const fitView = React.useCallback(() => {
    const { w, h } = svgDimsRef.current;
    const el = containerRef.current;
    if (!el || !w || !h) return;
    const rect = el.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const scale = Math.min(rect.width / w, rect.height / h, 1) * 0.88;
    setViewport({
      x: (rect.width - w * scale) / 2,
      y: Math.max(8, (rect.height - h * scale) / 2),
      scale,
    });
  }, []);

  // Auto-fit whenever nodes change
  React.useEffect(() => {
    const id = requestAnimationFrame(fitView);
    return () => cancelAnimationFrame(id);
  }, [nodes, fitView]);

  function handleWheel(e: React.WheelEvent) {
    e.preventDefault();
    const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    const el = containerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    setViewport((v) => {
      const newScale = Math.max(0.12, Math.min(4, v.scale * factor));
      return {
        x: mx - (mx - v.x) * (newScale / v.scale),
        y: my - (my - v.y) * (newScale / v.scale),
        scale: newScale,
      };
    });
  }

  function handleMouseDown(e: React.MouseEvent) {
    isDragging.current = true;
    dragMoved.current = false;
    dragStart.current = {
      x: e.clientX,
      y: e.clientY,
      vpx: viewport.x,
      vpy: viewport.y,
    };
  }

  function handleMouseMove(e: React.MouseEvent) {
    if (!isDragging.current) return;
    const dx = e.clientX - dragStart.current.x;
    const dy = e.clientY - dragStart.current.y;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) dragMoved.current = true;
    setViewport((v) => ({
      ...v,
      x: dragStart.current.vpx + dx,
      y: dragStart.current.vpy + dy,
    }));
  }

  function handleMouseUp() {
    isDragging.current = false;
  }

  const idSet = new Set(nodes.map((n) => n.id));
  const edges: { from: string; to: string }[] = [];
  for (const node of nodes) {
    for (const dep of node.depends_on ?? []) {
      if (idSet.has(dep)) edges.push({ from: dep, to: node.id });
    }
  }

  if (nodes.length === 0) {
    return (
      <div
        className={cn(
          "flex h-full items-center justify-center text-sm text-muted-foreground",
          className,
        )}
      >
        <div className="text-center">
          <Workflow className="mx-auto mb-2 h-8 w-8 opacity-30" />
          <p>Canvas is empty. Use the chat to build your workflow.</p>
        </div>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className={cn("overflow-hidden relative select-none", className)}
      style={{ cursor: isDragging.current ? "grabbing" : "grab" }}
      onWheel={handleWheel}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
    >
      <svg
        width={svgW}
        height={svgH}
        style={{
          transform: `translate(${viewport.x}px,${viewport.y}px) scale(${viewport.scale})`,
          transformOrigin: "0 0",
          display: "block",
          overflow: "visible",
        }}
      >
        <defs>
          <marker
            id="arrowhead"
            markerWidth="8"
            markerHeight="8"
            refX="4"
            refY="4"
            orient="auto"
          >
            <path d="M0,0 L0,8 L8,4 z" fill="hsl(var(--foreground)/0.75)" />
          </marker>
        </defs>

        {/* Island frames — a soft rounded card behind each multi-node chain,
            drawn first so it sits behind edges/nodes. This is what makes the
            "organic" layout read as distinct, calm groups (islands) instead
            of one dense grid, matching how n8n/Make show parallel branches. */}
        {islands.map((box) => (
          <rect
            key={`island-${box.id}`}
            x={PAD + box.x}
            y={PAD + box.y}
            width={box.w}
            height={box.h}
            rx={16}
            ry={16}
            fill="hsl(var(--muted-foreground)/0.05)"
            stroke="hsl(var(--muted-foreground)/0.18)"
            strokeWidth={1}
            strokeDasharray="4 3"
          />
        ))}

        {edges.map((e) => {
          const from = positions.get(e.from);
          const to = positions.get(e.to);
          if (!from || !to) return null;
          const x1 = PAD + from.x + NODE_W;
          const y1 = PAD + from.y + NODE_H / 2;
          const x2 = PAD + to.x;
          const y2 = PAD + to.y + NODE_H / 2;
          const cx = (x1 + x2) / 2;
          return (
            <path
              key={`${e.from}->${e.to}`}
              d={`M ${x1} ${y1} C ${cx} ${y1}, ${cx} ${y2}, ${x2} ${y2}`}
              fill="none"
              stroke="hsl(var(--foreground)/0.55)"
              strokeWidth="2"
              markerEnd="url(#arrowhead)"
            />
          );
        })}

        {nodes.map((node) => {
          const pos = positions.get(node.id);
          if (!pos) return null;
          const x = PAD + pos.x;
          const y = PAD + pos.y;
          const state = nodeStates[node.id] ?? "idle";
          const color = nodeColor(node.type, state);
          const isSelected = selectedId === node.id;
          return (
            <g
              key={node.id}
              transform={`translate(${x},${y})`}
              onClick={() => { if (!dragMoved.current) onSelect?.(node.id); }}
              style={{ cursor: "pointer" }}
            >
              <rect
                width={NODE_W}
                height={NODE_H}
                rx={8}
                ry={8}
                fill={color}
                opacity={0.1}
              />
              <rect
                width={NODE_W}
                height={NODE_H}
                rx={8}
                ry={8}
                fill="none"
                stroke={isSelected ? color : "hsl(var(--border))"}
                strokeWidth={isSelected ? 2.5 : 1}
              />
              <rect x={0} y={0} width={7} height={NODE_H} rx={3} fill={color} />
              {/* Type badge — a filled color circle with the type icon, far more
                  scannable at a glance than the previous 9px colored glyph. */}
              <circle cx={22} cy={19} r={11} fill={color} opacity={0.92} />
              <text x={22} y={23} fontSize={12} textAnchor="middle" fill="hsl(var(--card))" fontFamily="inherit">
                {NODE_TYPE_ICON[node.type] ?? "•"}
              </text>
              <text x={40} y={17} fontSize={11} fontWeight="700"
                fill="hsl(var(--foreground))" fontFamily="inherit">
                {node.id.length > 15 ? node.id.slice(0, 14) + "…" : node.id}
              </text>
              <text x={40} y={30} fontSize={9.5} fontWeight="600"
                fill={color} fontFamily="inherit" letterSpacing="0.02em">
                {node.type.toUpperCase()}
                {state === "waiting" ? " ⏸" : state === "running" ? " ⟳" : state === "ok" ? " ✓" : state === "failed" ? " ✗" : ""}
              </text>
              <text x={12} y={47} fontSize={8}
                fill="hsl(var(--muted-foreground)/0.7)" fontFamily="inherit">
                {(node.instructions || "").slice(0, 24)}{(node.instructions || "").length > 24 ? "…" : ""}
              </text>
              {((node as GraphNode & { forge_tools?: string[]; skills?: string[] }).forge_tools?.length ?? 0) > 0 && (
                <text x={12} y={NODE_H - 6} fontSize={7.5}
                  fill="hsl(38 90% 55%)" fontFamily="inherit">
                  🔧 {(node as GraphNode & { forge_tools?: string[] }).forge_tools!.slice(0, 2).join(",")}
                </text>
              )}
              {((node as GraphNode & { skills?: string[] }).skills?.length ?? 0) > 0 && (
                <text x={((node as GraphNode & { forge_tools?: string[] }).forge_tools?.length ?? 0) > 0 ? NODE_W / 2 : 12}
                  y={NODE_H - 6} fontSize={7.5}
                  fill="hsl(262 52% 60%)" fontFamily="inherit">
                  ✦ {(node as GraphNode & { skills?: string[] }).skills!.slice(0, 1).join(",")}
                </text>
              )}
            </g>
          );
        })}
      </svg>

      {/* Zoom / Fit controls */}
      <div className="absolute bottom-2 right-2 z-10 flex gap-1">
        <button
          className="rounded border border-border bg-background/80 px-2 py-0.5 text-xs shadow backdrop-blur hover:bg-muted active:scale-95"
          title="Zoom in"
          onClick={() => setViewport((v) => ({ ...v, scale: Math.min(v.scale * 1.25, 4) }))}
        >+</button>
        <button
          className="rounded border border-border bg-background/80 px-2 py-0.5 text-xs shadow backdrop-blur hover:bg-muted active:scale-95"
          title="Zoom out"
          onClick={() => setViewport((v) => ({ ...v, scale: Math.max(v.scale * 0.8, 0.12) }))}
        >−</button>
        <button
          className="rounded border border-border bg-background/80 px-2 py-0.5 text-xs shadow backdrop-blur hover:bg-muted active:scale-95"
          title="Fit view"
          onClick={fitView}
        >⊙</button>
      </div>
    </div>
  );
}

// ── Delivery inspector (editable deliver nodes) ──────────────────────────

import { listMessengerChats } from "@/lib/api";

const MESSENGERS: { id: string; label: string; icon: string; hint?: string }[] = [
  { id: "discord",  label: "Discord",   icon: "💬" },
  { id: "telegram", label: "Telegram",  icon: "✈️" },
  { id: "slack",    label: "Slack",     icon: "🔔" },
  { id: "whatsapp", label: "WhatsApp",  icon: "📱", hint: "Send a message to the bot first so the chat appears here" },
  { id: "email",    label: "Email",     icon: "📧", hint: "Enter recipient address as destination" },
  { id: "teams",    label: "Teams",     icon: "👥", hint: "Enter webhook URL as destination" },
  { id: "signal",   label: "Signal",    icon: "🔐", hint: "Send a message to the bot first so the chat appears here" },
];

function DeliveryInspector({
  config,
  nodeId: _nodeId,
  wid,
  yaml,
  csrf,
  onYamlChange,
}: {
  config: Record<string, unknown>;
  nodeId: string;
  wid: string;
  yaml: string;
  csrf: string;
  onYamlChange: (yaml: string) => void;
}) {
  const qc = useQueryClient();
  const [channel, setChannel] = React.useState(String(config.channel ?? "discord"));
  const [chatId, setChatId] = React.useState(String(config.chat_id ?? "auto"));
  const [format, setFormat] = React.useState(String(config.format ?? "markdown"));
  const [voice, setVoice] = React.useState(Boolean(config.voice ?? false));
  const [saving, setSaving] = React.useState(false);

  const messengerMeta = MESSENGERS.find((m) => m.id === channel);

  const chatsQuery = useQuery({
    queryKey: ["messenger-chats", channel],
    queryFn: ({ signal }) => listMessengerChats(channel, signal),
    staleTime: 60_000,
    retry: false,
  });
  const chats = React.useMemo(() => chatsQuery.data?.chats ?? [], [chatsQuery.data?.chats]);

  // Resolve current chatId to a human label for display
  const resolvedLabel = React.useMemo(() => {
    if (chatId === "auto") return "auto — same chat as trigger";
    const found = chats.find((c) => c.id === chatId || c.name === chatId);
    return found ? found.name : chatId;
  }, [chatId, chats]);

  const patchYaml = (ch: string, cid: string, fmt: string, v: boolean): string => {
    let patched = yaml
      .replace(/(channel\s*:\s*)(['"]?)([^\n'"]+)(['"]?)/g, (_m, p) => `${p}${ch}`)
      .replace(/(chat_id\s*:\s*)(['"]?)([^\n'"]+)(['"]?)/g, (_m, p) => `${p}"${cid}"`)
      .replace(/(format\s*:\s*)(['"]?)([^\n'"]+)(['"]?)/g, (_m, p) => `${p}${fmt}`);
    // Patch or inject voice field
    if (/voice\s*:/.test(patched)) {
      patched = patched.replace(/(voice\s*:\s*)(true|false)/g, `$1${v}`);
    } else {
      // Inject after format line
      patched = patched.replace(/(format\s*:\s*\S+)/, `$1\n        voice: ${v}`);
    }
    return patched;
  };

  const save = async (ch: string, cid: string, fmt: string, v: boolean) => {
    setSaving(true);
    try {
      const { putWorkflowYaml } = await import("@/lib/api");
      const newYaml = patchYaml(ch, cid, fmt, v);
      await putWorkflowYaml(wid, newYaml, csrf);
      onYamlChange(newYaml);
      qc.invalidateQueries({ queryKey: ["workflow", wid] });
    } catch { /* silent */ } finally { setSaving(false); }
  };

  const handleChannelChange = (ch: string) => {
    setChannel(ch);
    setChatId("auto");
    save(ch, "auto", format, voice);
  };

  const handleChatChange = (cid: string) => {
    setChatId(cid);
    save(channel, cid, format, voice);
  };

  const handleFormatChange = (fmt: string) => {
    setFormat(fmt);
    save(channel, chatId, fmt, voice);
  };

  const handleVoiceChange = (v: boolean) => {
    setVoice(v);
    save(channel, chatId, format, v);
  };

  // For freetext messengers (email, teams) use an input; for others use dropdown
  const isFreetext = channel === "email" || channel === "teams";

  return (
    <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-2.5 space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-medium uppercase tracking-widest text-emerald-600 dark:text-emerald-400">
          Delivery
        </span>
        {saving && <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />}
      </div>

      {/* Messenger (channel) dropdown */}
      <div className="space-y-1">
        <label className="text-[10px] text-muted-foreground">messenger</label>
        <select
          value={channel}
          onChange={(e) => handleChannelChange(e.target.value)}
          className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-accent"
        >
          {MESSENGERS.map((m) => (
            <option key={m.id} value={m.id}>
              {m.icon} {m.label}
            </option>
          ))}
        </select>
      </div>

      {/* Destination */}
      <div className="space-y-1">
        <label className="text-[10px] text-muted-foreground">destination</label>

        {isFreetext ? (
          <>
            <input
              type="text"
              value={chatId === "auto" ? "" : chatId}
              onChange={(e) => setChatId(e.target.value)}
              onBlur={(e) => e.target.value && save(channel, e.target.value, format, voice)}
              placeholder={channel === "email" ? "user@example.com" : "https://…webhook…"}
              className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-accent"
            />
          </>
        ) : chatsQuery.isLoading ? (
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" />
            Loading {messengerMeta?.label} chats…
          </div>
        ) : chats.length > 0 ? (
          <select
            value={chatId}
            onChange={(e) => handleChatChange(e.target.value)}
            className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-accent"
          >
            <option value="auto">↩ auto — same chat as trigger</option>
            {chats.map((c) => (
              <option
                key={c.id}
                value={channel === "discord" && c.name.startsWith("#") ? c.name : c.id}
              >
                {c.label}
              </option>
            ))}
          </select>
        ) : (
          <>
            <input
              type="text"
              value={chatId === "auto" ? "" : chatId}
              onChange={(e) => setChatId(e.target.value)}
              onBlur={(e) => e.target.value && save(channel, e.target.value, format, voice)}
              placeholder="Chat ID or name"
              className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-accent"
            />
            <div className="text-[9px] text-amber-600 dark:text-amber-400">
              {messengerMeta?.hint ?? `Configure ${messengerMeta?.label ?? channel} bridge first`}
            </div>
          </>
        )}

        {/* Always show resolved label */}
        {!isFreetext && chatId && chatId !== "auto" && (
          <div className="text-[9px] text-muted-foreground">
            <span className="text-foreground font-medium">{resolvedLabel}</span>
            {" · "}
            <span className="font-mono opacity-60">{chatId}</span>
            {chatsQuery.data && chats.length > 0 && (
              <span className="ml-1 opacity-50">
                · {chats.length} {channel === "discord" ? "channels" : "chats"}
                {chatsQuery.data.chats.some((c) => c.source === "inbox") ? " (inbox)" : ""}
              </span>
            )}
          </div>
        )}
      </div>

      {/* Format */}
      <div className="space-y-1">
        <label className="text-[10px] text-muted-foreground">format</label>
        <select
          value={format}
          onChange={(e) => handleFormatChange(e.target.value)}
          className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-accent"
        >
          <option value="markdown">markdown</option>
          <option value="text">plain text</option>
        </select>
      </div>

      {/* Voice note toggle */}
      <label className="flex cursor-pointer items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-1.5 text-xs font-medium">
            <Volume2 className="h-3.5 w-3.5 text-muted-foreground" />
            Also send voice note
          </div>
          <p className="text-[9px] text-muted-foreground mt-0.5">
            TTS reads the output aloud as an audio message
          </p>
        </div>
        <button
          role="switch"
          aria-checked={voice}
          onClick={() => handleVoiceChange(!voice)}
          className={cn(
            "relative h-5 w-9 shrink-0 rounded-full border-2 border-transparent transition-colors",
            voice ? "bg-accent" : "bg-input",
          )}
        >
          <span
            className={cn(
              "block h-4 w-4 rounded-full bg-white shadow-sm transition-transform",
              voice ? "translate-x-4" : "translate-x-0",
            )}
          />
        </button>
      </label>
    </div>
  );
}

// ── Node inspector ────────────────────────────────────────────────────────

function NodeInspector({
  node,
  nodeState,
  wid,
  yaml,
  csrf,
  onYamlChange,
}: {
  node: GraphNode | null;
  nodeState?: "idle" | "running" | "ok" | "failed" | "waiting";
  wid?: string;
  yaml?: string;
  csrf?: string;
  onYamlChange?: (yaml: string) => void;
}) {
  if (!node) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        <p className="text-center px-4">Click a node on the canvas to inspect it.</p>
      </div>
    );
  }

  const stateColor =
    nodeState === "waiting"
      ? "text-amber-400"
      : nodeState === "running"
        ? "text-amber-500"
        : nodeState === "ok"
          ? "text-emerald-500"
          : nodeState === "failed"
            ? "text-destructive"
            : "text-muted-foreground";

  return (
    <div className="space-y-4 p-4 text-sm">
      <div>
        <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-muted-foreground">
          Node
        </div>
        <div className="font-mono font-semibold">{node.id}</div>
      </div>
      <div className="flex flex-wrap gap-2">
        <Badge variant="outline" className="font-mono text-[10px]">
          {node.type}
        </Badge>
        {nodeState && nodeState !== "idle" && (
          <Badge className={cn("text-[10px]", stateColor)}>{nodeState}</Badge>
        )}
      </div>
      {node.agent && (
        <div>
          <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-muted-foreground">
            Agent
          </div>
          <div className="font-mono text-xs">{node.agent}</div>
        </div>
      )}
      {node.depends_on?.length > 0 && (
        <div>
          <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-muted-foreground">
            Depends on
          </div>
          <div className="flex flex-wrap gap-1">
            {node.depends_on.map((d) => (
              <Badge key={d} variant="secondary" className="font-mono text-[10px]">
                {d}
              </Badge>
            ))}
          </div>
        </div>
      )}
      {node.instructions && (
        <div>
          <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-muted-foreground">
            Instructions
          </div>
          <p className="text-xs text-muted-foreground leading-relaxed">{node.instructions}</p>
        </div>
      )}
      {node.type === "deliver" && node.config && wid && yaml && csrf && onYamlChange && (
        <DeliveryInspector
          config={node.config}
          nodeId={node.id}
          wid={wid}
          yaml={yaml}
          csrf={csrf}
          onYamlChange={onYamlChange}
        />
      )}
      {/* MCP connectors */}
      {node.tools && node.tools.length > 0 && (
        <div>
          <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-muted-foreground">
            Connectors
          </div>
          <div className="flex flex-wrap gap-1">
            {node.tools.map((t) => (
              <Badge key={t} variant="outline" className="font-mono text-[10px] text-accent">
                {t}
              </Badge>
            ))}
          </div>
        </div>
      )}
      {/* Forge tools */}
      {(node as GraphNode & { forge_tools?: string[] }).forge_tools?.length ? (
        <div>
          <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-muted-foreground">
            Forge tools
          </div>
          <div className="flex flex-wrap gap-1">
            {(node as GraphNode & { forge_tools?: string[] }).forge_tools!.map((t) => (
              <Badge key={t} variant="secondary" className="font-mono text-[10px] text-amber-600 dark:text-amber-400">
                🔧 {t}
              </Badge>
            ))}
          </div>
        </div>
      ) : null}
      {/* Skills */}
      {(node as GraphNode & { skills?: string[] }).skills?.length ? (
        <div>
          <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-muted-foreground">
            Skills
          </div>
          <div className="flex flex-wrap gap-1">
            {(node as GraphNode & { skills?: string[] }).skills!.map((s) => (
              <Badge key={s} variant="secondary" className="font-mono text-[10px] text-violet-600 dark:text-violet-400">
                ✦ {s}
              </Badge>
            ))}
          </div>
        </div>
      ) : null}
      {/* Delegation loop budget */}
      {node.type === "delegation_loop" && node.config && (
        <div className="rounded-lg border border-violet-500/30 bg-violet-500/5 p-2 space-y-1">
          <div className="text-[10px] font-medium uppercase tracking-widest text-violet-600 dark:text-violet-400">
            Loop budget
          </div>
          {["max_loops", "max_total_workers"].map((k) => {
            const v = (node.config as Record<string,Record<string,unknown>>)?.budget?.[k];
            return v != null ? (
              <div key={k} className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground">{k}</span>
                <span className="font-mono">{String(v)}</span>
              </div>
            ) : null;
          })}
        </div>
      )}
      {/* Approval (HITL) node info */}
      {node.type === "approval" && (
        <div className="rounded-lg border border-amber-400/30 bg-amber-50/50 dark:bg-amber-950/20 p-2 space-y-1.5">
          <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-widest text-amber-600 dark:text-amber-400">
            <CheckCircle2 className="h-3 w-3" />
            Human Review (Newman in the Loop)
          </div>
          {(node as GraphNode & { message?: string }).message && (
            <p className="text-[11px] text-muted-foreground leading-relaxed">
              {(node as GraphNode & { message?: string }).message}
            </p>
          )}
          {nodeState === "waiting" && (
            <div className="flex items-center gap-1 text-[10px] text-amber-600 dark:text-amber-400 animate-pulse">
              <span className="h-1.5 w-1.5 rounded-full bg-amber-500" />
              Waiting for approval…
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Chat pane ─────────────────────────────────────────────────────────────

interface DesignMessage extends ChatEntry {
  pending?: boolean;
}

function TemplateOfferCard({
  offer,
  onAccept,
  onDismiss,
}: {
  offer: { key: string; yaml: string; confidence: number };
  onAccept: (key: string) => void;
  onDismiss: () => void;
}) {
  return (
    <div className="rounded-lg border border-accent/30 bg-accent/5 p-3 text-sm">
      <div className="mb-2 flex items-center justify-between">
        <span className="font-medium text-accent">Template: {offer.key.replace(/-/g, " ")}</span>
        <Badge variant="outline" className="text-[10px]">
          {Math.round(offer.confidence * 100)}% match
        </Badge>
      </div>
      <div className="flex gap-2">
        <Button size="sm" variant="default" onClick={() => onAccept(offer.key)} className="h-7 text-xs">
          Load template
        </Button>
        <Button size="sm" variant="ghost" onClick={onDismiss} className="h-7 text-xs">
          Start from scratch
        </Button>
      </div>
    </div>
  );
}

function SummaryCard({ card }: { card: Record<string, unknown> }) {
  return (
    <div className="rounded-lg border border-border bg-muted/30 p-3 text-xs">
      <div className="mb-2 font-semibold text-foreground">Checkpoint summary</div>
      {!!card.goal && <div className="mb-1"><span className="text-muted-foreground">Goal: </span>{String(card.goal)}</div>}
      {!!card.trigger && <div className="mb-1"><span className="text-muted-foreground">Trigger: </span>{String(card.trigger)}</div>}
      {Array.isArray(card.steps) && card.steps.length > 0 && (
        <div className="mb-1">
          <span className="text-muted-foreground">Steps: </span>
          <span>{(card.steps as string[]).join(" → ")}</span>
        </div>
      )}
      {Array.isArray(card.conditions) && card.conditions.length > 0 && (
        <div>
          <span className="text-muted-foreground">Conditions: </span>
          {(card.conditions as string[]).map((c, i) => (
            <div key={i} className="mt-0.5 ml-2">• {c}</div>
          ))}
        </div>
      )}
    </div>
  );
}

function ChatMessage({ msg }: { msg: DesignMessage }) {
  const isUser = msg.role === "user";
  return (
    <div className={cn("flex flex-col gap-1.5", isUser ? "items-end" : "items-start")}>
      <div
        className={cn(
          "max-w-[90%] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed",
          isUser
            ? "bg-accent text-accent-foreground rounded-br-sm"
            : "bg-muted text-foreground rounded-bl-sm",
          msg.pending && "opacity-60",
        )}
      >
        {msg.content}
        {msg.yaml_update && (
          <div className="mt-1.5 flex items-center gap-1 text-[10px] opacity-70">
            <Workflow className="h-3 w-3" />
            Canvas updated
          </div>
        )}
        {msg.phase_update && (
          <div className="mt-1 text-[10px] opacity-70">
            Phase → {PHASE_LABELS[msg.phase_update] ?? msg.phase_update}
          </div>
        )}
      </div>
      {msg.summary_card && <SummaryCard card={msg.summary_card} />}
      {msg.template_offer && (
        <div className="max-w-[90%] text-sm">
          <TemplateOfferCard offer={msg.template_offer} onAccept={() => {}} onDismiss={() => {}} />
        </div>
      )}
    </div>
  );
}

function ChatPane({
  wid,
  initialChat,
  initialPhase,
  onYamlUpdate,
  onPhaseUpdate,
  onGraphUpdate,
  csrf,
}: {
  wid: string;
  initialChat: ChatEntry[];
  initialPhase: string;
  onYamlUpdate: (yaml: string) => void;
  onPhaseUpdate: (phase: string) => void;
  onGraphUpdate: (graph: GraphNode[]) => void;
  csrf: string;
}) {
  const [messages, setMessages] = React.useState<DesignMessage[]>(initialChat);
  const [phase, setPhase] = React.useState(initialPhase);
  const [input, setInput] = React.useState("");
  const [typing, setTyping] = React.useState(false);
  const [pendingOffer, setPendingOffer] = React.useState<{ key: string; yaml: string; confidence: number } | null>(null);
  const [ws, setWs] = React.useState<WebSocket | null>(null);
  const [connected, setConnected] = React.useState(false);
  // Phase 7: voice mode — on by default
  const [voiceEnabled, setVoiceEnabled] = React.useState(true);
  const [recording, setRecording] = React.useState(false);
  const [transcribing, setTranscribing] = React.useState(false);
  const [micError, setMicError] = React.useState<string | null>(null);
  const [ttsPlaying, setTtsPlaying] = React.useState(false);
  // Ref so onopen callback always sees current voiceEnabled
  const voiceEnabledRef = React.useRef(true);
  const recorderRef = React.useRef<MediaRecorder | null>(null);
  const chunksRef = React.useRef<Blob[]>([]);
  const bottomRef = React.useRef<HTMLDivElement>(null);
  const inputRef = React.useRef<HTMLTextAreaElement>(null);

  // Keep ref in sync with state
  React.useEffect(() => { voiceEnabledRef.current = voiceEnabled; }, [voiceEnabled]);

  React.useEffect(() => {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${protocol}//${window.location.host}/v1/console/workflows/${encodeURIComponent(wid)}/chat`;
    const socket = new WebSocket(url);

    socket.onopen = () => {
      setConnected(true);
      // Always send current voice preference immediately on connect
      socket.send(JSON.stringify({ type: "set_voice", enabled: voiceEnabledRef.current, lang: "en" }));
    };
    socket.onclose = () => setConnected(false);

    socket.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (data.type === "init") {
          setPhase(data.phase ?? "discovering");
          if (data.chat?.length) setMessages(data.chat);
          if (data.graph?.length) onGraphUpdate(data.graph);
          if (data.yaml) onYamlUpdate(data.yaml);
        } else if (data.type === "typing") {
          setTyping(true);
        } else if (data.type === "voice_ack") {
          voiceEnabledRef.current = data.enabled;
        } else if (data.type === "audio") {
          // Phase 7: decode base64 OGG and play
          try {
            const bytes = Uint8Array.from(atob(data.data), (c) => c.charCodeAt(0));
            const blob = new Blob([bytes], { type: data.mime_type ?? "audio/ogg" });
            const audioUrl = URL.createObjectURL(blob);
            const audio = new Audio(audioUrl);
            setTtsPlaying(true);
            audio.onended = () => { URL.revokeObjectURL(audioUrl); setTtsPlaying(false); };
            audio.onerror = () => { URL.revokeObjectURL(audioUrl); setTtsPlaying(false); };
            audio.play().catch(() => setTtsPlaying(false));
          } catch {
            setTtsPlaying(false);
          }
        } else if (data.type === "message") {
          setTyping(false);
          const entry: DesignMessage = {
            role: data.role,
            content: data.content,
            ts: data.ts ?? Date.now() / 1000,
            yaml_update: data.yaml_update,
            phase_update: data.phase_update,
            summary_card: data.summary_card,
            template_offer: data.template_offer,
            graph: data.graph,
          };
          if (data.yaml_update) onYamlUpdate(data.yaml_update);
          if (data.phase_update) {
            setPhase(data.phase_update);
            onPhaseUpdate(data.phase_update);
          }
          if (data.graph) onGraphUpdate(data.graph);
          if (data.template_offer) setPendingOffer(data.template_offer);
          setMessages((prev) => {
            const last = prev[prev.length - 1];
            // Server echoes the user message back — just confirm the optimistic copy,
            // don't add it as a second message.
            if (entry.role === "user" && last?.pending && last.role === "user") {
              return [...prev.slice(0, -1), { ...last, pending: false }];
            }
            // Assistant reply: confirm pending user message and append the reply.
            if (last?.pending && last.role === "user") {
              return [...prev.slice(0, -1), { ...last, pending: false }, entry];
            }
            return [...prev, entry];
          });
        }
      } catch {
        // ignore malformed frames
      }
    };

    setWs(socket);
    return () => socket.close();
  }, [wid]); // eslint-disable-line react-hooks/exhaustive-deps

  // Re-send voice preference whenever it changes or WS reconnects
  React.useEffect(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "set_voice", enabled: voiceEnabled, lang: "en" }));
    }
  }, [voiceEnabled, ws]);

  React.useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, typing]);

  const send = (text?: string) => {
    const t = (text ?? input).trim();
    if (!t || !ws || ws.readyState !== WebSocket.OPEN) return;
    const optimistic: DesignMessage = {
      role: "user",
      content: t,
      ts: Date.now() / 1000,
      pending: true,
    };
    setMessages((prev) => [...prev, optimistic]);
    ws.send(JSON.stringify({ type: "user", text: t }));
    setInput("");
    setPendingOffer(null);
  };

  const acceptTemplate = (key: string) => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "accept_template", key }));
      setPendingOffer(null);
    }
  };

  // Phase 7: microphone recording with visible error handling
  const startRecording = async () => {
    setMicError(null);
    if (!navigator.mediaDevices?.getUserMedia) {
      setMicError("Microphone not available in this browser.");
      return;
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      setMicError(err instanceof Error ? err.message : "Microphone permission denied.");
      return;
    }

    // Pick best supported MIME type
    const mimeType = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg"].find(
      (m) => MediaRecorder.isTypeSupported(m)
    ) ?? "";

    const rec = new MediaRecorder(stream, mimeType ? { mimeType } : {});
    chunksRef.current = [];
    rec.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data); };
    rec.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      const blob = new Blob(chunksRef.current, { type: mimeType || "audio/webm" });
      setRecording(false);
      setTranscribing(true);
      try {
        const { transcribeAudio } = await import("@/lib/api");
        const result = await transcribeAudio(blob, csrf);
        if (result.text) send(result.text);
        else setMicError("No speech detected.");
      } catch (err) {
        setMicError(err instanceof Error ? err.message : "Transcription failed.");
      } finally {
        setTranscribing(false);
      }
    };
    rec.start();
    recorderRef.current = rec;
    setRecording(true);
  };

  const stopRecording = () => {
    recorderRef.current?.stop();
    recorderRef.current = null;
  };

  return (
    <div className="flex flex-1 min-h-0 flex-col">
      <div className="flex shrink-0 items-center justify-between border-b border-border px-3 py-2">
        <PhaseBreadcrumb phase={phase} />
        <div className="flex items-center gap-2">
          {/* TTS active indicator */}
          {ttsPlaying && (
            <span className="flex items-center gap-1 rounded-full bg-accent/10 px-2 py-0.5 text-[10px] text-accent">
              <Volume2 className="h-2.5 w-2.5 animate-pulse" />
              speaking
            </span>
          )}
          {/* Voice toggle — always visibly on/off */}
          <button
            onClick={() => setVoiceEnabled((v) => !v)}
            title={voiceEnabled ? "Voice responses ON — click to disable" : "Voice responses OFF — click to enable"}
            className={cn(
              "flex items-center gap-1 rounded-md border px-2 py-1 text-[10px] transition-colors",
              voiceEnabled
                ? "border-accent/60 bg-accent/10 text-accent"
                : "border-border text-muted-foreground/50 hover:text-muted-foreground",
            )}
          >
            {voiceEnabled ? <Volume2 className="h-3 w-3" /> : <VolumeX className="h-3 w-3" />}
            {voiceEnabled ? "Voice ON" : "Voice OFF"}
          </button>
          <div
            className={cn("h-1.5 w-1.5 rounded-full", connected ? "bg-emerald-500" : "bg-muted-foreground/40")}
            title={connected ? "Connected" : "Disconnected"}
          />
        </div>
      </div>

      {/* Building indicator — shown while assistant is thinking */}
      {typing && (
        <div className="flex shrink-0 items-center gap-2 border-b border-accent/20 bg-accent/5 px-3 py-1.5 text-xs text-accent">
          <Loader2 className="h-3 w-3 animate-spin" />
          Designing workflow…
        </div>
      )}

      <div className="flex-1 min-h-0 overflow-y-auto px-3 py-4 space-y-3">
        {messages.map((m, i) => {
          const isLast = i === messages.length - 1;
          return (
            <React.Fragment key={i}>
              <ChatMessage msg={m} />
              {isLast && m.role === "assistant" && m.template_offer && pendingOffer?.key === m.template_offer.key && (
                <TemplateOfferCard
                  offer={m.template_offer}
                  onAccept={acceptTemplate}
                  onDismiss={() => setPendingOffer(null)}
                />
              )}
            </React.Fragment>
          );
        })}
        {typing && (
          <div className="flex items-start">
            <div className="rounded-2xl rounded-bl-sm bg-muted px-3 py-2 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="shrink-0 border-t border-border p-3">
        {micError && (
          <div
            className="mb-2 flex items-center justify-between rounded-md bg-destructive/10 px-2 py-1.5 text-xs text-destructive cursor-pointer"
            onClick={() => setMicError(null)}
          >
            <span>{micError}</span>
            <span className="ml-2 opacity-60">✕</span>
          </div>
        )}
        <div className="flex gap-2">
          <Textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            placeholder={connected ? "Describe your workflow… (Enter to send)" : "Connecting…"}
            disabled={!connected || transcribing}
            className="min-h-[60px] resize-none text-sm"
            rows={2}
          />
          <div className="flex flex-col justify-end gap-1.5">
            <Button
              size="sm"
              onClick={() => send()}
              disabled={!connected || !input.trim()}
            >
              Send
            </Button>
            <Button
              size="sm"
              variant={recording ? "destructive" : "outline"}
              onClick={recording ? stopRecording : startRecording}
              disabled={!connected || transcribing}
              title={recording ? "Stop recording" : "Voice input — click to speak"}
              className={cn("px-2", recording && "animate-pulse")}
            >
              {transcribing ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : recording ? (
                <MicOff className="h-3.5 w-3.5" />
              ) : (
                <Mic className="h-3.5 w-3.5" />
              )}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Schedule modal ────────────────────────────────────────────────────────

const CRON_PRESETS = [
  { label: "Every hour", value: "0 * * * *" },
  { label: "Daily at 07:00", value: "0 7 * * *" },
  { label: "Daily at 09:00", value: "0 9 * * *" },
  { label: "Weekdays at 08:00", value: "0 8 * * 1-5" },
  { label: "Every Monday 08:00", value: "0 8 * * 1" },
];

function ScheduleModal({
  wid,
  current,
  open,
  onOpenChange,
  csrf,
  onSaved,
}: {
  wid: string;
  current: WorkflowMeta["schedule"];
  open: boolean;
  onOpenChange: (v: boolean) => void;
  csrf: string;
  onSaved: () => void;
}) {
  const [cron, setCron] = React.useState(current?.cron ?? "");
  const [tz, setTz] = React.useState(current?.timezone ?? "Europe/Berlin");
  const [overrun, setOverrun] = React.useState(current?.overrun ?? "skip");
  const [saving, setSaving] = React.useState(false);
  const [removing, setRemoving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [schedulerOk, setSchedulerOk] = React.useState<boolean | null>(null);

  const save = async () => {
    if (!cron.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const res = await putWorkflowSchedule(wid, { cron: cron.trim(), timezone: tz, overrun }, csrf) as { ok: true; schedule: WorkflowMeta["schedule"]; scheduler_registered?: boolean };
      setSchedulerOk(res.scheduler_registered ?? null);
      onSaved();
      onOpenChange(false);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    setRemoving(true);
    setError(null);
    try {
      await deleteWorkflowSchedule(wid, csrf);
      onSaved();
      onOpenChange(false);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRemoving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Calendar className="h-5 w-5 text-accent" />
            Schedule workflow
          </DialogTitle>
          <DialogDescription>Set when this workflow runs automatically.</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div>
            <Label className="mb-1.5 block text-xs">Presets</Label>
            <div className="flex flex-wrap gap-2">
              {CRON_PRESETS.map((p) => (
                <button
                  key={p.value}
                  onClick={() => setCron(p.value)}
                  className={cn(
                    "rounded-md border px-2.5 py-1 text-xs transition-colors",
                    cron === p.value
                      ? "border-accent bg-accent/10 text-accent"
                      : "border-border text-muted-foreground hover:border-accent/50",
                  )}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>
          <div>
            <Label htmlFor="cron" className="mb-1.5 block text-xs">Cron expression</Label>
            <Input
              id="cron"
              value={cron}
              onChange={(e) => setCron(e.target.value)}
              placeholder="0 7 * * *"
              className="font-mono text-sm"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label htmlFor="tz" className="mb-1.5 block text-xs">Timezone</Label>
              <Input
                id="tz"
                value={tz}
                onChange={(e) => setTz(e.target.value)}
                placeholder="Europe/Berlin"
              />
            </div>
            <div>
              <Label className="mb-1.5 block text-xs">Overrun policy</Label>
              <select
                value={overrun}
                onChange={(e) => setOverrun(e.target.value)}
                className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm"
              >
                <option value="skip">Skip</option>
                <option value="queue">Queue</option>
                <option value="parallel">Parallel</option>
              </select>
            </div>
          </div>
          {schedulerOk === false && (
            <p className="text-xs text-amber-600 dark:text-amber-400">
              Schedule saved. corvin-scheduler was not available — the cron will take effect when the adapter next starts.
            </p>
          )}
          {error && <p className="text-xs text-destructive">{error}</p>}
          <div className="flex items-center justify-between">
            {current && (
              <Button variant="destructive" size="sm" onClick={remove} disabled={removing}>
                {removing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Remove schedule"}
              </Button>
            )}
            <div className="ml-auto flex gap-2">
              <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button size="sm" onClick={save} disabled={saving || !cron.trim()}>
                {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Save schedule"}
              </Button>
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ── Bottom toolbar ────────────────────────────────────────────────────────

function EditorToolbar({
  wid,
  csrf,
  yaml,
  onTest,
  onStop,
  isRunning,
  hasSchedule,
  onSchedule,
  onImport,
  graph,
}: {
  wid: string;
  csrf: string;
  yaml: string;
  onTest: (dry: boolean) => void;
  onStop: () => void;
  isRunning: boolean;
  hasSchedule: boolean;
  onSchedule: () => void;
  onImport: (file: File) => void;
  graph: GraphNode[];
}) {
  const [saving, setSaving] = React.useState(false);
  const [saved, setSaved] = React.useState(false);
  const [exportingPkg, setExportingPkg] = React.useState(false);
  const importInputRef = React.useRef<HTMLInputElement>(null);
  const qc = useQueryClient();

  const save = async () => {
    setSaving(true);
    try {
      await putWorkflowYaml(wid, yaml, csrf);
      await qc.invalidateQueries({ queryKey: ["workflow", wid] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch {
      // ignore
    } finally {
      setSaving(false);
    }
  };

  const exportAwpkg = async () => {
    setExportingPkg(true);
    try {
      const res = await fetch(`/v1/console/workflows/${encodeURIComponent(wid)}/export.awpkg`, {
        credentials: "include",
      });
      if (!res.ok) {
        console.error("PKG export failed:", res.status, res.statusText);
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const cd = res.headers.get("content-disposition") ?? "";
      const fn = cd.match(/filename="([^"]+)"/)?.[1] ?? `${wid}.awpkg`;
      a.download = fn;
      a.click();
      URL.revokeObjectURL(url);
    } finally {
      setExportingPkg(false);
    }
  };

  return (
    <div className="flex items-center gap-1.5 border-t border-border bg-background/80 px-4 py-2.5 backdrop-blur">
      {!isRunning ? (
        <>
          <Button size="sm" variant="default" onClick={() => onTest(false)} disabled={!yaml.trim()}>
            <Play className="h-3.5 w-3.5" />
            Test
          </Button>
          <Button size="sm" variant="outline" onClick={() => onTest(true)} disabled={!yaml.trim()}>
            <Zap className="h-3.5 w-3.5" />
            Dry-run
          </Button>
        </>
      ) : (
        <Button size="sm" variant="destructive" onClick={onStop}>
          <Square className="h-3.5 w-3.5" />
          Stop
        </Button>
      )}
      <div className="mx-1 h-4 w-px bg-border" />
      <Button size="sm" variant="outline" onClick={save} disabled={saving}>
        {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : saved ? "Saved ✓" : "Save"}
      </Button>
      <div className="flex items-center gap-1">
        <Button size="sm" variant="ghost" onClick={exportAwpkg} disabled={exportingPkg || !yaml.trim()} title="Export self-contained .awpkg bundle (includes tools & skills)">
          {exportingPkg ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Package className="h-3.5 w-3.5" />}
          PKG
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => importInputRef.current?.click()}
          title="Import .awp.yaml or .awpkg"
        >
          <Upload className="h-3.5 w-3.5" />
          Import
        </Button>
        <input
          ref={importInputRef}
          type="file"
          accept=".yaml,.yml,.awpkg"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) onImport(file);
            e.target.value = "";
          }}
        />
      </div>
      <Button
        size="sm"
        variant={hasSchedule ? "secondary" : "ghost"}
        onClick={onSchedule}
        className="ml-1"
      >
        <Calendar className="h-3.5 w-3.5" />
        {hasSchedule ? "Scheduled" : "Schedule"}
      </Button>
      <div className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
        <span>{graph.length} node{graph.length !== 1 ? "s" : ""}</span>
      </div>
    </div>
  );
}

// ── Create dialog ─────────────────────────────────────────────────────────

function CreateWorkflowDialog({
  open,
  onOpenChange,
  csrf,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  csrf: string;
  onCreated: (wid: string) => void;
}) {
  const [id, setId] = React.useState("");
  const [title, setTitle] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [saving, setSaving] = React.useState(false);

  const idFromTitle = (t: string) =>
    t
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 63);

  const handleTitleChange = (v: string) => {
    setTitle(v);
    if (!id || id === idFromTitle(title)) {
      setId(idFromTitle(v));
    }
  };

  const create = async () => {
    if (!id) return;
    setSaving(true);
    setError(null);
    try {
      await createWorkflow({ id, title: title || id, description: "" }, csrf);
      onCreated(id);
      onOpenChange(false);
    } catch (e: unknown) {
      // ApiError.message is String(detail.detail) — if detail is an object
      // (e.g. 402 license_limit), that becomes "[object Object]". Extract the
      // human-readable msg field from the nested detail object when present.
      const nested = (e as { detail?: { detail?: { msg?: string } } })?.detail?.detail;
      setError((typeof nested?.msg === "string" && nested.msg) || (e instanceof Error ? e.message : String(e)) || "Request failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New workflow</DialogTitle>
          <DialogDescription>Give your workflow a name to start.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label htmlFor="wf-title" className="mb-1.5 block text-xs">Title</Label>
            <Input
              id="wf-title"
              value={title}
              onChange={(e) => handleTitleChange(e.target.value)}
              placeholder="Morning Digest"
              autoFocus
            />
          </div>
          <div>
            <Label htmlFor="wf-id" className="mb-1.5 block text-xs">Workflow ID (snake_case)</Label>
            <Input
              id="wf-id"
              value={id}
              onChange={(e) => setId(e.target.value)}
              placeholder="morning_digest"
              className="font-mono"
            />
          </div>
          {error && <p className="text-xs text-destructive">{error}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button size="sm" onClick={create} disabled={saving || !id}>
              {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Create"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ── ADR-0062 M8: Workflow Templates ──────────────────────────────────────

const WORKFLOW_TEMPLATES: Array<{
  id: string;
  title: string;
  description: string;
  emoji: string;
}> = [
  {
    id: "daily_briefing",
    title: "Daily summary",
    description: "Sends a daily morning summary via messenger.",
    emoji: "📋",
  },
  {
    id: "alert_routing",
    title: "Alert routing",
    description: "Analyse incoming alerts and route them to the right agent.",
    emoji: "🔔",
  },
  {
    id: "content_approval",
    title: "Content approval",
    description: "Draft → AI review → approval or rejection with reason.",
    emoji: "✅",
  },
  {
    id: "qa_pipeline",
    title: "Q&A pipeline",
    description: "Ask a question → retrieve agent → generate answer → send.",
    emoji: "❓",
  },
  {
    id: "webhook_to_message",
    title: "Webhook → message",
    description: "Incoming HTTP webhook → formatted message to a channel.",
    emoji: "🔗",
  },
];

function TemplateCard({
  tpl,
  onUse,
}: {
  tpl: (typeof WORKFLOW_TEMPLATES)[0];
  onUse: (title: string) => void;
}) {
  return (
    <div className="flex flex-col gap-3 rounded-xl border border-border bg-card p-4 transition-colors hover:border-accent/30">
      <div className="flex items-start gap-3">
        <span className="text-2xl leading-none">{tpl.emoji}</span>
        <div className="min-w-0 flex-1">
          <p className="font-medium text-sm">{tpl.title}</p>
          <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">{tpl.description}</p>
        </div>
      </div>
      <Button size="sm" variant="outline" className="gap-1.5" onClick={() => onUse(tpl.title)}>
        <BookOpen className="h-3.5 w-3.5" />
        Use
      </Button>
    </div>
  );
}

// ── ADR-0062 M9: Run Timeline ─────────────────────────────────────────────

function RunTimeline({
  events,
  run,
}: {
  events: WorkflowRunEvent[];
  run: RunMeta | undefined;
}) {
  if (!run || events.length === 0) return null;

  // Collect per-node timing from events
  const nodeMap: Record<string, { start: number; end?: number; status: "ok" | "failed" | "running" }> = {};
  for (const evt of events) {
    if (!evt.node_id) continue;
    if (evt.type === "node_started") {
      nodeMap[evt.node_id] = { start: evt.ts, status: "running" };
    } else if (evt.type === "node_completed" && nodeMap[evt.node_id]) {
      nodeMap[evt.node_id].end = evt.ts;
      nodeMap[evt.node_id].status = "ok";
    } else if (evt.type === "node_failed" && nodeMap[evt.node_id]) {
      nodeMap[evt.node_id].end = evt.ts;
      nodeMap[evt.node_id].status = "failed";
    }
  }

  const nodeIds = Object.keys(nodeMap);
  if (nodeIds.length === 0) return null;

  const allTs = Object.values(nodeMap).flatMap((n) => [n.start, n.end ?? n.start]);
  const minTs = Math.min(...allTs);
  const maxTs = Math.max(...allTs);
  const totalMs = Math.max(maxTs - minTs, 0.001);

  const STATUS_COLOR: Record<string, string> = {
    ok: "bg-emerald-500",
    failed: "bg-destructive",
    running: "bg-amber-400 animate-pulse",
  };

  return (
    <div className="rounded-xl border border-border bg-card/50 p-4">
      <div className="mb-3 text-[10px] font-medium uppercase tracking-widest text-muted-foreground">
        Execution timeline
      </div>
      <div className="space-y-2">
        {nodeIds.map((id) => {
          const node = nodeMap[id];
          const left = ((node.start - minTs) / totalMs) * 100;
          const width = Math.max(((( node.end ?? maxTs) - node.start) / totalMs) * 100, 1.5);
          const durMs = ((node.end ?? maxTs) - node.start) * 1000;
          return (
            <div key={id} className="flex items-center gap-3">
              <span className="w-36 shrink-0 truncate font-mono text-[11px] text-muted-foreground text-right" title={id}>
                {id}
              </span>
              <div className="relative flex-1 h-5 rounded bg-muted/40">
                <div
                  className={cn("absolute top-1 h-3 rounded-sm", STATUS_COLOR[node.status])}
                  style={{ left: `${left}%`, width: `${width}%` }}
                  title={`${durMs.toFixed(0)} ms`}
                />
              </div>
              <span className="w-16 shrink-0 text-right text-[10px] text-muted-foreground tabular-nums">
                {durMs < 1000 ? `${durMs.toFixed(0)}ms` : `${(durMs / 1000).toFixed(1)}s`}
              </span>
            </div>
          );
        })}
      </div>
      <div className="mt-2 flex items-center gap-4 text-[10px] text-muted-foreground">
        <span className="flex items-center gap-1"><span className="inline-block h-2 w-3 rounded-sm bg-emerald-500" /> Completed</span>
        <span className="flex items-center gap-1"><span className="inline-block h-2 w-3 rounded-sm bg-destructive" /> Error</span>
      </div>
    </div>
  );
}

// ── ADR-0062 M7: Explanation Drawer ──────────────────────────────────────

function ExplanationDrawer({
  wid,
  csrf,
  onClose,
}: {
  wid: string;
  csrf: string;
  onClose: () => void;
}) {
  const { data, isLoading, refetch } = useQuery({
    queryKey: ["workflow-explanation", wid],
    queryFn: () => explainWorkflow(wid, csrf),
    staleTime: 300_000,
    retry: 1,
  });

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-widest text-muted-foreground">
          <Lightbulb className="h-3.5 w-3.5 text-accent" />
          What it does
        </div>
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon" className="h-6 w-6 text-muted-foreground hover:text-foreground" onClick={() => refetch()}>
            <Zap className="h-3 w-3" />
          </Button>
          <Button variant="ghost" size="icon" className="h-6 w-6 text-muted-foreground hover:text-foreground" onClick={onClose}>
            <X className="h-3 w-3" />
          </Button>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto px-3 py-3">
        {isLoading && (
          <div className="space-y-2">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-5/6" />
            <Skeleton className="h-4 w-4/6" />
          </div>
        )}
        {data && (
          <div className="space-y-3 text-sm leading-relaxed text-muted-foreground whitespace-pre-wrap">
            {data.explanation}
          </div>
        )}
        {!isLoading && !data && (
          <p className="text-xs text-muted-foreground">No YAML yet — build your workflow first.</p>
        )}
      </div>
    </div>
  );
}

// ── ADR-0062 M10: Quick-Add Palette ──────────────────────────────────────

const NODE_STUBS: Array<{ type: string; label: string; emoji: string; yaml: string }> = [
  {
    type: "trigger",
    label: "Trigger",
    emoji: "⚡",
    yaml: "\n- id: trigger\n  type: trigger\n  config:\n    event: message_received\n",
  },
  {
    type: "agent",
    label: "Agent",
    emoji: "🤖",
    yaml: "\n- id: agent_step\n  type: agent\n  depends_on: [trigger]\n  config:\n    prompt: \"{{ input }}\"\n",
  },
  {
    type: "condition",
    label: "Condition",
    emoji: "🔀",
    yaml: "\n- id: condition\n  type: condition\n  depends_on: [agent_step]\n  config:\n    expression: \"{{ output }} != ''\"\n",
  },
  {
    type: "deliver",
    label: "Send",
    emoji: "📤",
    yaml: "\n- id: deliver\n  type: deliver\n  depends_on: [condition]\n  config:\n    messenger: whatsapp\n    chat: auto\n    message: \"{{ output }}\"\n",
  },
  {
    type: "delay",
    label: "Wait",
    emoji: "⏱",
    yaml: "\n- id: wait\n  type: delay\n  config:\n    seconds: 60\n",
  },
  {
    type: "http",
    label: "HTTP",
    emoji: "🔗",
    yaml: "\n- id: http_call\n  type: http\n  config:\n    method: GET\n    url: https://example.com/api\n",
  },
  {
    type: "approval",
    label: "Human Review",
    emoji: "👤",
    yaml: "\n- id: human_review\n  type: approval\n  depends_on: []\n  message: \"Please review the results above and approve or reject to continue.\"\n  timeout_s: 3600\n",
  },
];

function QuickAddPalette({
  onAdd,
  onClose,
}: {
  onAdd: (yaml: string) => void;
  onClose: () => void;
}) {
  return (
    <div className="absolute bottom-14 right-4 z-20 w-56 rounded-xl border border-border bg-card shadow-xl shadow-black/20">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <span className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground">Add node</span>
        <Button variant="ghost" size="icon" className="h-5 w-5 text-muted-foreground" onClick={onClose}>
          <X className="h-3 w-3" />
        </Button>
      </div>
      <div className="p-2 space-y-1">
        {NODE_STUBS.map((stub) => (
          <button
            key={stub.type}
            onClick={() => { onAdd(stub.yaml); onClose(); }}
            className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-left hover:bg-muted/60 transition-colors"
          >
            <span className="text-base leading-none">{stub.emoji}</span>
            <span>{stub.label}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

// ── WorkflowsListPage ─────────────────────────────────────────────────────

export function WorkflowsListPage() {
  const { session } = useAuth();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [createOpen, setCreateOpen] = React.useState(false);
  const [deletePending, setDeletePending] = React.useState<string | null>(null);
  const importInputRef = React.useRef<HTMLInputElement>(null);

  const list = useQuery({
    queryKey: ["workflows"],
    queryFn: ({ signal }) => listWorkflows(signal),
  });

  // CCC M4: when a workflow is created from chat, invalidate the list
  useCCCEvents("workflow", React.useCallback(() => {
    qc.invalidateQueries({ queryKey: ["workflows"] });
  }, [qc]));

  const licQ = useQuery<LicenseInfo>({
    queryKey: ["license", "info"],
    queryFn: ({ signal }) => getLicenseInfo(signal),
    staleTime: 60_000,
  });

  const wfMaxRaw = licQ.data?.limits?.["workflows_max"];
  const wfMax: number | null = typeof wfMaxRaw === "number" ? wfMaxRaw : null;
  const atWfLimit =
    wfMax !== null &&
    !licQ.isLoading &&
    !list.isLoading &&
    (list.data?.count ?? 0) >= wfMax;

  const deleteMutation = useMutation({
    mutationFn: ({ wid }: { wid: string }) =>
      deleteWorkflow(wid, session?.csrf_token ?? ''),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workflows"] });
    },
  });

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-serif text-3xl font-light tracking-tight">Workflows</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Build, test, and schedule AWP workflows using guided natural language.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="text-xs">
            {list.data ? `${list.data.count} workflow${list.data.count !== 1 ? "s" : ""}` : "—"}
          </Badge>
          <Button
            size="sm"
            variant="outline"
            disabled={atWfLimit}
            title={atWfLimit ? `Free tier: ${wfMax} workflow max. Upgrade to Member Licensed for unlimited.` : "Import .awp.yaml or .awpkg"}
            onClick={() => importInputRef.current?.click()}
          >
            <Upload className="h-4 w-4" />
            Import
          </Button>
          <input
            ref={importInputRef}
            type="file"
            accept=".yaml,.yml,.awpkg"
            className="hidden"
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (!file || !session) return;
              try {
                const result = await importWorkflow(file, session.csrf_token);
                navigate(`/app/workflows/${result.id}`);
              } catch (err) {
                const nested = (err as { detail?: { detail?: { msg?: string } } })?.detail?.detail;
                const msg = typeof nested?.msg === "string" ? nested.msg : (err instanceof Error ? err.message : String(err));
                alert(`Import failed: ${msg}`);
              }
              e.target.value = "";
            }}
          />
          <Button
            size="sm"
            disabled={atWfLimit}
            title={atWfLimit ? `Free tier: ${wfMax} workflow max. Upgrade to Member Licensed for unlimited.` : undefined}
            onClick={() => setCreateOpen(true)}
          >
            <Plus className="h-4 w-4" />
            New workflow
          </Button>
        </div>
      </div>

      {/* License context strip */}
      {licQ.data && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-card/50 px-3 py-2 text-xs">
          <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            Licence
          </span>
          <Badge variant={licQ.data.loaded ? "ok" : "secondary"} className="text-[10px]">
            {licQ.data.tier}
          </Badge>
          {licQ.data.expires_at && (
            <span className="text-muted-foreground">
              · Expires {new Date(licQ.data.expires_at * 1000).toLocaleDateString()}
            </span>
          )}
          {Object.entries(licQ.data.features)
            .filter(([, v]) => v)
            .map(([k]) => (
              <Badge key={k} variant="outline" className="text-[10px]">{k}</Badge>
            ))}
          {Object.entries(licQ.data.limits)
            .filter(([, v]) => typeof v === "number")
            .map(([k, v]) => (
              <span key={k} className="text-muted-foreground">
                {k}: <span className="font-medium text-foreground">{String(v)}</span>
              </span>
            ))}
        </div>
      )}

      {/* Workflow limit banner (free tier) */}
      {atWfLimit && (
        <div className="flex items-center justify-between gap-3 rounded-md border border-amber-500/30 bg-amber-500/5 px-4 py-3 text-sm">
          <span className="text-amber-700 dark:text-amber-400">
            Free tier: you've reached the limit of <strong>{wfMax}</strong> workflow{wfMax !== 1 ? "s" : ""}.
            Delete the existing workflow{wfMax !== 1 ? "s" : ""} or upgrade to create more.
          </span>
          <a
            href="https://corvin-labs.com/pricing"
            target="_blank"
            rel="noopener noreferrer"
            className="shrink-0 text-xs font-semibold text-amber-600 underline-offset-2 hover:underline dark:text-amber-400"
          >
            Upgrade →
          </a>
        </div>
      )}

      <Tabs defaultValue="mine">
        <TabsList>
          <TabsTrigger value="mine">My workflows</TabsTrigger>
          <TabsTrigger value="templates">
            <BookOpen className="h-3.5 w-3.5" />
            Templates
          </TabsTrigger>
        </TabsList>

        <TabsContent value="mine" className="mt-4">
          {list.isLoading && (
            <div className="space-y-2">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-20 w-full" />
              ))}
            </div>
          )}
          {list.data && list.data.workflows.length === 0 && (
            <Card className="border-dashed">
              <CardContent className="py-12 text-center text-sm text-muted-foreground">
                <Workflow className="mx-auto mb-3 h-8 w-8 opacity-30" />
                <p>No workflows yet.</p>
                <p className="mt-1">Click <strong>New workflow</strong> or choose a <strong>Template</strong>.</p>
              </CardContent>
            </Card>
          )}
          <div className="space-y-2">
            {list.data?.workflows.map((wf) => (
              <WorkflowCard
                key={wf.id}
                wf={wf}
                onOpen={() => navigate(`/app/workflows/${wf.id}`)}
                onDelete={() => setDeletePending(wf.id)}
                deleting={deleteMutation.isPending && deletePending === wf.id}
              />
            ))}
          </div>
        </TabsContent>

        <TabsContent value="templates" className="mt-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {WORKFLOW_TEMPLATES.map((tpl) => (
              <TemplateCard
                key={tpl.id}
                tpl={tpl}
                onUse={(title) => {
                  setCreateOpen(true);
                  // Store template title for pre-filling — dialog reads from sessionStorage
                  sessionStorage.setItem("wf_template_title", title);
                  sessionStorage.setItem("wf_template_id", tpl.id);
                }}
              />
            ))}
          </div>
        </TabsContent>
      </Tabs>

      {deletePending && (
        <Dialog open onOpenChange={() => setDeletePending(null)}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Delete workflow?</DialogTitle>
              <DialogDescription>
                This removes <strong>{deletePending}</strong> and all its run history permanently.
              </DialogDescription>
            </DialogHeader>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={() => setDeletePending(null)}>Cancel</Button>
              <Button
                variant="destructive"
                size="sm"
                disabled={deleteMutation.isPending}
                onClick={async () => {
                  try {
                    await deleteMutation.mutateAsync({ wid: deletePending });
                  } finally {
                    setDeletePending(null);
                  }
                }}
              >
                {deleteMutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Delete"}
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      )}

      <CreateWorkflowDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        csrf={session!.csrf_token}
        onCreated={(wid) => navigate(`/app/workflows/${wid}`)}
      />
    </div>
  );
}

function WorkflowCard({
  wf,
  onOpen,
  onDelete,
  deleting,
}: {
  wf: WorkflowMeta;
  onOpen: () => void;
  onDelete: () => void;
  deleting: boolean;
}) {
  return (
    <Card className="transition-all hover:border-accent/40">
      <CardContent className="flex items-center gap-4 py-3">
        <button
          onClick={onOpen}
          className="flex min-w-0 flex-1 items-center gap-3 text-left focus:outline-none focus-visible:rounded-md focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Workflow className="h-5 w-5 shrink-0 text-accent" />
          <div className="min-w-0 flex-1 overflow-hidden">
            <div className="flex min-w-0 items-center gap-2">
              <span className="min-w-0 flex-1 truncate font-medium">{wf.title || wf.id}</span>
              <Badge variant="outline" className="shrink-0 font-mono text-[10px]">
                {wf.id}
              </Badge>
              <Badge
                variant="secondary"
                className={cn(
                  "shrink-0 text-[10px]",
                  wf.phase === "ready" && "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
                )}
              >
                {PHASE_LABELS[wf.phase] ?? wf.phase}
              </Badge>
              {wf.has_schedule && (
                <Badge variant="secondary" className="shrink-0 text-[10px]">
                  <Calendar className="mr-1 h-2.5 w-2.5" />
                  scheduled
                </Badge>
              )}
              {wf.source === "compute_pipeline" && (
                <Badge variant="secondary"
                  className="shrink-0 text-[10px] bg-violet-100 text-violet-700 dark:bg-violet-950/40 dark:text-violet-400">
                  ⚙ From Pipeline
                </Badge>
              )}
            </div>
            {wf.description && (
              <p className="line-clamp-1 text-xs text-muted-foreground">{wf.description}</p>
            )}
            <div className="mt-1 text-[10px] text-muted-foreground">
              Updated {formatTs(wf.updated_at)}
            </div>
          </div>
          <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
        </button>
        <div className="flex items-center gap-1">
          <Button asChild variant="ghost" size="sm">
            <Link to={`/app/workflows/${wf.id}/runs`}>
              <Clock className="h-3.5 w-3.5" />
            </Link>
          </Button>
          <Button variant="ghost" size="sm" onClick={onDelete} disabled={deleting}>
            {deleting ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <span className="text-xs text-muted-foreground">✕</span>
            )}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// ── HITL Approval Bar (Newman in the Loop) ───────────────────────────────

function HitlApprovalBar({
  wid,
  rid,
  nodeId,
  message,
  csrf,
  onDecision,
}: {
  wid: string;
  rid: string;
  nodeId: string;
  message: string;
  csrf: string;
  onDecision: (status: "approved" | "rejected") => void;
}) {
  const [comment, setComment] = React.useState("");
  const [loading, setLoading] = React.useState<"approving" | "rejecting" | null>(null);
  const [expanded, setExpanded] = React.useState(true);

  const handle = async (action: "approve" | "reject") => {
    setLoading(action === "approve" ? "approving" : "rejecting");
    try {
      if (action === "approve") {
        await approveRunNode(wid, rid, comment, csrf);
        onDecision("approved");
      } else {
        await rejectRunNode(wid, rid, comment, csrf);
        onDecision("rejected");
      }
    } catch {
      // stay visible so user can retry
    } finally {
      setLoading(null);
    }
  };

  return (
    <div className="border-t border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-950/40">
      <button
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs font-medium text-amber-700 dark:text-amber-400"
        onClick={() => setExpanded((v) => !v)}
      >
        <span className="flex h-2 w-2 shrink-0 rounded-full bg-amber-500 animate-pulse" />
        <span className="flex-1">Human approval required — node: <code className="font-mono">{nodeId}</code></span>
        <ChevronDown className={cn("h-3 w-3 transition-transform", expanded && "rotate-180")} />
      </button>
      {expanded && (
        <div className="px-3 pb-3 space-y-2">
          <p className="text-xs text-amber-800 dark:text-amber-300 leading-relaxed">{message}</p>
          <textarea
            className="w-full rounded border border-amber-300 dark:border-amber-700 bg-white dark:bg-amber-950/20 px-2 py-1.5 text-xs resize-none focus:outline-none focus:ring-1 focus:ring-amber-400"
            rows={2}
            placeholder="Optional comment…"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
          />
          <div className="flex gap-2">
            <Button
              size="sm"
              className="flex-1 bg-emerald-600 hover:bg-emerald-700 text-white text-xs"
              onClick={() => handle("approve")}
              disabled={loading !== null}
            >
              {loading === "approving" ? <Loader2 className="h-3 w-3 animate-spin" /> : <ThumbsUp className="h-3 w-3" />}
              Approve
            </Button>
            <Button
              size="sm"
              variant="destructive"
              className="flex-1 text-xs"
              onClick={() => handle("reject")}
              disabled={loading !== null}
            >
              {loading === "rejecting" ? <Loader2 className="h-3 w-3 animate-spin" /> : <ThumbsDown className="h-3 w-3" />}
              Reject
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Workflow Chat Panel (ADR-0188 M6/M10 — ask_human / chatflow) ─────────
//
// Distinct from HitlApprovalBar above: that one is a binary approve/reject
// gate for the older "approval" node type. This is a genuine chat window
// for `ask_human` nodes (ADR-0188) — free-text or yes/no replies, rendered
// as a conversation, matching how a chatflow (`orchestration.engine: chat`)
// actually talks to a user: the workflow's `answer`/`ask_human` messages
// appear as assistant bubbles, the human types a real reply, not just a
// click.

export interface ChatTurn {
  role: "assistant" | "user";
  text: string;
  ts: number;
}

export function WorkflowChatPanel({
  wid,
  rid,
  nodeId,
  prompt,
  history,
  csrf,
  onResumed,
}: {
  wid: string;
  rid: string;
  nodeId: string;
  prompt: string;
  history: ChatTurn[];
  csrf: string;
  onResumed: (status: "confirmed" | "declined" | "sent") => void;
}) {
  const [reply, setReply] = React.useState("");
  const [sending, setSending] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const bottomRef = React.useRef<HTMLDivElement>(null);
  const inputRef = React.useRef<HTMLTextAreaElement>(null);

  React.useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [history.length]);

  React.useEffect(() => {
    inputRef.current?.focus();
  }, [nodeId]);

  const send = async () => {
    const text = reply.trim();
    if (!text || sending) return;
    setSending(true);
    setError(null);
    try {
      const { resumeWorkflowRun } = await import("@/lib/api");
      const res = await resumeWorkflowRun(wid, rid, text, csrf);
      setReply("");
      onResumed(res.confirmed === undefined ? "sent" : res.confirmed ? "confirmed" : "declined");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
    }
  };

  const allTurns: ChatTurn[] = [...history, { role: "assistant", text: prompt, ts: Date.now() / 1000 }];

  return (
    <div className="border-t border-amber-300 dark:border-amber-700 bg-background flex flex-col max-h-[45vh]">
      <div className="flex items-center gap-2 border-b border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/40 px-3 py-2">
        <span className="flex h-2 w-2 shrink-0 rounded-full bg-amber-500 animate-pulse" />
        <span className="text-xs font-medium text-amber-700 dark:text-amber-400">
          Waiting for your reply — node <code className="font-mono">{nodeId}</code>
        </span>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto px-3 py-3 space-y-2.5">
        {allTurns.map((t, i) => (
          <div key={i} className={cn("flex", t.role === "user" ? "justify-end" : "justify-start")}>
            <div
              className={cn(
                "max-w-[85%] rounded-2xl px-3.5 py-2 text-sm leading-relaxed",
                t.role === "user"
                  ? "bg-accent text-accent-foreground rounded-br-sm"
                  : "bg-amber-100 dark:bg-amber-950/40 text-foreground rounded-bl-sm border border-amber-200 dark:border-amber-800",
              )}
            >
              {t.text}
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {error && (
        <div className="mx-3 mb-1 rounded-md bg-destructive/10 px-2 py-1.5 text-xs text-destructive">
          {error}
        </div>
      )}

      <div className="shrink-0 border-t border-border p-3">
        <div className="flex gap-2">
          <Textarea
            ref={inputRef}
            value={reply}
            onChange={(e) => setReply(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            placeholder="Type your reply… (Enter to send)"
            disabled={sending}
            className="min-h-[44px] resize-none text-sm"
            rows={1}
          />
          <Button size="sm" onClick={send} disabled={sending || !reply.trim()} className="self-end">
            {sending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Send"}
          </Button>
        </div>
      </div>
    </div>
  );
}

// ── Workflow I/O Panel ────────────────────────────────────────────────────

function WorkflowIOPanel({ yaml }: { yaml: string }) {
  const parsed = React.useMemo(() => {
    if (!yaml.trim()) return null;
    try {
      // Simple regex parse — avoid importing yaml lib just for this display
      const inputsMatch = yaml.match(/^\s{2}inputs:\s*\n((?:\s{4}-.+\n?)*)/m);
      const outputsMatch = yaml.match(/^\s{2}outputs:\s*\n((?:\s{4}-.+\n?)*)/m);
      const parseItems = (block: string | undefined) => {
        if (!block) return [];
        const names: Array<{ name: string; desc: string; required?: boolean }> = [];
        const lines = block.split("\n");
        let current: { name: string; desc: string; required?: boolean } | null = null;
        for (const line of lines) {
          const nameMatch = line.match(/^\s+-\s+name:\s+(.+)$/);
          const descMatch = line.match(/^\s+description:\s+(.+)$/);
          const reqMatch = line.match(/^\s+required:\s+(true|false)$/);
          if (nameMatch) {
            if (current) names.push(current);
            current = { name: nameMatch[1].trim(), desc: "" };
          } else if (descMatch && current) {
            current.desc = descMatch[1].trim();
          } else if (reqMatch && current) {
            current.required = reqMatch[1] === "true";
          }
        }
        if (current) names.push(current);
        return names;
      };
      return {
        inputs: parseItems(inputsMatch?.[1]),
        outputs: parseItems(outputsMatch?.[1]),
      };
    } catch {
      return null;
    }
  }, [yaml]);

  if (!parsed || (parsed.inputs.length === 0 && parsed.outputs.length === 0)) {
    return (
      <div className="px-3 py-4 text-[11px] text-muted-foreground text-center">
        No inputs/outputs declared yet.<br />
        Tell the assistant what data this workflow needs and what it produces.
      </div>
    );
  }

  const renderItems = (items: typeof parsed.inputs, label: string) => (
    <div className="space-y-1">
      <div className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground px-3 pt-2">{label}</div>
      {items.map((item) => (
        <div key={item.name} className="px-3 py-1.5 flex flex-col gap-0.5">
          <div className="flex items-center gap-1">
            <span className="font-mono text-[11px] font-medium">{item.name}</span>
            {item.required && <Badge variant="secondary" className="text-[9px] px-1 py-0">required</Badge>}
          </div>
          {item.desc && <span className="text-[10px] text-muted-foreground">{item.desc}</span>}
        </div>
      ))}
    </div>
  );

  return (
    <div className="divide-y divide-border">
      {parsed.inputs.length > 0 && renderItems(parsed.inputs, "Inputs")}
      {parsed.outputs.length > 0 && renderItems(parsed.outputs, "Outputs")}
    </div>
  );
}

// ── WorkflowEditorPage ────────────────────────────────────────────────────

export function WorkflowEditorPage() {
  const { wid } = useParams<{ wid: string }>();
  const { session } = useAuth();
  const qc = useQueryClient();
  const [searchParams] = useSearchParams();
  // Opt-in, non-default: ?canvas=organic breaks the strict grid layout into
  // a looser, hand-placed-looking arrangement — used for marketing/preview
  // screenshots. Real usage is unaffected (defaults to the strict grid).
  const organicCanvas = searchParams.get("canvas") === "organic";

  const detail = useQuery({
    queryKey: ["workflow", wid],
    queryFn: ({ signal }) => getWorkflow(wid!, signal),
    enabled: !!wid,
  });

  const [graph, setGraph] = React.useState<GraphNode[]>([]);
  const [yaml, setYaml] = React.useState("");
  const [phase, setPhase] = React.useState("discovering");
  const [selectedNode, setSelectedNode] = React.useState<string | null>(null);
  const [nodeStates, setNodeStates] = React.useState<NodeState>({});
  const [isRunning, setIsRunning] = React.useState(false);
  const [runError, setRunError] = React.useState<string | null>(null);
  const [scheduleOpen, setScheduleOpen] = React.useState(false);
  const [showExplanation, setShowExplanation] = React.useState(false);
  const [showQuickAdd, setShowQuickAdd] = React.useState(false);
  const [currentRid, setCurrentRid] = React.useState<string | null>(null);
  const [awaitingApproval, setAwaitingApproval] = React.useState<{ nodeId: string; message: string } | null>(null);
  const [awaitingReply, setAwaitingReply] = React.useState<{ nodeId: string; prompt: string; history: ChatTurn[] } | null>(null);
  const [inspectorTab, setInspectorTab] = React.useState<"node" | "io">("node");
  const abortRef = React.useRef<AbortController | null>(null);

  React.useEffect(() => {
    if (detail.data) {
      setGraph(detail.data.graph);
      setYaml(detail.data.yaml);
      setPhase(detail.data.workflow.phase);
    }
  }, [detail.data]);

  const selectedNodeData = graph.find((n) => n.id === selectedNode) ?? null;

  const startRun = async (dry: boolean) => {
    if (!session || !wid) return;
    setIsRunning(true);
    setRunError(null);
    setNodeStates({});
    setAwaitingApproval(null);
    setAwaitingReply(null);
    setCurrentRid(null);

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    try {
      const res = await fetch(`/v1/console/workflows/${encodeURIComponent(wid)}/runs`, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": session.csrf_token,
        },
        body: JSON.stringify({ inputs: {}, dry_run: dry }),
        signal: ctrl.signal,
      });

      if (res.status === 402) {
        const body = await res.json().catch(() => ({})) as Record<string, unknown>;
        const detail = body?.detail as Record<string, unknown> | undefined;
        const msg = (detail?.msg ?? detail?.error ?? "License limit reached — upgrade for concurrent workflow runs.") as string;
        setRunError(msg);
        return;
      }

      // Capture run ID from response header
      const runId = res.headers.get("X-Run-Id");
      if (runId) setCurrentRid(runId);

      const reader = res.body?.getReader();
      if (!reader) return;
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              const evt: WorkflowRunEvent = JSON.parse(line.slice(6));
              if (evt.type === "node_started" && evt.node_id) {
                setNodeStates((prev) => ({ ...prev, [evt.node_id!]: "running" }));
              } else if (evt.type === "node_awaiting_approval" && evt.node_id) {
                setNodeStates((prev) => ({ ...prev, [evt.node_id!]: "waiting" }));
                setAwaitingApproval({ nodeId: evt.node_id, message: evt.message ?? "Please review and approve or reject to continue." });
              } else if (evt.type === "node_awaiting_reply" && evt.node_id) {
                // ADR-0188 ask_human — chatflow reply, distinct from binary approve/reject.
                setNodeStates((prev) => ({ ...prev, [evt.node_id!]: "waiting" }));
                setAwaitingReply((prev) => ({
                  nodeId: evt.node_id!,
                  prompt: evt.message ?? "",
                  history: prev && prev.nodeId === evt.node_id ? prev.history : [],
                }));
              } else if (evt.type === "node_completed" && evt.node_id) {
                setNodeStates((prev) => ({ ...prev, [evt.node_id!]: "ok" }));
                if (awaitingApproval?.nodeId === evt.node_id) setAwaitingApproval(null);
                if (awaitingReply?.nodeId === evt.node_id) setAwaitingReply(null);
              } else if (evt.type === "node_failed" && evt.node_id) {
                setNodeStates((prev) => ({ ...prev, [evt.node_id!]: "failed" }));
                if (awaitingApproval?.nodeId === evt.node_id) setAwaitingApproval(null);
                if (awaitingReply?.nodeId === evt.node_id) setAwaitingReply(null);
              } else if (evt.type === "run_completed") {
                await qc.invalidateQueries({ queryKey: ["workflow-runs", wid] });
              }
              // Capture rid from first event that carries it
              if (!currentRid && (evt as WorkflowRunEvent & { rid?: string }).rid) {
                setCurrentRid((evt as WorkflowRunEvent & { rid?: string }).rid!);
              }
            } catch {
              // skip malformed
            }
          }
        }
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        // surface error if needed
      }
    } finally {
      setIsRunning(false);
      setAwaitingApproval(null);
      setAwaitingReply(null);
      abortRef.current = null;
    }
  };

  const stopRun = () => {
    abortRef.current?.abort();
    setIsRunning(false);
    setAwaitingApproval(null);
    setAwaitingReply(null);
  };

  // Phase 5: import a workflow file into the current editor slot
  const handleImport = async (file: File) => {
    if (!session) return;
    try {
      const result = await importWorkflow(file, session.csrf_token);
      // Navigate to the newly imported workflow
      window.location.href = `/console/app/workflows/${encodeURIComponent(result.id)}`;
    } catch (e) {
      alert(`Import failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  if (detail.isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (detail.isError || !detail.data) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-muted-foreground">
        <p>Workflow not found.</p>
        <Button asChild variant="ghost" size="sm">
          <Link to="/app/workflows">
            <ArrowLeft className="h-4 w-4" />
            Back
          </Link>
        </Button>
      </div>
    );
  }

  const wf = detail.data.workflow;

  return (
    <div className="-mx-6 -my-8 flex h-[calc(100vh-3.5rem)] flex-col overflow-hidden">
      {/* Pipeline-source banner (ADR-0090) */}
      {wf.source === "compute_pipeline" && (
        <div className="flex items-center gap-2 px-6 py-2 bg-violet-50 dark:bg-violet-950/30 border-b border-violet-200 dark:border-violet-800 text-xs text-violet-700 dark:text-violet-400">
          <span className="text-base">⚙</span>
          <span>
            <strong>Exported from Agentic Compute Pipeline</strong>
            {wf.pipeline_id && (
              <> — source: <code className="font-mono text-[11px] bg-violet-100 dark:bg-violet-900/40 rounded px-1">{wf.pipeline_id}</code></>
            )}
            {" "}· exported from Agentic Compute Pipeline
          </span>
          <a href="/app/compute?tab=pipelines"
            className="ml-auto shrink-0 underline underline-offset-2 hover:text-violet-900 dark:hover:text-violet-300">
            Back to Agentic Compute →
          </a>
        </div>
      )}
      {/* Header */}
      <div className="flex items-center gap-3 border-b border-border px-6 py-3">
        <Button asChild variant="ghost" size="icon" className="h-8 w-8">
          <Link to="/app/workflows">
            <ArrowLeft className="h-4 w-4" />
          </Link>
        </Button>
        <Workflow className="h-5 w-5 text-accent" />
        <div className="min-w-0">
          <h2 className="truncate font-semibold leading-tight">{wf.title || wf.id}</h2>
          <div className="text-[11px] font-mono text-muted-foreground">{wf.id}</div>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <Button asChild variant="ghost" size="sm" className="text-xs">
            <Link to={`/app/workflows/${wid}/runs`}>
              <Clock className="h-3.5 w-3.5" />
              Run history
            </Link>
          </Button>
          <Button
            variant={showExplanation ? "accent" : "ghost"}
            size="sm"
            className="text-xs gap-1"
            title="Explain workflow"
            onClick={() => setShowExplanation((v) => !v)}
          >
            <Lightbulb className="h-3.5 w-3.5" />
            Explain
          </Button>
        </div>
      </div>

      {/* Three-pane layout */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Left: Chat */}
        <div className="w-80 shrink-0 border-r border-border flex flex-col overflow-hidden">
          <ChatPane
            wid={wid!}
            initialChat={detail.data.chat}
            initialPhase={phase}
            onYamlUpdate={(y) => setYaml(y)}
            onPhaseUpdate={(p) => setPhase(p)}
            onGraphUpdate={(g) => setGraph(g)}
            csrf={session!.csrf_token}
          />
        </div>

        {/* Center: Canvas */}
        <div className="relative flex flex-1 flex-col min-w-0">
          <AwpCanvas
            nodes={graph}
            nodeStates={nodeStates}
            selectedId={selectedNode}
            onSelect={(id) => setSelectedNode((prev) => (prev === id ? null : id))}
            className="flex-1 bg-muted/10 p-4"
            organic={organicCanvas}
          />
          {/* M10: Quick-Add FAB */}
          <button
            className={cn(
              "absolute bottom-14 right-4 z-10 flex h-9 w-9 items-center justify-center rounded-full border border-border bg-card shadow-md",
              "text-muted-foreground transition-colors hover:border-accent/50 hover:text-accent active:scale-95",
              showQuickAdd && "border-accent/50 bg-accent/10 text-accent",
            )}
            title="Add node"
            onClick={() => setShowQuickAdd((v) => !v)}
          >
            <Plus className="h-4 w-4" />
          </button>
          {showQuickAdd && (
            <QuickAddPalette
              onAdd={(stub) => setYaml((prev) => prev + stub)}
              onClose={() => setShowQuickAdd(false)}
            />
          )}
          {/* HITL Approval Bar (binary approve/reject — "approval" node type) */}
          {awaitingApproval && currentRid && (
            <HitlApprovalBar
              wid={wid!}
              rid={currentRid}
              nodeId={awaitingApproval.nodeId}
              message={awaitingApproval.message}
              csrf={session!.csrf_token}
              onDecision={(status) => {
                setNodeStates((prev) => ({ ...prev, [awaitingApproval.nodeId]: status === "approved" ? "ok" : "failed" }));
                setAwaitingApproval(null);
              }}
            />
          )}
          {/* Workflow Chat Panel (free-text reply — "ask_human" node type, ADR-0188) */}
          {awaitingReply && currentRid && (
            <WorkflowChatPanel
              wid={wid!}
              rid={currentRid}
              nodeId={awaitingReply.nodeId}
              prompt={awaitingReply.prompt}
              history={awaitingReply.history}
              csrf={session!.csrf_token}
              onResumed={() => {
                setNodeStates((prev) => ({ ...prev, [awaitingReply.nodeId]: "ok" }));
                setAwaitingReply(null);
              }}
            />
          )}
          {runError && (
            <div className="flex items-center gap-2 border-t border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 px-3 py-1.5 text-xs text-amber-700 dark:text-amber-400">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
              <span>{runError}</span>
              <a
                href="https://corvin-labs.com/pricing"
                target="_blank"
                rel="noopener noreferrer"
                className="ml-1 underline font-semibold"
              >
                Upgrade →
              </a>
              <button className="ml-auto opacity-60 hover:opacity-100" onClick={() => setRunError(null)}>
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          )}
          <EditorToolbar
            wid={wid!}
            csrf={session!.csrf_token}
            yaml={yaml}
            onTest={startRun}
            onStop={stopRun}
            isRunning={isRunning}
            hasSchedule={wf.has_schedule}
            onSchedule={() => setScheduleOpen(true)}
            onImport={handleImport}
            graph={graph}
          />
        </div>

        {/* Right: Inspector / Explanation */}
        <div className="w-64 shrink-0 border-l border-border flex flex-col overflow-hidden">
          {showExplanation ? (
            <ExplanationDrawer
              wid={wid!}
              csrf={session!.csrf_token}
              onClose={() => setShowExplanation(false)}
            />
          ) : (
            <>
              <div className="flex shrink-0 items-center justify-between border-b border-border px-3 py-2">
                <div className="flex items-center gap-1">
                  <button
                    className={cn("text-[10px] font-medium uppercase tracking-widest px-1.5 py-0.5 rounded transition-colors",
                      inspectorTab === "node" ? "bg-accent/15 text-accent" : "text-muted-foreground hover:text-foreground")}
                    onClick={() => setInspectorTab("node")}
                  >
                    Node
                  </button>
                  <button
                    className={cn("text-[10px] font-medium uppercase tracking-widest px-1.5 py-0.5 rounded transition-colors",
                      inspectorTab === "io" ? "bg-accent/15 text-accent" : "text-muted-foreground hover:text-foreground")}
                    onClick={() => setInspectorTab("io")}
                  >
                    I/O
                  </button>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-5 w-5 text-muted-foreground/60 hover:text-accent"
                  title="Explain workflow"
                  onClick={() => setShowExplanation(true)}
                >
                  <Lightbulb className="h-3.5 w-3.5" />
                </Button>
              </div>
              <div className="flex-1 overflow-y-auto">
                {inspectorTab === "node" ? (
                  <NodeInspector
                    node={selectedNodeData}
                    nodeState={selectedNodeData ? (nodeStates[selectedNodeData.id] ?? "idle") : undefined}
                    wid={wid!}
                    yaml={yaml}
                    csrf={session!.csrf_token}
                    onYamlChange={(newYaml) => { setYaml(newYaml); }}
                  />
                ) : (
                  <WorkflowIOPanel yaml={yaml} />
                )}
              </div>
            </>
          )}
        </div>
      </div>

      {scheduleOpen && (
        <ScheduleModal
          wid={wid!}
          current={wf.schedule}
          open={scheduleOpen}
          onOpenChange={setScheduleOpen}
          csrf={session!.csrf_token}
          onSaved={() => qc.invalidateQueries({ queryKey: ["workflow", wid] })}
        />
      )}
    </div>
  );
}

// ── WorkflowRunsPage ──────────────────────────────────────────────────────

export function WorkflowRunsPage() {
  const { wid } = useParams<{ wid: string }>();
  const { session } = useAuth();
  const qc = useQueryClient();

  const runs = useQuery({
    queryKey: ["workflow-runs", wid],
    queryFn: ({ signal }) => listRuns(wid!, signal),
    enabled: !!wid,
    refetchInterval: 5000,
  });

  const deleteMutation = useMutation({
    mutationFn: ({ rid }: { rid: string }) =>
      deleteRun(wid!, rid, session?.csrf_token ?? ''),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["workflow-runs", wid] }),
  });

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div className="flex items-center gap-3">
        <Button asChild variant="ghost" size="icon" className="h-8 w-8">
          <Link to={`/app/workflows/${wid}`}>
            <ArrowLeft className="h-4 w-4" />
          </Link>
        </Button>
        <div>
          <h1 className="font-serif text-2xl font-light tracking-tight">Run history</h1>
          <div className="font-mono text-xs text-muted-foreground">{wid}</div>
        </div>
      </div>

      {runs.isLoading && (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-14 w-full" />)}
        </div>
      )}

      {runs.data && runs.data.runs.length === 0 && (
        <Card className="border-dashed">
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            No runs yet. Use the Test button in the editor to start one.
          </CardContent>
        </Card>
      )}

      <div className="space-y-2">
        {runs.data?.runs.map((r) => (
          <RunRow
            key={r.rid}
            run={r}
            wid={wid!}
            onDelete={() => deleteMutation.mutate({ rid: r.rid })}
            deleting={deleteMutation.isPending}
          />
        ))}
      </div>
    </div>
  );
}


function RunRow({
  run,
  wid,
  onDelete,
  deleting,
}: {
  run: RunMeta;
  wid: string;
  onDelete: () => void;
  deleting: boolean;
}) {
  const statusColor =
    run.status === "complete"
      ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
      : run.status === "failed"
        ? "bg-destructive/10 text-destructive"
        : "bg-amber-500/10 text-amber-600 dark:text-amber-400";

  return (
    <Card className="transition-all hover:border-accent/30">
      <CardContent className="flex items-center gap-4 py-3">
        <Link
          to={`/app/workflows/${wid}/runs/${run.rid}`}
          className="flex flex-1 items-center gap-3 focus:outline-none"
        >
          <div className="flex-1">
            <div className="flex items-center gap-2">
              <Badge className={cn("text-[10px]", statusColor)}>{run.status}</Badge>
              {run.dry_run && <Badge variant="outline" className="text-[10px]">dry-run</Badge>}
              <span className="font-mono text-[11px] text-muted-foreground">{run.rid}</span>
            </div>
            <div className="mt-1 flex items-center gap-3 text-[11px] text-muted-foreground">
              <span>Started {formatTs(run.started_at)}</span>
              <span>Duration {formatDuration(run.started_at, run.finished_at)}</span>
            </div>
            {run.error && (
              <p className="mt-0.5 truncate text-[11px] text-destructive">{run.error}</p>
            )}
          </div>
          <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
        </Link>
        <Button variant="ghost" size="sm" onClick={onDelete} disabled={deleting}>
          {deleting ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <span className="text-xs text-muted-foreground">✕</span>
          )}
        </Button>
      </CardContent>
    </Card>
  );
}

// ── Output format detection + rendering ──────────────────────────────────

type OutputFormat = "json" | "markdown" | "text";

function detectFormat(raw: string): OutputFormat {
  const trimmed = raw.trim();
  // JSON: must start with { or [ and parse cleanly
  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    try {
      JSON.parse(trimmed);
      return "json";
    } catch {
      // fall through
    }
  }
  // Markdown heuristics: common markdown syntax
  const markdownPatterns = [
    /^#{1,6}\s/m,       // headers
    /\*\*[^*]+\*\*/,    // bold
    /^[-*]\s/m,         // unordered list
    /^\d+\.\s/m,        // ordered list
    /^```/m,            // code fence
    /\[.+\]\(.+\)/,     // link
    /^>\s/m,            // blockquote
  ];
  if (markdownPatterns.some((p) => p.test(trimmed))) return "markdown";
  return "text";
}

const FORMAT_LABELS: Record<OutputFormat, string> = {
  json: "JSON",
  markdown: "MD",
  text: "TXT",
};

const FORMAT_COLORS: Record<OutputFormat, string> = {
  json: "text-blue-600 dark:text-blue-400 bg-blue-500/10 border-blue-500/30",
  markdown: "text-violet-600 dark:text-violet-400 bg-violet-500/10 border-violet-500/30",
  text: "text-muted-foreground bg-muted/50 border-border",
};

/** Renders a JSON value with syntax-coloured spans. */
function JsonValue({ value, depth = 0 }: { value: unknown; depth?: number }) {
  const indent = "  ".repeat(depth);
  const innerIndent = "  ".repeat(depth + 1);

  if (value === null) return <span className="text-muted-foreground">null</span>;
  if (typeof value === "boolean")
    return <span className="text-amber-600 dark:text-amber-400">{String(value)}</span>;
  if (typeof value === "number")
    return <span className="text-blue-600 dark:text-blue-400">{String(value)}</span>;
  if (typeof value === "string")
    return (
      <span className="text-emerald-600 dark:text-emerald-400">
        &quot;{value}&quot;
      </span>
    );
  if (Array.isArray(value)) {
    if (value.length === 0) return <span>{"[]"}</span>;
    return (
      <span>
        {"[\n"}
        {value.map((v, i) => (
          <span key={i}>
            {innerIndent}
            <JsonValue value={v} depth={depth + 1} />
            {i < value.length - 1 ? ",\n" : "\n"}
          </span>
        ))}
        {indent}
        {"]"}
      </span>
    );
  }
  if (typeof value === "object" && value !== null) {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) return <span>{"{}"}</span>;
    return (
      <span>
        {"{\n"}
        {entries.map(([k, v], i) => (
          <span key={k}>
            {innerIndent}
            <span className="text-accent font-medium">&quot;{k}&quot;</span>
            {": "}
            <JsonValue value={v} depth={depth + 1} />
            {i < entries.length - 1 ? ",\n" : "\n"}
          </span>
        ))}
        {indent}
        {"}"}
      </span>
    );
  }
  return <span>{String(value)}</span>;
}

/**
 * Smart output block: auto-detects JSON / Markdown / plain text.
 * Raw toggle always available. Format badge shown top-right.
 */
function OutputBlock({
  text,
  compact = false,
  csrf,
}: {
  text: string;
  compact?: boolean;
  csrf?: string;
}) {
  const fmt = React.useMemo(() => detectFormat(text), [text]);
  const [showRaw, setShowRaw] = React.useState(false);
  const maxH = compact ? "max-h-48" : "max-h-[28rem]";

  const parsedJson = React.useMemo(() => {
    if (fmt !== "json") return null;
    try {
      return JSON.parse(text.trim());
    } catch {
      return null;
    }
  }, [fmt, text]);

  return (
    <div className="rounded-md border border-border/60 bg-background overflow-hidden text-xs">
      {/* Toolbar */}
      <div className="flex items-center justify-between border-b border-border/40 bg-muted/30 px-3 py-1.5">
        <span
          className={cn(
            "rounded border px-1.5 py-0.5 text-[10px] font-medium",
            FORMAT_COLORS[fmt],
          )}
        >
          {FORMAT_LABELS[fmt]}
        </span>
        <div className="flex items-center gap-2">
          {fmt !== "text" && (
            <button
              onClick={() => setShowRaw((v) => !v)}
              className="text-[10px] text-muted-foreground hover:text-foreground"
            >
              {showRaw ? "Formatted" : "Raw"}
            </button>
          )}
          <ReadAloudButton text={text} csrf={csrf} />
          <CopyButtonInline text={text} />
        </div>
      </div>

      {/* Content */}
      <div className={cn("overflow-auto", maxH)}>
        {showRaw || fmt === "text" ? (
          <pre className="px-3 py-2.5 font-mono leading-relaxed whitespace-pre-wrap text-foreground">
            {text}
          </pre>
        ) : fmt === "json" && parsedJson !== null ? (
          <pre className="px-3 py-2.5 font-mono leading-relaxed whitespace-pre">
            <JsonValue value={parsedJson} />
          </pre>
        ) : fmt === "markdown" ? (
          <div className="prose prose-sm dark:prose-invert max-w-none px-4 py-3 leading-relaxed">
            <MarkdownContent text={text} />
          </div>
        ) : (
          <pre className="px-3 py-2.5 font-mono leading-relaxed whitespace-pre-wrap text-foreground">
            {text}
          </pre>
        )}
      </div>
    </div>
  );
}

/** Markdown renderer using react-markdown (already in the project). */
function MarkdownContent({ text }: { text: string }) {
  const [ReactMarkdown, setReactMarkdown] = React.useState<React.ComponentType<{ children: string }> | null>(null);
  React.useEffect(() => {
    import("react-markdown").then((m) => setReactMarkdown(() => m.default as React.ComponentType<{ children: string }>));
  }, []);
  if (!ReactMarkdown) return <pre className="whitespace-pre-wrap font-mono text-xs">{text}</pre>;
  return <ReactMarkdown>{text}</ReactMarkdown>;
}

// ── ReadAloudButton ───────────────────────────────────────────────────────

function ReadAloudButton({ text, csrf }: { text: string; csrf?: string }) {
  const [speaking, setSpeaking] = React.useState(false);
  const audioRef = React.useRef<HTMLAudioElement | null>(null);
  // cancelRef: set to true when the user clicks Stop before the fetch completes
  const cancelRef = React.useRef(false);

  if (!csrf) return null;

  const toggle = async () => {
    // ── Stop ──────────────────────────────────────────────────────────
    if (speaking) {
      cancelRef.current = true;       // abort any in-flight fetch
      audioRef.current?.pause();
      audioRef.current = null;
      setSpeaking(false);
      return;
    }

    // ── Play ──────────────────────────────────────────────────────────
    cancelRef.current = false;
    setSpeaking(true);
    try {
      const { ttsBlob } = await import("@/lib/api");
      const blob = await ttsBlob(text.slice(0, 2000), "de", csrf);
      if (!blob.size) { setSpeaking(false); return; }

      // User clicked Stop while we were fetching → discard
      if (cancelRef.current) {
        setSpeaking(false);
        return;
      }

      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audioRef.current = audio;
      audio.onended = () => { URL.revokeObjectURL(url); setSpeaking(false); };
      audio.onerror = () => { URL.revokeObjectURL(url); setSpeaking(false); };
      await audio.play();
    } catch {
      setSpeaking(false);
    }
  };

  return (
    <button
      onClick={toggle}
      title={speaking ? "Stop" : "Read aloud"}
      className={cn(
        "flex items-center gap-1 text-[10px] transition-colors",
        speaking
          ? "text-destructive hover:text-destructive/80"
          : "text-muted-foreground hover:text-accent",
      )}
    >
      {speaking
        ? <Square className="h-3 w-3" />
        : <Volume2 className={cn("h-3 w-3", speaking && "animate-pulse")} />}
      {speaking ? "Stop" : "Read"}
    </button>
  );
}

// ── CopyButton helper ─────────────────────────────────────────────────────

function CopyButtonInline({ text }: { text: string }) {
  const [copied, setCopied] = React.useState(false);
  return (
    <button
      onClick={() =>
        navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 2000);
        })
      }
      className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground"
    >
      {copied ? <Check className="h-3 w-3 text-emerald-500" /> : <Copy className="h-3 w-3" />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}


// ── ResultBanner — header + OutputBlock (Read button lives inside OutputBlock) ──

function ResultBanner({
  output,
  nodeId,
  csrf,
}: {
  output: string;
  nodeId: string; // used for future per-node save
  csrf: string;
}) {
  return (
    <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/5 overflow-hidden">
      <div className="flex items-center gap-2 border-b border-emerald-500/20 px-4 py-2.5">
        <Check className="h-4 w-4 text-emerald-500" />
        <span className="text-sm font-semibold text-emerald-700 dark:text-emerald-400">
          Workflow result
        </span>
        <span className="text-xs text-muted-foreground">
          — node <span className="font-mono">{nodeId}</span>
        </span>
      </div>
      <div className="p-3">
        <OutputBlock text={output} csrf={csrf} />
      </div>
    </div>
  );
}

// ── WorkflowRunDetailPage ─────────────────────────────────────────────────

export function WorkflowRunDetailPage() {
  const { wid, rid } = useParams<{ wid: string; rid: string }>();
  const { session } = useAuth();
  const [runTab, setRunTab] = React.useState<"log" | "media" | "tables">("log");
  const [expandedTable, setExpandedTable] = React.useState<TableEvent | null>(null);

  const detail = useQuery({
    queryKey: ["workflow-run-detail", wid, rid],
    queryFn: ({ signal }) => getRun(wid!, rid!, signal),
    enabled: !!wid && !!rid,
    refetchInterval: (q) =>
      q.state.data?.run.status === "running" ? 2000 : false,
  });

  const mediaQ = useQuery({
    queryKey: ["run-media", wid, rid],
    queryFn: ({ signal }) => getWorkflowRunMedia(wid!, rid!, signal),
    enabled: !!wid && !!rid && detail.data?.run.status !== "running",
    staleTime: 30_000,
  });
  const mediaItems: MediaItem[] = (mediaQ.data?.media ?? []);

  const tablesQ = useQuery({
    queryKey: ["run-tables", wid, rid],
    queryFn: ({ signal }) => getWorkflowRunTables(wid!, rid!, signal),
    enabled: !!wid && !!rid && detail.data?.run.status !== "running",
    staleTime: 30_000,
  });
  const tableItems = tablesQ.data?.tables ?? [];
  const mediaCount = mediaItems.length;

  if (detail.isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const run = detail.data?.run;
  const events = detail.data?.events ?? [];

  // Find the last completed node — that's the workflow result
  const completedNodes = events.filter(
    (e) => e.type === "node_completed" && e.output,
  );
  const finalResult = completedNodes[completedNodes.length - 1];

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      {/* ── Header ── */}
      <div className="flex items-center gap-3">
        <Button asChild variant="ghost" size="icon" className="h-8 w-8">
          <Link to={`/app/workflows/${wid}/runs`}>
            <ArrowLeft className="h-4 w-4" />
          </Link>
        </Button>
        <div>
          <h1 className="font-serif text-2xl font-light tracking-tight">Run detail</h1>
          <div className="font-mono text-xs text-muted-foreground">{rid}</div>
        </div>
      </div>

      {/* ── Status bar ── */}
      {run && (
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <Badge
            className={cn(
              "text-xs",
              run.status === "complete"
                ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                : run.status === "failed"
                  ? "bg-destructive/10 text-destructive"
                  : "bg-amber-500/10 text-amber-600",
            )}
          >
            {run.status}
          </Badge>
          {run.dry_run && <Badge variant="outline">dry-run</Badge>}
          <span className="text-muted-foreground">
            Started {formatTs(run.started_at)}
          </span>
          <span className="text-muted-foreground">
            Duration {formatDuration(run.started_at, run.finished_at)}
          </span>
          {run.error && <span className="text-destructive">{run.error}</span>}
        </div>
      )}

      {/* ── Workflow Result Banner (final node output) ── */}
      {finalResult?.output && run?.status === "complete" && !run.dry_run && (
        <ResultBanner output={finalResult.output} nodeId={finalResult.node_id ?? ""} csrf={session!.csrf_token} />
      )}

      {/* ── Log / Media / Tables tabs ── */}
      {expandedTable && wid && rid && (
        <TableOverlay
          event={expandedTable}
          onClose={() => setExpandedTable(null)}
          fetchPage={(params: DataTableFetchParams) =>
            getWorkflowRunTablePage(wid, rid, expandedTable.filename, {
              page: params.page,
              per_page: params.per_page,
              sort_col: params.sort_col ?? undefined,
              sort_dir: params.sort_dir,
              filter: params.filter || undefined,
            })
          }
        />
      )}
      <div className="space-y-4">
        {/* Tab switcher */}
        <div className="flex items-center gap-1 border-b border-border pb-0">
          <button
            onClick={() => setRunTab("log")}
            className={cn(
              "px-3 py-2 text-xs font-medium border-b-2 -mb-px transition-colors",
              runTab === "log"
                ? "border-foreground text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            Log
          </button>
          <button
            onClick={() => setRunTab("media")}
            className={cn(
              "flex items-center gap-1.5 px-3 py-2 text-xs font-medium border-b-2 -mb-px transition-colors",
              runTab === "media"
                ? "border-violet-500 text-violet-600 dark:text-violet-400"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            <Image className="h-3 w-3" />
            Media
            {mediaCount > 0 && (
              <span className="rounded-full bg-violet-100 dark:bg-violet-900/40 text-violet-700 dark:text-violet-300 px-1.5 text-[10px] font-semibold">
                {mediaCount}
              </span>
            )}
          </button>
          <button
            onClick={() => setRunTab("tables")}
            className={cn(
              "flex items-center gap-1.5 px-3 py-2 text-xs font-medium border-b-2 -mb-px transition-colors",
              runTab === "tables"
                ? "border-emerald-500 text-emerald-600 dark:text-emerald-400"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            📋 Tabellen
            {tableItems.length > 0 && (
              <span className="rounded-full bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300 px-1.5 text-[10px] font-semibold">
                {tableItems.length}
              </span>
            )}
          </button>
        </div>

        {/* Log tab */}
        {runTab === "log" && (
          <div className="space-y-4">
            {/* ── M9: Run Timeline ── */}
            {events.length > 0 && <RunTimeline events={events} run={run} />}

            {/* ── Event log ── */}
            <div>
              <div className="mb-2 text-[10px] font-medium uppercase tracking-widest text-muted-foreground">
                Step-by-step log
              </div>
              <div className="space-y-1">
                {events.map((evt, i) => (
                  <EventRow key={i} evt={evt} csrf={session?.csrf_token} />
                ))}
                {events.length === 0 && (
                  <Card className="border-dashed">
                    <CardContent className="py-8 text-center text-sm text-muted-foreground">
                      No events recorded.
                    </CardContent>
                  </Card>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Media tab */}
        {runTab === "media" && (
          <div>
            {mediaCount === 0 ? (
              <Card>
                <CardContent className="py-12 text-center">
                  <Image className="mx-auto mb-2 h-8 w-8 text-muted-foreground/30" />
                  <p className="text-sm font-medium">No media output.</p>
                  <p className="text-xs text-muted-foreground mt-1">
                    Images and charts produced by workflow nodes appear here.
                  </p>
                </CardContent>
              </Card>
            ) : (
              <div className="space-y-3">
                <MediaGallery
                  items={mediaItems}
                  zipUrl={workflowMediaZipUrl(wid!, rid!)}
                />
              </div>
            )}
          </div>
        )}

        {/* Tables tab */}
        {runTab === "tables" && (
          <div>
            {tableItems.length === 0 ? (
              <Card>
                <CardContent className="py-12 text-center">
                  <span className="text-3xl block mb-2">📋</span>
                  <p className="text-sm font-medium">Keine Tabellen-Outputs.</p>
                  <p className="text-xs text-muted-foreground mt-1">
                    CSV-, Parquet- und JSON-Artefakte von Workflow-Nodes erscheinen hier.
                  </p>
                </CardContent>
              </Card>
            ) : (
              <div className="space-y-3">
                {tableItems.map((tbl) => {
                  const evt: TableEvent = {
                    table_id: tbl.filename,
                    filename: tbl.filename,
                    mime_type: tbl.mime_type,
                    row_count: tbl.row_count,
                    size_bytes: tbl.size_bytes,
                    src: tbl.src,
                    ts: tbl.ts,
                  };
                  return (
                    <div key={tbl.filename} className="space-y-2">
                      <TableCard
                        event={evt}
                        onExpand={() => setExpandedTable(evt)}
                      />
                      {/* Inline compact preview */}
                      {wid && rid && (
                        <DataTable
                          compact
                          title={`Vorschau: ${tbl.filename}`}
                          defaultPerPage={10}
                          fetchPage={(params: DataTableFetchParams) =>
                            getWorkflowRunTablePage(wid, rid, tbl.filename, {
                              page: params.page,
                              per_page: params.per_page,
                              sort_col: params.sort_col ?? undefined,
                              sort_dir: params.sort_dir,
                              filter: params.filter || undefined,
                            })
                          }
                        />
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function EventRow({ evt, csrf }: { evt: WorkflowRunEvent; csrf?: string }) {
  const [expanded, setExpanded] = React.useState(false);

  // ── media event — render inline ImageCard ──────────────────────────────
  if (evt.type === "media") {
    const mediaItem: MediaItem = {
      media_id: evt.media_id ?? evt.filename ?? "media",
      node_id: evt.node_id ?? undefined,
      filename: evt.filename ?? "image",
      mime_type: evt.mime_type ?? "image/png",
      label: evt.label ?? null,
      src: evt.src ?? "",
      thumbnail_src: evt.thumbnail_src ?? null,
      ts: evt.ts,
    };
    return <ImageCard item={mediaItem} />;
  }

  // ── table event — render inline TableCard ───────────────────────────────
  if (evt.type === "table") {
    const tableEvt = evt as WorkflowRunEvent & { table_id?: string; row_count?: number | null; size_bytes?: number };
    const tableItem: TableEvent = {
      table_id: tableEvt.table_id ?? tableEvt.filename ?? "table",
      node_id: evt.node_id ?? undefined,
      filename: tableEvt.filename ?? "data.csv",
      mime_type: tableEvt.mime_type ?? "text/csv",
      row_count: tableEvt.row_count ?? null,
      size_bytes: tableEvt.size_bytes ?? 0,
      src: tableEvt.src ?? "",
      ts: evt.ts,
    };
    return <TableCard event={tableItem} />;
  }

  const hasOutput = evt.type === "node_completed" && !!evt.output;
  const hasError = evt.type === "node_failed" && !!evt.error;

  const colorClass =
    evt.type === "node_completed" || (evt.type === "run_completed" && evt.ok)
      ? "text-emerald-600 dark:text-emerald-400"
      : evt.type === "node_failed" || evt.type === "error" || (evt.type === "run_completed" && !evt.ok)
        ? "text-destructive"
        : evt.type === "node_started"
          ? "text-amber-600 dark:text-amber-400"
          : "text-muted-foreground";

  const icon =
    evt.type === "node_started" ? "▶" :
    evt.type === "node_completed" ? "✓" :
    evt.type === "node_failed" || evt.type === "error" ? "✗" :
    evt.type === "run_completed" ? (evt.ok ? "✓✓" : "✗✗") :
    "·";

  return (
    <div
      className={cn(
        "rounded-md border transition-colors",
        expanded ? "border-border bg-muted/20" : "border-transparent hover:bg-muted/30",
        (hasOutput || hasError) && "cursor-pointer",
      )}
    >
      {/* Summary row */}
      <div
        className="flex items-center gap-3 px-3 py-1.5 text-xs"
        onClick={() => (hasOutput || hasError) && setExpanded((v) => !v)}
      >
        <span className={cn("w-4 shrink-0 font-mono font-bold", colorClass)}>{icon}</span>
        <span className="w-28 shrink-0 font-mono text-muted-foreground">{formatTs(evt.ts)}</span>
        <span className={cn("w-32 shrink-0 font-medium", colorClass)}>{evt.type}</span>
        {evt.node_id && (
          <span className="shrink-0 font-mono text-muted-foreground">{evt.node_id}</span>
        )}
        {evt.elapsed_s != null && (
          <span className="shrink-0 text-muted-foreground">{evt.elapsed_s}s</span>
        )}
        {/* Preview — hidden when expanded */}
        {!expanded && evt.output_preview && (
          <span className="min-w-0 flex-1 truncate italic text-muted-foreground/60">
            {evt.output_preview}
          </span>
        )}
        {!expanded && (evt.error ?? evt.message) && (
          <span className="min-w-0 flex-1 truncate text-destructive">
            {evt.error ?? evt.message}
          </span>
        )}
        {/* Expand toggle */}
        {(hasOutput || hasError) && (
          <span className="ml-auto shrink-0 text-muted-foreground">
            {expanded
              ? <ChevronUp className="h-3.5 w-3.5" />
              : <ChevronDown className="h-3.5 w-3.5" />}
          </span>
        )}
      </div>

      {/* Expanded output block */}
      {expanded && hasOutput && evt.output && (
        <div className="border-t border-border px-3 pb-3 pt-2.5">
          <div className="mb-2 text-[10px] font-medium uppercase tracking-widest text-muted-foreground">
            Output — {evt.node_id}
          </div>
          <OutputBlock text={evt.output} compact csrf={csrf} />
        </div>
      )}

      {/* Expanded error block */}
      {expanded && hasError && evt.error && (
        <div className="border-t border-border px-3 pb-3 pt-2.5">
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground">
              Error — {evt.node_id}
            </span>
            <CopyButtonInline text={evt.error} />
          </div>
          <pre className="max-h-48 overflow-auto rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2.5 font-mono text-xs leading-relaxed whitespace-pre-wrap text-destructive">
            {evt.error}
          </pre>
        </div>
      )}
    </div>
  );
}

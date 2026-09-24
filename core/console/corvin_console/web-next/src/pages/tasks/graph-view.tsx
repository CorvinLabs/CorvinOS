/**
 * Graph view of the Tasks panel — the work items as a live DAG (ADR-2060).
 *
 * Nodes are work items, laid out left → right by graph-layout.ts (structure
 * only, so a poll recolours without moving anything). Edges: the breakdown
 * (parent → child, quiet) and dependencies (prerequisite → dependent, strong;
 * dashed while the prerequisite is still open). "Where we are" is encoded on
 * the nodes: running (a linked run is running now — pulsing), in progress,
 * ready (nothing left to wait for), waiting, blocked, done. Status is never
 * colour alone — every node carries the status icon and its state label.
 *
 * Data comes from the page's live items query (5 s poll); this view owns no
 * fetch. Clicking a node opens the detail drawer.
 */
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactFlow, {
  Background, BackgroundVariant, Controls, Handle, MarkerType, MiniMap, Position,
  type Edge, type Node, type NodeProps, type ReactFlowInstance,
} from "reactflow";
import "reactflow/dist/style.css";
import { Crosshair, Radio } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Item } from "@/lib/api/task-tracking";
import { cn } from "@/lib/utils";
import { KIND_META, STATUS_META, deadlineText, displayProgress } from "./encodings";
import {
  NODE_H, NODE_W, STATE_META, externalIds, layoutGraph, nodeState, scopeItems, stateCounts, type NodeState,
} from "./graph-layout";
import { StatusIcon } from "./parts";

interface NodeData {
  item: Item;
  state: NodeState;
  progress: number;
  /** false = nobody recorded a progress for this item — show no number rather than a fake 0%. */
  hasProgress: boolean;
  container: boolean;
  due: string | null;
  selected: boolean;
  /** Pulled in from outside the chosen scope because something here waits for it. */
  external: boolean;
}

const RING: Partial<Record<NodeState, string>> = {
  running: "var(--viz-status-progress)",
  active: "var(--viz-status-progress)",
  blocked: "var(--viz-status-blocked)",
};

function TaskNode({ data }: NodeProps<NodeData>) {
  const { item, state, progress, hasProgress, container, due, selected, external } = data;
  const ring = RING[state];
  const done = state === "done";
  const tip = [
    item.title, external ? "Outside the chosen scope — shown because something here waits for it" : null, `${KIND_META[item.kind].label} · ${STATUS_META[item.status].label}`,
    state !== "done" ? STATE_META[state].label : null, due,
    ...(item.live_run_titles ?? []),
  ].filter(Boolean).join("\n");
  return (
    <div
      data-testid={`graph-node-${item.id}`} data-state={state} title={tip}
      className={cn(
        "relative flex h-full w-full flex-col justify-between rounded-lg border bg-card px-2.5 py-1.5 text-left shadow-sm transition-opacity",
        container && "bg-muted/40",
        done && "opacity-60",
        selected && "outline outline-2 outline-offset-2 outline-foreground/60",
        state === "running" && "tasks-graph-pulse",
        external && "border-dashed",
      )}
      style={{ width: NODE_W, height: NODE_H, borderColor: ring, borderWidth: ring ? 2 : 1 }}
    >
      <Handle type="target" position={Position.Left} className="!h-1.5 !w-1.5 !border-0 !bg-border" isConnectable={false} />
      <div className="flex min-w-0 items-start gap-1.5">
        <StatusIcon status={item.status} className="mt-0.5 h-3.5 w-3.5" />
        <span className={cn("line-clamp-2 min-w-0 text-xs leading-4", container ? "font-semibold" : "font-medium",
          done && "line-through decoration-muted-foreground/60")}>{item.title}</span>
      </div>
      <div className="flex items-center gap-1.5 text-[10px] leading-4 text-muted-foreground">
        <span className="uppercase tracking-wide">{external ? "External" : KIND_META[item.kind].label}</span>
        {state === "running" && (
          <span className="inline-flex items-center gap-0.5 font-medium text-foreground">
            <Radio className="h-3 w-3" style={{ color: "var(--viz-status-progress)" }} aria-hidden />
            {item.running_runs} running
          </span>
        )}
        {!container && state !== "running" && state !== "done" && (
          <span className="font-medium text-foreground">
            {item.approval_state === "pending" && state === "ready" ? "Decision pending" : STATE_META[state].label}
          </span>
        )}
        {due && !done && <span className={cn("truncate", item.overdue && "font-medium text-destructive")}>{due}</span>}
        {hasProgress && <span className="ml-auto tabular-nums">{progress}%</span>}
      </div>
      {hasProgress && <div className="absolute inset-x-2.5 bottom-0.5 h-[3px] overflow-hidden rounded-full bg-muted" aria-hidden>
        <div className="h-full rounded-full" style={{
          width: `${progress}%`,
          background: done ? "var(--viz-status-complete)" : "var(--viz-status-progress)",
        }} />
      </div>}
      <Handle type="source" position={Position.Right} className="!h-1.5 !w-1.5 !border-0 !bg-border" isConnectable={false} />
    </div>
  );
}

// Module-level: React Flow re-mounts every node when nodeTypes changes identity.
const NODE_TYPES = { task: memo(TaskNode) };

const FOCUS: NodeState[] = ["running", "active", "ready", "waiting", "blocked"];
const SWATCH: Record<NodeState, string> = {
  running: "var(--viz-status-progress)", active: "var(--viz-status-progress)", ready: "hsl(var(--foreground))",
  waiting: "var(--viz-status-blocked)", blocked: "var(--viz-status-blocked)", done: "var(--viz-status-complete)",
  idle: "hsl(var(--border))",
};

export default function GraphView({ items, now, selectedId, onSelect }: {
  items: Item[]; now: number; selectedId: string | null; onSelect: (id: string) => void;
}) {
  const roots = useMemo(() => {
    const ids = new Set(items.map((i) => i.id));
    return items.filter((i) => !i.parent_id || !ids.has(i.parent_id))
      .filter((i) => KIND_META[i.kind].container)
      .sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at));
  }, [items]);
  const [scope, setScope] = useState("all");
  const [hideDone, setHideDone] = useState(false);
  const effectiveScope = scope === "all" || roots.some((r) => r.id === scope) ? scope : "all";

  const inScope = useMemo(() => scopeItems(items, effectiveScope, hideDone), [items, effectiveScope, hideDone]);
  const layout = useMemo(() => layoutGraph(inScope),
    // Structure only: the layout recomputes when ids / parents / dependencies change, not on a status poll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [structureKey(inScope)]);
  const byId = useMemo(() => new Map(inScope.map((i) => [i.id, i])), [inScope]);
  const external = useMemo(() => externalIds(items, effectiveScope), [items, effectiveScope]);
  const counts = useMemo(() => stateCounts(inScope.filter((i) => !external.has(i.id)), (i) => KIND_META[i.kind].container),
    [inScope, external]);

  const nodes: Node<NodeData>[] = useMemo(() => layout.nodes.flatMap((p) => {
    const it = byId.get(p.id);
    if (!it) return [];
    const closed = it.status === "complete" || it.status === "archived";
    return [{
      id: p.id, type: "task", position: { x: p.x, y: p.y }, draggable: false, connectable: false,
      data: {
        item: it, state: nodeState(it), progress: displayProgress(it), container: KIND_META[it.kind].container,
        hasProgress: closed || it.progress != null || it.child_ids.length > 0,
        due: deadlineText(it.deadline, now, closed), selected: it.id === selectedId, external: external.has(it.id),
      },
    }];
  }), [layout, byId, now, selectedId, external]);

  const edges: Edge[] = useMemo(() => layout.edges.flatMap((e): Edge[] => {
    const src = byId.get(e.source);
    const tgt = byId.get(e.target);
    if (!src || !tgt) return [];
    const tState = nodeState(tgt);
    if (e.kind === "hierarchy") {
      return [{
        id: e.id, source: e.source, target: e.target, type: "smoothstep",
        animated: tState === "running",
        style: { stroke: "hsl(var(--muted-foreground))", strokeOpacity: 0.45, strokeWidth: 1.25 },
      }];
    }
    const open = src.status !== "complete" && src.status !== "archived";
    const color = open ? "var(--viz-status-blocked)" : "var(--viz-status-complete)";
    return [{
      id: e.id, source: e.source, target: e.target, type: "smoothstep", animated: open && tState !== "done",
      style: { stroke: color, strokeWidth: 2, strokeDasharray: open ? "6 4" : undefined },
      markerEnd: { type: MarkerType.ArrowClosed, color, width: 16, height: 16 },
      label: open ? "waits for" : undefined,
      labelStyle: { fontSize: 10, fill: "hsl(var(--muted-foreground))" },
    }];
  }), [layout, byId]);

  const rf = useRef<ReactFlowInstance | null>(null);
  const nodesRef = useRef(nodes);
  nodesRef.current = nodes;
  const focus = useCallback((states: NodeState[], duration = 500) => {
    const ids = nodesRef.current.filter((n) => states.includes(n.data.state)).map((n) => ({ id: n.id }));
    // Readable, never a zoomed-out wall: a focus never goes below 0.55.
    rf.current?.fitView({ nodes: ids.length ? ids : undefined, padding: 0.3, duration, maxZoom: 1, minZoom: ids.length ? 0.55 : 0.08 });
  }, []);
  // Open (and refit on a structure change — never on a status poll) centred on
  // where we stand: running work, else work in progress, else everything.
  const standAt = useCallback((duration: number) => {
    const has = (s: NodeState) => nodesRef.current.some((n) => n.data.state === s);
    focus(has("running") ? ["running"] : has("active") ? ["active", "running"] : [], duration);
  }, [focus]);
  useEffect(() => {
    const t = setTimeout(() => standAt(300), 30);
    return () => clearTimeout(t);
  }, [layout.key, standAt]);

  return (
    <div className="space-y-2" data-testid="graph-view">
      <div className="flex flex-wrap items-center gap-2" aria-label="Graph controls">
        <select aria-label="Scope" className="h-7 rounded-full border bg-background px-2 text-xs" value={effectiveScope}
          onChange={(e) => setScope(e.target.value)}>
          <option value="all">All initiatives</option>
          {roots.map((r) => <option key={r.id} value={r.id}>{r.title}</option>)}
        </select>
        <button type="button" aria-pressed={hideDone} onClick={() => setHideDone(!hideDone)}
          className={cn("inline-flex h-7 items-center rounded-full border px-2.5 text-xs",
            hideDone ? "border-foreground/40 bg-muted font-medium" : "text-muted-foreground")}>
          Hide done
        </button>
        <span className="mx-1 h-4 border-l" aria-hidden />
        <span className="text-xs font-medium">Where we are</span>
        {FOCUS.map((s) => (
          <button key={s} type="button" data-testid={`graph-count-${s}`} disabled={!counts[s]} onClick={() => focus([s])}
            title={`Zoom to: ${STATE_META[s].label}`}
            className="inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-xs disabled:opacity-40">
            <span aria-hidden className={cn("h-2 w-2 rounded-full", s === "running" && "tasks-graph-pulse")}
              style={{ background: s === "ready" ? "transparent" : SWATCH[s], border: s === "ready" ? `1.5px solid ${SWATCH[s]}` : undefined }} />
            {STATE_META[s].label}<span className="tabular-nums font-medium">{counts[s]}</span>
          </button>
        ))}
        <Button size="sm" variant="ghost" className="ml-auto h-7" onClick={() => focus([])}>Show all</Button>
        <Button size="sm" variant="outline" className="h-7" onClick={() => standAt(500)}>
          <Crosshair className="mr-1 h-3.5 w-3.5" />Where we are now
        </Button>
      </div>

      <div className="h-[70vh] min-h-[480px] overflow-hidden rounded-lg border bg-background" role="figure"
        aria-label={`Task graph: ${layout.nodes.length} items, ${layout.edges.length} links`}>
        {layout.nodes.length === 0 ? (
          <p className="p-6 text-sm text-muted-foreground">
            {hideDone ? "Nothing open in this scope — turn off “Hide done” to see finished work." : "No items in this scope."}
          </p>
        ) : (
          <ReactFlow
            nodes={nodes} edges={edges} nodeTypes={NODE_TYPES}
            onInit={(inst) => { rf.current = inst; standAt(0); }}
            onNodeClick={(_, n) => onSelect(n.id)}
            nodesDraggable={false} nodesConnectable={false} elementsSelectable
            minZoom={0.08} maxZoom={1.75} proOptions={{ hideAttribution: true }}
          >
            <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="hsl(var(--border))" />
            <Controls showInteractive={false} />
            <MiniMap pannable zoomable ariaLabel="Graph overview"
              style={{ border: "1px solid hsl(var(--border))", borderRadius: 6 }}
              nodeColor={(n) => SWATCH[(n.data as NodeData).state]} nodeStrokeWidth={0} />
          </ReactFlow>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground" aria-label="Legend">
        <LegendLine label="Breakdown (parent → child)" stroke="hsl(var(--muted-foreground))" opacity={0.6} />
        <LegendLine label="Dependency still open (waits for)" stroke="var(--viz-status-blocked)" dash="6 4" width={2} />
        <LegendLine label="Dependency met" stroke="var(--viz-status-complete)" width={2} />
        <span className="inline-flex items-center gap-1.5"><StatusIcon status="in_progress" className="h-3 w-3" />In progress</span>
        <span className="inline-flex items-center gap-1.5"><StatusIcon status="blocked" className="h-3 w-3" />Blocked</span>
        <span className="inline-flex items-center gap-1.5"><StatusIcon status="complete" className="h-3 w-3" />Complete</span>
        <span className="inline-flex items-center gap-1.5"><StatusIcon status="open" className="h-3 w-3" />Open</span>
        <span>Pulsing = a linked agent session or run is running now · click a node for details</span>
      </div>
    </div>
  );
}

function LegendLine({ label, stroke, dash, width = 1.5, opacity = 1 }: {
  label: string; stroke: string; dash?: string; width?: number; opacity?: number;
}) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <svg width="28" height="8" aria-hidden><line x1="1" y1="4" x2="27" y2="4" style={{ stroke, strokeWidth: width, strokeDasharray: dash, strokeOpacity: opacity }} /></svg>
      {label}
    </span>
  );
}

/** Ids + parents + dependencies: what the layout depends on. */
export function structureKey(items: Item[]): string {
  return items.map((i) => `${i.id}<${i.parent_id ?? ""}<${i.depends_on.join("+")}<${i.sort_key}`).join(",");
}

/**
 * Graph view of the Tasks panel — one graph per initiative / loop (ADR-2060).
 *
 * Shows how each initiative was broken down, projected onto a graph: the
 * initiative on top, every level of its breakdown below (epics, stories,
 * tasks, subtasks; gates, checkpoints, preconditions, criteria labelled as
 * such), dependencies as arrows across. A picker above lists every graph with
 * its progress and whether something is running in it right now.
 *
 * "Where we are" is encoded on the nodes: running (a linked run is running
 * now — pulsing), in progress, ready (nothing left to wait for), waiting,
 * blocked, done. Status is never colour alone — every node carries the status
 * icon and its state label. Positions depend on structure only
 * (graph-layout.ts), so the page's 5 s poll recolours without moving anything.
 *
 * Data comes from the page's live items query; this view owns no fetch.
 * Clicking a node opens the detail drawer.
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
  NODE_H, NODE_W, STATE_META, breakdownStats, externalIds, graphChoices, nodeState, scopeItems,
  stateCounts, treeLayout, type GraphLayout, type NodeState,
} from "./graph-layout";
import { StatusIcon } from "./parts";

interface NodeData {
  item: Item;
  state: NodeState;
  progress: number;
  /** false = nobody recorded a progress for this item — show no number rather than a fake 0%. */
  hasProgress: boolean;
  container: boolean;
  root: boolean;
  due: string | null;
  selected: boolean;
  /** Pulled in from outside this initiative because something here waits for it. */
  external: boolean;
}

interface FrameData { label: string; w: number; h: number }

const RING: Partial<Record<NodeState, string>> = {
  running: "var(--viz-status-progress)",
  active: "var(--viz-status-progress)",
  blocked: "var(--viz-status-blocked)",
};

const CATEGORY_LABEL: Record<string, string> = {
  gate: "Gate", checkpoint: "Checkpoint", precondition: "Precondition", criterion: "Criterion", adr: "Decision record",
};

function typeLabel(it: Item): string {
  return (it.category && CATEGORY_LABEL[it.category]) || KIND_META[it.kind].label;
}

const HANDLE = "!h-1.5 !w-1.5 !min-h-0 !min-w-0 !border-0 !bg-border";

function TaskNode({ data }: NodeProps<NodeData>) {
  const { item, state, progress, hasProgress, container, root, due, selected, external } = data;
  const ring = RING[state];
  const done = state === "done";
  const tip = [
    item.title, external ? "Outside this initiative — shown because something here waits for it" : null,
    `${typeLabel(item)} · ${STATUS_META[item.status].label}`,
    state !== "done" ? STATE_META[state].label : null, due,
    ...(item.live_run_titles ?? []),
  ].filter(Boolean).join("\n");
  return (
    <div
      data-testid={`graph-node-${item.id}`} data-state={state} title={tip}
      className={cn(
        "relative flex h-full w-full flex-col justify-between rounded-lg border bg-card px-2.5 py-1.5 text-left shadow-sm transition-opacity",
        container && "bg-muted/40",
        root && "shadow-md",
        done && "opacity-60",
        selected && "outline outline-2 outline-offset-2 outline-foreground/60",
        state === "running" && "tasks-graph-pulse",
        external && "border-dashed",
      )}
      style={{ width: NODE_W, height: NODE_H, borderColor: ring, borderWidth: ring || root ? 2 : 1 }}
    >
      <Handle id="t" type="target" position={Position.Top} className={HANDLE} isConnectable={false} />
      <Handle id="dl" type="target" position={Position.Left} className={HANDLE} isConnectable={false} />
      <div className="flex min-w-0 items-start gap-1.5">
        <StatusIcon status={item.status} className="mt-0.5 h-3.5 w-3.5" />
        <span className={cn("line-clamp-2 min-w-0 text-xs leading-4", container ? "font-semibold" : "font-medium",
          root && "text-[13px]", done && "line-through decoration-muted-foreground/60")}>{item.title}</span>
      </div>
      <div className="flex items-center gap-1.5 text-[10px] leading-4 text-muted-foreground">
        <span className="shrink-0 uppercase tracking-wide">{external ? "External" : typeLabel(item)}</span>
        {state === "running" && (
          <span className="inline-flex shrink-0 items-center gap-0.5 font-medium text-foreground">
            <Radio className="h-3 w-3" style={{ color: "var(--viz-status-progress)" }} aria-hidden />
            {item.running_runs} running
          </span>
        )}
        {!container && state !== "running" && state !== "done" && (
          <span className="truncate font-medium text-foreground">
            {item.approval_state === "pending" && state === "ready" ? "Decision pending" : STATE_META[state].label}
          </span>
        )}
        {due && !done && <span className={cn("truncate", item.overdue && "font-medium text-destructive")}>{due}</span>}
        {hasProgress && <span className="ml-auto tabular-nums">{progress}%</span>}
      </div>
      {hasProgress && (
        <div className="absolute inset-x-2.5 bottom-0.5 h-[3px] overflow-hidden rounded-full bg-muted" aria-hidden>
          <div className="h-full rounded-full" style={{
            width: `${progress}%`, background: done ? "var(--viz-status-complete)" : "var(--viz-status-progress)",
          }} />
        </div>
      )}
      <Handle id="b" type="source" position={Position.Bottom} className={HANDLE} isConnectable={false} />
      <Handle id="dr" type="source" position={Position.Right} className={HANDLE} isConnectable={false} />
    </div>
  );
}

/** Frame around a grid of leaf children; the parent's breakdown edge ends on it. */
function FrameNode({ data }: NodeProps<FrameData>) {
  return (
    <div className="rounded-xl border border-dashed bg-muted/20" style={{ width: data.w, height: data.h }}
      data-testid="graph-frame">
      <Handle id="t" type="target" position={Position.Top} className={HANDLE} isConnectable={false} />
      <span className="absolute left-3 top-1 text-[10px] uppercase tracking-wide text-muted-foreground">{data.label}</span>
    </div>
  );
}

// Module-level: React Flow re-mounts every node when nodeTypes changes identity.
const NODE_TYPES = { task: memo(TaskNode), frame: memo(FrameNode) };

const FOCUS: NodeState[] = ["running", "active", "ready", "waiting", "blocked"];
const SWATCH: Record<NodeState, string> = {
  running: "var(--viz-status-progress)", active: "var(--viz-status-progress)", ready: "hsl(var(--foreground))",
  waiting: "var(--viz-status-blocked)", blocked: "var(--viz-status-blocked)", done: "var(--viz-status-complete)",
  idle: "hsl(var(--border))",
};
const CLOSED = new Set(["complete", "archived"]);
const PICK_KEY = "corvin.tasks.graph.scope";

const IRREGULAR: Record<string, string> = { criterion: "criteria", dependency: "dependencies", story: "stories" };

function plural(n: number, word: string): string {
  const w = word.toLowerCase();
  return `${n} ${n === 1 ? w : IRREGULAR[w] ?? (w.endsWith("s") ? w : `${w}s`)}`;
}

function typeName(key: string): string {
  return CATEGORY_LABEL[key] ?? KIND_META[key as Item["kind"]]?.label ?? key;
}

export default function GraphView({ items, now, selectedId, onSelect }: {
  items: Item[]; now: number; selectedId: string | null; onSelect: (id: string) => void;
}) {
  const choices = useMemo(() => {
    const kids = new Map<string, Item[]>();
    for (const it of items) if (it.parent_id) kids.set(it.parent_id, [...(kids.get(it.parent_id) ?? []), it]);
    const subtree = (roots: Item[]) => {
      const out: Item[] = [];
      const stack = [...roots];
      const seen = new Set<string>();
      while (stack.length) {
        const it = stack.pop()!;
        if (seen.has(it.id)) continue;
        seen.add(it.id);
        out.push(it);
        stack.push(...(kids.get(it.id) ?? []));
      }
      return out;
    };
    return graphChoices(items).map((c) => {
      const all = subtree(c.roots);
      const work = all.filter((i) => !KIND_META[i.kind].container);
      const root = c.roots.length === 1 ? c.roots[0] : null;
      const done = work.filter((i) => CLOSED.has(i.status)).length;
      return {
        id: c.id, root,
        title: root ? root.title : "Items without an initiative",
        progress: root ? displayProgress(root) : work.length ? Math.round((100 * done) / work.length) : 0,
        items: all.length, done, work: work.length,
        running: all.filter((i) => (i.running_runs ?? 0) > 0).length,
        active: work.filter((i) => i.status === "in_progress").length,
        latest: Math.max(0, ...all.map((i) => Date.parse(i.updated_at) || 0)),
      };
    });
  }, [items]);

  const [picked, setPicked] = useState<string | null>(() => {
    try { return localStorage.getItem(PICK_KEY); } catch { return null; }
  });
  const scope = useMemo(() => {
    if (picked && choices.some((c) => c.id === picked)) return picked;
    // Default: where something runs right now, else the most recently touched graph.
    const best = [...choices].sort((a, b) => b.running - a.running || b.latest - a.latest)[0];
    return best?.id ?? null;
  }, [picked, choices]);
  const pick = (id: string) => {
    setPicked(id);
    try { localStorage.setItem(PICK_KEY, id); } catch { /* storage blocked — the choice still holds for this view */ }
  };

  const [hideDone, setHideDone] = useState(false);
  const inScope = useMemo(() => (scope ? scopeItems(items, scope, hideDone) : []), [items, scope, hideDone]);
  const external = useMemo(() => (scope ? externalIds(items, scope) : new Set<string>()), [items, scope]);
  const layout: GraphLayout = useMemo(() => treeLayout(inScope, external),
    // Structure only: recomputed when ids / parents / dependencies / order change, not on a status poll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [structureKey(inScope), [...external].sort().join(",")]);
  const byId = useMemo(() => new Map(inScope.map((i) => [i.id, i])), [inScope]);
  const counts = useMemo(() => stateCounts(inScope.filter((i) => !external.has(i.id)), (i) => KIND_META[i.kind].container),
    [inScope, external]);
  const stats = useMemo(() => breakdownStats(layout, byId), [layout, byId]);
  const current = choices.find((c) => c.id === scope) ?? null;

  const nodes: Node[] = useMemo(() => {
    const out: Node[] = layout.frames.map((f) => ({
      id: f.id, type: "frame", position: { x: f.x, y: f.y }, draggable: false, connectable: false, selectable: false,
      zIndex: -1, data: { w: f.w, h: f.h, label: plural(f.count, typeName(f.groupKey)) } satisfies FrameData,
    }));
    for (const p of layout.nodes) {
      const it = byId.get(p.id);
      if (!it) continue;
      const closed = CLOSED.has(it.status);
      out.push({
        id: p.id, type: "task", position: { x: p.x, y: p.y }, draggable: false, connectable: false,
        data: {
          item: it, state: nodeState(it), progress: displayProgress(it), container: KIND_META[it.kind].container,
          root: p.depth === 0, hasProgress: closed || it.progress != null || it.child_ids.length > 0,
          due: deadlineText(it.deadline, now, closed), selected: it.id === selectedId, external: external.has(it.id),
        } satisfies NodeData,
      });
    }
    return out;
  }, [layout, byId, now, selectedId, external]);

  const edges: Edge[] = useMemo(() => layout.edges.flatMap((e): Edge[] => {
    const src = byId.get(e.source);
    if (!src) return [];
    if (e.kind === "hierarchy") {
      const tgt = byId.get(e.target);
      return [{
        id: e.id, source: e.source, target: e.target, sourceHandle: "b", targetHandle: "t", type: "smoothstep",
        animated: tgt ? nodeState(tgt) === "running" : false,
        style: { stroke: "hsl(var(--muted-foreground))", strokeOpacity: 0.5, strokeWidth: 1.25 },
      }];
    }
    const tgt = byId.get(e.target);
    if (!tgt) return [];
    const open = !CLOSED.has(src.status);
    const color = open ? "var(--viz-status-blocked)" : "var(--viz-status-complete)";
    return [{
      id: e.id, source: e.source, target: e.target, sourceHandle: "dr", targetHandle: "dl", type: "default",
      animated: open && nodeState(tgt) !== "done", zIndex: 1,
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
    const ids = nodesRef.current
      .filter((n) => n.type === "task" && states.includes((n.data as NodeData).state)).map((n) => ({ id: n.id }));
    rf.current?.fitView({ nodes: ids.length ? ids : undefined, padding: ids.length ? 0.3 : 0.08, duration, maxZoom: 1,
      minZoom: ids.length ? 0.55 : 0.05 });
  }, []);
  const standAt = useCallback((duration: number) => {
    const has = (s: NodeState) => nodesRef.current.some((n) => n.type === "task" && (n.data as NodeData).state === s);
    focus(has("running") ? ["running"] : has("active") ? ["active", "running"] : [], duration);
  }, [focus]);
  // A new graph (or a changed breakdown) opens on the whole breakdown — the
  // shape IS the point here; "Where we are now" zooms in. Never on a poll.
  useEffect(() => {
    const t = setTimeout(() => focus([], 300), 30);
    return () => clearTimeout(t);
  }, [layout.key, focus]);

  return (
    <div className="space-y-3" data-testid="graph-view">
      <div className="flex gap-2 overflow-x-auto pb-1" role="tablist" aria-label="Initiatives">
        {choices.map((c) => (
          <button key={c.id} type="button" role="tab" aria-selected={c.id === scope} data-testid={`graph-pick-${c.id}`}
            onClick={() => pick(c.id)}
            className={cn("flex w-60 shrink-0 flex-col gap-1.5 rounded-lg border px-3 py-2 text-left transition-colors",
              c.id === scope ? "border-foreground/50 bg-muted" : "hover:bg-muted/50")}>
            <span className="flex min-w-0 items-start gap-1.5">
              {c.root ? <StatusIcon status={c.root.status} className="mt-0.5 h-3.5 w-3.5" /> : null}
              <span className="line-clamp-2 text-xs font-semibold leading-4">{c.title}</span>
            </span>
            <span className="h-1 w-full overflow-hidden rounded-full bg-background" aria-hidden>
              <span className="block h-full rounded-full" style={{ width: `${c.progress}%`, background: "var(--viz-status-progress)" }} />
            </span>
            <span className="flex items-center gap-2 text-[11px] text-muted-foreground">
              <span>{c.done}/{c.work} done</span>
              {c.active > 0 && <span>{c.active} in progress</span>}
              {c.running > 0 && (
                <span className="ml-auto inline-flex items-center gap-1 font-medium text-foreground">
                  <span aria-hidden className="tasks-graph-pulse h-2 w-2 rounded-full" style={{ background: "var(--viz-status-progress)" }} />
                  {c.running} running
                </span>
              )}
            </span>
          </button>
        ))}
      </div>

      {current && (
        <div className="space-y-2">
          <p className="text-sm" data-testid="graph-breakdown">
            <span className="font-semibold">{current.title}</span>
            <span className="text-muted-foreground">
              {" "}— broken down into {plural(stats.items - 1, "item")} over {plural(Math.max(0, stats.levels - 1), "level")}
              {stats.perKind.length > 0 && `: ${stats.perKind.map(([k, n]) => plural(n, typeName(k))).join(" · ")}`}
              {stats.dependencies > 0 && ` · ${plural(stats.dependencies, "dependency")}`}
            </span>
          </p>
          <div className="flex flex-wrap items-center gap-2" aria-label="Graph controls">
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
            <button type="button" aria-pressed={hideDone} onClick={() => setHideDone(!hideDone)}
              className={cn("inline-flex h-7 items-center rounded-full border px-2.5 text-xs",
                hideDone ? "border-foreground/40 bg-muted font-medium" : "text-muted-foreground")}>
              Hide done
            </button>
            <Button size="sm" variant="ghost" className="ml-auto h-7" onClick={() => focus([])}>Whole breakdown</Button>
            <Button size="sm" variant="outline" className="h-7" onClick={() => standAt(500)}>
              <Crosshair className="mr-1 h-3.5 w-3.5" />Where we are now
            </Button>
          </div>
        </div>
      )}

      <div className="h-[70vh] min-h-[480px] overflow-hidden rounded-lg border bg-background" role="figure"
        aria-label={`Breakdown graph: ${layout.nodes.length} items, ${layout.edges.length} links`}>
        {layout.nodes.length === 0 ? (
          <p className="p-6 text-sm text-muted-foreground">
            {hideDone ? "Nothing open here — turn off “Hide done” to see the finished breakdown." : "No items to draw."}
          </p>
        ) : (
          <ReactFlow
            nodes={nodes} edges={edges} nodeTypes={NODE_TYPES}
            onInit={(inst) => { rf.current = inst; focus([], 0); }}
            onNodeClick={(_, n) => { if (n.type === "task") onSelect(n.id); }}
            nodesDraggable={false} nodesConnectable={false} elementsSelectable
            minZoom={0.05} maxZoom={1.75} proOptions={{ hideAttribution: true }}
          >
            <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="hsl(var(--border))" />
            <Controls showInteractive={false} />
            <MiniMap pannable zoomable ariaLabel="Graph overview"
              style={{ border: "1px solid hsl(var(--border))", borderRadius: 6 }}
              nodeColor={(n) => (n.type === "task" ? SWATCH[(n.data as NodeData).state] : "transparent")} nodeStrokeWidth={0} />
          </ReactFlow>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground" aria-label="Legend">
        <LegendLine label="Breakdown (parent → part)" stroke="hsl(var(--muted-foreground))" opacity={0.6} />
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

/** Ids + parents + dependencies + order: what the layout depends on. */
export function structureKey(items: Item[]): string {
  return items.map((i) => `${i.id}<${i.parent_id ?? ""}<${i.depends_on.join("+")}<${i.sort_key}`).join(",");
}


/**
 * Graph view — how an initiative was broken down, projected onto a graph (ADR-2060).
 *
 * One graph per initiative (or loop): the initiative on top, every level of
 * its breakdown below it (epic → story → task → subtask, gates, checkpoints,
 * preconditions, criteria), dependencies drawn across as arrows. Laid out top
 * → bottom as a tidy tree:
 *
 *   - a node sits centred above the block of its children;
 *   - sibling subtrees sit side by side in their stored order (sort_key);
 *   - a node with GROUP_AT or more parts groups its LEAF parts by type (task,
 *     precondition, gate, decision record …): each group of two or more is a
 *     framed, labelled grid ("6 tasks", "4 preconditions"), so an initiative with
 *     90 tasks is a block, not a 25 000 px row; parts with their own breakdown
 *     stay subtrees beside the groups;
 *   - prerequisites from OUTSIDE the initiative sit in a column to its left.
 *
 * Pure and deterministic: positions depend on STRUCTURE only (ids, parents,
 * dependencies, order) — never on status — so a live poll recolours nodes
 * without moving them.
 */
import type { Item, ItemStatus } from "@/lib/api/task-tracking";

export const NODE_W = 240;
export const NODE_H = 76;
export const GAP_X = 28;        // between sibling subtrees
export const GAP_LEVEL = 70;    // between a parent and its children
export const GRID_GAP_Y = 18;   // between grid rows
export const GROUP_AT = 3;      // this many parts or more → leaf parts grouped by type
export const MAX_GRID_COLS = 8;
export const FRAME_PAD = 14;
export const FRAME_LABEL = 18;

export type EdgeKind = "hierarchy" | "dependency";

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  kind: EdgeKind;
}

export interface PlacedNode {
  id: string;
  /** breakdown depth below the root (0 = the initiative); -1 = an outside prerequisite */
  depth: number;
  x: number;
  y: number;
}

/** A frame around a group of same-type leaf parts — the breakdown edge ends on the frame. */
export interface GridFrame {
  id: string;
  parentId: string;
  /** the parts' type: their category (gate, precondition …) or else their kind */
  groupKey: string;
  count: number;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface GraphLayout {
  nodes: PlacedNode[];
  edges: GraphEdge[];
  frames: GridFrame[];
  /** changes only when the structure changes — the view refits on it. */
  key: string;
}

/** Where a node stands right now — derived from status + dependencies + runs. */
export type NodeState = "running" | "active" | "ready" | "waiting" | "blocked" | "done" | "idle";

const CLOSED: ItemStatus[] = ["complete", "archived"];

export function nodeState(it: Item): NodeState {
  // A run working on an item right now is where we stand — even when the item
  // is already marked complete (e.g. an accepted decision still being worked on).
  if ((it.running_runs ?? 0) > 0) return "running";
  if (CLOSED.includes(it.status)) return "done";
  if (it.status === "blocked") return "blocked";
  if (it.waiting_on.length > 0) return "waiting";
  if (it.status === "in_progress") return "active";
  // open + nothing to wait for = can start now (the DAG's frontier)
  return "ready";
}

export const STATE_META: Record<NodeState, { label: string; order: number }> = {
  running: { label: "Running now", order: 0 },
  active: { label: "In progress", order: 1 },
  ready: { label: "Ready to start", order: 2 },
  waiting: { label: "Waiting on a dependency", order: 3 },
  blocked: { label: "Blocked", order: 4 },
  done: { label: "Done", order: 5 },
  idle: { label: "—", order: 6 },
};

/** Items in scope: one initiative's subtree (or every root), optionally without closed work. */
export function scopeItems(items: Item[], scope: string, hideDone: boolean): Item[] {
  const byParent = new Map<string | null, Item[]>();
  const ids = new Set(items.map((i) => i.id));
  for (const it of items) {
    const p = it.parent_id && ids.has(it.parent_id) ? it.parent_id : null;
    byParent.set(p, [...(byParent.get(p) ?? []), it]);
  }
  let picked: Item[];
  if (scope === "all") {
    picked = items;
  } else {
    picked = [];
    const stack = scopeRoots(items, scope);
    const seen = new Set<string>();
    while (stack.length) {
      const it = stack.pop()!;
      if (seen.has(it.id)) continue;
      seen.add(it.id);
      picked.push(it);
      stack.push(...(byParent.get(it.id) ?? []));
    }
  }
  if (hideDone) {
    // Hide closed LEAVES; a closed container stays while it still holds open work.
    const keep = new Set<string>();
    const openBelow = (id: string, depth = 0): boolean => {
      if (depth > 64) return false;
      return (byParent.get(id) ?? []).some((c) => !CLOSED.includes(c.status) || openBelow(c.id, depth + 1));
    };
    for (const it of picked) if (!CLOSED.includes(it.status) || openBelow(it.id)) keep.add(it.id);
    picked = picked.filter((i) => keep.has(i.id));
  }
  if (scope === "all") return picked;
  // A prerequisite outside the scope is still what this scope waits for: bring
  // it in (one hop), so a blocked initiative shows WHAT blocks it.
  const inside = new Set(picked.map((i) => i.id));
  const byId = new Map(items.map((i) => [i.id, i]));
  const outside: Item[] = [];
  for (const it of picked) {
    for (const d of it.depends_on) {
      const dep = byId.get(d);
      if (dep && !inside.has(d)) { inside.add(d); outside.push(dep); }
    }
  }
  return [...picked, ...outside];
}

/** The pseudo-scope for top-level work items that belong to no initiative. */
export const LOOSE_SCOPE = "__loose";
const CONTAINER_KINDS = new Set(["initiative", "epic", "story"]);

/** The graphs to choose from: every top-level container (an initiative, a loop, a
 *  stand-alone epic), plus the loose top-level work items as one more graph. */
export function scopeRoots(items: Item[], scope: string): Item[] {
  const ids = new Set(items.map((i) => i.id));
  const top = (i: Item) => !i.parent_id || !ids.has(i.parent_id);
  if (scope === LOOSE_SCOPE) return items.filter((i) => top(i) && !CONTAINER_KINDS.has(i.kind));
  return items.filter((i) => i.id === scope);
}

export function graphChoices(items: Item[]): { id: string; roots: Item[] }[] {
  const ids = new Set(items.map((i) => i.id));
  const tops = items.filter((i) => !i.parent_id || !ids.has(i.parent_id));
  const out = tops.filter((i) => CONTAINER_KINDS.has(i.kind)).map((i) => ({ id: i.id, roots: [i] }));
  const loose = tops.filter((i) => !CONTAINER_KINDS.has(i.kind));
  if (loose.length) out.push({ id: LOOSE_SCOPE, roots: loose });
  return out;
}

/** Ids a scope pulled in only as outside prerequisites. */
export function externalIds(items: Item[], scope: string): Set<string> {
  if (scope === "all") return new Set();
  const inScope = new Set(scopeItems(items, scope, false).map((i) => i.id));
  const own = new Set<string>();
  const stack = scopeRoots(items, scope).map((i) => i.id);
  while (stack.length) {
    const id = stack.pop()!;
    if (own.has(id)) continue;
    own.add(id);
    for (const it of items) if (it.parent_id === id) stack.push(it.id);
  }
  return new Set([...inScope].filter((id) => !own.has(id)));
}

export function graphEdges(items: Item[]): GraphEdge[] {
  const ids = new Set(items.map((i) => i.id));
  const edges: GraphEdge[] = [];
  for (const it of items) {
    if (it.parent_id && ids.has(it.parent_id)) {
      edges.push({ id: `h:${it.parent_id}>${it.id}`, source: it.parent_id, target: it.id, kind: "hierarchy" });
    }
    for (const dep of it.depends_on) {
      if (ids.has(dep) && dep !== it.id) {
        edges.push({ id: `d:${dep}>${it.id}`, source: dep, target: it.id, kind: "dependency" });
      }
    }
  }
  return edges;
}

/**
 * Tidy top-down tree of ``items`` (one scope). Roots are the items whose parent
 * is not in ``items`` — normally the one initiative; ``external`` ids are placed
 * in a column left of the roots instead of as roots of their own.
 */
export function typeKey(it: Pick<Item, "category" | "kind">): string {
  return it.category ?? it.kind;
}

/** Roughly square groups (the graph area is landscape, but the tree already spreads wide). */
function gridCols(n: number): number {
  return Math.min(MAX_GRID_COLS, Math.max(1, Math.ceil(Math.sqrt(n))));
}

export function treeLayout(items: Item[], external: Set<string> = new Set()): GraphLayout {
  const sorted = [...items].sort((a, b) => a.sort_key - b.sort_key || a.id.localeCompare(b.id));
  const byId = new Map(sorted.map((i) => [i.id, i]));
  const kids = new Map<string, string[]>();
  const roots: string[] = [];
  for (const it of sorted) {
    if (external.has(it.id)) continue;
    const p = it.parent_id && byId.has(it.parent_id) && !external.has(it.parent_id) ? it.parent_id : null;
    if (p) kids.set(p, [...(kids.get(p) ?? []), it.id]);
    else roots.push(it.id);
  }
  const isLeaf = (id: string) => !(kids.get(id) ?? []).length;

  type Unit =
    | { kind: "node"; id: string; w: number; h: number }
    | { kind: "grid"; key: string; ids: string[]; cols: number; w: number; h: number };
  interface Block { w: number; h: number; units: Unit[]; unitsW: number }
  const blocks = new Map<string, Block>();
  const onPath = new Set<string>();

  const measure = (id: string): Block => {
    const hit = blocks.get(id);
    if (hit) return hit;
    onPath.add(id);
    const ks = (kids.get(id) ?? []).filter((k) => !onPath.has(k));  // never recurse into a cycle
    const units: Unit[] = [];
    if (ks.length >= GROUP_AT) {
      const groups = new Map<string, string[]>();
      for (const k of ks) if (isLeaf(k)) groups.set(typeKey(byId.get(k)!), [...(groups.get(typeKey(byId.get(k)!)) ?? []), k]);
      const emitted = new Set<string>();
      for (const k of ks) {   // units in the order of their first part
        const g = isLeaf(k) ? groups.get(typeKey(byId.get(k)!))! : null;
        if (g && g.length >= 2) {
          const key = typeKey(byId.get(k)!);
          if (emitted.has(key)) continue;
          emitted.add(key);
          const cols = gridCols(g.length);
          const rows = Math.ceil(g.length / cols);
          units.push({
            kind: "grid", key, ids: g, cols,
            w: cols * NODE_W + (cols - 1) * GAP_X + 2 * FRAME_PAD,
            h: rows * NODE_H + (rows - 1) * GRID_GAP_Y + 2 * FRAME_PAD + FRAME_LABEL,
          });
        } else {
          const b = measure(k);
          units.push({ kind: "node", id: k, w: b.w, h: b.h });
        }
      }
    } else {
      for (const k of ks) {
        const b = measure(k);
        units.push({ kind: "node", id: k, w: b.w, h: b.h });
      }
    }
    const unitsW = units.reduce((s, u) => s + u.w, 0) + Math.max(0, units.length - 1) * GAP_X;
    const b: Block = {
      w: Math.max(NODE_W, unitsW),
      h: units.length ? NODE_H + GAP_LEVEL + Math.max(...units.map((u) => u.h)) : NODE_H,
      units, unitsW,
    };
    onPath.delete(id);
    blocks.set(id, b);
    return b;
  };

  const nodes: PlacedNode[] = [];
  const frames: GridFrame[] = [];
  const gridded = new Set<string>();  // parts placed inside a group frame
  const placed = new Set<string>();
  const place = (id: string, x0: number, y0: number, depth: number) => {
    if (placed.has(id)) return;
    placed.add(id);
    const b = measure(id);
    nodes.push({ id, depth, x: x0 + (b.w - NODE_W) / 2, y: y0 });
    const top = y0 + NODE_H + GAP_LEVEL;
    let x = x0 + (b.w - b.unitsW) / 2;
    for (const u of b.units) {
      if (u.kind === "node") {
        place(u.id, x, top, depth + 1);
      } else {
        frames.push({ id: `grid:${id}:${u.key}`, parentId: id, groupKey: u.key, count: u.ids.length, x, y: top, w: u.w, h: u.h });
        u.ids.forEach((k, i) => {
          placed.add(k);
          gridded.add(k);
          nodes.push({
            id: k, depth: depth + 1,
            x: x + FRAME_PAD + (i % u.cols) * (NODE_W + GAP_X),
            y: top + FRAME_PAD + FRAME_LABEL + Math.floor(i / u.cols) * (NODE_H + GRID_GAP_Y),
          });
        });
      }
      x += u.w + GAP_X;
    }
  };
  let x = 0;
  for (const r of roots) {
    place(r, x, 0, 0);
    x += measure(r).w + GAP_X * 3;
  }
  // Outside prerequisites: one column left of everything, top-aligned.
  const ext = sorted.filter((i) => external.has(i.id));
  ext.forEach((it, i) => nodes.push({ id: it.id, depth: -1, x: -(NODE_W + GAP_X * 4), y: i * (NODE_H + GRID_GAP_Y) }));

  // A grouped part's breakdown edge is replaced by ONE edge onto its group frame.
  const edges = graphEdges(sorted).filter((e) => !(e.kind === "hierarchy" && gridded.has(e.target)));
  for (const f of frames) edges.push({ id: `h:${f.parentId}>${f.id}`, source: f.parentId, target: f.id, kind: "hierarchy" });
  const key = [sorted.map((i) => `${i.id}<${i.parent_id ?? ""}`).join(","), edges.map((e) => e.id).join(","),
    [...external].sort().join(",")].join("|");
  return { nodes, edges, frames, key };
}

/** Shape of one breakdown, for the header line: levels and counts per kind/category. */
export function breakdownStats(layout: GraphLayout, byId: Map<string, Item>): {
  levels: number; items: number; perKind: [string, number][]; dependencies: number;
} {
  const inside = layout.nodes.filter((n) => n.depth >= 0);
  const per = new Map<string, number>();
  for (const n of inside) {
    const it = byId.get(n.id);
    if (!it || n.depth === 0) continue;
    const k = it.category ?? it.kind;
    per.set(k, (per.get(k) ?? 0) + 1);
  }
  return {
    levels: inside.length ? Math.max(...inside.map((n) => n.depth)) + 1 : 0,
    items: inside.length,
    perKind: [...per.entries()].sort((a, b) => b[1] - a[1]),
    dependencies: layout.edges.filter((e) => e.kind === "dependency").length,
  };
}

/** Counts per state over WORK items (containers summarise, they do not count). */
export function stateCounts(items: Item[], isContainer: (it: Item) => boolean): Record<NodeState, number> {
  const c: Record<NodeState, number> = { running: 0, active: 0, ready: 0, waiting: 0, blocked: 0, done: 0, idle: 0 };
  for (const it of items) if (!isContainer(it)) c[nodeState(it)] += 1;
  return c;
}

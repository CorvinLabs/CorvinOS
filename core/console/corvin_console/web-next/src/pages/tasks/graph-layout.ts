/**
 * Graph view — the work items as a DAG, laid out left → right (ADR-2060).
 *
 * Pure and deterministic: the same items give the same positions, and the
 * positions depend on STRUCTURE only (ids, parents, dependencies, scope) — never
 * on status — so a live poll recolours nodes without moving them.
 *
 * Edges:
 *   hierarchy   parent → child           (the plan's breakdown)
 *   dependency  prerequisite → dependent (the item must wait for it)
 *
 * Layout (Sugiyama-lite):
 *   1. layer = longest path from a source over both edge kinds; a cycle
 *      (possible only across the two kinds) is broken, never looped on;
 *   2. order within a layer by the barycenter of neighbours, 4 sweeps;
 *   3. a layer taller than MAX_PER_COLUMN wraps into sub-columns, so an
 *      initiative with 90 tasks is a grid, not a 6 000 px column.
 */
import type { Item, ItemStatus } from "@/lib/api/task-tracking";

export const NODE_W = 260;
export const NODE_H = 76;
export const GAP_X = 70;
export const GAP_Y = 18;
export const SUBCOL_GAP = 24;
export const MAX_PER_COLUMN = 12;

export type EdgeKind = "hierarchy" | "dependency";

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  kind: EdgeKind;
}

export interface PlacedNode {
  id: string;
  layer: number;
  x: number;
  y: number;
}

export interface GraphLayout {
  nodes: PlacedNode[];
  edges: GraphEdge[];
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
    const stack = items.filter((i) => i.id === scope);
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

/** Ids a scope pulled in only as outside prerequisites. */
export function externalIds(items: Item[], scope: string): Set<string> {
  if (scope === "all") return new Set();
  const inScope = new Set(scopeItems(items, scope, false).map((i) => i.id));
  const own = new Set<string>();
  const stack = [scope];
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

function assignLayers(order: string[], edges: GraphEdge[]): Map<string, number> {
  const indeg = new Map(order.map((id) => [id, 0]));
  const out = new Map<string, string[]>();
  for (const e of edges) {
    indeg.set(e.target, (indeg.get(e.target) ?? 0) + 1);
    out.set(e.source, [...(out.get(e.source) ?? []), e.target]);
  }
  const layer = new Map<string, number>();
  const done = new Set<string>();
  const queue = order.filter((id) => indeg.get(id) === 0);
  for (const id of queue) layer.set(id, 0);
  while (done.size < order.length) {
    if (!queue.length) {
      // A cycle: release the pending node with the fewest open inputs (stable by input order).
      let pick = "";
      let best = Infinity;
      for (const id of order) {
        if (done.has(id)) continue;
        const d = indeg.get(id) ?? 0;
        if (d < best) { best = d; pick = id; }
      }
      indeg.set(pick, 0);
      if (!layer.has(pick)) layer.set(pick, 0);
      queue.push(pick);
    }
    const id = queue.shift()!;
    if (done.has(id)) continue;
    done.add(id);
    for (const t of out.get(id) ?? []) {
      if (done.has(t)) continue;  // a broken cycle edge
      layer.set(t, Math.max(layer.get(t) ?? 0, (layer.get(id) ?? 0) + 1));
      const d = (indeg.get(t) ?? 0) - 1;
      indeg.set(t, d);
      if (d === 0) queue.push(t);
    }
  }
  return layer;
}

export function layoutGraph(items: Item[]): GraphLayout {
  const sorted = [...items].sort((a, b) => a.sort_key - b.sort_key || a.id.localeCompare(b.id));
  const order = sorted.map((i) => i.id);
  const edges = graphEdges(sorted);
  const layerOf = assignLayers(order, edges);

  const nLayers = Math.max(0, ...layerOf.values()) + 1;
  const layers: string[][] = Array.from({ length: nLayers }, () => []);
  for (const id of order) layers[layerOf.get(id) ?? 0].push(id);

  const nbrs = new Map<string, { up: string[]; down: string[] }>();
  for (const id of order) nbrs.set(id, { up: [], down: [] });
  for (const e of edges) {
    nbrs.get(e.target)?.up.push(e.source);
    nbrs.get(e.source)?.down.push(e.target);
  }
  const pos = new Map<string, number>();
  const index = () => layers.forEach((l) => l.forEach((id, i) => pos.set(id, i)));
  index();
  const bary = (id: string, dir: "up" | "down", fallback: number) => {
    const ns = nbrs.get(id)![dir].filter((n) => pos.has(n));
    return ns.length ? ns.reduce((s, n) => s + pos.get(n)!, 0) / ns.length : fallback;
  };
  for (let sweep = 0; sweep < 4; sweep++) {
    const down = sweep % 2 === 0;
    const range = down ? layers.map((_, i) => i).slice(1) : layers.map((_, i) => i).reverse().slice(1);
    for (const li of range) {
      const l = layers[li];
      const key = new Map(l.map((id, i) => [id, bary(id, down ? "up" : "down", i)]));
      l.sort((a, b) => key.get(a)! - key.get(b)! || pos.get(a)! - pos.get(b)!);
      l.forEach((id, i) => pos.set(id, i));
    }
  }

  const nodes: PlacedNode[] = [];
  let x = 0;
  const colHeights: number[] = [];
  const columns: { ids: string[]; layer: number; x: number }[] = [];
  layers.forEach((l, li) => {
    const sub = Math.max(1, Math.ceil(l.length / MAX_PER_COLUMN));
    const per = Math.ceil(l.length / sub);
    for (let s = 0; s < sub; s++) {
      const ids = l.slice(s * per, (s + 1) * per);
      columns.push({ ids, layer: li, x });
      colHeights.push(ids.length * (NODE_H + GAP_Y) - GAP_Y);
      x += NODE_W + (s < sub - 1 ? SUBCOL_GAP : GAP_X);
    }
  });
  const tallest = Math.max(0, ...colHeights);
  columns.forEach((c, ci) => {
    const top = (tallest - colHeights[ci]) / 2;  // centre each column on the tallest
    c.ids.forEach((id, i) => nodes.push({ id, layer: c.layer, x: c.x, y: top + i * (NODE_H + GAP_Y) }));
  });

  const key = [order.join(","), edges.map((e) => e.id).join(",")].join("|");
  return { nodes, edges, key };
}

/** Counts per state over WORK items (containers summarise, they do not count). */
export function stateCounts(items: Item[], isContainer: (it: Item) => boolean): Record<NodeState, number> {
  const c: Record<NodeState, number> = { running: 0, active: 0, ready: 0, waiting: 0, blocked: 0, done: 0, idle: 0 };
  for (const it of items) if (!isContainer(it)) c[nodeState(it)] += 1;
  return c;
}

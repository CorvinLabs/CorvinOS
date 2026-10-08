/**
 * Island layout for the Knowledge Graph explorer: the graph is split into communities
 * ("islands"), each island is laid out on its own (hub in the middle, neighbours spiralling out
 * in breadth-first order) and the islands are packed so that no two touch. Positions are fixed,
 * so no physics runs and nothing can collapse into a clump.
 *
 * Why not vis-network's hierarchical layout: with ~1300 nodes nearly all sit on a few levels, so
 * the layout becomes one very wide row and "fit" shrinks it to a line.
 */
import type { GEdge, GNode } from "./graph-model";

/** Minimum distance between any two node centres (fits an id label such as "ADR-2206"). */
export const NODE_PITCH = 84;
/** Free space kept between two islands. */
export const ISLAND_GAP = 140;
const SPIRAL_SCALE = NODE_PITCH * 1.1;
const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));

export interface Island { id: string; name: string; count: number; cx: number; cy: number; r: number; loose: boolean }
export interface IslandLayout { positions: Map<string, { x: number; y: number }>; islands: Island[]; islandOf: Map<string, string> }

/** One pass of local moving; returns the community per node and whether any node moved. */
function localMoving(nbr: Map<number, number>[]): { comm: number[]; moved: boolean } {
  const n = nbr.length;
  const k = nbr.map((m) => { let t = 0; for (const w of m.values()) t += w; return t; });
  const m2 = k.reduce((a, b) => a + b, 0);
  const comm = Array.from({ length: n }, (_, i) => i);
  const tot = [...k];
  let moved = false;
  if (m2 === 0) return { comm, moved };
  for (let pass = 0; pass < 30; pass++) {
    let changed = false;
    for (let i = 0; i < n; i++) {
      const own = comm[i];
      const kin = new Map<number, number>();
      for (const [j, w] of nbr[i]) if (j !== i) kin.set(comm[j], (kin.get(comm[j]) ?? 0) + w);
      tot[own] -= k[i];
      const gain = (c: number) => (kin.get(c) ?? 0) - (tot[c] * k[i]) / m2;
      let best = own;
      let bestGain = gain(own);
      for (const c of [...kin.keys()].sort((a, b) => a - b)) {
        const g = gain(c);
        if (g > bestGain + 1e-12) { best = c; bestGain = g; }
      }
      tot[best] += k[i];
      if (best !== own) { comm[i] = best; changed = true; moved = true; }
    }
    if (!changed) break;
  }
  return { comm, moved };
}

/** Louvain community detection (modularity): robust to bridge edges and hubs, deterministic. */
function communities(keys: string[], adj: Map<string, string[]>): Map<string, string> {
  const index = new Map(keys.map((k, i) => [k, i] as const));
  let nbr: Map<number, number>[] = keys.map((k) => new Map((adj.get(k) ?? []).map((n) => [index.get(n)!, 1] as const)));
  let members: number[][] = keys.map((_, i) => [i]);
  for (let level = 0; level < 10; level++) {
    const { comm, moved } = localMoving(nbr);
    if (!moved) break;
    const ids = [...new Set(comm)].sort((a, b) => a - b);
    const compact = new Map(ids.map((c, i) => [c, i] as const));
    const next: Map<number, number>[] = ids.map(() => new Map());
    const nextMembers: number[][] = ids.map(() => []);
    nbr.forEach((row, u) => {
      const cu = compact.get(comm[u])!;
      nextMembers[cu].push(...members[u]);
      for (const [v, w] of row) { const cv = compact.get(comm[v])!; next[cu].set(cv, (next[cu].get(cv) ?? 0) + w); }
    });
    nbr = next;
    members = nextMembers;
  }
  const label = new Map<string, string>();
  for (const group of members) {
    const rep = group.map((i) => keys[i]).sort()[0];
    for (const i of group) label.set(keys[i], rep);
  }
  return label;
}

/** Breadth-first order from the best-connected member, so linked nodes end up near each other. */
function bfsOrder(members: string[], adj: Map<string, string[]>): string[] {
  const inGroup = new Set(members);
  const deg = (k: string) => adj.get(k)?.length ?? 0;
  const byRank = [...members].sort((a, b) => deg(b) - deg(a) || (a < b ? -1 : 1));
  const seen = new Set<string>();
  const out: string[] = [];
  for (const start of byRank) {
    if (seen.has(start)) continue;
    seen.add(start);
    const queue = [start];
    for (let i = 0; i < queue.length; i++) {
      out.push(queue[i]);
      const next = (adj.get(queue[i]) ?? []).filter((n) => inGroup.has(n) && !seen.has(n)).sort();
      for (const n of next) { seen.add(n); queue.push(n); }
    }
  }
  return out;
}

export function computeIslandLayout(nodes: GNode[], edges: GEdge[]): IslandLayout {
  const byKey = new Map(nodes.map((n) => [n.id, n] as const));
  const keys = [...byKey.keys()].sort();
  const adjSet = new Map<string, Set<string>>(keys.map((k) => [k, new Set<string>()] as const));
  for (const e of edges) {
    if (e.from_id === e.to_id || !byKey.has(e.from_id) || !byKey.has(e.to_id)) continue;
    adjSet.get(e.from_id)!.add(e.to_id);
    adjSet.get(e.to_id)!.add(e.from_id);
  }
  const adj = new Map([...adjSet].map(([k, s]) => [k, [...s].sort()] as const));

  const groups = new Map<string, string[]>();
  for (const [k, l] of communities(keys, adj)) (groups.get(l) ?? groups.set(l, []).get(l)!).push(k);
  const linked = [...groups.values()].filter((g) => g.length > 1);
  const loose = [...groups.values()].filter((g) => g.length === 1).map((g) => g[0]).sort();

  interface Local { island: Island; local: Map<string, { x: number; y: number }> }
  const build = (id: string, name: string, members: string[], isLoose: boolean): Local => {
    const order = isLoose ? members : bfsOrder(members, adj);
    const local = new Map<string, { x: number; y: number }>();
    order.forEach((k, i) => {
      const r = SPIRAL_SCALE * Math.sqrt(i);
      local.set(k, { x: r * Math.cos(i * GOLDEN_ANGLE), y: r * Math.sin(i * GOLDEN_ANGLE) });
    });
    const r = SPIRAL_SCALE * Math.sqrt(Math.max(order.length - 1, 0)) + NODE_PITCH;
    return { island: { id, name, count: order.length, cx: 0, cy: 0, r, loose: isLoose }, local };
  };

  const locals: Local[] = linked.map((members) => {
    const head = bfsOrder(members, adj)[0];
    const n = byKey.get(head)!;
    return build(head, `${n.label ?? n.id} · ${n.title.length > 38 ? `${n.title.slice(0, 37)}…` : n.title}`, members, false);
  });
  if (loose.length > 0) locals.push(build("__unlinked__", "Unlinked", loose, true));
  locals.sort((a, b) => b.island.r - a.island.r || (a.island.id < b.island.id ? -1 : 1));

  // Pack: walk an outward spiral from the middle and take the first spot that touches no island.
  const placed: Island[] = [];
  for (const l of locals) {
    const isl = l.island;
    for (let step = 0; ; step++) {
      const rad = 14 * step;
      const ang = 0.7 * step;
      const cx = rad * Math.cos(ang);
      const cy = rad * Math.sin(ang);
      if (placed.every((p) => Math.hypot(p.cx - cx, p.cy - cy) >= p.r + isl.r + ISLAND_GAP)) {
        isl.cx = cx;
        isl.cy = cy;
        break;
      }
    }
    placed.push(isl);
  }

  const positions = new Map<string, { x: number; y: number }>();
  for (const l of locals) for (const [k, p] of l.local) positions.set(k, { x: p.x + l.island.cx, y: p.y + l.island.cy });
  const islandOf = new Map<string, string>();
  for (const l of locals) for (const k of l.local.keys()) islandOf.set(k, l.island.id);
  return { positions, islands: locals.map((l) => l.island), islandOf };
}

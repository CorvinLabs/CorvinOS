import { existsSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { ISLAND_GAP, NODE_PITCH, computeIslandLayout } from "@/pages/corvin-knowledge/island-layout";
import type { GEdge, GNode } from "@/pages/corvin-knowledge/graph-model";

const node = (id: string): GNode => ({ id, title: `Title ${id}`, type: "decision", status: "accepted", tags: [] });
const edge = (a: string, b: string): GEdge => ({ from_id: a, to_id: b, relation: "related" });
const clique = (prefix: string, n: number) => {
  const ids = Array.from({ length: n }, (_, i) => `${prefix}${i}`);
  return { ids, edges: ids.flatMap((a, i) => ids.slice(i + 1).map((b) => edge(a, b))) };
};

function minNodeDistance(pos: Map<string, { x: number; y: number }>): number {
  const pts = [...pos.values()];
  let min = Infinity;
  for (let i = 0; i < pts.length; i++) for (let j = i + 1; j < pts.length; j++) min = Math.min(min, Math.hypot(pts[i].x - pts[j].x, pts[i].y - pts[j].y));
  return min;
}
function expectIslandsApart(l: ReturnType<typeof computeIslandLayout>) {
  for (let i = 0; i < l.islands.length; i++) for (let j = i + 1; j < l.islands.length; j++) {
    const a = l.islands[i], b = l.islands[j];
    expect(Math.hypot(a.cx - b.cx, a.cy - b.cy)).toBeGreaterThanOrEqual(a.r + b.r + ISLAND_GAP - 1e-6);
  }
}

describe("computeIslandLayout", () => {
  it("gives every node a position and no two nodes closer than the pitch", () => {
    const a = clique("a", 8), b = clique("b", 8);
    const nodes = [...a.ids, ...b.ids, "solo1", "solo2"].map(node);
    const l = computeIslandLayout(nodes, [...a.edges, ...b.edges, edge("a0", "b0")]);
    expect(l.positions.size).toBe(nodes.length);
    expect(minNodeDistance(l.positions)).toBeGreaterThanOrEqual(NODE_PITCH);
    expectIslandsApart(l);
  });
  it("splits two dense groups joined by one edge into two islands, and collects loose nodes in one", () => {
    const a = clique("a", 8), b = clique("b", 8);
    const l = computeIslandLayout([...a.ids, ...b.ids, "x", "y", "z"].map(node), [...a.edges, ...b.edges, edge("a0", "b0")]);
    expect(l.islands.filter((i) => !i.loose).map((i) => i.count).sort()).toEqual([8, 8]);
    const loose = l.islands.filter((i) => i.loose);
    expect(loose).toHaveLength(1);
    expect(loose[0].count).toBe(3);
    expect(loose[0].name).toBe("Unlinked");
  });
  it("is deterministic", () => {
    const a = clique("a", 6);
    const nodes = a.ids.map(node);
    const one = computeIslandLayout(nodes, a.edges), two = computeIslandLayout([...nodes].reverse(), [...a.edges].reverse());
    expect([...one.positions]).toEqual([...two.positions]);
  });
  it("ignores edges to unknown nodes and self loops, and handles an empty graph", () => {
    expect(computeIslandLayout([], []).islands).toEqual([]);
    const l = computeIslandLayout([node("a"), node("b")], [edge("a", "ghost"), edge("a", "a")]);
    expect(l.positions.size).toBe(2);
  });
  it("keeps a hub from pulling two otherwise separate groups into one island", () => {
    const a = clique("a", 8), b = clique("b", 8);
    const spokes = Array.from({ length: 40 }, (_, i) => `s${i}`);
    const hubEdges = [...a.ids.slice(0, 2), ...b.ids.slice(0, 2), ...spokes].map((k) => edge("HUB", k));
    const l = computeIslandLayout([...a.ids, ...b.ids, ...spokes, "HUB"].map(node), [...a.edges, ...b.edges, ...hubEdges]);
    const big = l.islands.filter((i) => !i.loose && i.count >= 8);
    expect(big.length).toBeGreaterThanOrEqual(2);
    expect(Math.max(...l.islands.map((i) => i.count))).toBeLessThan(a.ids.length + b.ids.length + spokes.length + 1);
  });
});

const KB = "/home/shumway/projects/Corvin-Knowledge/kb/graph";
describe.skipIf(!existsSync(`${KB}/entities.jsonl`))("on the real knowledge graph", () => {
  const rows = (f: string) => readFileSync(`${KB}/${f}`, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
  const nodes: GNode[] = rows("entities.jsonl").map((e) => ({ id: e.uid ?? e.id, label: e.id, title: e.title ?? "", type: e.type, status: e.status, tags: [] }));
  const edges: GEdge[] = rows("relations.jsonl").map((r) => ({ from_id: r.src, to_id: r.dst, relation: r.rel }));
  const t0 = Date.now();
  const l = computeIslandLayout(nodes, edges);
  const ms = Date.now() - t0;

  it("lays out every node, no two closer than the pitch", () => {
    expect(l.positions.size).toBe(nodes.length);
    expect(minNodeDistance(l.positions)).toBeGreaterThanOrEqual(NODE_PITCH);
  });
  it("shows several separate islands instead of one clump, and they do not touch", () => {
    expect(l.islands.filter((i) => !i.loose).length).toBeGreaterThanOrEqual(8);
    expect(Math.max(...l.islands.map((i) => i.count))).toBeLessThan(nodes.length * 0.7);
    expectIslandsApart(l);
  });
  it("is fast enough for the UI thread", () => { expect(ms).toBeLessThan(3000); });
});

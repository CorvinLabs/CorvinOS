/** Pure graph helpers for the Knowledge Graph explorer (ADR-2237). */

export interface GNode { id: string; label?: string; title: string; type: string; status: string; tags: string[] }
export interface GEdge { from_id: string; to_id: string; relation: string }

/** Keys within `hops` steps of `key`, following edges in both directions. Includes `key` itself. */
export function neighbourhood(edges: GEdge[], key: string, hops: number): Set<string> {
  const adj = new Map<string, string[]>();
  for (const e of edges) {
    (adj.get(e.from_id) ?? adj.set(e.from_id, []).get(e.from_id)!).push(e.to_id);
    (adj.get(e.to_id) ?? adj.set(e.to_id, []).get(e.to_id)!).push(e.from_id);
  }
  const seen = new Set<string>([key]);
  let frontier = [key];
  for (let h = 0; h < hops; h++) {
    const next: string[] = [];
    for (const k of frontier) for (const n of adj.get(k) ?? []) if (!seen.has(n)) { seen.add(n); next.push(n); }
    frontier = next;
  }
  return seen;
}

/** The start node: the decision with the highest number (the entities carry no creation time). */
export function newestDecisionKey(nodes: GNode[]): string | null {
  let best: { key: string; n: number } | null = null;
  for (const e of nodes) {
    if (e.type !== "decision") continue;
    const n = Number(/(\d+)$/.exec(e.label ?? e.id)?.[1] ?? NaN);
    if (Number.isFinite(n) && (best === null || n > best.n)) best = { key: e.id, n };
  }
  return best?.key ?? nodes[0]?.id ?? null;
}

/** Id/title/tag search: an exact id first, then id prefix, then title/tag substring. */
export function searchNodes(nodes: GNode[], query: string, limit = 8): GNode[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const rank = (e: GNode): number => {
    const id = (e.label ?? e.id).toLowerCase();
    if (id === q) return 0;
    if (id.startsWith(q)) return 1;
    if (id.includes(q)) return 2;
    if (e.title.toLowerCase().includes(q)) return 3;
    if (e.tags.some((t) => t.toLowerCase().includes(q))) return 4;
    return 9;
  };
  return nodes.map((e) => [rank(e), e] as const).filter(([r]) => r < 9)
    .sort((a, b) => a[0] - b[0] || (a[1].label ?? a[1].id).localeCompare(b[1].label ?? b[1].id))
    .slice(0, limit).map(([, e]) => e);
}

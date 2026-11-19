/**
 * Knowledge graph of the child's record: read-only, drawn with vis-network (already in the console). Nodes are the
 * plugin's entries, edges are the links the plugin recorded (an episode uses an entry; a method was used in an episode).
 * Labels are plain text; a click shows the entry's type and day, never a file path.
 */
import { useEffect, useRef, useState } from "react";
import { DataSet, Network, type Options } from "vis-network/standalone";
import { Badge } from "@/components/ui/badge";
import type { GraphDoc, GraphNode } from "@/lib/api/daddy-ready";

const COLOURS: Record<string, string> = {
  episode: "#f59e0b", method: "#22c55e", moment: "#60a5fa", observation: "#a78bfa",
  milestone: "#f472b6", feedback: "#f87171",
};

export function GraphView({ doc }: { doc: GraphDoc }) {
  const host = useRef<HTMLDivElement | null>(null);
  const [picked, setPicked] = useState<GraphNode | null>(null);
  useEffect(() => {
    if (!host.current) return;
    const nodes = new DataSet(doc.nodes.map((n) => ({
      id: n.id, label: n.label || n.id, color: { background: COLOURS[n.type] ?? "#94a3b8", border: "#0e1320" },
      font: { color: "#e5e7eb", size: 12 }, shape: n.type === "episode" ? "diamond" : "dot", size: n.type === "episode" ? 16 : 10,
    })));
    const edges = new DataSet(doc.edges.map((e, i) => ({
      id: `e${i}`, from: e.from, to: e.to, arrows: "to", dashes: e.rel === "method_of",
      color: { color: "#64748b" }, title: e.rel === "used_in" ? "used in the episode" : "method of the episode",
    })));
    const options: Options = {
      physics: { stabilization: { iterations: 150 }, barnesHut: { gravitationalConstant: -2500 } },
      interaction: { hover: true, tooltipDelay: 200 }, nodes: { borderWidth: 1 },
    };
    const net = new Network(host.current, { nodes, edges }, options);
    net.on("click", (params: { nodes: string[] }) => {
      const id = params.nodes[0];
      setPicked(doc.nodes.find((n) => n.id === id) ?? null);
    });
    return () => net.destroy();
  }, [doc]);
  if (doc.nodes.length === 0) {
    return <p className="p-6 text-sm text-muted-foreground" data-testid="dr-graph-empty">Nothing to draw yet. Notes, moments and episodes appear here as they are recorded.</p>;
  }
  return (
    <div className="space-y-3" data-testid="dr-graph">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {Object.entries(COLOURS).map(([t, c]) => (
          <span key={t} className="flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: c }} aria-hidden />{t}</span>
        ))}
        {doc.truncated ? <Badge variant="outline">showing the first {doc.nodes.length} entries</Badge> : null}
      </div>
      <div ref={host} className="h-[32rem] w-full rounded-md border bg-card" role="img" aria-label="Knowledge graph" />
      <div className="text-sm" data-testid="dr-graph-picked">
        {picked ? <span><Badge variant="outline">{picked.type}</Badge> {picked.day ?? "no date"} · {picked.label}</span>
          : <span className="text-muted-foreground">Click a node for its type and day.</span>}
      </div>
    </div>
  );
}

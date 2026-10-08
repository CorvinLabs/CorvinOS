import { useEffect, useRef } from "react";
import { Network } from "vis-network";
import "vis-network/styles/vis-network.min.css";
import { Maximize2, Minus, Plus, Crosshair } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { GEdge, GNode } from "./graph-model";

interface Props {
  nodes: GNode[];
  edges: GEdge[];
  selectedKey: string | null;
  statusColours: Record<string, string>;
  onSelect: (key: string) => void;
  /** true: keep the layout running until it settles, then freeze it (large graphs stay calm). */
  settleThenFreeze?: boolean;
}

/** 2D graph with zoom (wheel, buttons, +/-) and pan (drag, arrow keys when focused). The network
 *  is rebuilt only when the node/edge SET changes; a change of selection only re-selects and
 *  re-centres, so the user's zoom is not thrown away on every click. */
export function GraphCanvas({ nodes, edges, selectedKey, statusColours, onSelect, settleThenFreeze = true }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const net = useRef<Network | null>(null);
  const selected = useRef<string | null>(selectedKey);
  const pick = useRef(onSelect);
  selected.current = selectedKey;
  pick.current = onSelect;

  useEffect(() => {
    const el = host.current;
    if (!el || nodes.length === 0) return;
    const cs = getComputedStyle(document.documentElement);
    const fg = cs.getPropertyValue("--foreground").trim();
    const font = fg ? `hsl(${fg})` : undefined;
    const fallback = statusColours.superseded;
    const known = new Set(nodes.map((n) => n.id));
    const vNodes = nodes.map((e) => {
      const c = statusColours[e.status] ?? fallback;
      return {
        id: e.id,
        label: (e.label ?? e.id) + "\n" + (e.title.length > 28 ? `${e.title.slice(0, 27)}…` : e.title),
        color: { background: c, border: c, highlight: { background: c, border: font ?? "#fff" } },
        shape: e.type === "decision" ? "box" : "dot",
        size: e.type === "decision" ? 18 : 10,
        borderWidthSelected: 4,
        font: { size: e.type === "decision" ? 13 : 11, color: font },
        title: `${e.type} · ${e.status}\n${e.title}`,
      };
    });
    const vEdges = edges.filter((r) => known.has(r.from_id) && known.has(r.to_id))
      .map((r) => ({ from: r.from_id, to: r.to_id, title: r.relation, arrows: "to", smooth: false }));
    const network = new Network(el, { nodes: vNodes, edges: vEdges }, {
      physics: { solver: "forceAtlas2Based", forceAtlas2Based: { gravitationalConstant: -50, springLength: 170, avoidOverlap: 0.8 }, stabilization: { iterations: 150 } },
      interaction: { hover: true, navigationButtons: false, keyboard: { enabled: true, bindToWindow: false }, zoomView: true, dragView: true },
      layout: { randomSeed: 42 },
    });
    net.current = network;
    const centre = () => {
      const k = selected.current;
      if (k && known.has(k)) { network.selectNodes([k]); network.focus(k, { scale: 1, animation: { duration: 250, easingFunction: "easeInOutQuad" } }); }
    };
    if (settleThenFreeze) network.once("stabilized", () => { network.setOptions({ physics: false }); centre(); });
    else centre();
    network.on("click", (p: { nodes: string[] }) => { if (p.nodes.length) pick.current(String(p.nodes[0])); });
    return () => { network.destroy(); net.current = null; };
  }, [nodes, edges, statusColours, settleThenFreeze]);

  useEffect(() => {
    const n = net.current;
    if (!n || !selectedKey) return;
    try {
      n.selectNodes([selectedKey]);
      n.focus(selectedKey, { scale: Math.max(n.getScale(), 0.8), animation: { duration: 250, easingFunction: "easeInOutQuad" } });
    } catch { /* the node is not in the drawn subset */ }
  }, [selectedKey, nodes]);

  const zoom = (f: number) => { const n = net.current; if (n) n.moveTo({ scale: n.getScale() * f, animation: { duration: 150, easingFunction: "easeInOutQuad" } }); };
  return (
    <div className="relative">
      <div
        ref={host}
        tabIndex={0}
        role="application"
        aria-label="Knowledge graph. Arrow keys pan, plus and minus zoom."
        className="h-[620px] w-full rounded-lg border border-border bg-card focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        data-testid="knowledge-graph-canvas"
      />
      <div className="absolute right-2 top-2 flex flex-col gap-1">
        <Button size="icon" variant="outline" aria-label="Zoom in" onClick={() => zoom(1.3)}><Plus className="h-4 w-4" /></Button>
        <Button size="icon" variant="outline" aria-label="Zoom out" onClick={() => zoom(1 / 1.3)}><Minus className="h-4 w-4" /></Button>
        <Button size="icon" variant="outline" aria-label="Fit graph" onClick={() => net.current?.fit({ animation: { duration: 250, easingFunction: "easeInOutQuad" } })}><Maximize2 className="h-4 w-4" /></Button>
        <Button size="icon" variant="outline" aria-label="Centre on selection" onClick={() => selectedKey && net.current?.focus(selectedKey, { scale: 1, animation: { duration: 250, easingFunction: "easeInOutQuad" } })}><Crosshair className="h-4 w-4" /></Button>
      </div>
    </div>
  );
}

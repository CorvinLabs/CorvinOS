import { useEffect, useMemo, useRef } from "react";
import { Network } from "vis-network";
import "vis-network/styles/vis-network.min.css";
import { Maximize2, Minus, Plus, Crosshair } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { GEdge, GNode } from "./graph-model";
import { computeIslandLayout } from "./island-layout";

interface Props {
  nodes: GNode[];
  edges: GEdge[];
  selectedKey: string | null;
  statusColours: Record<string, string>;
  onSelect: (key: string) => void;
  /** true: keep the layout running until it settles, then freeze it (large graphs stay calm). */
  settleThenFreeze?: boolean;
  /** force: organic physics layout. islands: fixed positions, one separate island per community. */
  layoutMode?: "force" | "islands";
  onLayoutModeChange?: (mode: "force" | "islands") => void;
}

/** 2D graph with zoom (wheel, buttons, +/-) and pan (drag, arrow keys when focused). The network
 *  is rebuilt only when the node/edge SET changes; a change of selection only re-selects and
 *  re-centres, so the user's zoom is not thrown away on every click. */
export function GraphCanvas({ nodes, edges, selectedKey, statusColours, onSelect, settleThenFreeze = true, layoutMode = "force", onLayoutModeChange }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const net = useRef<Network | null>(null);
  const selected = useRef<string | null>(selectedKey);
  const justBuilt = useRef(false);
  const showCross = useRef<(sel: string | null) => void>(() => {});
  const pick = useRef(onSelect);
  selected.current = selectedKey;
  pick.current = onSelect;
  const islandLayout = useMemo(() => (layoutMode === "islands" ? computeIslandLayout(nodes, edges) : null), [layoutMode, nodes, edges]);

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
        label: islandLayout ? (e.label ?? e.id) : (e.label ?? e.id) + "\n" + (e.title.length > 28 ? `${e.title.slice(0, 27)}…` : e.title),
        ...(islandLayout?.positions.get(e.id) ?? {}),
        color: { background: c, border: c, highlight: { background: c, border: font ?? "#fff" } },
        shape: e.type === "decision" ? "box" : "dot",
        size: e.type === "decision" ? 18 : 10,
        borderWidthSelected: 4,
        font: { size: e.type === "decision" ? 13 : 11, color: font },
        title: `${e.type} · ${e.status}\n${e.title}`,
      };
    });
    const vEdges = edges.filter((r) => known.has(r.from_id) && known.has(r.to_id))
      .map((r, n) => ({ id: `e${n}`, hidden: !!islandLayout && islandLayout.islandOf.get(r.from_id) !== islandLayout.islandOf.get(r.to_id), from: r.from_id, to: r.to_id, title: r.relation, arrows: "to", smooth: false,
        ...(islandLayout ? { width: 1, color: { color: font ?? "#888", opacity: 0.16, highlight: statusColours.proposed ?? "#d6b171" } } : {}) }));
    const network = new Network(el, { nodes: vNodes, edges: vEdges }, {
      physics: islandLayout
        ? { enabled: false }
        : { solver: "forceAtlas2Based", forceAtlas2Based: { gravitationalConstant: -50, springLength: 170, avoidOverlap: 0.8 }, stabilization: { iterations: 150 } },
      interaction: { hover: true, navigationButtons: false, keyboard: { enabled: true, bindToWindow: false }, zoomView: true, dragView: true },
      layout: { randomSeed: 42 },
    });
    if (islandLayout) {
      network.on("beforeDrawing", (ctx: CanvasRenderingContext2D) => {
        const scale = network.getScale();
        ctx.save();
        ctx.strokeStyle = font ?? "#888";
        ctx.fillStyle = font ?? "#888";
        ctx.lineWidth = 1.5 / scale;
        ctx.globalAlpha = 0.28;
        for (const i of islandLayout.islands) {
          ctx.beginPath();
          ctx.arc(i.cx, i.cy, i.r + 40, 0, Math.PI * 2);
          ctx.setLineDash(i.loose ? [10 / scale, 8 / scale] : []);
          ctx.stroke();
        }
        ctx.globalAlpha = 0.85;
        ctx.textAlign = "center";
        ctx.font = `${Math.min(15 / scale, 90)}px sans-serif`;
        for (const i of islandLayout.islands) ctx.fillText(`${i.name} (${i.count})`, i.cx, i.cy - i.r - 40 - 8 / scale);
        ctx.restore();
      });
    }
    net.current = network;
    justBuilt.current = true;
    // Edges between islands would cross the whole canvas: show them only for the selected node.
    const crossIds = vEdges.filter((e) => e.hidden).map((e) => ({ id: e.id, from: e.from, to: e.to }));
    showCross.current = (sel: string | null) => {
      if (crossIds.length === 0) return;
      (network as unknown as { body: { data: { edges: { update: (u: unknown[]) => void } } } }).body.data.edges
        .update(crossIds.map((e) => ({ id: e.id, hidden: !(sel && (e.from === sel || e.to === sel)) })));
    };
    showCross.current(selected.current);
    const centre = () => {
      const k = selected.current;
      if (k && known.has(k)) { network.selectNodes([k]); network.focus(k, { scale: 1, animation: { duration: 250, easingFunction: "easeInOutQuad" } }); }
    };
    if (!islandLayout && settleThenFreeze) network.once("stabilized", () => { network.setOptions({ physics: false }); centre(); });
    else if (islandLayout) { network.fit(); if (selected.current && known.has(selected.current)) network.selectNodes([selected.current]); }
    else centre();
    network.on("click", (p: { nodes: string[] }) => { if (p.nodes.length) pick.current(String(p.nodes[0])); });
    return () => { network.destroy(); net.current = null; };
  }, [nodes, edges, statusColours, settleThenFreeze, islandLayout]);

  useEffect(() => {
    const n = net.current;
    if (!n || !selectedKey) return;
    showCross.current(selectedKey);
    if (justBuilt.current) { justBuilt.current = false; return; }
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
        data-layout={layoutMode}
        data-islands={islandLayout?.islands.length ?? 0}
      />
      <div className="absolute right-2 top-2 flex flex-col gap-1">
        <div className="flex rounded-md border border-border bg-card" role="group" aria-label="Layout">
          {(["force", "islands"] as const).map((m) => (
            <Button key={m} size="sm" variant={layoutMode === m ? "accent" : "ghost"} aria-pressed={layoutMode === m} onClick={() => onLayoutModeChange?.(m)}
              title={m === "force" ? "Organic force layout" : "Separate islands, nodes never overlap"}>
              {m === "force" ? "Force" : "Islands"}
            </Button>
          ))}
        </div>
        <Button size="icon" variant="outline" aria-label="Zoom in" onClick={() => zoom(1.3)}><Plus className="h-4 w-4" /></Button>
        <Button size="icon" variant="outline" aria-label="Zoom out" onClick={() => zoom(1 / 1.3)}><Minus className="h-4 w-4" /></Button>
        <Button size="icon" variant="outline" aria-label="Fit graph" onClick={() => net.current?.fit({ animation: { duration: 250, easingFunction: "easeInOutQuad" } })}><Maximize2 className="h-4 w-4" /></Button>
        <Button size="icon" variant="outline" aria-label="Centre on selection" onClick={() => selectedKey && net.current?.focus(selectedKey, { scale: 1, animation: { duration: 250, easingFunction: "easeInOutQuad" } })}><Crosshair className="h-4 w-4" /></Button>
      </div>
    </div>
  );
}

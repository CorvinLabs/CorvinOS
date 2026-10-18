/**
 * Video Producer — board steps: an imported design (a Penpot board exported as SVG) is revealed step by step in a
 * video. Here the user assigns the design's shapes to ordered steps by clicking them, names a step if they like, and
 * previews the order. The design is drawn from the stored model's TYPED fields as React elements - no markup from the
 * upload is ever inserted - and the server validates the steps again when they are saved.
 */
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Loader2, Play, Plus, Square, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api/client";
import {
  designPath, stepOfShape, stepsFromGroups, stepsPayload, stepsProblem, toggleShape, unassignedShapes, updateAsset,
  type Asset, type BoardDesign, type BoardDesignResponse, type BoardShape, type BoardStep,
} from "@/lib/api/video-assets";

// an ordinal-free categorical set for step identity (which step a shape is in), readable on both themes
const STEP_COLOURS = ["#e8a83a", "#3ba3ff", "#2fbf71", "#d65db1", "#ff7a59", "#8a7dff", "#20b2aa", "#c0ca33",
  "#ef5350", "#26a69a", "#ab47bc", "#ffa726"];

function ShapeEl({ s, onClick, outline, dim, testId }: {
  s: BoardShape; onClick?: () => void; outline?: string; dim?: boolean; testId?: string;
}) {
  const common = {
    fill: s.fill ?? "none", fillOpacity: s.fill_opacity, stroke: outline ?? s.stroke ?? undefined,
    strokeWidth: outline ? Math.max(s.stroke_width, 3) : s.stroke_width || undefined,
    opacity: dim ? s.opacity * 0.25 : s.opacity, transform: `matrix(${s.matrix.join(" ")})`,
    onClick, style: onClick ? { cursor: "pointer" } : undefined, "data-testid": testId,
    vectorEffect: outline ? ("non-scaling-stroke" as const) : undefined,
  };
  if (s.kind === "rect") return <rect x={s.x} y={s.y} width={s.w} height={s.h} rx={s.rx} ry={s.ry} {...common} />;
  if (s.kind === "ellipse") return <ellipse cx={s.x} cy={s.y} rx={s.w} ry={s.h} {...common} />;
  if (s.kind === "path") return <path d={s.d} {...common} />;
  return (
    <text x={s.x} y={s.y} fontSize={s.font_size} fontWeight={s.font_weight} textAnchor={s.anchor as "start" | "middle" | "end"}
      dominantBaseline={s.baseline as never} fontFamily="sans-serif" {...common}>{s.text}</text>
  );
}

function DesignView({ design, steps, active, shownUpTo, onShape }: {
  design: BoardDesign; steps: BoardStep[]; active: number; shownUpTo: number | null; onShape?: (i: number) => void;
}) {
  const of = stepOfShape(steps, design.shapes.length);
  return (
    <svg viewBox={`${design.ox} ${design.oy} ${design.width} ${design.height}`} className="w-full max-h-[50vh] rounded-md border border-border"
      data-testid="board-design" role="img" aria-label="The imported design">
      <rect x={design.ox} y={design.oy} width={design.width} height={design.height} fill={design.background ?? "#ffffff"} />
      {design.shapes.map((s, i) => {
        if (shownUpTo !== null && (of[i] < 0 || of[i] > shownUpTo)) return null;  // preview: only steps revealed so far
        const k = of[i];
        return (
          <ShapeEl key={i} s={s} testId={`board-shape-${i}`}
            onClick={shownUpTo === null && onShape ? () => onShape(i) : undefined}
            outline={shownUpTo === null && k >= 0 ? STEP_COLOURS[k % STEP_COLOURS.length] : undefined}
            dim={shownUpTo === null && k >= 0 && k !== active} />
        );
      })}
    </svg>
  );
}

export function BoardStepsDialog({ asset, csrf, onClose, onChanged }: {
  asset: Asset; csrf: string; onClose: () => void; onChanged: () => void;
}) {
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const q = useQuery({ queryKey: ["video", "asset-design", asset.id], queryFn: () => api<BoardDesignResponse>(designPath(asset.id)) });
  const [steps, setSteps] = useState<BoardStep[] | null>(null);
  const [active, setActive] = useState(0);
  const [preview, setPreview] = useState<number | null>(null);
  const err = (e: unknown, fallback: string) => (e instanceof ApiError && e.message ? e.message : fallback);

  useEffect(() => {
    if (q.data && steps === null) setSteps(q.data.steps.length ? q.data.steps.map((s) => ({ ...s })) : [{ shapes: [] }]);
  }, [q.data, steps]);
  useEffect(() => { dialogRef.current?.focus(); }, []);
  useEffect(() => {
    if (preview === null || !steps) return;
    if (preview >= steps.length - 1) { const t = window.setTimeout(() => setPreview(null), 1600); return () => window.clearTimeout(t); }
    const t = window.setTimeout(() => setPreview((p) => (p === null ? null : p + 1)), 900);
    return () => window.clearTimeout(t);
  }, [preview, steps]);

  const save = useMutation({
    mutationFn: (payload: BoardStep[] | null) => updateAsset(asset.id, { steps: payload }, csrf),
    onSuccess: () => { onChanged(); onClose(); },
  });

  const design = q.data?.design;
  const limits = q.data?.limits;
  const problem = useMemo(() => (design && limits && steps ? stepsProblem(steps, design.shapes.length, limits) : null),
    [design, limits, steps]);
  const left = design && steps ? unassignedShapes(steps, design.shapes.length).length : 0;

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4" data-testid="board-overlay">
      <div role="dialog" aria-modal="true" aria-labelledby={titleId} ref={dialogRef} tabIndex={-1} data-testid="board-dialog"
        className="bg-background rounded-lg border border-border p-4 w-full max-w-3xl max-h-[92vh] overflow-y-auto space-y-3"
        onKeyDown={(e) => { if (e.key === "Escape" && !save.isPending) onClose(); }}>
        <h3 id={titleId} className="font-semibold">Board steps · {asset.caption}</h3>
        <p className="text-xs text-muted-foreground">
          A board scene reveals this design one step at a time while the narration speaks. Click shapes to put them into the
          selected step; the order of the steps is the order they appear. A step's label is what the narration can name to
          reveal it - without one, the step's first text is used, and a step with neither appears on an even rhythm.
        </p>
        {q.isLoading && <p className="text-sm text-muted-foreground"><Loader2 className="w-3.5 h-3.5 mr-1 inline animate-spin" />Loading the design…</p>}
        {q.isError && <p className="text-sm text-destructive" role="alert" data-testid="board-load-error">{err(q.error, "The design could not be loaded.")}</p>}
        {design && limits && steps && (
          <>
            <DesignView design={design} steps={steps} active={active} shownUpTo={preview}
              onShape={(i) => setSteps((st) => toggleShape(st!, active, i))} />
            <ol className="space-y-1.5" data-testid="board-steps">
              {steps.map((st, k) => (
                <li key={k} className={`flex items-center gap-2 rounded-md border p-1.5 ${k === active ? "border-foreground" : "border-border"}`}
                  data-testid={`board-step-${k}`}>
                  <button type="button" className="flex items-center gap-2 text-sm min-w-[7rem] text-left" onClick={() => setActive(k)}
                    aria-pressed={k === active} data-testid={`board-step-select-${k}`}>
                    <span className="inline-block w-3 h-3 rounded-full" style={{ background: STEP_COLOURS[k % STEP_COLOURS.length] }} />
                    Step {k + 1} · {st.shapes.length} shape{st.shapes.length === 1 ? "" : "s"}
                  </button>
                  <Input className="h-8" value={st.label ?? ""} maxLength={limits.max_label} aria-label={`Label of step ${k + 1}`}
                    placeholder={q.data?.labels[k] ?? "Label (optional)"} data-testid={`board-step-label-${k}`}
                    onChange={(e) => setSteps((all) => all!.map((x, j) => (j === k ? { ...x, label: e.target.value } : x)))} />
                  <Button type="button" variant="ghost" size="sm" aria-label={`Remove step ${k + 1}`} disabled={steps.length === 1}
                    onClick={() => { setSteps((all) => all!.filter((_, j) => j !== k)); setActive((a) => Math.max(0, a >= k ? a - 1 : a)); }}>
                    <X className="w-3.5 h-3.5" />
                  </Button>
                </li>
              ))}
            </ol>
            <div className="flex items-center gap-2 flex-wrap">
              <Button type="button" variant="outline" size="sm" disabled={steps.length >= limits.max_steps}
                onClick={() => { setSteps((all) => [...all!, { shapes: [] }]); setActive(steps.length); }} data-testid="board-add-step">
                <Plus className="w-3.5 h-3.5 mr-1" />Add step
              </Button>
              <Button type="button" variant="outline" size="sm" onClick={() => { setSteps(stepsFromGroups(design, limits.max_steps)); setActive(0); }}
                data-testid="board-from-groups">One step per group</Button>
              <Button type="button" variant="outline" size="sm" disabled={!!problem} data-testid="board-preview"
                onClick={() => setPreview((p) => (p === null ? 0 : null))}>
                {preview === null ? <Play className="w-3.5 h-3.5 mr-1" /> : <Square className="w-3.5 h-3.5 mr-1" />}
                {preview === null ? "Preview steps" : `Step ${preview + 1} of ${steps.length}`}
              </Button>
              <span className="text-xs text-muted-foreground" data-testid="board-unassigned">
                {left ? `${left} of ${design.shapes.length} shapes not in a step` : `All ${design.shapes.length} shapes are in a step`}
              </span>
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <Button type="button" size="sm" disabled={!!problem || save.isPending || !csrf} onClick={() => save.mutate(stepsPayload(steps))}
                data-testid="board-save">
                {save.isPending ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : null}Save steps
              </Button>
              {q.data?.steps.length ? (
                <Button type="button" variant="ghost" size="sm" disabled={save.isPending || !csrf} onClick={() => save.mutate(null)}
                  data-testid="board-clear">Remove steps</Button>
              ) : null}
              <Button type="button" variant="ghost" size="sm" disabled={save.isPending} onClick={onClose}>Close</Button>
              {problem && <span className="text-xs text-muted-foreground" data-testid="board-problem">{problem}</span>}
            </div>
            {save.isError && <p className="text-xs text-destructive" role="alert" data-testid="board-save-error">{err(save.error, "The steps could not be saved.")}</p>}
          </>
        )}
      </div>
    </div>
  );
}

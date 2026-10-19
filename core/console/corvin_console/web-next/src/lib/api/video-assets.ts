/**
 * Video Producer expressive scenes (Code, Canvas, Assets tabs): wire types, the asset upload and the
 * pure helpers the tabs read the job with. Kept out of the components so the mapping is testable.
 *
 * Nothing here is invented: which scenes of a video are code or canvas scenes is read from the job's
 * own cue log (metadata.cues: one record per web scene, `template` = what was RENDERED), the canvas
 * signals come from the job's production report, and an asset is exactly what the store returned.
 */
import { ApiError, BASE } from "@/lib/api/client";

export const ASSET_ACCEPT = ".png,image/png";

export interface AssetHotspot { name: string; x: number; y: number; description: string }
/** Whether an image can be a board: a stored design, within every board bound, reveal steps assigned. */
export interface AssetBoardState { has_design: boolean; ready: boolean; steps: number; reason: string | null }
export interface Asset {
  id: string; caption: string; hotspots: AssetHotspot[]; width: number; height: number; bytes: number;
  created: string | number | null; source: string | null; board?: AssetBoardState;
}
export interface AssetList {
  assets: Asset[];
  limits: { max_assets: number; max_image_bytes: number; max_upload_bytes: number; max_caption: number; max_hotspots: number };
}

/** Multipart upload of one PNG plus what the user typed and clicked; the generic api() helper is JSON-only. */
export async function uploadAsset(file: File, fields: { caption: string; source: string; hotspots: AssetHotspot[] }, csrf: string): Promise<Asset> {
  const form = new FormData();
  form.append("file", file, file.name);
  form.append("caption", fields.caption);
  form.append("source", fields.source);
  form.append("hotspots", JSON.stringify(fields.hotspots));
  const res = await fetch(`${BASE}/video/assets`, { method: "POST", body: form, credentials: "include", headers: { "X-CSRF-Token": csrf } });
  const text = await res.text();
  let payload: unknown = text;
  try { payload = JSON.parse(text); } catch { /* keep text */ }
  if (!res.ok) throw new ApiError(res.status, payload);
  return (payload as { asset: Asset }).asset;
}

/** A Penpot board exported as SVG, rendered server-side into an image asset (ADR-2257). */
export async function importPenpotBoard(file: File, csrf: string): Promise<{ asset: Asset; notes: string[] }> {
  const form = new FormData();
  form.append("file", file, file.name);
  const res = await fetch(`${BASE}/video/assets/import-penpot`, { method: "POST", body: form, credentials: "include", headers: { "X-CSRF-Token": csrf } });
  const text = await res.text();
  let payload: unknown = text;
  try { payload = JSON.parse(text); } catch { /* keep text */ }
  if (!res.ok) throw new ApiError(res.status, payload);
  return payload as { asset: Asset; notes: string[] };
}

/** "Add website screenshot" (POST /video/capture): the console captures the page once and stores it as
 * an asset. Every failure that depends on the address answers the same message after the same time. */
export async function captureAsset(
  fields: { url: string; caption: string; viewport: CaptureViewport; hotspots?: AssetHotspot[] },
  csrf: string,
): Promise<Asset> {
  const res = await fetch(`${BASE}/video/capture`, {
    method: "POST",
    body: JSON.stringify({ hotspots: [], ...fields }),
    credentials: "include",
    headers: { "X-CSRF-Token": csrf, "Content-Type": "application/json" },
  });
  const text = await res.text();
  let payload: unknown = text;
  try { payload = JSON.parse(text); } catch { /* keep text */ }
  if (!res.ok) throw new ApiError(res.status, payload);
  return (payload as { asset: Asset }).asset;
}

/** Change a stored image's caption, hotspots and/or board steps (PATCH /video/assets/{id}); the image never
 * changes. `steps: null` clears the steps (the design is unassigned again); an absent key keeps them. */
export async function updateAsset(id: string, fields: { caption?: string; hotspots?: AssetHotspot[]; steps?: BoardStep[] | null }, csrf: string): Promise<Asset> {
  const res = await fetch(`${BASE}/video/assets/${encodeURIComponent(id)}`, {
    method: "PATCH", body: JSON.stringify(fields), credentials: "include",
    headers: { "X-CSRF-Token": csrf, "Content-Type": "application/json" },
  });
  const text = await res.text();
  let payload: unknown = text;
  try { payload = JSON.parse(text); } catch { /* keep text */ }
  if (!res.ok) throw new ApiError(res.status, payload);
  return (payload as { asset: Asset }).asset;
}

// ── website capture ──────────────────────────────────────────────────────────

export type CaptureViewport = "desktop" | "mobile";
/** What the capture worker really renders (capture.VIEWPORTS: width, height, device scale factor). */
export const CAPTURE_VIEWPORTS: Record<CaptureViewport, string> = {
  desktop: "Desktop · 1920×1080",
  mobile: "Phone · 412×915 at 2× (824×1830 px)",
};
/** Upper bound of one capture as the user experiences it: a failure answers at the console's floor (13 s). */
export const CAPTURE_WAIT_S = 15;

export interface CaptureSettings { capture_enabled?: boolean; capture_policy_configured?: boolean; capture_host_problem?: string | null }

/** Why the capture action cannot be used right now, or null. */
export function captureBlockedReason(s: CaptureSettings | undefined): string | null {
  if (!s) return "Loading the CorvinTV settings…";
  if (s.capture_host_problem === "no_browser") return "Website capture is not available on this host: Chrome or Chromium is not installed.";
  if (s.capture_host_problem) return "Website capture is not available on this host (it needs Linux or macOS).";
  if (!s.capture_enabled) return "Website capture is off for this workspace. Turn it on under Settings.";
  if (!s.capture_policy_configured) return "Website capture needs an egress policy for this workspace (spec.egress in tenant.corvin.yaml).";
  return null;
}

/** The first plain http(s) address in a task text (what the composer offers to capture), or null. */
export function firstUrlIn(text: string): { url: string; host: string } | null {
  const m = /\bhttps?:\/\/[^\s<>"'`)\]]+/i.exec(text);
  if (!m) return null;
  const url = m[0].replace(/[.,;:!?]+$/, "");
  try {
    const u = new URL(url);
    if (u.protocol !== "http:" && u.protocol !== "https:") return null;
    return { url, host: u.hostname };
  } catch {
    return null;
  }
}

export const assetImageUrl = (id: string) => `${BASE}/video/assets/${encodeURIComponent(id)}/image`;
export const sceneSlideUrl = (jobId: string, index: number) => `${BASE}/video/videos/${encodeURIComponent(jobId)}/scenes/${index}/slide`;

// ── hotspots ─────────────────────────────────────────────────────────────────

/** Mirrors asset_store.SPOT_RE: 2-32 characters of a-z, 0-9 and _. */
export const SPOT_NAME = /^[a-z0-9_]{2,32}$/;

/** The point clicked in the image as fractions 0..1 of its displayed box (3 decimals), clamped. */
export function clickToPoint(box: { left: number; top: number; width: number; height: number }, clientX: number, clientY: number): { x: number; y: number } | null {
  if (!(box.width > 0) || !(box.height > 0)) return null;
  const clamp = (v: number) => Math.min(1, Math.max(0, Math.round(v * 1000) / 1000));
  return { x: clamp((clientX - box.left) / box.width), y: clamp((clientY - box.top) / box.height) };
}

/** The next free default name: spot_1, spot_2, ... (never one already used). */
export function nextSpotName(spots: AssetHotspot[]): string {
  const used = new Set(spots.map((s) => s.name));
  for (let i = 1; ; i++) if (!used.has(`spot_${i}`)) return `spot_${i}`;
}

/** Why the form cannot be sent yet, or null. Mirrors the store's refusals so the user sees them before uploading. */
export function uploadProblem(file: File | null, caption: string, spots: AssetHotspot[], limits?: AssetList["limits"]): string | null {
  if (!file) return "Choose a PNG image.";
  if (!/\.png$/i.test(file.name) && file.type !== "image/png") return "Only PNG images can be uploaded.";
  if (limits && file.size > limits.max_upload_bytes) return `The file is larger than ${Math.round(limits.max_upload_bytes / 1048576)} MB.`;
  if (!caption.trim()) return "A caption is required: it is what the storyboard reads about the image.";
  if (limits && caption.length > limits.max_caption) return `The caption is longer than ${limits.max_caption} characters.`;
  if (limits && spots.length > limits.max_hotspots) return `At most ${limits.max_hotspots} hotspots.`;
  const names = new Set<string>();
  for (const s of spots) {
    if (!SPOT_NAME.test(s.name)) return `Hotspot name "${s.name}": 2-32 characters of a-z, 0-9 and _.`;
    if (names.has(s.name)) return `Hotspot "${s.name}" appears twice.`;
    names.add(s.name);
  }
  return null;
}

// ── reading a job ────────────────────────────────────────────────────────────

interface CueLike { scene?: unknown; template?: unknown }

/** 1-based scene indexes whose RENDERED template is `template`, from the job's cue log. */
export function scenesWithTemplate(metadata: Record<string, unknown> | null | undefined, template: string): number[] {
  const cues = metadata && Array.isArray((metadata as { cues?: unknown }).cues) ? ((metadata as { cues: CueLike[] }).cues) : [];
  const out = cues.filter((c) => c && c.template === template && typeof c.scene === "number" && Number.isInteger(c.scene) && (c.scene as number) >= 1)
    .map((c) => c.scene as number);
  return [...new Set(out)].sort((a, b) => a - b);
}

/** The canvas authoring record the storyboard step wrote (metadata.canvas_authoring), or null. */
export function canvasAuthoring(metadata: Record<string, unknown> | null | undefined): { total: number; first_try: number; after_repair: number } | null {
  const a = metadata ? (metadata as { canvas_authoring?: unknown }).canvas_authoring : null;
  if (!a || typeof a !== "object") return null;
  const r = a as Record<string, unknown>;
  const n = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);
  const total = n(r.total), first = n(r.first_try), after = n(r.after_repair);
  return total === null || first === null ? null : { total, first_try: first, after_repair: after ?? first };
}

export const fmtAssetBytes = (n: number) => (n < 1048576 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1048576).toFixed(2)} MB`);


// ── board: an imported design revealed step by step ─────────────────────────

/** One shape of the stored design: typed fields only, exactly what the server validated. */
export interface BoardShape {
  kind: "rect" | "ellipse" | "path" | "text";
  x: number; y: number; w: number; h: number; rx: number; ry: number; d: string; text: string;
  fill: string | null; fill_opacity: number; stroke: string | null; stroke_width: number; opacity: number;
  font_family: string; font_size: number; font_weight: number; text_length: number; baseline: string; anchor: string;
  matrix: number[]; group: number;
}
export interface BoardDesign { width: number; height: number; ox: number; oy: number; background: string | null; shapes: BoardShape[] }
export interface BoardStep { shapes: number[]; label?: string }
export interface BoardDesignResponse {
  design: BoardDesign; steps: BoardStep[]; labels: (string | null)[]; board: AssetBoardState;
  limits: { max_steps: number; max_label: number; max_shapes: number };
}

/** Path for api() (which prefixes the console base itself). */
export const designPath = (id: string) => `/video/assets/${encodeURIComponent(id)}/design`;

/** Put shape `shape` into step `step` (taking it out of any other step); clicking it again in the same step removes it. */
export function toggleShape(steps: BoardStep[], step: number, shape: number): BoardStep[] {
  const inThis = steps[step]?.shapes.includes(shape);
  return steps.map((st, k) => ({
    ...st,
    shapes: k === step
      ? (inThis ? st.shapes.filter((i) => i !== shape) : [...st.shapes.filter((i) => i !== shape), shape].sort((a, b) => a - b))
      : st.shapes.filter((i) => i !== shape),
  }));
}

/** Shapes no step contains yet, in shape order. */
export function unassignedShapes(steps: BoardStep[], n: number): number[] {
  const seen = new Set(steps.flatMap((s) => s.shapes));
  return Array.from({ length: n }, (_, i) => i).filter((i) => !seen.has(i));
}

/** The step index of every shape (-1 = in no step). */
export function stepOfShape(steps: BoardStep[], n: number): number[] {
  const out = Array.from({ length: n }, () => -1);
  steps.forEach((st, k) => st.shapes.forEach((i) => { if (i >= 0 && i < n) out[i] = k; }));
  return out;
}

/** Why the steps cannot be saved yet (the server checks the same rules), or null. */
export function stepsProblem(steps: BoardStep[], n: number, limits: { max_steps: number; max_label: number; max_shapes: number }): string | null {
  if (n > limits.max_shapes) return `This design has ${n} shapes; a board can reveal at most ${limits.max_shapes}.`;
  if (steps.length === 0) return "Add at least one step.";
  if (steps.length > limits.max_steps) return `A board has at most ${limits.max_steps} steps.`;
  if (steps.some((s) => s.shapes.length === 0)) return "Every step needs at least one shape (remove empty steps).";
  const left = unassignedShapes(steps, n).length;
  if (left) return `${left} shape${left === 1 ? " is" : "s are"} not in a step yet.`;
  if (steps.some((s) => (s.label ?? "").length > limits.max_label)) return `A step label is at most ${limits.max_label} characters.`;
  if (steps.some((s) => /[<>&"\\`{}]/.test(s.label ?? ""))) return "A step label is plain text: no markup characters.";
  return null;
}

/** What the server stores: empty labels dropped, labels trimmed. */
export function stepsPayload(steps: BoardStep[]): BoardStep[] {
  return steps.map((s) => {
    const label = (s.label ?? "").trim().replace(/\s+/g, " ");
    return label ? { shapes: [...s.shapes], label } : { shapes: [...s.shapes] };
  });
}

/** One step per top-level group of the design, in group order - a starting point the user edits, never saved unasked. */
export function stepsFromGroups(design: BoardDesign, maxSteps: number): BoardStep[] {
  const groups = Array.from(new Set(design.shapes.map((s) => s.group))).sort((a, b) => a - b);
  const steps = groups.map((g) => ({ shapes: design.shapes.map((s, i) => (s.group === g ? i : -1)).filter((i) => i >= 0) }));
  while (steps.length > maxSteps) {
    const last = steps.pop()!;
    steps[steps.length - 1].shapes.push(...last.shapes);
  }
  return steps;
}

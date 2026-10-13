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
export interface Asset {
  id: string; caption: string; hotspots: AssetHotspot[]; width: number; height: number; bytes: number;
  created: string | number | null; source: string | null;
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

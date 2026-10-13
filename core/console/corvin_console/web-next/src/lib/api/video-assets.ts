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

/** Change a stored image's caption and/or hotspots (PATCH /video/assets/{id}); the image never changes. */
export async function updateAsset(id: string, fields: { caption?: string; hotspots?: AssetHotspot[] }, csrf: string): Promise<Asset> {
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
  if (!s) return "Loading the Video Producer settings…";
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

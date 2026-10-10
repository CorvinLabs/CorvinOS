/**
 * Video styles: wire types, upload, and the pure helpers the import dialog edits a draft with.
 * A draft is opaque server data; only the fields below are edited and every other field travels back untouched.
 */
import { ApiError, BASE } from "@/lib/api/client";

export const STYLE_DECK_ACCEPT = ".pptx,.potx";
export const BUILTIN_STYLE_ID = "corvin";
export const FONT_FAMILIES = ["Newsreader", "Instrument Sans", "JetBrains Mono"] as const;
export const COLOUR_KEYS = ["bg", "bg_card", "border", "text", "text_muted", "text_faint", "accent", "accent_hi", "success"] as const;
export type ColourKey = (typeof COLOUR_KEYS)[number];
export type Theme = "dark" | "light";
export type FontRole = "heading_family" | "body_family" | "mono_family";

export interface StyleSummary {
  id: string; name: string;
  source: { kind: string; sha256: string | null; deck_aspect: string | null; imported_at: string | null };
  default_theme: Theme; decor: string;
  brand: { wordmark: string; has_mark: boolean; intro_mark: boolean; credit: boolean };
  warnings: string[]; mark_data_uri: string | null;
}
export interface StyleList {
  styles: StyleSummary[]; default_style_id: string | null; default_style_error?: string | null;
  builtin: { id: string; name: string }; limits: { max_styles: number; max_upload_bytes: number };
}
export interface StylePreview { template: string; theme: string; data_uri: string }
/* eslint-disable-next-line @typescript-eslint/no-explicit-any -- opaque server object, edited by path */
export type Draft = Record<string, any>;
export interface ImportResult { draft: Draft; previews: StylePreview[]; notes: string[] }

export const isDeckFile = (name: string) => /\.(pptx|potx)$/i.test(name);

/** Multipart upload; the generic api() helper is JSON-only. */
export async function importStyleDeck(file: File, csrf: string, signal?: AbortSignal): Promise<ImportResult> {
  const form = new FormData();
  form.append("file", file, file.name);
  const res = await fetch(`${BASE}/video/styles/import`, { method: "POST", body: form, credentials: "include", headers: { "X-CSRF-Token": csrf }, signal });
  const text = await res.text();
  let payload: unknown = text;
  try { payload = JSON.parse(text); } catch { /* keep text */ }
  if (!res.ok) throw new ApiError(res.status, payload);
  return payload as ImportResult;
}

// ── colour helpers ───────────────────────────────────────────────────────────

const HEX = /^#[0-9a-fA-F]{6}$/;
export const isHex = (v: unknown): v is string => typeof v === "string" && HEX.test(v);

export function hexToRgb(hex: string): [number, number, number] {
  return [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16)];
}

/** rgba() of the new accent that keeps the alpha of the previous glow (0.35 when it cannot be read). */
export function accentGlow(accentHex: string, previousGlow?: unknown): string {
  const m = typeof previousGlow === "string" ? /rgba?\([^)]*?,\s*([0-9.]+)\s*\)\s*$/.exec(previousGlow) : null;
  const alpha = m && Number.isFinite(Number(m[1])) ? Number(m[1]) : 0.35;
  const [r, g, b] = hexToRgb(accentHex);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/** WCAG relative luminance. */
function luminance(hex: string): number {
  const [r, g, b] = hexToRgb(hex).map((c) => { const s = c / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
export function contrastRatio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}
export const MIN_TEXT_CONTRAST = 4.5;
// Server floors (style_pack.py MIN_TEXT/MIN_MUTED/MIN_CARD_TEXT/MIN_ACCENT/MIN_HIGHLIGHT/MIN_DIM, DIM = 0.38).
export const MIN_MUTED_CONTRAST = 3;
export const MIN_HIGHLIGHT_CONTRAST = 1.8;
export const MIN_DIM_CONTRAST = 2;
export const DIM_MIX = 0.38;

/** Python's round(): halves go to the even neighbour (style_pack._hex), so the client and server agree on every channel. */
function roundHalfEven(x: number): number {
  const f = Math.floor(x);
  const d = x - f;
  return d < 0.5 ? f : d > 0.5 ? f + 1 : f % 2 === 0 ? f : f + 1;
}
/** `a` moved `t` (0..1) toward `b`; identical to style_pack.mix. */
export function mix(a: string, b: string, t: number): string {
  const [r1, g1, b1] = hexToRgb(a);
  const [r2, g2, b2] = hexToRgb(b);
  const ch = (x: number, y: number) => Math.max(0, Math.min(255, roundHalfEven(x + (y - x) * t))).toString(16).padStart(2, "0");
  return `#${ch(r1, r2)}${ch(g1, g2)}${ch(b1, b2)}`;
}
/** The colour an out-of-focus item is drawn in (the server's readability rule is contrast(bg, this) >= 2). */
export const dimmedText = (bg: string, text: string) => mix(bg, text, DIM_MIX);

// ── immutable draft edits (unknown fields are spread through untouched) ──────

export function setName(d: Draft, name: string): Draft { return { ...d, name }; }
export function setTopLevel(d: Draft, key: "default_theme" | "decor", value: string): Draft { return { ...d, [key]: value }; }
export function setBrand(d: Draft, key: "wordmark" | "intro_mark" | "credit", value: string | boolean): Draft {
  return { ...d, brand: { ...(d.brand ?? {}), [key]: value } };
}
export function removeLogo(d: Draft): Draft { return { ...d, mark_png_b64: null, brand: { ...(d.brand ?? {}), intro_mark: false } }; }

/**
 * `deriveCard` (default true) re-derives the surfaces from Background/Text exactly like the importer does
 * (style_import_pptx: bg_card = mix(bg, text, 0.05), border = mix(bg, text, 0.14)); pass false once the
 * card colour was set by hand. The border is always derived (it has no control of its own).
 */
export function setColour(d: Draft, theme: Theme, key: ColourKey, hex: string, opts: { deriveCard?: boolean } = {}): Draft {
  const t = d.tokens?.[theme] ?? {};
  const next = { ...t, [key]: hex };
  if (key === "accent") next.glow = accentGlow(hex, t.glow);
  if ((key === "bg" || key === "text") && isHex(next.bg) && isHex(next.text)) {
    if (opts.deriveCard !== false) next.bg_card = mix(next.bg, next.text, 0.05);
    next.border = mix(next.bg, next.text, 0.14);
  }
  return { ...d, tokens: { ...d.tokens, [theme]: next } };
}

/**
 * Change a typography role and keep the display-only mapping in step: the entry that mapped a deck font
 * onto the role's previous family now names the new one. With two entries on the same family the heading
 * takes the first, body and mono the last (the server lists the deck's heading font first).
 */
export function setFamily(d: Draft, role: FontRole, family: string): Draft {
  const typo = d.tokens?.typography ?? {};
  const old = typo[role];
  const mapping: Array<{ from: string; to: string; reason: string }> = d.fonts?.mapping ?? [];
  const idxs = mapping.map((m, i) => (m.to === old ? i : -1)).filter((i) => i >= 0);
  const target = idxs.length === 0 ? -1 : role === "heading_family" ? idxs[0] : idxs[idxs.length - 1];
  const nextMap = old === family ? mapping : mapping.map((m, i) => (i === target ? { ...m, to: family, reason: "Chosen by you" } : m));
  return { ...d, tokens: { ...d.tokens, typography: { ...typo, [role]: family } }, fonts: { ...(d.fonts ?? {}), mapping: nextMap } };
}

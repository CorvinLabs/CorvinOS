/** Pure helpers of the DaddyReady page (testable without a browser). */
import type { SchemaProp } from "@/lib/api/daddy-ready";

export type Json = Record<string, unknown>;

/** Immutable set of a nested path; an `undefined` value removes the key (and empty parents), so a cleared field
 *  falls back to the plugin default instead of being saved as an empty string. */
export function setPath(obj: Json, path: string[], value: unknown): Json {
  const [head, ...rest] = path;
  const next: Json = { ...obj };
  if (rest.length === 0) {
    if (value === undefined) delete next[head];
    else next[head] = value;
    return next;
  }
  const child = setPath((obj[head] as Json) ?? {}, rest, value);
  if (Object.keys(child).length === 0) delete next[head];
  else next[head] = child;
  return next;
}

export function getPath(obj: Json, path: string[]): unknown {
  let cur: unknown = obj;
  for (const k of path) {
    if (cur === null || typeof cur !== "object") return undefined;
    cur = (cur as Json)[k];
  }
  return cur;
}

/** What a text input yields for a property: numbers are parsed, an empty field means "use the default". */
export function coerce(prop: SchemaProp, raw: string): unknown {
  const t = raw.trim();
  if (t === "") return undefined;
  if (prop.type === "integer") return /^-?\d+$/.test(t) ? Number.parseInt(t, 10) : raw;
  if (prop.type === "number") return Number.isFinite(Number(t)) ? Number(t) : raw;
  if (prop.type === "array") return t.split(",").map((s) => s.trim()).filter(Boolean);
  return raw;
}

export function displayValue(prop: SchemaProp, value: unknown): string {
  if (value === undefined || value === null) return "";
  if (Array.isArray(value)) return value.join(", ");
  return String(value);
}

export function formatAge(days: number | null | undefined): string {
  if (days === null || days === undefined) return "—";
  if (days < 0) return "before birth";
  if (days < 61) return `${days} d`;
  const months = Math.floor(days / 30.4375);
  return months < 24 ? `${months} mo` : `${Math.floor(months / 12)} y ${months % 12} mo`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const s = Math.round(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function formatBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function formatDate(iso: string | null | undefined): string {
  return iso ? iso.slice(0, 10) : "—";
}

/** Page window for the knowledge list: "11–20 of 134". */
export function pageLabel(offset: number, limit: number, total: number): string {
  if (total === 0) return "0 entries";
  return `${offset + 1}–${Math.min(offset + limit, total)} of ${total}`;
}

/** Status words of a pipeline step -> a badge tone. */
export function stepTone(status: string | undefined): "ok" | "bad" | "wait" | "none" {
  if (status === "done") return "ok";
  if (status === "failed" || status === "unconfirmed") return "bad"; // unconfirmed: check the channel, then `deliver --resend`
  if (status === "running" || status === "waiting" || status === "invalidated" || status === "sending" || status === "retry") return "wait";
  return "none";
}

/** A new, empty source of the given type, with a key not yet in use. */
export function newSource(type: string, taken: string[]): Json {
  const base = type.replace(/_/g, "-");
  let key = base;
  let i = 2;
  while (taken.includes(key)) key = `${base}-${i++}`;
  return { key, type, enabled: true, options: {}, allowed_destinations: ["local"] };
}

export function isDirty(a: Json, b: Json): boolean {
  return JSON.stringify(a) !== JSON.stringify(b);
}

const WORDS: Record<string, string> = { kb: "KB", http: "HTTP", url: "URL", id: "ID", dirs: "folders", dir: "folder", bin: "program", max: "Maximum", min: "Minimum" };

/** A readable label for a config key: `http_timeout_s` -> "HTTP timeout (seconds)". */
export function humanizeKey(key: string): string {
  const parts = key.split("_");
  const unit = parts[parts.length - 1] === "s" && parts.length > 1 ? " (seconds)" : "";
  const words = (unit ? parts.slice(0, -1) : parts).map((w, i) => WORDS[w] ?? (i === 0 ? w.charAt(0).toUpperCase() + w.slice(1) : w));
  const label = words.join(" ");
  return (label.charAt(0).toUpperCase() + label.slice(1)) + unit;
}

export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

export type BriefBlock = { kind: "h1" | "h2" | "li" | "p"; parts: { text: string; bold: boolean }[] };

function inline(text: string): { text: string; bold: boolean }[] {
  return text.split(/(\*\*[^*]+\*\*)/).filter(Boolean).map((s) => (s.startsWith("**") && s.endsWith("**") ? { text: s.slice(2, -2), bold: true } : { text: s, bold: false }));
}

/** The brief is plain Markdown written by the plugin: headings, bullets, bold, paragraphs. Parsed into blocks that React
 *  renders as TEXT (never as HTML), so nothing in a note can inject markup. HTML comments are dropped. */
export function briefBlocks(md: string): BriefBlock[] {
  const out: BriefBlock[] = [];
  for (const raw of md.replace(/<!--.*?-->/gs, "").split("\n")) {
    const line = raw.trimEnd();
    if (!line.trim()) continue;
    if (line.startsWith("## ")) out.push({ kind: "h2", parts: inline(line.slice(3)) });
    else if (line.startsWith("# ")) out.push({ kind: "h1", parts: inline(line.slice(2)) });
    else if (/^[-*] /.test(line)) out.push({ kind: "li", parts: inline(line.slice(2)) });
    else out.push({ kind: "p", parts: inline(line) });
  }
  return out;
}

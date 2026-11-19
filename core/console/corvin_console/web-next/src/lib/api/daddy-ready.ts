/**
 * DaddyReady — typed calls to the console's own `/plugins/daddy-ready/*` routes (private plugin `daddy_ready`).
 * Reads are plain GETs; the video is a same-origin URL the <video> element fetches with the session cookie.
 */
import { api, BASE } from "./client";

const P = "/plugins/daddy-ready";

export type SchemaProp = {
  type: "string" | "integer" | "number" | "boolean" | "array" | "object";
  enum?: string[];
  default?: unknown;
  minimum?: number;
  maximum?: number;
  items?: SchemaProp;
  secret?: boolean;
  description?: string;
};
export type BlockSchema = { type: "object"; properties?: Record<string, SchemaProp>; required?: string[] };

export interface Overview {
  child_name: string;
  birth_date: string | null;
  age_months: number | null;
  user: string;
  counts: Record<string, number>;
  paused: boolean;
  length_minutes: number;
  language: string;
  episodes: number;
  sources: { key: string; type: string; enabled: boolean; destinations: string[] }[];
  video_enabled: boolean;
  delivery_enabled: boolean;
  approvals: Record<string, string>;
  rejected: number;
}
export interface KbItem {
  id: string;
  type: string;
  captured_at: string | null;
  age_days: number | null;
  source_key: string | null;
  author: string | null;
  snippet: string;
}
export interface KbPage { total: number; offset: number; limit: number; items: KbItem[] }
export interface KbEntry { id: string; type: string; meta: Record<string, unknown>; body: string }
export interface EpisodeRow {
  day: string;
  has_brief: boolean;
  has_video: boolean;
  video_bytes: number | null;
  duration_s: number | null;
  method: string | null;
  steps: Record<string, string>;
}
export interface Episodes { episodes: EpisodeRow[]; autoplay: boolean }
export interface SettingsDoc {
  config: Record<string, unknown>;
  blocks: Record<string, BlockSchema>;
  destinations: string[];
  source_types: Record<string, { display_name: string; schema: BlockSchema; capabilities: string[] }>;
  locked: string[];
  defaults: { data_dir: string };
  effective_data_dir: string;
}

export const getOverview = (signal?: AbortSignal) => api<Overview>(`${P}/overview`, { signal });
export const getKb = (p: { type?: string; q?: string; offset?: number; limit?: number }, signal?: AbortSignal) => {
  const qs = new URLSearchParams();
  if (p.type) qs.set("type", p.type);
  if (p.q) qs.set("q", p.q);
  if (p.offset) qs.set("offset", String(p.offset));
  if (p.limit) qs.set("limit", String(p.limit));
  const s = qs.toString();
  return api<KbPage>(`${P}/kb${s ? `?${s}` : ""}`, { signal });
};
export const getKbEntry = (id: string, signal?: AbortSignal) => api<KbEntry>(`${P}/kb/${encodeURIComponent(id)}`, { signal });
export const getEpisodes = (signal?: AbortSignal) => api<Episodes>(`${P}/episodes`, { signal });
export const getBrief = (day: string, signal?: AbortSignal) =>
  api<{ day: string; markdown: string }>(`${P}/episodes/${encodeURIComponent(day)}/brief`, { signal });
export const videoUrl = (day: string) => `${BASE}${P}/episodes/${encodeURIComponent(day)}/video`;
export const getSettings = (signal?: AbortSignal) => api<SettingsDoc>(`${P}/settings`, { signal });
export const saveSettings = (config: Record<string, unknown>, csrf: string) =>
  api<{ ok: boolean; backup: string }>(`${P}/settings`, { method: "PUT", csrf, body: config });

export interface GraphNode { id: string; type: string; label: string; day: string | null; source: string | null }
export interface GraphEdge { from: string; to: string; rel: "used_in" | "method_of" }
export interface GraphDoc { nodes: GraphNode[]; edges: GraphEdge[]; truncated: boolean }
export const getGraph = (signal?: AbortSignal) => api<GraphDoc>(`${P}/graph`, { signal });
export const renderEpisode = (day: string, csrf: string) =>
  api<{ day: string; status: string }>(`${P}/episodes/${encodeURIComponent(day)}/render`, { method: "POST", csrf });

export interface TemplateVideo { name: string; concept: string; day: string; bytes: number; duration_s: number | null; made_at: string }
export interface VideoList { videos: TemplateVideo[]; jobs: { concept: string; state: "running" | "done" | "failed" }[] }
export interface ConceptRow { id: string; name: string; definition: string; source: string; verified: boolean; age_min: number; age_max: number }
export interface ConceptList { concepts: ConceptRow[]; photos: number; language: string }
export const getVideos = (signal?: AbortSignal) => api<VideoList>(`${P}/videos`, { signal });
export const getConcepts = (signal?: AbortSignal) => api<ConceptList>(`${P}/concepts`, { signal });
export const templateVideoUrl = (name: string) => `${BASE}${P}/videos/${encodeURIComponent(name)}`;
export const makeConceptVideo = (id: string, csrf: string) =>
  api<{ concept: string; state: string }>(`${P}/videos/concept/${encodeURIComponent(id)}`, { method: "POST", csrf });
